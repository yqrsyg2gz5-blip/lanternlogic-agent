"""两个用户实测问题的锚点：① 纪要与议题"跑题" ② 点允许后整页变黑。

① 《纪要跑题》2026-10-05 用户实测：
   问"一天赚 300 能不能做"，收口那次模型**编了一份完全无关的纪要**（"复购口径/项目 vs 劳务"，
   讨论记录里根本没这些词）。这种"看着像纪要、其实跑题"比没有纪要更糟 —— 用户会以为真讨论过。
   判据：**接地校验**（纪要与讨论记录的引用命中率），太低就换成自动整理那份。

② 《整页变黑》用户实测：点「允许一次」后"屏幕就没了，全屏纯颜色/黑了"。
   React 的默认行为是**渲染期异常卸载整棵树** —— 深色主题下就是"全黑"。
   复现失败（Playwright 里点允许一切正常），所以按"别让界面变黑"来加固：
   错误边界 + 去掉 backdrop-filter（已知的 GPU 花屏/黑屏诱因）。
"""
from __future__ import annotations

import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MAIN = (_ROOT / "backend" / "app" / "main.py").read_text("utf-8")
_BOUNDARY = (_ROOT / "frontend" / "src" / "components" / "ErrorBoundary.tsx").read_text("utf-8")
_APP = (_ROOT / "frontend" / "src" / "App.tsx").read_text("utf-8")
_CSS = (_ROOT / "frontend" / "src" / "styles.css").read_text("utf-8")
_DIAG = (_ROOT / "frontend" / "scripts" / "diag_approve_black_screen.mjs").read_text("utf-8")


def test_grounding_guard_exists_and_is_wired_on_both_paths():
    assert "def _summary_grounding(" in _MAIN, "没有接地校验"
    # ★ 2026-10-06：正常收口那条**多了一步**（先复问、再判）——
    #   原来是 `if _summary_grounding(...) < MIN:` 直接换兜底 ✗；
    #   现在那句还留着当"复问后仍不合格"的兜底判据 ✓，而**入口那句**改成带 `and moderator is not None` ✓。
    #   所以这里按**语义**数：接地校验这个判据必须仍然出现在**两条路**上 ✓。
    assert _MAIN.count("< SUMMARY_GROUNDING_MIN") >= 2, \
        "正常收口与「重新出纪要」两条路都要过接地校验"
    assert "疑似跑题" in _MAIN, "校验不过时要说明原因（跑题），而不是悄悄换掉"
    assert "_STRICT_SUMMARY_NUDGE" in _MAIN, "跑题时应先**带着原因复问一次**（2026-10-06 加）"


def test_fallback_digest_still_has_the_three_sections():
    """兜底也要按 结论/分歧/待办 三段写 —— 否则用户拿到的是"一坨要点"，不是纪要。"""
    assert "1. **结论**" in _MAIN and "2. **分歧 / 各方立场**" in _MAIN and "3. **待办**" in _MAIN


def test_error_boundary_exists_and_wraps_the_task_view():
    for k in ("getDerivedStateFromError", "componentDidCatch", "crash-panel", "重试渲染"):
        assert k in _BOUNDARY, f"错误边界缺 {k}"
        assert '<ErrorBoundary label="任务视图"' in _APP, "任务视图没被兜住（它最容易因卡片崩溃拖垮整页）"

def test_no_backdrop_filter_left():
    """backdrop-filter 是已知的整屏变黑/花屏诱因（用户报过黑屏）——全部去掉，用纯色遮罩即可。"""
    live = [ln for ln in _CSS.splitlines() if "backdrop-filter" in ln and "★" not in ln]
    assert not live, f"还有没去掉的 backdrop-filter：{live[:3]}"


def test_black_screen_repro_tool_exists():
    """留一个可复跑的复现脚本：用假响应拦下审批，只验界面、不真的执行命令。"""
    assert "waiting_approval" in _DIAG, "没找等审批的任务"
    assert "fulfill" in _DIAG, "没拦住审批请求（会真的执行命令）"
    assert "fullscreenish" in _DIAG, "没检测【有没有超大遮罩盖住整页】"


# ═══ ★ 真凶：搜索高亮直接改 DOM ⇒ React 协调崩溃（用户实测 insertBefore 报错）═══


def test_no_component_mutates_the_dom_behind_react():
    """**任何组件都不许绕过 React 增删节点**。

    用户实测的崩溃：搜索用 `createTreeWalker` + `replaceWith` 把文本节点换成 `<mark>`，
    React 仍记着旧节点 ⇒ 之后任何一次重渲染都抛
        Failed to execute 'insertBefore' on 'Node': ... is not a child of this node.
    ⇒ 整棵任务视图被卸载（深色主题下就是"屏幕全黑"）。

    这类写法必须在源码层面绝迹（只读 DOM 是允许的：`querySelectorAll` 数命中、滚动）。
    """
    src = (_ROOT / "frontend" / "src")
    banned = ("createTreeWalker", "replaceWith(", "insertBefore(", "appendChild(document.create")
    strip = re.compile(r"/\*.*?\*/|^\s*//.*$", re.S | re.M)
    hits: list[str] = []
    for f in src.rglob("*.ts*"):
        # ★ 先剥注释再扫：说明"以前是怎么错的"的注释里会**出现**这些词（本班就被绊了一次）
        code = strip.sub("", f.read_text("utf-8"))
        for b in banned:
            if b in code:
                hits.append(f"{f.name}: {b}")
    assert not hits, f"有组件在绕过 React 改 DOM（会引发 insertBefore 崩溃）：{hits}"


def test_search_highlight_is_done_in_the_render_tree():
    hl = (_ROOT / "frontend" / "src" / "lib" / "rehypeHighlight.ts").read_text("utf-8")
    assert "rehypeHighlightQuery" in hl and "search-hit" in hl, "渲染树高亮插件缺失"
    md = (_ROOT / "frontend" / "src" / "components" / "Markdown.tsx").read_text("utf-8")
    assert "rehypePlugins={highlight ? [rehypeHighlightQuery(highlight)] : []}" in md, \
        "Markdown 没接上高亮插件"
    ei = (_ROOT / "frontend" / "src" / "components" / "EventItem.tsx").read_text("utf-8")
    assert "function HighlightedText(" in ei, "用户气泡的纯文本高亮缺失"
    tv = (_ROOT / "frontend" / "src" / "components" / "TaskView.tsx").read_text("utf-8")
    assert "highlight={search.trim() || undefined}" in tv, "没把搜索词传给消息（只在搜索时传，避免破坏 memo）"
    assert "useLayoutEffect" in tv, "数命中/滚动应当用 layout effect（渲染后立刻量）"
    verify = (_ROOT / "frontend" / "scripts" / "verify_search_safe.mjs").read_text("utf-8")
    assert "切任务再切回来不崩" in verify, "缺少【搜索后重渲染不崩】的浏览器验证"
