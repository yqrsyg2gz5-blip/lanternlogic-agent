# -*- coding: utf-8 -*-
"""DeepSeek 思考模式：`reasoning_content` **必须原样传回去** ✗ —— 锁死这条

★ 用户实测（2026-10-09）：
    RuntimeError: 上游 400: The `reasoning_content` in the thinking mode must be passed back
★ 根因：全仓**一处都没处理**它 ✗ ⇒ 收到就丢 ⇒ 第二轮必挂 ✓（不是偶发 ✓ 是必然 ✓）

★ 为什么用"源码级断言"而不是真调 DeepSeek：
    真调需要用户的 Key 与真钱 ✓ 测试里不能干 ✓（也不该把线上接口当测试夹具 ✓）
  但这条 bug 的形态是"**字段根本没被处理**"✗ ⇒ 源码级断言**恰好**能锁住它 ✓
  （本仓已有先例 ✓：test_click_audit 里那条"CSP sandbox 不许被放宽"同理 ✓）
"""
from __future__ import annotations

import pathlib

APP = pathlib.Path(__file__).resolve().parents[1] / "app"


def test_assistant_turn_can_carry_reasoning():
    """① 载体：turn 上得有地方存它 ✓"""
    from app.providers.base import AssistantTurn
    assert AssistantTurn(reasoning="想了一下").reasoning == "想了一下"
    assert AssistantTurn().reasoning is None


def test_both_parse_paths_capture_it():
    """② ★ 两条解析路都要收 —— **流式那条尤其关键**（默认走的就是它 ✗ 不改等于没修 ✓）"""
    src = (APP / "providers" / "openai_compat.py").read_text(encoding="utf-8")
    n = src.count("reasoning_content")
    assert n >= 2, f"解析侧只出现 {n} 次 ✗ —— 非流式与流式**各**要收一次 ✓"
    assert '"reasoning_content"' in src, "要按上游字段名取 ✓"


def test_loop_sends_it_back_with_the_message():
    """③ ★ 最关键：拼历史时必须**和那条 assistant 消息一起**带回去 ✗（单独放别处 DeepSeek 不认 ✓）"""
    src = (APP / "loop.py").read_text(encoding="utf-8")
    assert "reasoning_content" in src, "拼历史时没带回去 ✗ ⇒ 思考模型第二轮就会 400 ✓"
    assert "turn.reasoning" in src, "要从 turn 上取（而不是凭空造一个 ✗）"
