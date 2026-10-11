"""交付纪律门 —— P2（用户要求"必须让他听话"的硬约束）。

用户原话意图：模型的上限改不了，但系统可以**强制它在交付前验证**——
写了代码却没任何验证（code_check / 真跑）就 task_done → 第一次被拦回重做。

规则（loop._should_block_delivery）：
  1. 没写过代码文件 → 不拦（写作/调研类任务不受影响）
  2. 写过代码 + 有验证记录（code_check 或 shell_exec 成功）→ 不拦
  3. 写过代码 + 零验证 → 拦一次（发 observation 说明原因，模型下一轮看到后去验证）
  4. 只拦一次（_delivery_gate_fired），避免模型反复被拦死循环
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.approval import ApprovalManager
from app.bus import EventBus
from app.executors.base import ExecResult
from app.loop import TaskRun
from app.providers.base import AssistantTurn, ModelProvider, ToolCall
from app.schemas import TaskSummary
from app.store import FsStore


class _StubExecutor:
    def resolve_in_workspace(self, workdir: Path, rel: str) -> Path:
        return Path(workdir) / rel

    async def run_tool(self, tool: str, args: dict, workdir: Path) -> ExecResult:
        if tool == "code_check":  # 与真实输出语义一致：✓ 开头才算校验通过
            return ExecResult(True, f"✓ {args.get('path', 'x.py')} 语法正确", 1)
        return ExecResult(True, "ok", 1)


class _Scripted(ModelProvider):
    name = "scripted"

    def __init__(self, turns):
        self._turns = turns
        self.n = 0

    async def next_turn(self, task_input, history, tools, on_delta=None):  # type: ignore[override]
        i = min(self.n, len(self._turns) - 1)
        self.n += 1
        return self._turns[i]


def _write_code() -> AssistantTurn:
    return AssistantTurn(tool_call=ToolCall(name="file_write", arguments={"path": "x.py", "content": "print(1)"}))


def _check() -> AssistantTurn:
    return AssistantTurn(tool_call=ToolCall(name="code_check", arguments={"path": "x.py"}))


def _shell() -> AssistantTurn:
    return AssistantTurn(tool_call=ToolCall(name="shell_exec", arguments={"command": "python x.py"}))


def _done(msg="完工") -> AssistantTurn:
    return AssistantTurn(tool_call=ToolCall(name="task_done", arguments={"message": msg}))


def _mk_run(tmp_path, turns):
    store = FsStore(tmp_path / "data")
    task = TaskSummary(
        id="task_20261001_gate",
        title="纪律门",
        created_at="2026-10-01T00:00:00Z",
        updated_at="2026-10-01T00:00:00Z",
    )
    return TaskRun(
        task,
        "写个脚本",
        store=store,
        bus=EventBus(),
        provider=_Scripted(turns),
        executor=_StubExecutor(),  # type: ignore[arg-type]
        approval=ApprovalManager(),
        tools=[],
        max_iterations=8,
        timeout_seconds=5.0,
        approval_required=[],
        on_finish=lambda r: None,
    )


def _run(tmp_path, turns):
    run = _mk_run(tmp_path, turns)
    asyncio.run(run._run())
    return run


def _events(run: TaskRun):
    return run.store.read_events(run.task.id)


def test_gate_blocks_unverified_code_delivery(tmp_path):
    """写了代码零验证 → task_done 被拦（observation ok=False 含拦截说明）。

    §8.3：脚本耗尽后 _Scripted 会重复末尾的 task_done——正好模拟"连喊多次交付"。
    旧版拦 1 次就放（弱点）；现在拦满 3 次才放行，且 outcome 强制降级 partial。
    """
    run = _run(tmp_path, [_write_code(), _done()])
    evs = _events(run)
    obs = [e for e in evs if e.type == "observation"]
    blocked = [e for e in obs if e.payload.get("ok") is False and "纪律门" in str(e.payload.get("result", ""))]
    assert len(blocked) == 3, f"连喊多次应被拦满 3 次，实际拦 {len(blocked)} 次"
    assert run.task.status == "partial", f"拦满放行后必须降级 partial，实际 {run.task.status}"

    # 对照：拦满放行时终态必须是 partial 而非 success（诚实降级可感知）
    assert run.task.status == "partial" and "纪律门" in "".join(
        str(e.payload.get("result", "")) for e in obs if e.payload.get("ok") is False)


def test_gate_allows_after_code_check(tmp_path):
    """写过代码 + code_check → 不拦，直接交付。"""
    run = _run(tmp_path, [_write_code(), _check(), _done()])
    evs = _events(run)
    blocked = [e for e in evs if e.type == "observation" and e.payload.get("ok") is False]
    assert not blocked, "验证过就不该拦"
    assert run.task.status == "done"


def test_gate_allows_after_shell_run(tmp_path):
    """写过代码 + 跑过 shell → 不拦。"""
    run = _run(tmp_path, [_write_code(), _shell(), _done()])
    evs = _events(run)
    blocked = [e for e in evs if e.type == "observation" and e.payload.get("ok") is False]
    assert not blocked, "真跑过就不该拦"
    assert run.task.status == "done"


def test_gate_ignores_non_code_tasks(tmp_path):
    """没写代码的任务（纯写作/调研）不受门影响。"""
    turn = AssistantTurn(tool_call=ToolCall(name="file_write", arguments={"path": "note.md", "content": "# 笔记"}))
    run = _run(tmp_path, [turn, _done()])
    evs = _events(run)
    blocked = [e for e in evs if e.type == "observation" and e.payload.get("ok") is False]
    assert not blocked, "非代码文件不该触发纪律门"
    assert run.task.status == "done"
