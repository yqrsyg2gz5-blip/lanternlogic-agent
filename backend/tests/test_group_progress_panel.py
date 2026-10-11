# -*- coding: utf-8 -*-
"""★ 2026-10-06 群聊「进度面板」——「这活到哪一步了」✓

## 为什么做它（用户原话）

组长拆完分工，那张分工单**就是一条消息** ✓ 滚上去就找不着了 ✗
—— 用户想知道的是"**到哪了**"（几项 ✓ 谁在跑 ✓ 有没有失败 ✓ 有没有在等我点 ✓），
而不是往上翻聊天记录 ✗。

## 边界（都写了断言 ✓）

· **没分工单就不显示** ✓（不是组长模式 / 还没拆解 ⇒ 整块不出现 ✓ 不留空框 ✗）
· **只读、不写** ✓ —— 它只渲染 `leader_plan` ✓ **不碰消息流** ✓（改动面小 = 风险小 ✓）
· 数据**后端早就有了** ✓（`leader_goal` / `leader_plan` ✓）⇒ **没改后端** ✓
"""
from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_PANEL = (_ROOT / "frontend" / "src" / "components" / "GroupProgress.tsx").read_text("utf-8")
_TEAMVIEW = (_ROOT / "frontend" / "src" / "components" / "TeamView.tsx").read_text("utf-8")


def test_panel_shows_the_goal_and_every_item_status():
    """面板要把"在做什么 + 每项到哪了"讲清楚 ✓（这是它存在的**唯一**理由 ✓）。"""
    assert "这活在做什么" in _PANEL, "面板没有标题 ✗"
    assert "leader_goal" in _PANEL, "没显示总目标 ✗"
    assert "leader_plan" in _PANEL, "没读分工单 ✗"
    for st in ("done", "running", "failed", "blocked", "pending"):
        assert st in _PANEL, f"状态 {st} 没处理 ✗（后端就这五种 ✓）"
    # 汇总（✅n 🔄n ❌n ⏸n）——用户扫一眼就要看到的那行 ✓
    assert "✅" in _PANEL and "🔄" in _PANEL and "❌" in _PANEL and "⏸" in _PANEL


def test_panel_is_hidden_when_there_is_no_plan():
    """**没分工单就整块不显示** ✓ —— 不是组长模式、或者还没拆解时，
    留一个空框在那儿纯粹是占地方 ✗（本班第一版就想这么干 ✓ 被自己拦住了 ✓）。"""
    assert re.search(r"if \(!plan\.length\) return null", _PANEL), "没有'空则隐藏' ✗"


def test_panel_reads_only_and_does_not_touch_the_message_stream():
    """**只读** ✓：面板不许改 `leader_plan`、不许碰消息渲染 ✓
    （它是"显示器" ✓ 不是"控制器" ✓ —— 混在一起就会出"看着看着状态变了"那种鬼故事 ✗）。"""
    assert "api." not in _PANEL, "面板自己去调接口了 ✗（数据由父组件传进来 ✓）"
    assert "fetch(" not in _PANEL, "面板自己发请求了 ✗"
    assert "setLeader" not in _PANEL and "leader_plan =" not in _PANEL, "面板改了分工单 ✗"
    # 它被挂在消息流**上面**（不是塞进消息里 ✓）
    assert _TEAMVIEW.index("<GroupProgress") < _TEAMVIEW.index("ref={feedRef}"), \
        "面板没挂在消息流上面 ✗（滚上去就看不见了 ✓ 那就白做了 ✓）"


def test_panel_can_collapse_and_remembers():
    """能**收起** ✓ 而且**记住** ✓（否则每次进来都要手动收一遍 ✗ 用户会烦 ✓）。"""
    assert "localStorage" in _PANEL and "teamProgressOpen" in _PANEL, "没收起状态记忆 ✗"
    assert "收起" in _PANEL and "展开" in _PANEL, "没有收起/展开的入口 ✗"
