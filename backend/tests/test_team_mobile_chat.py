# -*- coding: utf-8 -*-
"""★★ A-3（2026-10-07 深夜）：**群聊页手机档** —— 照着 390px 实看的结果改的三处。

## 怎么看的（**不是猜的** ✓）

交接书里写：手机端量过两次都卡在"进团队页"那一步 ✗ ——
窄档下导航收进汉堡菜单「☰」✓ 点它老卡 ✓。
★ 正解（交接书自己也给了）：**先宽屏进群 → 再把视口改成 390×844** ✓ 这样绕开汉堡菜单 ✓

照着这个顺序在**用户真正用的那个入口**（8642 的 dist ✓）上量到的三件事：

| # | 量到的事实 | 后果 |
|---|---|---|
| ① | 群列表列 **131px** ⇒ 聊天列只剩 **192px** | 气泡最宽 **115px**、一句话折 4~5 行；群名只剩 3~5 个字 |
| ② | 汇总块在滚动区**最上面**，而 feed 一进来就**自动滚到底** ⇒ 卡片顶边在可视区**上方 573px** | 有历史记录的群**根本看不到**「还差 N 个决定」✗ |
| ③ | 那条命令在 153px 宽的卡里被压到 **13~41px**（命令本身 265px） | 只剩两三个字 ✓ 看不出要批什么 ✗ |

## 改了什么

① ≤760px **收成一列**（选中的群占满、列表让位；没选群时列表占满）✓ 聊天头上加「← 群列表」✓
② 汇总块**移出滚动区** ✓ 与 `GroupProgress` 同一层常驻 ✓
   （它本来就是按"常驻"设计的 ✓ 只是位置放错了 ✓ —— **用户看不见的提示等于没有** ✓）
③ ≤760px 命令**独占一行并换行** ✓ 显示全 ✓

★ 三条**只动窄档** ✓：规则全在 `@media (max-width: 760px)` 里 ✓
  基规则只多一个 `.team-back { display: none }`（而且它必须排在媒体查询**前面** ✓ 否则会把窄档那条盖死 ✓）

## 本文件钉的就是上面三条 + 那个"只动窄档"

（真机行为另有 `frontend/scripts/verify_team_mobile.mjs` ✓ 9 项 ✓ 直接量 8642 ✓）
"""
from __future__ import annotations

import pathlib

from app import main as m

ROOT = pathlib.Path(m.__file__).resolve().parents[2]
TEAM = ROOT / "frontend" / "src" / "components" / "TeamView.tsx"
CSS = ROOT / "frontend" / "src" / "styles.css"


def _around(text: str, needle: str, back: int = 240, fwd: int = 400) -> str:
    i = text.find(needle)
    assert i > 0, f"找不到 {needle!r} ⇒ 这条测试没在测东西 ✗"
    return text[max(0, i - back) : i + fwd]


def test_narrow_becomes_one_column():
    """★ ① 窄档**不许**再并排两列 ✗ —— 390px 实量：并排时聊天列只剩 192px ✓

    回滚实验：把 `has-group` 那个类去掉（= 修复前的形态 ✓）⇒ 本条必红 ✓
    """
    src = TEAM.read_text("utf-8")
    assert "`team-chat-grid${gid && curGroup ? ' has-group' : ''}`" in src, \
        "列表/聊天的取舍没跟「选没选群」绑定 ⇒ 窄档收不成一列 ✗"
    assert 'className="team-chat-col"' in src, "聊天列没有类名 ⇒ CSS 点不到它 ✗"
    css = CSS.read_text("utf-8")
    seg = _around(css, ".team-chat-grid.has-group .team-grouplist")
    assert "max-width: 760px" in seg, "这条不在**窄档**里 ⇒ 宽屏的两列也被收掉了 ✗"
    assert "display: none !important" in seg, "选中群后列表没让位 ✗"
    seg2 = _around(css, ".team-chat-grid:not(.has-group) .team-chat-col")
    assert "max-width: 760px" in seg2, "这条不在窄档里 ✗"
    assert "display: none !important" in seg2, "没选群时聊天列没收起 ⇒ 右边空一块 ✗"


def test_the_back_button_returns_to_the_list():
    """★ ① 的配套：**手机上一列时得有回头路** ✗（不然进了群就回不去列表 ✓）

    ★ 两个坑都钉住：
      · 宽档不该多这个按钮 ✓（两列都在时它没用 ✓）
      · 基规则**必须排在媒体查询前面** ✓ —— 同优先级下后面的会盖死前面的 ✓
        （本仓老教训：`styles.css` 尾部那条注释就是为这个写的 ✓ 我这次真踩到了 ✓）
    """
    src = TEAM.read_text("utf-8")
    seg = _around(src, 'className="team-back"')
    assert "setGid(null)" in seg and "setFeed([])" in seg, \
        "点了「← 群列表」没真的回列表 ✗（群也没清 ⇒ 切回来还是那个群 ✓）"
    css = CSS.read_text("utf-8")
    i_base = css.find(".team-back {")
    assert i_base > 0, "没有 `.team-back` 的基规则 ✗"
    assert "display: none" in css[i_base : i_base + 120], "宽档也显示它 ⇒ 多一个没用的按钮 ✗"
    i_media = css.find(".team-back { display: inline-flex; }")
    assert i_media > i_base, \
        "窄档那条排在基规则**前面**（或被盖死了）⇒ 手机上根本看不到回头路 ✗"


def test_the_summary_block_sits_outside_the_scroller():
    """★★ ② **那块汇总必须在滚动区外面** ✗ —— 390px 实量：

    它原来在消息列表最上面 ✓ 而 feed 一进来就**自动滚到底** ✗
    ⇒ 卡片顶边落在可视区**上方 573px** ✓ 有历史记录的群**根本看不到它** ✗
    （而这句"还差几个决定"正是用户唯一能看出"后面还排着几条"的地方 ✓）

    判据：它在源码里必须排在 `ref={feedRef}` 那个滚动容器**前面** ✓
    回滚实验：把滚动容器挪到它上面（= 搬走前的形态 ✓）⇒ 本条必红 ✓
    """
    src = TEAM.read_text("utf-8")
    i_block = src.find("个决定等你")
    i_feed = src.find("ref={feedRef}")
    assert i_block > 0 and i_feed > 0, "找不到那块汇总或消息区 ⇒ 这条测试没在测东西 ✗"
    assert i_block < i_feed, (
        "汇总块又回到**滚动区里面**了 ✗ —— 消息一进来就自动滚到底 ✓ "
        "块在它上面 = 用户**看不见** ✓（实量：在可视区上方 573px ✓）")


def test_the_command_is_not_cut_on_narrow():
    """★ ③ 待批那一行里的**命令**在窄档不许被切成两三个字 ✗

    实量：153px 宽的卡里它只剩 **13~41px**（命令本身 265px）✓ 用户看不出要批什么 ✗
    ★ `flex` 写在**内联样式**上（简写 ⇒ flex-basis:0%）✓ CSS 类盖不过内联 ⇒ 必须 `!important` ✓
    """
    src = TEAM.read_text("utf-8")
    assert 'className="mono pend-cmd"' in src, "命令那一栏没有类名 ⇒ CSS 点不到它 ✗"
    css = CSS.read_text("utf-8")
    seg = _around(css, ".pend-cmd {", 200, 400)
    assert "max-width: 760px" in seg, "命令换行只该在窄档生效（宽档保持一行省略 ✓）"
    assert "white-space: normal !important" in seg, "还是 nowrap ⇒ 长命令被切 ✗"
    assert "flex: 1 1 100% !important" in seg, "没独占一行 ⇒ 还是跟标题/按钮挤一行 ✗"


def test_the_has_group_flag_needs_the_group_to_really_exist():
    """★ 边角（**我改的那一行自己带出来的** ✓ 自己收拾 ✓）：

    `gid` 还在、`curGroup` 没了 —— 即"**这个群在别处被删掉了**" ✓
    （局域网手机直连时真会发生 ✓ 手机上删、桌面上还开着 ✓）
    ⇒ 窄档会变成"列表收起了 + 中间一句『选一个群开始聊天』" = **死路** ✗
      （列表看不见 ⇒ 没得选 ✓ 也回不去 ✓）

    ★ 判据：`has-group` 必须**同时**看 `gid` 与 `curGroup` ✓
    """
    src = TEAM.read_text("utf-8")
    assert "${gid && curGroup ? ' has-group' : ''}" in src, \
        "只看 `gid` ⇒ 群在别处被删之后，窄档收成一列就成了死路（列表没了、又没得选）✗"


def test_the_wide_layout_is_untouched():
    """★ 三条**只动窄档** ✓ —— 宽屏（>760px）一个像素都不该变 ✓

    判据：这几条规则出现的位置**前面**必须带着 `max-width: 760px` ✓
    （谁哪天把它挪出媒体查询 ⇒ 宽屏也跟着变 ⇒ 本条必红 ✓）
    """
    css = CSS.read_text("utf-8")
    for sel in (".team-chat-grid.has-group .team-grouplist",
                ".team-chat-grid:not(.has-group) .team-chat-col",
                ".team-chat-grid:not(.has-group) .team-grouplist",
                ".pend-cmd {"):
        i = css.find(sel)
        assert i > 0, f"找不到 {sel} ✗"
        assert "max-width: 760px" in css[max(0, i - 400) : i], \
            f"{sel} 不在窄档媒体查询里 ⇒ 宽屏也被改了 ✗"
