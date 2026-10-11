"""MCP 工具审批门回归 —— 审计 §8.2。

此前 `mcp__*` 工具在审批分支**之前** return：免审批 + 不受 per-task 约束，
实测 `mcp__filesystem__write_file` 写到过跨任务共享目录。修复后：
  · confirm 模式：所有 MCP 工具都要问；
  · auto_edit（默认）：**写入类**工具名（write/create/delete/move/…）要问；
  · full：不问（完全访问模式语义不变）。
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
from app.tools.builtin import ALL_TOOLS, register_mcp_tool, unregister_mcp_tool


def _register(tool: str) -> None:
    """生产环境 MCP 工具由启动时装载注册（mcp.py → register_mcp_tool）；
    测试里手动注册同名工具，绕开 _validate 的"未知工具"拦截。"""
    register_mcp_tool("filesystem", {
        "name": tool,
        "description": "test tool",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    })


def _unregister(tool: str) -> None:
    unregister_mcp_tool(f"mcp__filesystem__{tool}")


class _Scripted(ModelProvider):
    name = "scripted"

    def __init__(self, turns: list[AssistantTurn]) -> None:
        self._turns = turns
        self.n = 0

    async def next_turn(self, task_input, history, tools, on_delta=None):  # type: ignore[override]
        i = min(self.n, len(self._turns) - 1)
        self.n += 1
        return self._turns[i]


class _StubExecutor:
    def resolve_in_workspace(self, workdir: Path, rel: str) -> Path:
        return Path(workdir) / rel

    async def run_tool(self, tool: str, args: dict, workdir: Path) -> ExecResult:
        return ExecResult(True, "mcp-ok", 1)


def _mk(tmp_path: Path, tool_name: str, mode: str) -> TaskRun:
    return TaskRun(
        TaskSummary(id="task_20261002_mcp1", title="mcp", created_at="2026-10-02T00:00:00Z",
                    updated_at="2026-10-02T00:00:00Z"),
        "用 MCP 干活",
        store=FsStore(tmp_path / "data"),
        bus=EventBus(),
        provider=_Scripted([
            AssistantTurn(tool_call=ToolCall(name=tool_name, arguments={"path": "x.txt"})),
            AssistantTurn(tool_call=ToolCall(name="task_done", arguments={"message": "完成"})),
        ]),
        executor=_StubExecutor(),  # type: ignore[arg-type]
        approval=ApprovalManager(),
        tools=[],
        max_iterations=5,
        timeout_seconds=5.0,
        approval_required=["rm"],
        access_mode_getter=lambda: mode,
        on_finish=lambda r: None,
    )


def _states(run: TaskRun) -> list[str]:
    return [str(e.payload.get("state")) for e in run.store.read_events(run.task.id) if e.type == "status"]


def _run_with_approval(run: TaskRun) -> None:
    async def go() -> None:
        t = run.start()
        await asyncio.sleep(0.05)
        # 弹出审批后自动放行（once），让流程走完
        for _ in range(50):
            if run.approval.resolve(run.task.id, "call_001", "once"):
                break
            await asyncio.sleep(0.02)
        await t

    asyncio.run(go())


def test_mcp_write_tool_asks_in_auto_edit(tmp_path):
    _register("write_file")
    run = _mk(tmp_path, "mcp__filesystem__write_file", "auto_edit")
    _run_with_approval(run)
    assert "waiting_approval" in _states(run), "auto_edit 下 MCP 写入工具必须弹审批（§8.2）"


def test_mcp_any_tool_asks_in_confirm(tmp_path):
    _register("read_file")
    run = _mk(tmp_path, "mcp__filesystem__read_file", "confirm")
    _run_with_approval(run)
    assert "waiting_approval" in _states(run), "confirm 模式下 MCP 只读工具也要问"


def test_mcp_read_tool_passes_in_auto_edit(tmp_path):
    _register("read_file")
    run = _mk(tmp_path, "mcp__filesystem__read_file", "auto_edit")
    asyncio.run(run._run())
    assert "waiting_approval" not in _states(run), "auto_edit 下 MCP 只读工具不应弹审批"


def teardown_module(module) -> None:
    for name in list(ALL_TOOLS):
        if name.startswith("mcp__"):
            unregister_mcp_tool(name)
