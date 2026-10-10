/**
 * ★ 设置页统一节头（Phase 3 ⑧ 设置页正规化）—— 用户在"设置页乱"上的具体诉求：
 *   每节要一眼看懂三件事：**这节是干什么的**（说明）、**现在什么状态**（徽章）、
 *   **能不能一键回到出厂**（恢复默认）。
 *
 * 徽章三态用色区分：ok（绿，已就绪）/ warn（黄，需要你动手或未启用）/ plain（灰，中性信息）。
 */
export type HeadTone = 'ok' | 'warn' | 'plain';

export function SectionHead({ desc, status, tone = 'plain', onReset, resetHint }: {
  desc: string;
  status?: string;
  tone?: HeadTone;
  onReset?: () => void;
  resetHint?: string;
}) {
  return (
    <div className="sec-head">
      <div className="sec-desc">{desc}</div>
      <div className="sec-right">
        {status && <span className={`sec-status sec-${tone}`}>{status}</span>}
        {onReset && (
          <button
            className="sec-reset"
            title={resetHint ?? '把这一节恢复成出厂默认（其它节不受影响）'}
            onClick={onReset}
          >
            恢复默认
          </button>
        )}
      </div>
    </div>
  );
}
