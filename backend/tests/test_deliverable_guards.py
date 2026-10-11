"""交付守卫 —— P2-21（附件校验）与 P1-15（缺 Key 不留僵尸任务）。

P2-21 真实事故：`task_done` 的 attachments 被写成
  `["/c/Users/y/Desktop/上下文验证"]` —— 一个**刚被删掉的目录**，既不存在也不在工作区，
  前端会渲染出一个点不开的附件芯片。

P1-15 真实事故：缺 Key 时服务照常启动、界面看着正常，直到用户建第一个任务才 500，
  而任务**已经落盘** → 永久卡在 `created`（data/tasks/index.json 里那 3 条就是这么来的）。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.approval import ApprovalManager
from app.bus import EventBus
from app.executors.local import LocalExecutor
from app.loop import TaskRun
from app.providers.base import AssistantTurn, ModelProvider, ToolCall
from app.schemas import TaskSummary
from app.store import FsStore


class _DoneWithBadAttachment(ModelProvider):
    """直接调 task_done，并带一个不存在的附件。"""

    name = "bad-attachment"

    async def next_turn(self, task_input, history, tools, on_delta=None):  # type: ignore[override]
        return AssistantTurn(
            tool_call=ToolCall(
                name="task_done",
                arguments={"message": "做完了", "attachments": ["/c/Users/y/Desktop/上下文验证"]},
            )
        )


def _mk_run(tmp_path, provider=None) -> TaskRun:
    store = FsStore(tmp_path / "data")
    task = TaskSummary(
        id="task_20260930_grd1",  # 契约要求 task_<8位日期>_<4位[a-z0-9]>，少一位会被 EventEnvelope 拒绝
        title="交付守卫",
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
        "随便做点什么",
        store=store,
        bus=EventBus(),
        provider=provider or _DoneWithBadAttachment(),
        executor=LocalExecutor(ex_cfg),
        approval=ApprovalManager(),
        tools=[],
        max_iterations=3,
        timeout_seconds=5.0,
        approval_required=[],
        on_finish=lambda r: None,
    )


# ---------- P2-21 附件校验 ----------


def test_only_existing_files_in_workspace_are_kept(tmp_path):
    run = _mk_run(tmp_path)
    (run.workdir / "ok.txt").write_text("x", encoding="utf-8")
    (run.workdir / "sub").mkdir()

    kept, dropped = run._filter_attachments(
        ["ok.txt", "/c/Users/y/Desktop/上下文验证", "sub", "missing.txt"]
    )
    assert kept == ["ok.txt"]
    assert set(dropped) == {"/c/Users/y/Desktop/上下文验证", "sub", "missing.txt"}


def test_file_outside_workspace_is_rejected(tmp_path):
    run = _mk_run(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    kept, dropped = run._filter_attachments([str(outside)])
    assert kept == []
    assert dropped == [str(outside)]


def test_task_done_drops_bad_attachment_and_tells_the_model(tmp_path):
    run = _mk_run(tmp_path)
    asyncio.run(run._run())
    evs = run.store.read_events(run.task.id)

    delivered = [e for e in evs if e.type == "message" and e.payload.get("role") == "assistant"]
    assert delivered, "应当有交付消息"
    assert "attachments" not in delivered[-1].payload, "坏附件不得传给前端"

    obs = [e for e in evs if e.type == "observation"]
    assert any("已忽略" in str(o.payload.get("result", "")) for o in obs), (
        "被丢弃的附件要写进观察结果，模型才知道"
    )


def test_good_attachment_survives(tmp_path):
    class _Good(ModelProvider):
        name = "good-attachment"

        async def next_turn(self, task_input, history, tools, on_delta=None):  # type: ignore[override]
            return AssistantTurn(
                tool_call=ToolCall(
                    name="task_done",
                    arguments={"message": "做完了", "attachments": ["ok.txt"]},
                )
            )

    async def _prepare() -> TaskRun:
        run = _mk_run(tmp_path, provider=_Good())
        (run.workdir / "ok.txt").write_text("x", encoding="utf-8")
        await run._run()
        return run

    run = asyncio.run(_prepare())
    evs = run.store.read_events(run.task.id)
    delivered = [e for e in evs if e.type == "message" and e.payload.get("role") == "assistant"]
    assert delivered[-1].payload.get("attachments") == ["ok.txt"]


# ---------- P1-15 启动预检：不留僵尸任务 ----------


def test_launch_task_returns_503_and_creates_no_task(monkeypatch):
    from app import main as m

    before = len(m.tasks)
    monkeypatch.setattr(m.cfg.model, "provider", "no-such-provider", raising=False)

    with pytest.raises(HTTPException) as ei:
        m._launch_task("这条任务不该被创建")

    assert ei.value.status_code == 503
    assert "不可用" in str(ei.value.detail)
    assert len(m.tasks) == before, "提供者不可用时不得留下卡在 created 的僵尸任务"
