import { memo, useState } from 'react';
import type {
  ActionPayload,
  ErrorPayload,
  EventEnvelope,
  KnowledgePayload,
  MessagePayload,
  ObservationPayload,
  PlanPayload,
  StatusPayload,
  DatasourcePayload,
} from '../types';
import { Markdown } from './Markdown';
import { stripInlineToolCalls } from '../lib/sanitize';
import { API_BASE, authedUrl } from '../api';
import { safeHref } from '../lib/safeUrl';
import { Wrench, ListTodo, Paperclip, Link2, BookOpen, Database, AlertTriangle, Globe, Download,
  Terminal, FileText, FilePen, FolderOpen, Search, MousePointerClick, Keyboard, Image as ImageIcon,
  Video, Volume2, CheckCircle2, Plug, MessageCircleQuestion } from 'lucide-react';

/** 单个事件的差异化渲染——七类事件 + error，样式全在 styles.css。
 * 打磨（审计 §9.12）：SSE 每条事件都会重渲染整个时间线，事件对象引用稳定，
 * memo 让旧条目在新事件到达时跳过重渲染（622 次工具调用的任务实测卡顿主因）。 */
function EventItemImpl({ event, taskId, duplicate, onEditResend, highlight }: {
  event: EventEnvelope; taskId?: string;
  /** ★ 应用层去重：这条回复是否"上面已经说过了"（由 TaskView 算好传进来 ——
   *  它是**每个事件稳定**的布尔值，不会破坏 memo） */
  duplicate?: boolean;
  /** ★ 改一句重发：只在任务**没在跑**时由 TaskView 传入（跑着改会被后端 409 拒掉） */
  onEditResend?: (seq: number, text: string) => void;
  /** ★ 对话内搜索的高亮词（在**渲染树**里切 `<mark>`，不碰 DOM —— 见 lib/rehypeHighlight.ts） */
  highlight?: string;
}) {
  switch (event.type) {
    case 'message':
      return <Message p={event.payload as MessagePayload} taskId={taskId} duplicate={duplicate}
                      seq={event.seq} ts={event.ts} onEditResend={onEditResend}
                      highlight={highlight} />;
    case 'action':
      return <Action p={event.payload as ActionPayload} />;
    case 'observation':
      return <Observation p={event.payload as ObservationPayload} />;
    case 'plan':
      return <Plan p={event.payload as PlanPayload} />;
    case 'knowledge':
      return <Knowledge p={event.payload as KnowledgePayload} />;
    case 'datasource':
      return <Datasource p={event.payload as DatasourcePayload} />;
    case 'status':
      return <Status p={event.payload as StatusPayload} />;
    case 'error':
      return <Error p={event.payload as ErrorPayload} />;
    default:
      return null;
  }
}

export const EventItem = memo(EventItemImpl);

const CHAT_VIDEO_RE = /\.(mp4|webm|m4v|mov)$/i;
const CHAT_IMG_RE = /\.(png|jpe?g|gif|webp|bmp|svg)$/i;

function rawUrl(taskId: string, name: string): string {
  // ★ 图片/视频是浏览器直接发起的请求，带不了自定义头 ⇒ 必须把 token 放进查询串（见 api.ts authedUrl）
  return authedUrl(`${API_BASE}/tasks/${taskId}/files/raw?path=${encodeURIComponent(name)}`);
}

/** 交付附件内联媒体：视频/图片直接在聊天气泡里播放——
 *  用户明确要求"播放器显示在聊天框里"（右栏太窄放不下播放器，还得左右拉）。 */
function Media({ taskId, name }: { taskId: string; name: string }) {
  const url = rawUrl(taskId, name);
  if (CHAT_VIDEO_RE.test(name)) {
    return <video className="chat-video" src={url} controls preload="metadata" />;
  }
  if (CHAT_IMG_RE.test(name)) {
    return (
      <a href={url} target="_blank" rel="noreferrer">
        <img className="chat-img" data-preview src={url} alt={name} loading="lazy" />
      </a>
    );
  }
  const isHtml = /\.html?$/i.test(name);
  if (isHtml) {
    return (
      <>
        <a className="chip" href={url} target="_blank" rel="noreferrer" title="点开即看（预览受沙箱保护，脚本不执行）">
          <Globe size={11} style={{ display: "inline", verticalAlign: -1, marginRight: 3 }} />{name.split("/").pop()} · 点开即看
        </a>
        <a className="chip" href={url} download={name}>
          <Download size={11} style={{ display: "inline", verticalAlign: -1, marginRight: 3 }} />下载到电脑
        </a>
      </>
    );
  }
  return (
    <a className="chip" href={url} download={name}>
      <Paperclip size={11} style={{ display: "inline", verticalAlign: -1, marginRight: 2 }} />{name.split("/").pop()}
    </a>
  );
}

/** 纯文本高亮（用户气泡用）：按查询词切 `<mark>`，**不碰 DOM** ——
 *  由 React 自己渲染，虚拟 DOM 与真实 DOM 永远一致（旧实现直接改 DOM，导致
 *  `insertBefore ... is not a child of this node` 崩溃，整页被卸载）。 */
function HighlightedText({ text, query }: { text: string; query?: string }) {
  const q = (query || '').trim().toLowerCase();
  if (!q) return <>{text}</>;
  const lower = text.toLowerCase();
  const out: React.ReactNode[] = [];
  let at = 0;
  let key = 0;
  for (;;) {
    const i = lower.indexOf(q, at);
    if (i < 0) break;
    if (i > at) out.push(text.slice(at, i));
    out.push(<mark className="search-hit" key={key++}>{text.slice(i, i + q.length)}</mark>);
    at = i + q.length;
  }
  if (!out.length) return <>{text}</>;
  if (at < text.length) out.push(text.slice(at));
  return <>{out}</>;
}

/** 相对路径才算"工作区里的文件"（http(s)/data:/绝对路径原样留着，别乱拼）。 */
function isRelative(src: string): boolean {
  const s = src.trim();
  if (!s) return false;
  if (/^([a-z]+:)?\/\//i.test(s) || s.startsWith('data:')) return false;
  if (/^[a-zA-Z]:[\\/]/.test(s) || s.startsWith('/')) return false;
  return true;
}

/** 事件时间 → 本地 HH:MM（解析不出来就返回空串，界面上宁可不显示也不显示乱码）。 */
function clockOf(iso?: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}

function Message({ p, taskId, duplicate, seq, ts, onEditResend, highlight }: {
  p: MessagePayload; taskId?: string; duplicate?: boolean;
  /** ★ 改一句重发（Phase 3 ⑦ 多轮）：这条用户消息的事件 seq + 回调（TaskView 提供） */
  seq?: number; onEditResend?: (seq: number, text: string) => void;
  /** ★ 时间戳（Phase 3 ⑦）：只在署名行/气泡角上放一个小字 —— 别把对话又搞乱 */
  ts?: string;
  /** ★ 对话内搜索的高亮词（渲染树里切 mark，不碰 DOM） */
  highlight?: string;
}) {
  const [copied, setCopied] = useState(false);
  const [showDup, setShowDup] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  if (p.role === 'user') {
    const canEdit = !!onEditResend && typeof seq === 'number';
    if (editing) {
      return (
        <div className="msg-user msg-user-edit">
          <textarea
            className="edit-box"
            value={draft}
            autoFocus
            rows={Math.min(8, Math.max(2, draft.split('\n').length))}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setEditing(false);
              if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                onEditResend?.(seq as number, draft.trim());
                setEditing(false);
              }
            }}
          />
          <div className="edit-actions">
            <span className="edit-hint">从这里重跑：这一句之后的内容会作废（上面保留）</span>
            <button className="btn-mini" onClick={() => setEditing(false)}>取消</button>
            <button
              className="btn-mini edit-save"
              disabled={!draft.trim() || draft.trim() === p.text.trim()}
              onClick={() => { onEditResend?.(seq as number, draft.trim()); setEditing(false); }}
            >
              改完重跑
            </button>
          </div>
        </div>
      );
    }
    return (
      <div className="msg-user">
        <div className="bubble bubble-user"><HighlightedText text={p.text} query={highlight} /></div>
        <div className="msg-user-side">
          {clockOf(ts) && <span className="msg-time">{clockOf(ts)}</span>}
          {canEdit && (
            <button
              className="msg-edit"
              title="改这一句，然后从它这里重新跑（这一句之后的内容会作废）"
              onClick={() => { setDraft(p.text); setEditing(true); }}
            >
              改一句重发
            </button>
          )}
        </div>
      </div>
    );
  }
  // ★ 去重：这条回复就是把上面的工具结果又抄了一遍 ⇒ 默认收起来（点开仍能看全文，不丢数据）
  if (duplicate && !showDup) {
    return (
      <div className="msg-assistant msg-dup">
        <div className="avatar">◍</div>
        <button className="dup-row" onClick={() => setShowDup(true)}
          title="内容和上面的工具结果基本一样，已收起 —— 点开看原文">
          <span className="dup-icon">↩</span>
          <span>同上一步结果（重复内容已收起）</span>
          <span className="dup-len">{(p.text || '').length} 字</span>
          <span className="dup-open">点开看</span>
        </button>
      </div>
    );
  }
  // ★ 清洗：模型偶尔把工具调用当文本吐进正文（2026-10-05 用户任务实测）——
  //   那种内容不该出现"助手说的话"里。剥完为空 ⇒ 整条不渲染（别给用户看空壳）。
  const clean = stripInlineToolCalls(p.text);
  if (!clean) return null;

  // ★ 2026-10-04 回退：**不切段**了。用户看了实机后明确说"你这么切不对"——
  //   要的是"问一个回一个"（一次回答 = 一个气泡），总结另做一个可折叠的块
  //   （见 components/TaskDigest.tsx），而不是把一条回复拆成好几段气泡。
  return (
    <div className="msg-assistant">
      <div className="avatar">◍</div>
      <div className="bubble bubble-assistant">
        <div className="msg-who">Agent{clockOf(ts) && <span className="msg-time">{clockOf(ts)}</span>}</div>
        <Markdown text={clean} resolveSrc={(s) => (isRelative(s) && taskId ? rawUrl(taskId, s) : s)} highlight={highlight} />
        {!!p.text && (
          <button
            className="msg-copy"
            aria-label="复制这条回复"
            title={copied ? '已复制' : '复制全文'}
            onClick={() => {
              void navigator.clipboard.writeText(clean).then(() => {
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              }).catch(() => undefined);
            }}
          >{copied ? '✓' : '⧉'}</button>
        )}
        {p.attachments && p.attachments.length > 0 && (
          <div className="attachments">
            {p.attachments.map((a) =>
              taskId ? (
                <Media key={a} taskId={taskId} name={a} />
              ) : (
                <span key={a} className="chip">
                  <Paperclip size={11} style={{ display: "inline", verticalAlign: -1, marginRight: 2 }} />{a.split("/").pop()}
                </span>
              ),
            )}
          </div>
        )}
        {p.sources && p.sources.length > 0 && (
          <div className="sources">
            <div className="sources-head"><Link2 size={12} style={{ display: "inline", verticalAlign: -2, marginRight: 4 }} />来源（本次已核实访问）</div>
            {p.sources.map((s) => {
              // K6b：协议白名单——sources 的 url 来自模型/联网结果（不可控），
              // javascript:/data: 伪协议会被 React 原样放进 href，一律降级为纯文本
              const h = safeHref(s.url);
              return h ? (
                <a
                  key={s.url}
                  className="source-item"
                  href={h}
                  target="_blank"
                  rel="noreferrer"
                >
                  {s.title || s.url}
                </a>
              ) : (
                <span key={s.url} className="source-item" title="非 http(s) 链接，已拦截">
                  {s.title || s.url}（已拦截）
                </span>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

/** 工具调用的「一句话摘要」：让时间线一眼看懂它在干什么。
 *  改前只显示 `🔧 shell_exec #call_001` —— 用户看不到执行了什么，观感像流水账。 */
function summarize(tool: string, params: Record<string, unknown>): string {
  const s = (k: string): string => (typeof params[k] === 'string' ? (params[k] as string) : '');
  switch (tool) {
    case 'shell_exec':
      return s('command');
    case 'file_write':
    case 'file_read':
    case 'list_dir':
      return s('path');
    case 'web_fetch':
    case 'browser_navigate':
      return s('url');
    case 'web_search':
      return s('query');
    case 'browser_click':
      return s('text') || s('selector');
    case 'browser_type':
    case 'speak':
      return s('text');
    case 'load_skill':
      return s('name');
    case 'image_gen':
    case 'video_gen':
      return s('prompt');
    case 'image_read':
      return s('path');
    case 'wide_research':
      return s('topic') || s('input');
    case 'task_done':
      return s('message');
    default:
      return '';
  }
}

/** 工具图标：Phase 3 ⑦ 迁移自原型 —— 一眼分清"这是跑命令 / 读写文件 / 联网 / 出图"。
 *  用 lucide 图标（与真界面同一套视觉），不用 emoji。 */
const TOOL_ICON: Record<string, typeof Wrench> = {
  shell_exec: Terminal, file_write: FilePen, file_read: FileText, list_dir: FolderOpen,
  web_fetch: Globe, web_search: Search, browser_navigate: Globe, browser_click: MousePointerClick,
  browser_type: Keyboard, load_skill: BookOpen, image_gen: ImageIcon, image_read: ImageIcon,
  video_gen: Video, speak: Volume2, wide_research: Search, task_done: CheckCircle2,
  mcp_call: Plug, ask_user: MessageCircleQuestion,
};

function toolIcon(tool: string) {
  return TOOL_ICON[tool] ?? Wrench;
}

function Action({ p }: { p: ActionPayload }) {
  const full = summarize(p.tool, (p.params || {}) as Record<string, unknown>);
  const oneLine = full.replace(/\s+/g, ' ').trim();
  // ★ task_done 的 message 就是"最终交付语"，紧接着会被应用发成正式回复 ⇒
  //   卡片摘要只留个预览，别再抄一遍全文（那会导致同一句话在屏幕上出现两次）
  const isDone = p.tool === 'task_done';
  const limit = isDone ? 20 : 120;
  const shown = oneLine.length > limit ? oneLine.slice(0, limit) + ' …' : oneLine;
  const Icon = toolIcon(p.tool);
  return (
    // ★ Phase 3 ⑦：工具调用做成**一张卡的上半**（动作），下半是紧随其后的 observation。
    //   刻意保持"每条事件各自渲染 + memo"的原结构 —— 622 次工具调用的任务实测会卡，
    //   把 action/observation 配对会破坏 memo（每个事件都要看邻居才知道状态）。
    <div className="ev-action">
      <details>
        <summary>
          <Icon size={12} className="tool-icon" />
          <span className="mono tool-name">{p.tool}</span>
          {shown && (
            <span className="ev-cmd mono" title={full}>
              {isDone ? '交付：' : ''}{shown}
            </span>
          )}
          <span className="ev-meta">#{p.call_id}</span>
        </summary>
        <pre className="mono">{JSON.stringify(p.params, null, 2)}</pre>
      </details>
    </div>
  );
}

function Observation({ p }: { p: ObservationPayload }) {
  const text = p.result ?? '';
  const long = text.length > 600;
  return (
    <div className={`ev-obs ${p.ok ? 'obs-ok' : 'obs-fail'}`}>
      <span className="obs-mark">{p.ok ? '✓' : '✗'}</span>
      {long ? (
        // 长输出默认折叠一行摘要，点击展开 —— 避免时间线被几百行日志冲垮
        <details className="obs-details">
          <summary className="mono">
            {text.slice(0, 150).replace(/\s+/g, ' ')} …（共 {text.length} 字符，点击展开）
          </summary>
          <pre className="mono">{text}</pre>
        </details>
      ) : (
        <pre className="mono">{text}</pre>
      )}
      <span className="ev-meta">{p.duration_ms}ms</span>
    </div>
  );
}

function Plan({ p }: { p: PlanPayload }) {
  // 第 41 班（用户反馈"对话流窜"）：完整计划卡不再重复出现在对话流里——
  // 每次更新只占一行小字，完整计划看右侧栏"计划"标签（始终是最新版）。
  const current = p.steps.find((s) => s.no === p.current_step);
  const doneCount = p.steps.filter((s) => s.status === 'done').length;
  const stepText = (current?.text ?? p.reflection ?? '').slice(0, 60);
  return (
    <div className="ev-plan plan-line">
      <ListTodo size={12} style={{ display: 'inline', verticalAlign: -2, marginRight: 5, opacity: 0.75 }} />
      更新了计划（{doneCount}/{p.steps.length}）{stepText ? `：${stepText}` : ''}
    </div>
  );
}

function Knowledge({ p }: { p: KnowledgePayload }) {
  // 用量事件：渲染为紧凑单行（不占屏，默认展开可见）
  if (p.title.includes('用量')) {
    return <div className="usage-line">{p.content}</div>;
  }
  return (
    <div className="ev-know">
      <details>
        <summary><BookOpen size={12} style={{ display: 'inline', verticalAlign: -2, marginRight: 4 }} />{p.title}</summary>
        <p>{p.content}</p>
      </details>
    </div>
  );
}

function Datasource({ p }: { p: DatasourcePayload }) {
  return (
    <div className="ev-ds">
      <b><Database size={12} style={{ display: 'inline', verticalAlign: -2, marginRight: 4 }} />{p.name}</b>
      <span> — {p.description}</span>
      {p.endpoint && <span className="mono"> ({p.endpoint})</span>}
    </div>
  );
}

function Status({ p }: { p: StatusPayload }) {
  return <div className="ev-status">— {p.detail ?? p.state} —</div>;
}

function Error({ p }: { p: ErrorPayload }) {
  return (
    <div className="ev-error">
      <AlertTriangle size={12} style={{ display: "inline", verticalAlign: -1, marginRight: 4 }} />{p.message}
      {p.code && <span className="mono"> [{p.code}]</span>}
    </div>
  );
}
