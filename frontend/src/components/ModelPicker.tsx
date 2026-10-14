import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { SWITCHER_PRESETS, displayNameForModel, effortLabel, effortsFor } from '../modelPresets';

/**
 * 统一的模型切换器（第 48 班：首页与任务页同款——用户反馈"为什么两处不一样"）。
 *
 * 形态与任务页的权限/身份控件同族：`ctl-btn` 按钮 + `ctl-pop` 弹层，
 * 显示**友好名**（MiMo（小米）/ 智谱 GLM / …），点击即保存（无需失焦）。
 * Escape / 点外部关闭，机制与 TaskView 的全局弹层监听一致。
 *
 * props：
 *   current     —— 父组件已加载的当前模型名（model_name）；不传则组件自行加载一次
 *   onChanged   —— 切换成功后回传新的 model_name（父组件同步自己的状态）
 *   onOpen      —— 弹层展开时回调（页面用它关掉自己打开的其它弹层，保证同时只开一个）
 */
export function ModelPicker({
  current,
  onChanged,
  onOpen,
}: {
  current?: string;
  onChanged?: (model_name: string) => void;
  onOpen?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [selfName, setSelfName] = useState('');
  const [loaded, setLoaded] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  // 父组件没给 current 时自己加载一次当前模型（首页场景）
  useEffect(() => {
    if (current !== undefined || loaded) return;
    setLoaded(true);
    void api.getSettings().then((s) => {
      const m = (s as { model?: { model_name?: string } }).model;
      setSelfName(m?.model_name ?? '');
    }).catch(() => undefined);
  }, [current, loaded]);

  const modelName = current !== undefined ? current : selfName;

  // Escape / 点外部关闭（与 TaskView 全局弹层监听同机制：.ctl-wrap 内的点击交给 onClick）
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    const onDown = (e: MouseEvent) => {
      const el = e.target as HTMLElement | null;
      // 十二轮 🔴2：只豁免【自己这个】.ctl-wrap——此前豁免任何 .ctl-wrap，
      // 导致点权限按钮时模型弹层不关（两个弹层同屏）
      if (el && rootRef.current && rootRef.current.contains(el)) return;
      setOpen(false);
    };
    window.addEventListener('keydown', onKey);
    window.addEventListener('mousedown', onDown);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('mousedown', onDown);
    };
  }, [open]);

  // 十九轮 🔴3：右缘夹取【整体移交 CSS】（见 styles.css @container：宽度公式 +
  // 按 wrap 位置择边 + ≤390 翻转）。JS clamp 删除的根因：单跳大 resize 时
  // 过期坐标 + left:'4px' 相对 .ctl-wrap 而非视口——坐标系统性错误无法局部修好。
  // ★ A5：显示名走共享表的 displayNameForModel——此前只查 6 项预设，
  //   跑 Claude/Mock 时按钮上直接显示原始 id（claude-sonnet-4-20250514）。
  const display = displayNameForModel(modelName);

  // ★★ 2026-10-14（用户要求）：**思考档位**就放在这个弹层里 ——
  //   原话：「多麻烦，你能不能就在这个界面……点这个模型，然后左面或右面就出现一个
  //          可以选项的那种……这个模型的思考方式，包括档位……根据模型来」
  //   ⇒ 每个模型行下面显示**它自己支持的档**（不支持的不显示，不许硬塞 ✗）
  const [effort, setEffort] = useState('');
  const [savingEffort, setSavingEffort] = useState(false);

  // 打开时读一次当前档位（后端 `reasoning_effort` 字段）
  useEffect(() => {
    if (!open) return;
    void api.getSettings().then((s) => {
      const m = (s as { model?: { reasoning_effort?: string } }).model;
      setEffort(m?.reasoning_effort ?? '');
    }).catch(() => undefined);
  }, [open]);

  const pickEffort = (level: string): void => {
    setSavingEffort(true);
    void api.setThinking(level)
      .then(() => setEffort(level))
      .catch(() => undefined)
      .finally(() => setSavingEffort(false));
    // ★ 不关弹层：用户可能还想接着改 ✓（与选模型不同）
  };

  const pick = (model_name: string): void => {
    const p = SWITCHER_PRESETS.find((x) => x.model_name === model_name);
    if (!p) return;
    if (p.model_name === modelName) {  // 十四轮④：已选中 → 不再重复 POST（冗余落盘）
      setOpen(false);
      return;
    }
    // 十二轮 🔴3：provider 用预设里的显式值——providerForModel 的 qwen 前缀
    // 匹配会把 ollama 的 qwen3.5:9b 错标成 qwen
    void api
      .setModel(p.provider, p.model_name, p.base_url, null, p.key_env ?? null)
      .then(() => {
        if (current === undefined) setSelfName(p.model_name);
        onChanged?.(p.model_name);
        setOpen(false);
      })
      .catch(() => undefined);
  };

  return (
    <div className="ctl-wrap" ref={rootRef}>
      <button
        className="ctl-btn ctl-model"
        onClick={() => {
          setOpen((v) => {
            if (!v) onOpen?.();
            return !v;
          });
        }}
        title="当前模型"
      >
        {display || '模型'} ▾
      </button>
      {open && (
        <div className="ctl-pop">
          {/* A5：清单来自共享表（SWITCHER_PRESETS）——不许在这里再写死一份 */}
          {SWITCHER_PRESETS.map((p) => {
            // ★★ 2026-10-14：**这个模型支持哪几档思考**（空 = 不支持 ⇒ 不显示那一行）✓
            const levels = effortsFor(p.provider);
            const isCur = p.model_name === modelName;
            return (
              <div key={p.model_name} className="ctl-model-block">
                <button
                  className={'ctl-item' + (isCur ? ' ctl-on' : '')}
                  onClick={() => pick(p.model_name)}
                >
                  {p.label}
                  <span>{p.model_name}</span>
                </button>
                {/* ★ 思考档位：只给**当前选中**的模型显示（就是用户在调的那一个）✓ */}
                {isCur && levels.length > 0 && (
                  <div className="ctl-effort" role="group" aria-label="思考档位">
                    <span className="ctl-effort-title">思考</span>
                    {levels.map((lv) => (
                      <button
                        key={lv}
                        className={'ctl-effort-btn' + (effort === lv ? ' ctl-on' : '')}
                        disabled={savingEffort}
                        title={
                          lv === 'off'
                            ? '关掉思考：快、省钱；简单活够用，硬活会变笨'
                            : lv === 'high'
                              ? '最高档：慢、贵；写代码/排错这类硬活靠它'
                              : `思考强度：${effortLabel(p.provider, lv)}`
                        }
                        onClick={() => pickEffort(lv)}
                      >
                        {effortLabel(p.provider, lv)}
                      </button>
                    ))}
                    {effort === '' && (
                      <span className="ctl-effort-note">（默认）</span>
                    )}
                  </div>
                )}
              </div>
            );
          })}
          <div className="ctl-hint">更多模型/Key 配置在 设置 → 模型</div>
        </div>
      )}
    </div>
  );
}
