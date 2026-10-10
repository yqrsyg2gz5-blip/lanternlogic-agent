import { useEffect, useState } from 'react';
import { api } from '../api';
import { useVoiceDraft } from '../lib/useVoiceDraft';
import { Mic, Square } from 'lucide-react';
import { ModelPicker } from './ModelPicker';

/**
 * 空态欢迎页（对标 DSH `conversation.hero` / Codex 首屏）。
 *
 * 布局要点（2026-09-30 用户反馈后修正）：
 *   ① **输入框放底部** —— 原来居中，用户指出"太往上了，Codex/DSH 都在底部"；
 *   ② **创建后把任务 id 回调给上层** —— 原来只刷新列表、不选中，
 *      用户建完任务还得手动点右侧才看得见（真 bug）。
 *
 * 样式用内联对象写死，**刻意不动 `styles.css`**（第 33/34 班连续两次因该文件的
 * 编辑追踪失效而落空）；后续统一设计系统时再迁进 `.hero-*`。
 */
const S = {
  page: { display: 'flex', flexDirection: 'column' as const, height: '100%', minHeight: 0 },
  top: {
    flex: '1 1 auto',
    display: 'flex',
    flexDirection: 'column' as const,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    padding: '0 24px',
  },
  mark: { fontSize: 44, opacity: 0.9, lineHeight: 1 },
  title: { fontSize: 26, fontWeight: 600, margin: '8px 0 0' },
  sub: { fontSize: 13, opacity: 0.6, margin: 0 },
  bottom: {
    flex: '0 0 auto',
    display: 'flex',
    flexDirection: 'column' as const,
    alignItems: 'center',
    gap: 10,
    padding: '0 24px 26px',
  },
  chips: {
    display: 'flex',
    flexWrap: 'wrap' as const,
    gap: 8,
    justifyContent: 'center',
    maxWidth: 780, // 第 48 班：与输入框同步加宽（用户反馈"再宽一点"）
  },
  chip: {
    fontSize: 12,
    padding: '6px 10px',
    borderRadius: 999,
    border: '1px solid var(--border)',
    background: 'transparent',
    color: 'inherit',
    cursor: 'pointer',
    opacity: 0.85,
  },
  box: { width: '100%', maxWidth: 780 }, // 第 48 班：680→780（用户反馈"输入框再宽一点"）
  input: {
    width: '100%',
    minHeight: 84,
    padding: '12px 14px',
    borderRadius: 12,
    border: '1px solid var(--border)',
    background: 'rgba(0,0,0,0.28)',
    color: 'inherit',
    fontFamily: 'inherit',
    fontSize: 14,
    lineHeight: 1.6,
    resize: 'vertical' as const,
    outline: 'none',
  },
  row: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: 8,
    gap: 10,
    // 十二轮 🔴1 回归修正：本行【禁止 wrap】——.btn-primary{width:100%} 的
    // flex 基准是整行宽，允许换行时按钮会被甩到第二行并撑满 780px（截图里那条
    // 横贯长条）。窄屏防溢出改用「提示可收缩(ellipsis)、按钮/选择器不缩」。
  },
  hint: {
    fontSize: 12,
    opacity: 0.5,
    whiteSpace: 'nowrap' as const,
    overflow: 'hidden' as const,
    textOverflow: 'ellipsis' as const,
    minWidth: 0,
  },
  err: { fontSize: 12, color: '#f0736a', marginTop: 6 },
  // 「首次上手」提示条（Phase 1 ①）：Key 没配时第一眼就能看见、知道去哪配。
  // 刻意用内联样式（与本文件其它样式同规矩：不动 styles.css）。
  setup: {
    maxWidth: 780,
    width: '100%',
    boxSizing: 'border-box' as const,
    textAlign: 'left' as const,
    padding: '10px 12px',
    borderRadius: 10,
    border: '1px solid rgba(247,178,106,0.5)',
    background: 'rgba(247,178,106,0.10)',
    display: 'flex',
    flexDirection: 'column' as const,
    gap: 6,
  },
  setupTitle: { fontSize: 13, fontWeight: 600, color: '#f7b26a' },
  setupHint: { fontSize: 12, lineHeight: 1.6, opacity: 0.75 },
} as const;

const SAMPLES = [
  '把 Downloads 里的图片按类型分类，产出一份清单',
  '联网调研一个主题，给出带来源的结论',
  '把这个文件夹里的资料整理成一份表格',
];

export function Hero({ onCreated, hotkeyEnabled = true }: {
  onCreated(taskId: string): void;
  /** 首页输入框与任务内输入框可能同时挂载 ⇒ 由上层决定快捷键归谁（避免两边一起录） */
  hotkeyEnabled?: boolean;
}) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // ★ Phase 1 ①「首次上手」第一步：还没配模型 Key 时，第一眼就要看见
  //   —— 此前新用户看到的是一个能输入、但发出去永远不回话的空态
  //   （后端 `key_set:false` 时 provider 构造就失败，建任务直接 503）。
  //   判据用 `=== false`（三态）：true=已配、false=**需要但没配**、
  //   null=这个提供者不需要 Key（本地 Ollama / Mock）⇒ 不许误报。
  const [needKey, setNeedKey] = useState<{ provider: string; keyEnv: string } | null>(null);
  useEffect(() => {
    void api.getSettings().then((s) => {
      const mdl = (s as { model?: { key_set?: boolean | null; provider?: string; api_key_env?: string } }).model;
      if (mdl?.key_set === false) {
        setNeedKey({ provider: String(mdl.provider ?? ''), keyEnv: String(mdl.api_key_env ?? '') });
      }
    }).catch(() => undefined);
  }, []);
  // 语音草稿（录音→转写→填输入框）
  // ★ 2026-10-05 改用**唯一实现** `lib/useVoiceDraft`：
  //   此前这里和 TaskView 各写一份，我修了任务内那个、**首页这个照旧坏着** ——
  //   而用户日常用的正是这个"在输入框下方说话"。一份实现、两处调用，才不会再各修各的。
  const voice = useVoiceDraft({
    onText: (t) => setText((prev) => (prev ? `${prev} ${t}` : t)),
    hotkeyEnabled,
  });
  const voiceOn = voice.on;
  const voiceHint = voice.hint;

  const go = async (value?: string): Promise<void> => {
    const v = (value ?? text).trim();
    if (!v || busy) return;
    setBusy(true);
    setErr(null);
    try {
      const t = await api.createTask(v);
      setText('');
      // ★ 必须把新任务 id 交回上层去 setCurrentId —— 否则建完还停在欢迎页
      onCreated(t.id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="taskview" style={S.page}>
      <div style={S.top}>
        <div style={S.mark}>◍</div>
        <h1 style={S.title}>让 Agent 去做那件事</h1>
        <p style={S.sub}>本地运行 · 数据不出本机 · 危险动作先问你</p>
      </div>

      <div style={S.bottom}>
        {needKey && (
          <div style={S.setup} role="status">
            <span style={S.setupTitle}>⚠ 还没配模型 Key —— 现在发消息，Agent 不会回你</span>
            <span style={S.setupHint}>
              ① 打开左下角「设置 → 模型设置」　② 选服务商（推荐 MiMo，便宜够用）　③ 粘贴 Key 后保存。
              Key 只写进本机环境变量（{needKey.keyEnv || '—'}），<b>不写进配置文件</b>；保存后按提示重启一次后端即可。
              {needKey.provider ? `（当前提供者：${needKey.provider}）` : ''}
            </span>
            <button
              className="btn-primary"
              style={{ width: 'auto', alignSelf: 'flex-start', marginTop: 2, padding: '6px 14px' }}
              // 设置面板由侧栏持有（Hero 拿不到它的 state）。这里直接点侧栏那个入口按钮：
              // 一行、不引入跨组件状态提升，窄屏下按钮在抽屉里也照样能被程序点中。
              onClick={() => {
                const fab = document.querySelector<HTMLButtonElement>('button[title="设置"]');
                fab?.click();
              }}
            >
              去设置
            </button>
          </div>
        )}
        <div style={S.chips}>
          {SAMPLES.map((s) => (
            <button key={s} style={S.chip} disabled={busy} onClick={() => void go(s)}>
              {s}
            </button>
          ))}
        </div>
        <div style={S.box}>
          <textarea
            style={S.input}
            value={text}
            placeholder="描述你要它做的事…（Enter 发送 · Shift+Enter 换行）"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void go(); }
            }}
          />
          <div style={S.row}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
              <button
                className={`voice-btn ${voiceOn ? 'voice-on' : ''}`}
                aria-label="语音输入"
                onClick={() => voice.toggle()}
                title={voiceOn ? '停止并填入输入框（发送前可检查修改）' : '语音输入：点一下开始、再点一下停止；也可以按住右 Ctrl 说话，松开自动转写'}
              >
                {voiceOn ? <Square size={13} /> : <Mic size={14} />}
              </button>
              {/* 第 48 班：与任务页同款模型切换器（此前是原生 select，切换后还会在
                  旁边冒一行"模型已切到…"提示把整行挤乱——用户反馈"不整齐"）。
                  固定 min-width 在组件内，切到长名字也不再横向跳动。 */}
              <ModelPicker />
            </div>
            <span style={S.hint}>Enter 发送 · 任务记在左侧列表 · 按住 <b>右 Ctrl</b> 说话</span>
            <button
              className="btn-primary"
              style={{ width: 'auto', flex: '0 0 auto', marginTop: 0 }}  // 十四轮③：根因是 .btn-primary{margin-top:8px} 让按钮盒下沉 4px
              disabled={!text.trim() || busy}
              onClick={() => void go()}
            >
              {busy ? '创建中…' : '开始'}
            </button>
          </div>
          {/* 语音类提示（没听清/转写失败等）固定在行**下方**——不再插进行内挤动布局 */}
          {voiceHint && (
            <div style={{ fontSize: 11.5, opacity: 0.7, marginTop: 6 }}>{voiceHint}</div>
          )}
          {err && <div style={S.err}>创建失败：{err}</div>}
        </div>
      </div>
    </main>
  );
}
