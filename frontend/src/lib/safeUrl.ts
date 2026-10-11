/** K6b：外部 URL 协议白名单。
 *
 * sources/链接里的 url 字符串来自模型输出或联网结果（不可控）。React 不会对
 * href 做协议过滤——`javascript:alert(1)`、`data:text/html,...` 会原样进 DOM，
 * 用户一点即在页面上下文执行脚本（可读全部任务数据、代发 API 请求）。
 * 只允许 http/https/mailto；其余一律返回 null（调用方降级为纯文本）。
 */
const SAFE_PROTOCOL_RE = /^(https?:\/\/|mailto:)/i;

export function safeHref(url: string): string | null {
  const u = (url || "").trim();
  return SAFE_PROTOCOL_RE.test(u) ? u : null;
}
