"""Anthropic 消息适配 —— P0-3 回归（不需要真 Key，纯形状验证）。

修复前的两处硬伤：
  ① history 被**原样转发**（含 `assistant.tool_calls` 与 `role:"tool"`），
     而 Anthropic Messages API 只接受 user/assistant → 第一次工具调用后的第二轮必 400；
  ② `_assemble` 用 `if role != "system"` 把**所有 system 消息丢光**，
     于是项目 master 指令与技能清单对 Claude 静默失效。

本文件锁死转换后的形状：system 并入顶层、tool_calls→tool_use、role:tool→tool_result、
相邻同角色合并、首条必须是 user。
"""
from __future__ import annotations

from app.providers.anthropic import build_messages


def _tc(call_id: str, name: str = "shell_exec", args: str = '{"command":"ls"}') -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}


def test_system_messages_go_to_top_level_not_dropped():
    """① 的回归：system 内容必须进 system 参数，不能丢。"""
    history = [
        {"role": "system", "content": "[项目指令]\n所有回复必须以【验收】开头。"},
        {"role": "system", "content": "[可用技能]\n- 写作助手：写诗文案"},
        {"role": "user", "content": "写首诗"},
    ]
    system, msgs = build_messages(history)
    assert "[项目指令]" in system
    assert "[可用技能]" in system
    assert msgs == [{"role": "user", "content": [{"type": "text", "text": "写首诗"}]}]


def test_tool_history_becomes_anthropic_blocks():
    """② 的回归：多轮工具历史必须转成 tool_use / tool_result，且不出现 tool 角色。"""
    history = [
        {"role": "user", "content": "删掉临时文件"},
        {"role": "assistant", "content": "", "tool_calls": [_tc("call_001")]},
        {"role": "tool", "tool_call_id": "call_001", "content": "已删除"},
        {"role": "user", "content": "再确认一下"},
    ]
    _, msgs = build_messages(history)

    assert [m["role"] for m in msgs] == ["user", "assistant", "user"], "不得出现 tool 角色"
    assert any(b["type"] == "tool_use" and b["id"] == "call_001" for b in msgs[1]["content"])
    kinds = [b["type"] for b in msgs[2]["content"]]
    assert "tool_result" in kinds
    assert any(
        b["type"] == "tool_result" and b["tool_use_id"] == "call_001" for b in msgs[2]["content"]
    )
    # 回执与紧随其后的用户文本合并进同一条 user 消息（保持交替）
    assert kinds[0] == "tool_result" and "text" in kinds


def test_multi_round_tool_history_alternates():
    """两轮工具调用也不该产生连续同角色消息（Anthropic 要求交替）。"""
    history = [
        {"role": "user", "content": "任务"},
        {"role": "assistant", "content": "", "tool_calls": [_tc("call_001")]},
        {"role": "tool", "tool_call_id": "call_001", "content": "r1"},
        {"role": "assistant", "content": "", "tool_calls": [_tc("call_002")]},
        {"role": "tool", "tool_call_id": "call_002", "content": "r2"},
        {"role": "user", "content": "继续"},
    ]
    _, msgs = build_messages(history)
    roles = [m["role"] for m in msgs]
    assert roles == ["user", "assistant", "user", "assistant", "user"]
    assert all(roles[i] != roles[i + 1] for i in range(len(roles) - 1)), "必须 user/assistant 交替"


def test_assistant_text_and_tool_use_coexist():
    history = [
        {"role": "user", "content": "干活"},
        {"role": "assistant", "content": "我先看看目录", "tool_calls": [_tc("call_009", "list_dir")]},
    ]
    _, msgs = build_messages(history)
    blocks = msgs[1]["content"]
    assert blocks[0] == {"type": "text", "text": "我先看看目录"}
    assert blocks[1]["type"] == "tool_use" and blocks[1]["name"] == "list_dir"


def test_malformed_tool_arguments_do_not_crash():
    history = [
        {"role": "user", "content": "x"},
        {"role": "assistant", "content": "", "tool_calls": [_tc("call_1", args="{ 不是 JSON")]},
    ]
    _, msgs = build_messages(history)
    assert msgs[1]["content"][0]["input"] == {}


def test_first_message_is_always_user():
    """history 以 assistant 开头时（异常情况），补一条占位 user，避免 API 直接拒绝。"""
    history = [{"role": "assistant", "content": "我准备好了"}]
    _, msgs = build_messages(history)
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"


def test_empty_history_is_safe():
    system, msgs = build_messages([])
    assert system  # 至少有基础 SYSTEM_PROMPT
    assert msgs == []


def test_system_prompt_is_always_first_in_system_text():
    system, _ = build_messages([{"role": "system", "content": "[项目指令]X"}])
    assert system.startswith("你是 LanternLogic Agent"), "基础提示词必须在最前"
    assert "[项目指令]X" in system
