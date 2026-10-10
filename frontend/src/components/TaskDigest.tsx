/**
 * ★ 「这一趟做了什么」—— 折叠式**全过程小结**（用户点名的功能）。
 *
 * 用户的诉求（原话）：
 *   "底下有个折叠…就相当于给你一个总结…不用再往上翻之前说啥，而是在这里面就总结了，
 *    一次一下子就看到了，想看直接点开看"
 * 所以它不是"把回复切碎"，而是：**上面照旧问一个回一个**，底下给一个可折叠的总账。
 *
 * 内容全部**由本地事件推导**（不调模型、不花 token、瞬时给出）：
 *   你提了几次要求 → 它跑了哪些工具（按工具归并计数）→ 有没有失败 → 产出哪些文件
 *   → 用了多久 → 最后一句答复
 * 想细化成"模型写的散文总结"是下一步的事（那要花 token，且要挑时机触发）。
 */
import { useMemo } from 'react';
import type {
  ActionPayload, EventEnvelope, MessagePayload, ObservationPayload,
} from '../types';

type Props = { events: EventEnvelope[]; running?: boolean };

const TOOL_LABEL: Record<string, string> = {
  shell_exec: '跑命令', file_write: '写文件', file_read: '读文件', list_dir: '看目录',
  web_fetch: '抓网页', web_search: '联网搜', browser_navigate: '开浏览器', browser_click: '点页面',
  browser_type: '填表单', load_skill: '用技能', image_gen: '生成图片', image_read: '看图',
  video_gen: '生成视频', speak: '说话', wide_research: '深度调研', task_done: '交付',
  update_plan: '更新计划', mcp_call: '用外部工具', ask_user: '问你',
};

/** 摘要：与 EventItem 的 summarize 同款取第一个有用字段（这里不 import，避免循环依赖） */
function brief(params: Record<string, unknown>): string {
  const s = (k: string): string => (typeof params?.[k] === 'string' ? String(params[k]) : '');
  const v = s('command') || s('path') || s('url') || s('query') || s('text') || s('name') || s('prompt') || s('message');
  const one = v.replace(/\s+/g, ' ').trim();
  return one.length > 64 ? one.slice(0, 64) + ' …' : one;
}

function fmtDur(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60000)} 分 ${Math.round((ms % 60000) / 1000)} 秒`;
}

export function TaskDigest({ events, running }: Props) {
  const d = useMemo(() => {
    const asks: string[] = [];
    const steps: Array<{ tool: string; label: string; brief: string; ok: boolean | null; ms: number }> = [];
    const files = new Set<string>();
    const sources = new Set<string>();
    let fails = 0;
    let lastText = '';
    const byTool: Record<string, number> = {};
    const obsByCall: Record<string, ObservationPayload> = {};

    for (const e of events) {
      if (e.type === 'observation') {
        const o = e.payload as ObservationPayload;
        obsByCall[o.call_id] = o;
      }
    }
    for (const e of events) {
      if (e.type === 'message') {
        const m = e.payload as MessagePayload;
        if (m.role === 'user') asks.push(m.text);
        else lastText = m.text || lastText;
        (m.sources ?? []).forEach((s) => sources.add(s.title || s.url));
        (m.attachments ?? []).forEach((a) => files.add(a));
      } else if (e.type === 'action') {
        const a = e.payload as ActionPayload;
        const o = obsByCall[a.call_id];
        const ok = o ? o.ok : null;
        if (ok === false) fails += 1;
        byTool[a.tool] = (byTool[a.tool] ?? 0) + 1;
        const p = (a.params ?? {}) as Record<string, unknown>;
        if (a.tool === 'file_write' && typeof p.path === 'string') files.add(p.path);
        steps.push({
          tool: a.tool,
          label: TOOL_LABEL[a.tool] ?? a.tool,
          brief: brief(p),
          ok,
          ms: o?.duration_ms ?? 0,
        });
      }
    }
    const totalMs = Object.values(obsByCall).reduce((n, o) => n + (o.duration_ms || 0), 0);
    const counts = Object.entries(byTool).sort((a, b) => b[1] - a[1]);
    return { asks, steps, files: [...files], sources: [...sources], fails, lastText, counts, totalMs };
  }, [events]);

  if (d.steps.length === 0 && d.asks.length === 0) return null;

  const head = [
    d.asks.length ? `你说过 ${d.asks.length} 次` : '',
    d.steps.length ? `${d.steps.length} 步` : '',
    ...d.counts.slice(0, 3).map(([t, n]) => `${TOOL_LABEL[t] ?? t}×${n}`),
    d.fails ? `⚠ ${d.fails} 步失败` : '',
    d.files.length ? `产出 ${d.files.length} 个文件` : '',
    d.totalMs ? `工具耗时 ${fmtDur(d.totalMs)}` : '',
  ].filter(Boolean).join(' · ');

  return (
    <details
      className="digest"
      // 点开时把它滚进视野：它钉在对话流最底下，不滚一下会"展开了却看不见"
      onToggle={(e) => {
        const el = e.currentTarget;
        if (el.open) setTimeout(() => el.scrollIntoView({ block: 'end', behavior: 'smooth' }), 60);
      }}
    >
      <summary>
        <span className="digest-icon">☰</span>
        <b>这一趟做了什么</b>
        <span className="digest-head">{head}</span>
        <span className="digest-hint">点开看全过程（不用往上翻）</span>
      </summary>
      <div className="digest-body">
        {d.asks.length > 0 && (
          <section className="digest-sec">
            <div className="digest-sec-title">你提的要求</div>
            <ol className="digest-asks">
              {d.asks.map((a, i) => <li key={i}>{a.length > 120 ? a.slice(0, 120) + ' …' : a}</li>)}
            </ol>
          </section>
        )}
        <section className="digest-sec">
          <div className="digest-sec-title">过程（{d.steps.length} 步{running ? '，还在跑' : ''}）</div>
          <ol className="digest-steps">
            {d.steps.map((s, i) => (
              <li key={i} className={s.ok === false ? 'digest-step-fail' : s.ok === null ? 'digest-step-run' : ''}>
                <span className="digest-step-mark">{s.ok === false ? '✗' : s.ok === null ? '…' : '✓'}</span>
                <span className="digest-step-label">{s.label}</span>
                <span className="digest-step-brief mono" title={s.brief}>{s.brief}</span>
                {s.ms ? <span className="digest-step-ms">{fmtDur(s.ms)}</span> : null}
              </li>
            ))}
          </ol>
        </section>
        {(d.files.length > 0 || d.sources.length > 0) && (
          <section className="digest-sec">
            <div className="digest-sec-title">产出与来源</div>
            <div className="digest-chips">
              {d.files.map((f) => <span className="chip" key={f}>{f.split('/').pop()}</span>)}
              {d.sources.map((s) => <span className="chip" key={s}>来源：{s}</span>)}
            </div>
          </section>
        )}
        {d.lastText && (
          <section className="digest-sec">
            <div className="digest-sec-title">它最后说的</div>
            {/* ★ 用户点出"跟上面重复" ⇒ 这里不再抄全文，只留一行指引 + 点一下跳到那条消息 */}
            <button
              className="digest-jump"
              title="跳到上面那条完整回复"
              onClick={() => {
                const rows = document.querySelectorAll('.stream .msg-assistant');
                rows[rows.length - 1]?.scrollIntoView({ block: 'center', behavior: 'smooth' });
              }}
            >
              <span className="mono">{d.lastText.replace(/\s+/g, ' ').slice(0, 56)}{d.lastText.length > 56 ? ' …' : ''}</span>
              <span className="digest-jump-hint">就是上面那条 · 点这里跳过去 ↑</span>
            </button>
          </section>
        )}
      </div>
    </details>
  );
}
