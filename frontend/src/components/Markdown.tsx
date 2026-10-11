import { useState, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { rehypeHighlightQuery } from '../lib/rehypeHighlight';
// @ts-expect-error 纯 JS（带 JSDoc），为了让后端测试能用 node 直接跑它做行为验证 ✓
import { tokenize } from '../lib/tinyHighlight.js';

/**
 * 交付消息 / 模型回复的 Markdown 渲染。
 *
 * **改前**：`{p.text}` 直接塞进气泡 —— 报告里的标题、列表、表格、代码块全变成死文字，
 * 这是成品感的关键短板。
 *
 * **安全**：react-markdown **默认不渲染原始 HTML**（不走 dangerouslySetInnerHTML），
 * 所以模型生成的 `<script>` 不会执行 —— 延续第 17 班 P2-7「同源 XSS」的修复思路。
 * 链接一律 `target="_blank"` + `rel="noreferrer noopener"`，防 window.opener 劫持。
 *
 * **表格**：靠 `remark-gfm`（GFM 表格/删除线/自动链接），样式见 `.md table`。
 *
 * ★ 2026-10-05：**代码块加语言标签 + 一键复制**（Phase 3 ⑦ 零碎批）。
 *   模型给的代码块此前只能靠鼠标划选，长命令/长脚本经常选不全；语言标签也让人一眼知道
 *   这是什么语言（`bash`/`python`/`json`…）。复制按钮的状态是**每块独立**的，所以单独拆组件。
 */
export function Markdown({ text, resolveSrc, highlight }: {
  text: string;
  /** ★ 正文里的图片：模型常写相对路径（`artifacts/x.png`），不解析就是**裂图**。
   *  调用方（TaskView/产物面板）把它解析成工作区取文件的地址；解析不了就原样留着。 */
  resolveSrc?: (src: string) => string;
  /** ★ 对话内搜索的高亮词（**在渲染树里切 <mark>**，不碰 DOM —— 见 lib/rehypeHighlight.ts） */
  highlight?: string;
}) {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={highlight ? [rehypeHighlightQuery(highlight)] : []}
        components={{
          a: (props) => <a {...props} target="_blank" rel="noreferrer noopener" />,
          pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
          img: ({ src, alt }) => {
            const raw = typeof src === 'string' ? src : '';
            const url = resolveSrc && raw ? resolveSrc(raw) : raw;
            // data-preview：交给全局灯箱（components/PreviewOverlay）—— 点一下就地放大
            return <img className="md-img" data-preview src={url} alt={alt ?? ''} loading="lazy" />;
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

/** 把 React 节点树里的纯文本抠出来（用于复制）= `<code>` 里的原始内容。 */
function textOf(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === 'boolean') return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(textOf).join('');
  const props = (node as { props?: { children?: ReactNode } }).props;
  return props ? textOf(props.children) : '';
}

function CodeBlock({ children }: { children?: ReactNode }) {
  const [copied, setCopied] = useState(false);
  const first = Array.isArray(children) ? children[0] : children;
  const cls = (first as { props?: { className?: string } } | undefined)?.props?.className ?? '';
  const lang = /language-([\w+#.-]+)/.exec(cls)?.[1] ?? '';
  const raw = textOf(children);

  const copy = (): void => {
    void navigator.clipboard?.writeText(raw).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    }).catch(() => undefined);
  };

  // ★ 2026-10-06：**极小的语法高亮**（不引 100KB 的库 ✗ —— 见 lib/tinyHighlight.js）
  //   只在"认识这门语言 + 代码不太长"时启用；超长代码直接原样显示（避免卡）✓
  const tokens: { t: string; k: string }[] | null =
    lang && raw.length <= 20000 ? (tokenize(raw, lang) as { t: string; k: string }[]) : null;

  return (
    <div className="md-code">
      <div className="md-code-head">
        <span className="md-code-lang">{lang || '文本'}</span>
        <button className="md-code-copy" onClick={copy} title="复制这段代码">
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      <pre>
        {tokens
          ? <code>{tokens.map((t, i) => (t.k ? <span key={i} className={`tok-${t.k}`}>{t.t}</span> : t.t))}</code>
          : children}
      </pre>
    </div>
  );
}
