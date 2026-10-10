/**
 * ★ Phase 3 ⑦ 对话界面重做 —— **可点原型**（先看效果再动真界面）。
 *
 * 为什么先做原型：真界面（TaskView）牵着 40+ 个已验收的行为（审批条、宿主模式告知、
 * 跳到底部、用量面板…），闷头重写等于把这些一次性推翻。原型只做"新观感"，
 * 用**假数据**（形状与真实事件一致，见 src/types.ts），你看过拍板后再考虑迁移。
 *
 * 打开方式：地址后加 `?proto=chat`（例如 http://127.0.0.1:5173/?proto=chat）。
 * 它**不调任何接口**、不写任何状态，删掉这个文件 + App.tsx 里那 3 行分支即可完全回退。
 *
 * 新观感（对照现在的"日志流"）：
 *   ① 一轮一组：用户气泡右对齐、助手回复左对齐占满，工具调用收进卡片
 *   ② 工具调用 = 可折叠卡片：图标 + 工具名 + 一行摘要 + 状态点 + 耗时；展开看参数与结果
 *   ③ 流式：正文带光标 + "生成中"，思考过程可折叠（不刷屏）
 *   ④ 交付来源做成小胶囊；助手消息有复制按钮
 *   ⑤ 底部输入区常驻，右上有"跳到最新"
 */
import { useState } from 'react';
import type { ActionPayload, MessagePayload, ObservationPayload, PlanStep } from '../types';

type Turn = {
  user: MessagePayload;
  assistant?: MessagePayload & { streaming?: boolean };
  tools: Array<{ action: ActionPayload; observation?: ObservationPayload }>;
  plan?: { steps: PlanStep[]; reflection?: string };
  time: string;
};

// —— 假数据（形状与真实事件一致，便于日后直接换成真事件流）——
const TURNS: Turn[] = [
  {
    user: { role: 'user', text: '把 Downloads 里的图片按类型分类，产出一份清单' },
    time: '22:41',
    assistant: {
      role: 'assistant', streaming: true,
      text: '我先看一下 Downloads 里都有什么，再按扩展名分组统计。\n\n已经扫到 3 类共 128 个图片文件，正在归类并生成清单……',
      sources: [{ title: 'Downloads 目录扫描结果', url: 'file:///C:/Users/y/Downloads' }],
    },
    tools: [
      {
        action: { tool: 'shell', params: { command: 'dir /b C:\\Users\\y\\Downloads\\*.png' }, call_id: 'c1' },
        observation: { call_id: 'c1', ok: true, duration_ms: 412, result: 'a.png\nb.png\n…（共 128 行）' },
      },
      {
        action: { tool: 'file_write', params: { path: 'workspace/图片清单.md', content: '# 图片清单\n…' }, call_id: 'c2' },
        observation: { call_id: 'c2', ok: true, duration_ms: 88, result: '已写入 128 行' },
      },
    ],
    plan: {
      steps: [
        { no: 1, text: '扫描 Downloads 图片', status: 'done' },
        { no: 2, text: '按扩展名分类统计', status: 'done' },
        { no: 3, text: '写入清单文件', status: 'in_progress' },
      ],
      reflection: '数量比预期多，按类型分三张表更清楚。',
    },
  },
  {
    user: { role: 'user', text: '顺手把重复的挑出来' },
    time: '22:47',
    assistant: {
      role: 'assistant',
      text: '好，我用文件内容哈希比对，重复的单独列一节。',
    },
    tools: [
      {
        action: { tool: 'shell', params: { command: 'certutil -hashfile a.png SHA1' }, call_id: 'c3' },
        observation: { call_id: 'c3', ok: false, duration_ms: 1503, result: 'Access is denied.（该文件被占用）' },
      },
    ],
  },
];

const TOOL_ICON: Record<string, string> = {
  shell: '▶', file_write: '✎', file_read: '▤', web_fetch: '⇩', web_search: '⌕', mcp: '⚙',
};

function summary(a: ActionPayload): string {
  const p = a.params || {};
  const first = p.command ?? p.path ?? p.url ?? p.query ?? p.name ?? '';
  return String(first).slice(0, 76) || '(无参数)';
}

function ToolCard({ action, observation }: { action: ActionPayload; observation?: ObservationPayload }) {
  const [open, setOpen] = useState(false);
  const state = !observation ? 'run' : observation.ok ? 'ok' : 'fail';
  return (
    <div className={`cp-tool cp-${state}`}>
      <button className="cp-tool-head" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="cp-caret">{open ? '▾' : '▸'}</span>
        <span className="cp-tool-icon mono">{TOOL_ICON[action.tool] ?? '●'}</span>
        <span className="cp-tool-name mono">{action.tool}</span>
        <span className="cp-tool-sum mono">{summary(action)}</span>
        <span className="cp-tool-state">
          {state === 'run' ? '运行中…' : state === 'ok' ? `✓ ${observation?.duration_ms}ms` : '✗ 失败'}
        </span>
      </button>
      {open && (
        <div className="cp-tool-body">
          <div className="cp-tool-label">参数</div>
          <pre className="cp-pre mono">{JSON.stringify(action.params, null, 2)}</pre>
          {observation && (
            <>
              <div className="cp-tool-label">结果{observation.ok ? '' : '（失败）'}</div>
              <pre className="cp-pre mono">{observation.result}</pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function Assistant({ msg }: { msg: MessagePayload & { streaming?: boolean } }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="cp-assistant">
      <div className="cp-assistant-head">
        <span className="cp-avatar">◍</span>
        <span className="cp-who">Agent</span>
        {msg.streaming && <span className="cp-live">生成中…</span>}
        <button
          className="cp-copy"
          onClick={() => {
            void navigator.clipboard?.writeText(msg.text).catch(() => undefined);
            setCopied(true);
            setTimeout(() => setCopied(false), 1200);
          }}
        >
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      <div className="cp-text">
        {msg.text.split('\n').map((line, i) => (
          <p key={i}>{line || '\u00a0'}</p>
        ))}
        {msg.streaming && <span className="cp-cursor" />}
      </div>
      {msg.sources?.length ? (
        <div className="cp-sources">
          <span className="cp-tool-label">来源</span>
          {msg.sources.map((s) => (
            <span className="cp-chip" key={s.url}>{s.title}</span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function ChatProto({ onExit }: { onExit?: () => void }) {
  const [thinking, setThinking] = useState<Record<number, boolean>>({ 0: true });
  const [jump, setJump] = useState(true);
  return (
    <div className="cp-wrap">
      <header className="cp-head">
        <b>对话界面原型</b>
        <span className="cp-head-note">假数据 · 不调后端 · 看过拍板后再迁移到真界面</span>
        {onExit && <button className="cp-exit" onClick={onExit}>退出原型</button>}
      </header>

      <div className="cp-body">
        {TURNS.map((t, i) => (
          <section className="cp-turn" key={i}>
            <div className="cp-row-user">
              <div className="cp-bubble-user">{t.user.text}</div>
              <div className="cp-time">{t.time}</div>
            </div>

            {t.plan && (
              <div className="cp-think">
                <button className="cp-think-head" onClick={() => setThinking((s) => ({ ...s, [i]: !s[i] }))}>
                  {thinking[i] ? '▾' : '▸'} 思考过程 · {t.plan.steps.filter((s) => s.status === 'done').length}/{t.plan.steps.length} 步已完成
                </button>
                {thinking[i] && (
                  <ol className="cp-steps">
                    {t.plan.steps.map((s) => (
                      <li key={s.no} className={`cp-step cp-${s.status}`}>
                        <span className="cp-dot" />
                        {s.text}
                        <span className="cp-step-state">
                          {s.status === 'done' ? '完成' : s.status === 'in_progress' ? '进行中' : s.status === 'failed' ? '失败' : '待办'}
                        </span>
                      </li>
                    ))}
                    {t.plan.reflection && <li className="cp-reflect">反思：{t.plan.reflection}</li>}
                  </ol>
                )}
              </div>
            )}

            {t.tools.map((x) => <ToolCard key={x.action.call_id} action={x.action} observation={x.observation} />)}

            {t.assistant && <Assistant msg={t.assistant} />}
          </section>
        ))}
      </div>

      {jump && <button className="cp-jump" onClick={() => setJump(false)}>↓ 跳到最新</button>}

      <footer className="cp-composer">
        <textarea placeholder="描述你要它做的事…（Enter 发送 · Shift+Enter 换行）" rows={1} />
        <div className="cp-composer-row">
          <span className="cp-chip">MiMo（小米）</span>
          <span className="cp-hint">Enter 发送 · Shift+Enter 换行</span>
          <button className="cp-send">开始</button>
        </div>
      </footer>
    </div>
  );
}
