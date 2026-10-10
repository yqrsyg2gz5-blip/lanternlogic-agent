/**
 * ★ 对话内搜索的高亮：**在 React 渲染树里做**，不再直接改 DOM。
 *
 * 为什么改（2026-10-05 用户实测的崩溃）：
 *   旧实现用 `document.createTreeWalker` + `replaceWith(<mark>)` **绕过 React 改 DOM**，
 *   而 React 仍然记着那些被换掉的文本节点 ⇒ 之后任何一次重渲染（例如点"允许一次"、
 *   流式事件到达）它去 `insertBefore` 就会抛：
 *     Failed to execute 'insertBefore' on 'Node': The node before which the new node
 *     is to be inserted is not a child of this node.
 *   整棵任务视图因此被卸载（深色主题下就是"屏幕全黑"）。
 *
 * 现在：作为 **rehype 插件**在 HAST 上把命中片段切成 `<mark class="search-hit">` 节点 ——
 * 输出仍由 React 生成，DOM 与虚拟 DOM 永远一致，怎么重渲染都不会错位。
 * 导航（下一个/上一个）只**读** `mark.search-hit`，不写。
 */
type HastNode = {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
};

function markNode(text: string): HastNode {
  return {
    type: 'element',
    tagName: 'mark',
    properties: { className: ['search-hit'] },
    children: [{ type: 'text', value: text }],
  };
}

/** 把一个文本节点按 query 切分（大小写不敏感），返回替换后的节点数组。 */
function splitText(value: string, q: string): HastNode[] {
  const lower = value.toLowerCase();
  const out: HastNode[] = [];
  let at = 0;
  for (;;) {
    const i = lower.indexOf(q, at);
    if (i < 0) break;
    if (i > at) out.push({ type: 'text', value: value.slice(at, i) });
    out.push(markNode(value.slice(i, i + q.length)));
    at = i + q.length;
  }
  if (!out.length) return [{ type: 'text', value }];
  if (at < value.length) out.push({ type: 'text', value: value.slice(at) });
  return out;
}

function walk(node: HastNode, q: string): void {
  if (!Array.isArray(node.children)) return;
  const next: HastNode[] = [];
  for (const child of node.children) {
    if (child.type === 'text' && typeof child.value === 'string' && child.value.toLowerCase().includes(q)) {
      next.push(...splitText(child.value, q));
      continue;
    }
    // 别再往代码块里插高亮（会把代码结构切碎，看着也乱）
    if (child.type === 'element' && child.tagName !== 'code' && child.tagName !== 'pre') {
      walk(child, q);
    }
    next.push(child);
  }
  node.children = next;
}

/** rehype 插件工厂：`rehypePlugins={[rehypeHighlightQuery(search)]}`。 */
export function rehypeHighlightQuery(query: string) {
  const q = (query || '').trim().toLowerCase();
  return () => (tree: HastNode) => {
    if (q.length >= 1) walk(tree, q);
  };
}
