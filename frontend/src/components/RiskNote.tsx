/**
 * ★ 「高风险项说明」（Phase 3 ⑧ 收尾批）。
 *
 * 为什么需要：设置页里有一批开关，改错了不是"界面难看"，而是**把电脑的权限交出去** ——
 * 沙箱关着 = 命令直接在本机跑；审批清单清空 = 删除类命令不再问你；允许目录加个 `C:\` = 全盘可读写。
 * 这些"不会有任何报错"的边界放开，用户很难从一行标题上看出来。
 *
 * 每条说明讲三件事：**它控制什么 / 改了的最坏后果 / 怎么退回去**。
 * 默认只显示一行摘要（避免又把设置页搞乱），点 `?` 才展开详情。
 *
 * ★ 纯展示组件：**不发任何请求、不改任何值**（有锚点钉着"不许出现 api./fetch"）——
 *   它的存在不会改变 Agent 能做什么，只是把"你现在开着什么口子"摆到眼前。
 */
import { useState, type ReactNode } from 'react';

export type RiskLevel = 'ok' | 'warn' | 'danger';

const ICON: Record<RiskLevel, string> = { ok: '✓', warn: '⚠', danger: '⚠' };

export function RiskNote({ level = 'ok', summary, detail }: {
  /** 当前值处于什么状态：ok = 安全；warn = 有口子；danger = 明显危险 */
  level?: RiskLevel;
  /** 一行摘要（常驻显示）——把"现在的后果"说清楚 */
  summary: string;
  /** 点 `?` 展开：它控制什么 / 最坏后果 / 怎么退回 */
  detail: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`risk-note risk-${level}`}>
      <div className="risk-line">
        <span className="risk-icon" aria-hidden>{ICON[level]}</span>
        <span className="risk-summary">{summary}</span>
        <button
          className="risk-toggle"
          aria-expanded={open}
          aria-label={open ? '收起说明' : '这是什么？有什么风险？'}
          title={open ? '收起说明' : '这是什么？有什么风险？'}
          onClick={() => setOpen((v) => !v)}
        >
          ?
        </button>
      </div>
      {open && <div className="risk-detail">{detail}</div>}
    </div>
  );
}
