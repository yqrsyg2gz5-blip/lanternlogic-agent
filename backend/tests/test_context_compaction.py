"""上下文裁剪的护栏 —— P1-7（裁剪不能删掉用户的任务书）。

**真实坑**：原实现的注释写着「history[0] 是任务输入，永不丢」，于是从 `pop(1)` 开始丢。
但 `history[0]` 其实是**每次续聊注入的技能 system 消息**（见 loop 里 `_inject_skills`），
任务书在它后面 —— 结果"保护任务书"的代码恰好删掉了任务书。
长任务跑到后半程，模型就不记得用户要它干什么了，而且**落盘不可逆**。

本文件放在这里是因为复用了 `test_delivery_outcome._mk_run`（构造 TaskRun 的样板只写一处）。
"""
from __future__ import annotations

from tests.test_delivery_outcome import _mk_run

TASK_INPUT = "帮我把 C:\\Users\\y\\Downloads 里的图片按类型分类整理"


def _long_history(run) -> None:
    """技能 system + 任务书 + 大量工具往返（足以触发裁剪）。"""
    run.history = [
        {"role": "system", "content": "[可用技能]\n- 桌面整理：整理、归类、清理桌面或某目录下的文件"},
        {"role": "user", "content": TASK_INPUT},
    ]
    for i in range(40):
        run.history.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": f"call_{i:03d}",
                "type": "function",
                "function": {"name": "shell_exec", "arguments": "{}"},
            }],
        })
        run.history.append({"role": "tool", "tool_call_id": f"call_{i:03d}", "content": "x" * 600})


def _has_task_input(run) -> bool:
    return any(
        m.get("role") == "user" and TASK_INPUT in str(m.get("content") or "")
        for m in run.history
    )


def test_compaction_keeps_the_task_input(tmp_path):
    """★ P1-7 回归：无论裁多少轮，用户的任务书必须还在。"""
    run = _mk_run(tmp_path, [])
    run.max_context_tokens = 3000  # 小预算，逼它真的裁剪
    _long_history(run)
    before = len(run.history)

    import asyncio as _aio
    _aio.run(run._compact_history())

    assert len(run.history) < before, "测试前提：历史必须真的被裁掉了"
    assert _has_task_input(run), "任务书被裁剪删掉了（P1-7 复发）"


def test_compaction_keeps_protocol_pairs_intact(tmp_path):
    """裁剪后不能留下悬空的 tool_calls（否则下一轮上游必 400）。"""
    run = _mk_run(tmp_path, [])
    run.max_context_tokens = 3000
    _long_history(run)

    import asyncio as _aio
    _aio.run(run._compact_history())

    for i, m in enumerate(run.history):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            assert i + 1 < len(run.history), "末尾留下悬空 tool_calls"
            assert run.history[i + 1].get("role") == "tool", f"第 {i} 条 tool_calls 没有配对的回执"


def test_compaction_stops_when_only_task_input_remains(tmp_path):
    """极端小预算下：宁可超预算，也不丢任务书。"""
    run = _mk_run(tmp_path, [])
    run.max_context_tokens = 400  # 小到不可能满足
    _long_history(run)

    import asyncio as _aio
    _aio.run(run._compact_history())

    assert _has_task_input(run), "预算再小也不能丢任务书"
    assert len(run.history) >= 7, "保护尾窗（6 条）不应被突破"
