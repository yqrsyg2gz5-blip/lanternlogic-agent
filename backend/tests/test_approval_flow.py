"""审批流：status 事件必须携带 call_id —— P0-1 的后端契约部分。

为什么需要：
  前端要渲染"允许一次 / 总是允许 / 拒绝"三个按钮，就必须精确知道**待审批的是哪一次
  工具调用**。此前 `status.waiting_approval` 的 payload 不带 call_id，前端只能靠
  "最近一条 action" 去猜——一旦事件顺序有偏差就会点错。
  2026-09-30 起 status payload 增加 add-only 字段 `call_id`（见 contracts/01-events.md）。

这两个用例同时覆盖了"批准后继续"和"拒绝后如实反馈"两条路径。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.approval import ApprovalManager
from app.bus import EventBus
from app.executors.local import LocalExecutor
from app.loop import TaskRun
from app.providers.base import AssistantTurn, ModelProvider, ToolCall
from app.schemas import TaskSummary
from app.store import FsStore

DANGEROUS_CMD = "rm -f tmp.txt"


class _RmProvider(ModelProvider):
    """第 1 轮调 shell_exec 执行 rm（命中审批清单），第 2 轮纯文本收尾。"""

    name = "rm-once"

    def __init__(self) -> None:
        self.calls = 0

    async def next_turn(self, task_input, history, tools, on_delta=None):  # type: ignore[override]
        self.calls += 1
        if self.calls == 1:
            return AssistantTurn(
                tool_call=ToolCall(name="shell_exec", arguments={"command": DANGEROUS_CMD})
            )
        return AssistantTurn(text="已结束")


def _mk_run(tmp_path) -> TaskRun:
    store = FsStore(tmp_path / "data")
    task = TaskSummary(
        id="task_20260930_appr",
        title="审批流",
        created_at="2026-09-30T00:00:00Z",
        updated_at="2026-09-30T00:00:00Z",
    )
    ex_cfg = SimpleNamespace(
        workspace_root=tmp_path / "ws",
        timeout_seconds=5.0,
        shell="",
        search_url="",
        browser_channel="",
        comfyui_url="",
        image_checkpoint="",
        allowed_dirs=[],
    )
    return TaskRun(
        task,
        "删掉临时文件",
        store=store,
        bus=EventBus(),
        provider=_RmProvider(),
        executor=LocalExecutor(ex_cfg),
        approval=ApprovalManager(),
        tools=[],
        max_iterations=3,
        timeout_seconds=5.0,
        approval_required=["rm"],  # 命中审批清单
        on_finish=lambda r: None,
    )


def _await_waiting(q, timeout=10.0) -> str:
    """等到 waiting_approval 的 status 事件，返回它携带的 call_id。"""
    async def _inner() -> str:
        while True:
            ev = await asyncio.wait_for(q.get(), timeout=timeout)
            if ev.type == "status" and ev.payload.get("state") == "waiting_approval":
                return str(ev.payload.get("call_id") or "")

    return asyncio.run(_inner())


def _scenario(tmp_path, decision: str) -> tuple[TaskRun, str]:
    """跑一条会在 shell_exec 处挂起审批的任务，按 decision 处理后返回 (run, call_id)。"""
    run = _mk_run(tmp_path)
    q = run.bus.subscribe(run.task.id)

    async def _inner() -> str:
        task = asyncio.create_task(run._run())
        call_id = ""
        while True:
            ev = await asyncio.wait_for(q.get(), timeout=10)
            if ev.type == "status" and ev.payload.get("state") == "waiting_approval":
                call_id = str(ev.payload.get("call_id") or "")
                break
        # P1-6 起审批按 (task_id, call_id) 隔离
        assert run.approval.resolve(run.task.id, call_id, decision) is True, "resolve 必须成功"
        await asyncio.wait_for(task, timeout=10)
        return call_id

    return run, asyncio.run(_inner())


def test_waiting_approval_status_carries_call_id(tmp_path):
    """核心断言：待审批的 status 事件必须带 call_id，且与 action 事件一致。"""
    run, call_id = _scenario(tmp_path, "once")
    assert call_id, "waiting_approval 的 status payload 缺少 call_id（前端将无法精确关联）"

    evs = run.store.read_events(run.task.id)
    actions = [e for e in evs if e.type == "action"]
    assert actions, "应当有 action 事件"
    assert actions[0].payload["call_id"] == call_id, "status.call_id 必须等于对应 action 的 call_id"
    assert actions[0].payload["params"]["command"] == DANGEROUS_CMD


def test_approve_once_lets_run_continue(tmp_path):
    """批准后：状态回到 running，并产生观察结果，任务最终 done。"""
    run, _ = _scenario(tmp_path, "once")
    evs = run.store.read_events(run.task.id)
    states = [e.payload["state"] for e in evs if e.type == "status"]
    assert "waiting_approval" in states
    assert states.index("waiting_approval") < len(states) - 1, "审批后应有后续状态"
    assert any(e.type == "observation" and e.payload["ok"] for e in evs)
    assert run.task.status == "done"


def test_deny_is_reported_as_failed_observation(tmp_path):
    """拒绝后：观察结果 ok=false 且写明 deny，任务仍能收尾（不炸 loop）。"""
    run, _ = _scenario(tmp_path, "deny")
    evs = run.store.read_events(run.task.id)
    denied = [
        e for e in evs
        if e.type == "observation" and e.payload.get("ok") is False and "deny" in str(e.payload.get("result", ""))
    ]
    assert denied, f"拒绝必须体现为失败的观察结果，实际事件：{[e.type for e in evs]}"
