/**
 * ★ 错误边界（2026-10-05 用户报"点允许一次屏幕就全黑了"）。
 *
 * 现象：整页变成纯色/黑 —— 这是 React 的默认行为：**渲染期抛异常会卸载整棵树**，
 * 页面就剩一个空 body（深色主题下看着就是"全黑"）。
 * 问题在于：用户既看不到原因，也没法继续用，只能刷新。
 *
 * 所以这里兜住：任何子树的渲染异常都变成一块**可读的面板**（错误摘要 + 重试 + 回任务列表），
 * 并把原始堆栈留在控制台给排查用。**绝不让界面变黑。**
 *
 * 用法：把它套在**可能出问题的子树**外面（比如整页一处、任务消息流一处）——
 * 套得越靠近出问题的地方，损失越小（一个卡片崩了不该拖垮整个应用）。
 */
import { Component, type ErrorInfo, type ReactNode } from 'react';

type Props = { children: ReactNode; label?: string; onReset?: () => void };
type State = { err: Error | null };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { err: null };

  static getDerivedStateFromError(err: Error): State {
    return { err };
  }

  componentDidCatch(err: Error, info: ErrorInfo): void {
    // 控制台留全量信息（用户截图给我就能定位）；界面上只给他能看懂的一行
    console.error('[ErrorBoundary]', this.props.label ?? '', err, info?.componentStack);
  }

  render(): ReactNode {
    const { err } = this.state;
    if (!err) return this.props.children;
    return (
      <div className="crash-panel" role="alert">
        <div className="crash-title">⚠️ 这一块渲染出错了（界面没有崩掉，其它部分还能用）</div>
        <div className="crash-msg mono">{String(err.message || err).slice(0, 300)}</div>
        <div className="crash-hint">
          {this.props.label ? `位置：${this.props.label}。` : ''}
          原因已打到浏览器控制台（F12 → Console），按 <b>Ctrl+F5</b> 强制刷新一般能恢复。
        </div>
        <div className="crash-actions">
          <button
            className="btn-mini"
            onClick={() => { this.setState({ err: null }); this.props.onReset?.(); }}
          >
            重试渲染
          </button>
        </div>
      </div>
    );
  }
}
