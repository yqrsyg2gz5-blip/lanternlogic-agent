/**
 * ★ 图片灯箱（Phase 3 ⑦ 预览批）—— 点图片就地放大，不用跳新标签页。
 *
 * 现状（改前）：对话里的图片是缩略图，点它 `target="_blank"` **跳新标签页**；
 * 产物面板里的图 `max-width` 顶死，看不清细节。看一张图要离开对话再切回来，很别扭。
 *
 * 做法：**全局委托**（document 级 click）—— 凡是带 `data-preview` 的 `<img>` 被点到就弹层。
 * 为什么用委托而不是给每个组件传 props：图片出现在对话气泡、附件、Markdown 正文、产物面板
 * 四个地方，逐个接线容易漏（本会话已经吃过"同一个功能两份实现、只修了一处"的亏）。
 */
import { useEffect, useState } from 'react';
import { X } from 'lucide-react';
import { safeHref } from '../lib/safeUrl';

export function PreviewOverlay() {
  const [src, setSrc] = useState<string | null>(null);
  const [alt, setAlt] = useState('');

  useEffect(() => {
    const onClick = (e: MouseEvent): void => {
      const el = (e.target as HTMLElement | null)?.closest?.('img[data-preview]') as HTMLImageElement | null;
      if (!el || !el.src) return;
      e.preventDefault();
      e.stopPropagation();
      setSrc(el.src);
      setAlt(el.alt || '');
    };
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') setSrc(null);
    };
    document.addEventListener('click', onClick, true);   // 捕获阶段：先于外层的链接跳转
    window.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('click', onClick, true);
      window.removeEventListener('keydown', onKey);
    };
  }, []);

  if (!src) return null;
  // ★ 必须过 safeHref（项目守卫 test_no_other_bare_external_href 盯着所有裸 href 直绑；
  //   它登记的安全写法是"经 safeHref 过滤后的 href={h}" —— 所以变量名沿用 h）
  const h = safeHref(src);
  return (
    <div className="preview-mask" onClick={() => setSrc(null)} title="点任意处或按 Esc 关闭">
      <div className="preview-bar" onClick={(e) => e.stopPropagation()}>
        <span className="preview-name">{alt || '图片预览'}</span>
        {h && <a className="preview-open" href={h} target="_blank" rel="noreferrer">在新标签打开</a>}
        <button className="preview-x" onClick={() => setSrc(null)} aria-label="关闭预览"><X size={14} /></button>
      </div>
      <img className="preview-img" src={src} alt={alt} onClick={(e) => e.stopPropagation()} />
    </div>
  );
}
