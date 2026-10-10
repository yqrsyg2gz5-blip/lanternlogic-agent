"""Phase 3 ⑦ 零碎批（消息时间戳 + 对话内搜索）的锚点。

两件事都属于"看得见的体验"，所以锚点分两层：
  · 源码层：入口在、关键行为在（时间戳格式、DOM 文本节点高亮、清场、Ctrl+F）
  · ★ 还钉一个**容易复发**的坑：`timeline` 必须 memo ——
    不 memo 的话每次渲染都是新数组 ⇒ 依赖它的搜索高亮 effect 每渲染都重跑，
    "Enter 切下一个命中"刚设好就被重置（本班实测踩到，浏览器验证抓出来的）
"""
from __future__ import annotations

import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_TV = (_ROOT / "frontend" / "src" / "components" / "TaskView.tsx").read_text("utf-8")
_EV = (_ROOT / "frontend" / "src" / "components" / "EventItem.tsx").read_text("utf-8")
_CSS = (_ROOT / "frontend" / "src" / "styles.css").read_text("utf-8")
_VERIFY = (_ROOT / "frontend" / "scripts" / "verify_find_and_time.mjs").read_text("utf-8")


def test_timestamps_are_rendered_on_messages_only():
    assert "function clockOf(" in _EV, "没有时间格式化函数"
    assert "toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })" in _EV, \
        "时间格式不是 HH:MM"
    assert "clockOf(ts)" in _EV, "消息上没渲染时间戳"
    assert "ts={event.ts}" in _EV, "事件时间没传给消息组件"
    # 时间戳只出现在消息气泡里（每处都是"判断 + 取值"两个 clockOf(ts) ⇒ 助手 2 个 + 用户 2 个）；
    # 工具卡片/计划/状态一律不加 —— 用户要的是"别更乱"
    assert _EV.count("clockOf(ts)") == 4, "时间戳出现的位置不对（应当只有助手署名行与用户气泡角）"


def test_timeline_is_memoized_because_search_depends_on_it():
    """★ 这条钉的是本班踩到的真坑：timeline 不 memo ⇒ 搜索的"下一个"会被立刻重置。"""
    assert "const timeline = useMemo(() => events.filter((e) => e.type !== 'message_delta'), [events]);" in _TV, \
        "timeline 没 memo（搜索高亮 effect 会每渲染重跑、把当前位置重置）"


def test_search_highlights_in_the_render_tree_and_cleans_up():
    """★ 2026-10-05 重写：高亮**不再直接改 DOM**。

    旧实现（createTreeWalker + replaceWith 换文本节点）会让 React 的虚拟 DOM 与真实 DOM 脱节，
    之后任何一次重渲染都抛 `insertBefore ... is not a child of this node`，整页被卸载
    （用户实测"点允许一次屏幕全黑"就是这个）。所以这条锚点从"检查怎么改 DOM"变成
    **"检查谁都不许改 DOM"**：
      · 高亮在渲染树里切 <mark>（rehype 插件 + 纯文本高亮组件）
      · TaskView 只**读** DOM（数命中、滚动），清空搜索靠重渲染而不是手工还原节点
    """
    # ★ 剥掉注释再查：解释"以前怎么错的"的注释里会**出现**这些词（本班被绊过两次）
    code = re.sub(r"/\*.*?\*/|^\s*//.*$", "", _TV, flags=re.S | re.M)
    assert "createTreeWalker" not in code and "replaceWith" not in code, \
        "TaskView 又在直接改 DOM 了（会引发 insertBefore 崩溃）"
    assert "clearSearchMarks" not in code, "手工清场已废弃：清空搜索词后重渲染即可"
    hl = (_ROOT / "frontend" / "src" / "lib" / "rehypeHighlight.ts").read_text("utf-8")
    assert "className: ['search-hit']" in hl, "渲染树高亮没按约定打 mark class"
    assert "highlight={search.trim() || undefined}" in _TV, "搜索词没传给消息组件"
    assert "marks[hit]?.scrollIntoView" in _TV, "当前命中没滚动到视野里"
    assert "mark.search-hit" in _TV, "没有读 DOM 数命中"


def test_search_ui_and_shortcuts_exist():
    for k in ("find-bar", "find-input", "find-count", "search-hit-on"):
        assert k in _TV or k in _CSS, f"缺 {k}"
    assert "e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f'" in _TV, "没有 Ctrl+F 快捷键"
    assert "stepHit(e.shiftKey ? -1 : 1)" in _TV, "Enter / Shift+Enter 的切换没接上"
    assert "e.key === 'Escape'" in _TV, "Esc 没关闭搜索"


def test_verification_covers_navigation_and_cleanup():
    for k in ("Enter 切到下一个", "Shift+Enter 回上一个", "换词后旧高亮被清掉", "关闭后高亮全部清掉"):
        assert k in _VERIFY, f"验证脚本没覆盖：{k}"
    assert "时间戳（HH:MM）" in _VERIFY and re.search(r"\\d\{1,2\}:\\d\{2\}", _VERIFY), \
        "没验证时间戳格式"
