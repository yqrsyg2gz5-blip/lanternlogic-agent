/**
 * ★ 2026-10-06（用户提的）：「**这活到哪一步了**」——群聊顶部一条常驻进度。
 *
 * 为什么做它：组长把目标拆完，那张分工单**就是一条消息** ✓ 滚上去就找不着了 ✗
 * 用户想知道的其实是"**到哪了**"（几项 ✓ 谁在跑 ✓ 有没有失败 ✓ 有没有在等我点 ✓），
 * 而不是往上翻聊天记录 ✗。
 *
 * 为什么单独一个组件：① 只读 `leader_plan`，**不碰消息渲染** ✓（改动面小 = 风险小 ✓）
 * ② 好在测试里当锚点钉住 ✓ ③ 将来要加"点一项跳到它的消息"也在这儿加 ✓。
 *
 * 边界：**没数据就不显示** ✓（不是组长模式、或者还没拆解 ⇒ 整块不出现 ✓，
 * 绝不留一条空框在那儿占地方 ✗）。
 */
import { useState } from 'react';

/** 只声明**用得到的那几个字段** ✓ —— 不把 TeamView 里那个大类型搬过来（免得两处各写一份 ✓） */
export type ProgressGroup = {
  id: string;
  leader_goal?: string | null;
  leader_plan?: ProgressItem[];
};

const ORDER = ['running', 'pending', 'blocked', 'done', 'failed'] as const;

const ICON: Record<string, string> = {
  done: '✅', running: '🔄', failed: '❌', blocked: '⏸', pending: '⏳',
};
const WORD: Record<string, string> = {
  done: '已完成', running: '正在跑', failed: '失败', blocked: '等前置', pending: '待开工',
};
const COLOR: Record<string, string> = {
  done: '#3fb950', running: '#6ba3f5', failed: '#f0736a', blocked: '#8b949e', pending: '#8b949e',
};

/** 分工单里的一项（只挑画面板要用的 ✓） */
export type ProgressItem = {
  name: string;
  status: string;
  depends_on?: string[];
  verdict_note?: string;
};

type Item = ProgressItem;

export function GroupProgress({ group }: { group: ProgressGroup }) {
  const plan = (group.leader_plan ?? []) as Item[];
  const goal = String(group.leader_goal ?? '');
  const [open, setOpen] = useState(() => {
    try { return localStorage.getItem('teamProgressOpen') !== '0'; } catch { return true; }
  });
  if (!plan.length) return null;                      // ★ 没数据就不显示 ✓（别留空框 ✗）

  const n = (s: string): number => plan.filter((i) => i.status === s).length;
  const running = plan.filter((i) => i.status === 'running');
  const toggle = (): void => {
    setOpen((v) => {
      try { localStorage.setItem('teamProgressOpen', v ? '0' : '1'); } catch { /* 无痕模式等 */ }
      return !v;
    });
  };

  return (
    <div className="group-progress" style={{
      flexShrink: 0, margin: '8px 14px 0', padding: '8px 10px', borderRadius: 10,
      background: 'rgba(107,163,245,0.08)', border: '1px solid rgba(107,163,245,0.22)', fontSize: 12.5,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }} onClick={toggle}>
        <b style={{ flexShrink: 0 }}>📋 这活在做什么</b>
        <span style={{ flex: 1, minWidth: 0, opacity: 0.85, overflow: 'hidden',
                       textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={goal}>
          {goal || '（还没写目标）'}
        </span>
        {/* 汇总：一眼看到"几项成了、几项在跑、有没有失败、有没有等我" ✓ */}
        <span style={{ flexShrink: 0, opacity: 0.9 }}>
          {n('done') ? `✅${n('done')} ` : ''}{running.length ? `🔄${running.length} ` : ''}
          {n('failed') ? `❌${n('failed')} ` : ''}{n('blocked') + n('pending') ? `⏸${n('blocked') + n('pending')}` : ''}
        </span>
        <span style={{ flexShrink: 0, opacity: 0.55 }}>{open ? '收起 ▲' : '展开 ▼'}</span>
      </div>

      {open && (
        <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 3 }}>
          {[...plan].sort((a, b) => ORDER.indexOf(a.status as typeof ORDER[number])
                                    - ORDER.indexOf(b.status as typeof ORDER[number]))
            .map((it, i) => (
            <div key={it.name + i} style={{ display: 'flex', alignItems: 'baseline', gap: 6, minWidth: 0 }}>
              <span style={{ flexShrink: 0 }}>{ICON[it.status] ?? '•'}</span>
              <span style={{ flexShrink: 0, fontWeight: 600 }}>{it.name}</span>
              <span style={{ flexShrink: 0, color: COLOR[it.status] ?? 'inherit' }}>
                {WORD[it.status] ?? it.status}
              </span>
              {it.status === 'blocked' && !!it.depends_on?.length && (
                <span style={{ opacity: 0.55, overflow: 'hidden', textOverflow: 'ellipsis',
                               whiteSpace: 'nowrap' }}>（等 {it.depends_on.join('、')}）</span>
              )}
              {!!it.verdict_note && (
                <span style={{ opacity: 0.55, overflow: 'hidden', textOverflow: 'ellipsis',
                               whiteSpace: 'nowrap' }} title={it.verdict_note}>
                  —— {it.verdict_note}
                </span>
              )}
            </div>
          ))}
          <div style={{ opacity: 0.5, marginTop: 2 }}>
            按状态排的 ✓ 点标题能收起（记住你的选择 ✓）—— 详细过程看下面的消息流 ✓
          </div>
        </div>
      )}
    </div>
  );
}
