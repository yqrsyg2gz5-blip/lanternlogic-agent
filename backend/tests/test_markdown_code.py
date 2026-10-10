"""Phase 3 ⑦ 零碎批：Markdown 代码块（语言标签 + 一键复制）的锚点。

为什么值得钉：
  · 复制必须复制**原始文本**（从 React 节点树里抠），不是"看起来的样子" ——
    直接读 DOM 会把高亮/折行算进去，粘贴到终端就跑不了
  · 状态要**每块独立**（所以拆了 CodeBlock 组件）；共用一个状态的话点一块全变"已复制"
  · 安全边界不能顺手破坏：react-markdown **不许**引入原始 HTML 渲染（raw HTML = XSS 面）
  · 表格靠 remarkGfm（这次只加代码块，别把它碰掉）
"""
from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MD = (_ROOT / "frontend" / "src" / "components" / "Markdown.tsx").read_text("utf-8")
_CSS = (_ROOT / "frontend" / "src" / "styles.css").read_text("utf-8")
_VERIFY = (_ROOT / "frontend" / "scripts" / "verify_markdown_code.mjs").read_text("utf-8")
# ★ 可选文件要用 exists() 兜一下：红绿检查跑的是**副本**，只拷了 frontend/src 与 frontend/scripts，
#   package.json 不在里面 —— 模块级硬读会让收集期直接 rc=2（本班回滚组就是这么红的）。
_PKG_PATH = _ROOT / "frontend" / "package.json"
_PKG = _PKG_PATH.read_text("utf-8") if _PKG_PATH.exists() else ""


def test_code_blocks_get_a_shell_with_language_and_copy():
    assert "pre: ({ children }) => <CodeBlock>{children}</CodeBlock>" in _MD, \
        "代码块没接到自定义外壳上（写了组件但没挂也算没做）"
    assert "md-code-head" in _MD and "md-code-lang" in _MD and "md-code-copy" in _MD, \
        "语言标签/复制按钮不全"


def test_copy_copies_the_raw_source_not_the_rendered_text():
    assert "function textOf(" in _MD, "没有从 React 节点树抠原始文本"
    assert "navigator.clipboard?.writeText(raw)" in _MD, "复制的不是原始文本"


def test_copy_state_is_per_block():
    assert "function CodeBlock(" in _MD and "useState(false)" in _MD.split("function CodeBlock(")[1], \
        "复制状态不是每块独立（点一块全变'已复制'）"


def _code_only(src: str) -> str:
    """剔掉注释行后再扫描。

    ★ 本会话第四次栽在同一件事上：锚点想禁掉某个写法，结果**注释里正好写了那个词**
      （这里就是 `不走 dangerouslySetInnerHTML` 这句说明）⇒ 锚点对着注释报红/放绿。
      凡是"禁某写法"的断言，一律先过这个函数。
    """
    out = []
    for line in src.splitlines():
        s = line.lstrip()
        if s.startswith("//") or s.startswith("*") or s.startswith("/*"):
            continue
        out.append(line.split("//")[0])       # 行尾注释也去掉
    return "\n".join(out)


def test_raw_html_stays_disabled_and_gfm_kept():
    """安全边界：绝不能让模型生成的 HTML 变成真 DOM（第 17 班 P2-7 的同源 XSS 思路）。"""
    code = _code_only(_MD)
    assert "dangerouslySetInnerHTML" not in code, "引入了原始 HTML 渲染（XSS 面）"
    assert "rehypeRaw" not in code and "rehype-raw" not in code, "引入了原始 HTML 插件"
    assert "rehype-raw" not in _PKG, "依赖里加了 rehype-raw"
    assert "remarkGfm" in code, "GFM（表格/删除线）插件被碰掉了"
    assert 'target="_blank" rel="noreferrer noopener"' in code, "外链的安全属性没了"


def test_code_block_css_exists():
    for k in (".md-code {", ".md-code-head", ".md-code-copy", ".md-code pre {", "max-height: 420px"):
        assert k in _CSS, f"样式缺 {k}"
    assert ".md table" in _CSS, "表格样式被碰掉了"


def test_verification_covers_copy_equality_and_feedback():
    assert "复制内容 == 代码块原文" in _VERIFY, "没验证复制内容与原文一致"
    assert "点过后按钮有反馈" in _VERIFY, "没验证已复制反馈"
    assert "dup-row" in _VERIFY, "没处理'那条消息可能被去重收起'（会在真任务上假红）"
