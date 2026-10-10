import { useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Settings as SettingsIcon, Plus, X, Pin, Pencil, Users } from 'lucide-react';
import { SettingsPanel } from './SettingsPanel';
import { TeamView } from './TeamView';
import type { TaskStatus, TaskSummary } from '../types';

interface Props {
  tasks: TaskSummary[];
  currentId: string | null;
  onSelect(id: string): void;
  onDeleteTask(taskId: string): void;
  onNewChat(): void;
  onRename(taskId: string, title: string): void;
  onPin(taskId: string, pinned: boolean): void;
  onOpenSettings?(): void;
}

type Filter = 'all' | 'active' | 'done';

const STATUS_DOT: Record<TaskStatus, string> = {
  created: 'dot-grey',
  running: 'dot-blue blink',
  waiting_approval: 'dot-yellow',
  done: 'dot-green',
  failed: 'dot-red',
  cancelled: 'dot-grey',
  partial: 'dot-yellow',
};

function relTime(iso: string): string {
  // 第 41 班（用户定稿）：紧凑相对标签——刚刚 / N分钟前 / 今天 / 昨天 / N天前 / 日期
  const d = new Date(iso);
  const now = new Date();
  const day0 = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const ts = d.getTime();
  const mins = Math.floor((now.getTime() - ts) / 60000);
  if (mins < 1) return '刚刚';
  if (mins < 60) return mins + '分钟前';
  if (ts >= day0) return '今天';
  if (ts >= day0 - 86400000) return '昨天';
  const days = Math.floor((day0 - ts) / 86400000) + 1;
  if (days <= 7) return days + '天前';
  return d.toLocaleDateString('zh-CN');
}

export function Sidebar({ tasks, currentId, onSelect, onDeleteTask, onNewChat, onRename, onPin, loading = false, mobileOpen = false, onNavigate }: Props & { loading?: boolean; mobileOpen?: boolean; onNavigate?: () => void }) {
  const [q, setQ] = useState('');
  // 侧栏拖拽调宽（第 41 班，对标 ZCode）：localStorage 记忆，200–420px
  const [sbw, setSbw] = useState<number>(() => {
    const v = Number(localStorage.getItem('sbWidth'));
    return v >= 200 && v <= 420 ? v : 280;
  });
  const sidebarRef = useRef<HTMLElement | null>(null);
  const startResize = (e: React.MouseEvent): void => {
    e.preventDefault();
    const x0 = e.clientX;
    const w0 = sbw;
    const move = (ev: MouseEvent): void => {
      const w = Math.max(200, Math.min(420, w0 + (ev.clientX - x0)));
      setSbw(w);
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
    };
    const up = (): void => {
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      setSbw((w) => { localStorage.setItem('sbWidth', String(w)); return w; });
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  };
  const [filter, setFilter] = useState<Filter>('all');
  const [showSettings, setShowSettings] = useState(false);
  const [showTeam, setShowTeam] = useState(false);
  const [showAll, setShowAll] = useState(false);

  // 置顶优先，其余按更新时间倒序（第 41 班）
  const sortedTasks = [...tasks].sort((a, b) => {
    if (!!a.pinned !== !!b.pinned) return a.pinned ? -1 : 1;
    return a.updated_at < b.updated_at ? 1 : -1;
  });
  const filtered = sortedTasks.filter((t) =>
    filter === 'all'
      ? true
      : filter === 'active'
        ? t.status === 'running' || t.status === 'waiting_approval' || t.status === 'created'
        : t.status === 'done' || t.status === 'failed' || t.status === 'cancelled',
  );

  // 搜索：标题或 id 命中（不区分大小写）
  const kw = q.trim().toLowerCase();
  const visible = kw
    ? filtered.filter(
        (t) => t.title.toLowerCase().includes(kw) || t.id.toLowerCase().includes(kw),
      )
    : filtered;

  // 按日期分组（今天 / 昨天 / 最近 7 天 / 更早）
  const dayBucket = (iso: string): string => {
    const ts = new Date(iso).getTime();
    const now = new Date();
    const day = 86400000;
    const today0 = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    if (ts >= today0) return '今天';
    if (ts >= today0 - day) return '昨天';
    if (ts >= today0 - day * 7) return '最近 7 天';
    return '更早';
  };
  const grouped = visible.reduce<{ label: string; items: typeof visible }[]>((acc, t) => {
    const label = dayBucket(t.updated_at);
    const last = acc[acc.length - 1];
    if (last && last.label === label) last.items.push(t);
    else acc.push({ label, items: [t] });
    return acc;
  }, []);

  // 默认只显示前 15 条，底部「显示更多」展开（对标 ZCode 侧栏）
  const LIMIT = 15;
  let shown = 0;
  let truncated = false;
  const shownGroups: typeof grouped = [];
  for (const g of grouped) {
    if (truncated) break;
    const items: typeof g.items = [];
    for (const t of g.items) {
      if (!showAll && shown >= LIMIT) { truncated = true; break; }
      items.push(t);
      shown++;
    }
    if (items.length) shownGroups.push({ label: g.label, items });
  }
  const hiddenCount = visible.length - shown;

  return (
    <aside className={`sidebar ${mobileOpen ? 'sidebar-open' : ''}`} ref={sidebarRef} style={{ width: sbw, flex: '0 0 auto' }}>
      <div className="resize-handle" onMouseDown={startResize} title="拖动调整侧栏宽度" />
      <div className="brand">
        <span className="brand-dot">◍</span>
        <div>
          <div className="brand-name">LanternLogic Agent</div>
          <div className="brand-sub">本地智能体</div>
        </div>
      </div>

      <button className="new-chat-btn" title="新建会话" onClick={onNewChat}>
        <Plus size={14} style={{ verticalAlign: -2, marginRight: 4 }} />新会话
      </button>
      <input
        className="task-search"
        style={{ width: '100%', marginBottom: 8, boxSizing: 'border-box' }}
        value={q}
        placeholder="搜索任务…"
        onChange={(e) => setQ(e.target.value)}
      />

      <div className="filters">
        {(
          [
            ['all', '全部'],
            ['active', '进行中'],
            ['done', '已完成'],
          ] as [Filter, string][]
        ).map(([k, label]) => (
          <button
            key={k}
            className={filter === k ? 'filter-on' : ''}
            onClick={() => setFilter(k)}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="tasklist">
        {loading && visible.length === 0 && (
          <div aria-hidden="true">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="taskitem skeleton">
                <span className="sk-dot" />
                <span className="sk-line" style={{ width: `${72 - i * 9}%` }} />
              </div>
            ))}
          </div>
        )}
        {!loading && visible.length === 0 && (
          <div className="tasklist-empty">{kw ? '没有匹配的任务' : '还没有任务'}</div>
        )}
        {shownGroups.map((g) => (
          <div key={g.label}>
            <div
              className="taskgroup-head"
              style={{ fontSize: 11, opacity: 0.55, padding: '8px 4px 4px', fontWeight: 600 }}
            >
              {g.label}
            </div>
            {g.items.map((t) => (
              <div
                key={t.id}
                role="button"
                tabIndex={0}
                className={`taskitem ${t.id === currentId ? 'taskitem-on' : ''}`}
                onClick={() => { onSelect(t.id); onNavigate?.(); }}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onSelect(t.id); }}
              >
                <span className={`dot ${STATUS_DOT[t.status]}`} />
                <span className="taskitem-title">{t.title}</span>
                <span className="taskitem-actions">
                  <button
                    className={`taskitem-act ${t.pinned ? 'act-on' : ''}`}
                    title={t.pinned ? '取消置顶' : '置顶'}
                    onClick={(e) => { e.stopPropagation(); void onPin(t.id, !t.pinned); }}
                  >
                    <Pin size={12} style={t.pinned ? { transform: 'rotate(45deg)' } : undefined} />
                  </button>
                  <button
                    className="taskitem-act"
                    title="重命名"
                    onClick={(e) => {
                      e.stopPropagation();
                      const name = prompt('重命名任务：', t.title);
                      if (name && name.trim()) void onRename(t.id, name.trim());
                    }}
                  >
                    <Pencil size={12} />
                  </button>
                  <button
                    className="taskitem-act taskitem-del"
                    title="删除任务"
                    aria-label="删除任务"
                    // ★★ 2026-10-07：**删之前问一句** ✓ —— 用户就是这么没的 198 个任务：
                    //   他原话"我以为那个叉只是表面删除"✓ 而那时它**当场永久销毁** ✗
                    //   （后端现在已经改成**挪到 `data\_deleted_` 可回滚** ✓ 但"不打招呼就动手"
                    //    还是不对 ✓ —— 用户该知道"删的是什么"再决定 ✓）
                    //   话里必须写清**会连什么一起删** ✓ 以及**能找回来** ✓（不然他还是会怕 ✓）
                    onClick={(e) => {
                      e.stopPropagation();
                      const ok = window.confirm(
                        `删除任务「${t.title || t.id}」？\n\n`
                        + '连同它的对话记录与工作区文件一起删。\n'
                        + '★ 不是永久销毁：会挪到 data\\_deleted_ 里，想找回就把它挪回 data\\tasks\\ 即可。',
                      );
                      if (ok) onDeleteTask(t.id);
                    }}
                  >
                    <X size={12} />
                  </button>
                </span>
                <span className="taskitem-time">{t.pinned ? '📌' : ''}{relTime(t.updated_at)}</span>
              </div>
            ))}
          </div>
        ))}
        {truncated && (
          <button className="task-more" onClick={() => setShowAll(true)}>
            显示更多（还有 {hiddenCount} 条）
          </button>
        )}
        {showAll && visible.length > LIMIT && (
          <button className="task-more" onClick={() => setShowAll(false)}>
            收起
          </button>
        )}
      </div>

      <div className="sidebar-bottom">
        <button className="settings-fab" title="团队（AI 员工群聊）" onClick={() => setShowTeam(true)}>
          <Users size={14} style={{ display: 'inline', verticalAlign: -2, marginRight: 6 }} />团队
        </button>
        <button
          className="settings-fab"
          title="设置"
          onClick={() => setShowSettings(true)}
        >
          <SettingsIcon size={14} style={{ display: 'inline', verticalAlign: -2, marginRight: 6 }} />设置
        </button>
      </div>

      {/* 十九轮 🔴2 顺带：SettingsPanel 同为 .sidebar 子树的 fixed 层——
          同一层叠上下文病根，一并 portal 到 body；二十一轮 🔴2 同加 inert 管理 */}
      {showSettings && createPortal(
        <SettingsPanel onClose={() => setShowSettings(false)} rootId="root" />,
        document.body,
      )}
      {/* 十九轮 🔴2：portal 到 body——≤760 时 .sidebar{position:fixed;z-index:60}
          创建层叠上下文，TeamView 内联的 zIndex:100 也冲不出 60（.sidebar-toggle
          的 70 永远赢）。portal 后团队 overlay 在 body 级，结构性解决——
          不再靠加 z-index（100→200 已证无效）。
          二十一轮 🔴2：portal 只解决层叠——焦点顺序还得配 inert：
          overlay 打开时把 #root 设 inert（背景不可聚焦），Tab 第 1 次就进 overlay。 */}
      {showTeam && createPortal(
        <TeamView onClose={() => setShowTeam(false)} rootId="root" />,
        document.body,
      )}
    </aside>
  );
}
