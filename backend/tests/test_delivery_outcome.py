"""交付语义与来源核实 —— P0-5 / P0-6 的护栏。

P0-5（`done` 不可信）：实测 `task_20260930_eb65` 生成视频两次失败、工作区为空、
  交付消息明写"本次没有可交付的视频文件"，任务状态却是 `done`、侧边栏显示绿点。
  现在：模型用 `task_done.outcome` 如实声明 success / partial / failed，状态随之变化。

P0-6（知识类交付必须可追溯）：实测那份调研报告**全文零 URL**，厂商名还是编的；
  根因是两个 Wide 子任务一次检索都没做。现在：sources 只保留本次真实访问过的链接，
  联网检索过却没给来源 → 自动降级为 partial。

本文件用**桩执行器**代替真实网络，测试不依赖外网。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.approval import ApprovalManager
from app.bus import EventBus
from app.executors.base import ExecResult
from app.loop import TaskRun, _normalize_url
from app.providers.base import AssistantTurn, ModelProvider, ToolCall
from app.schemas import TaskSummary
from app.store import FsStore

REAL_URL = "https://example.com/real"
FAKE_URL = "https://totally-made-up.example/nope"


class _StubExecutor:
    """任何工具都返回成功，输出里带一个真实 URL；不碰网络。"""

    def resolve_in_workspace(self, workdir: Path, rel: str) -> Path:
        return Path(workdir) / rel

    async def run_tool(self, tool: str, args: dict, workdir: Path) -> ExecResult:
        if tool == "code_check":  # 与真实输出语义一致：✓ 开头才算校验通过
            return ExecResult(True, f"✓ {args.get('path', 'x.py')} 语法正确", 1)
        return ExecResult(True, f"{REAL_URL}\n标题：真实页面", 1)


class _Scripted(ModelProvider):
    """按脚本依次返回若干 turn。"""

    name = "scripted"

    def __init__(self, turns: list[AssistantTurn]) -> None:
        self._turns = turns
        self.n = 0

    async def next_turn(self, task_input, history, tools, on_delta=None):  # type: ignore[override]
        i = min(self.n, len(self._turns) - 1)
        self.n += 1
        return self._turns[i]


def _done(**args) -> AssistantTurn:
    return AssistantTurn(tool_call=ToolCall(name="task_done", arguments=args))


def _mk_run(tmp_path, turns: list[AssistantTurn]) -> TaskRun:
    store = FsStore(tmp_path / "data")
    task = TaskSummary(
        id="task_20260930_outs",
        title="交付语义",
        created_at="2026-09-30T00:00:00Z",
        updated_at="2026-09-30T00:00:00Z",
    )
    return TaskRun(
        task,
        "调研一下",
        store=store,
        bus=EventBus(),
        provider=_Scripted(turns),
        executor=_StubExecutor(),  # type: ignore[arg-type]
        approval=ApprovalManager(),
        tools=[],
        max_iterations=5,
        timeout_seconds=5.0,
        approval_required=[],
        on_finish=lambda r: None,
    )


def _run(tmp_path, turns: list[AssistantTurn]) -> TaskRun:
    run = _mk_run(tmp_path, turns)
    asyncio.run(run._run())
    return run


def _states(run: TaskRun) -> list[str]:
    return [str(e.payload.get("state")) for e in run.store.read_events(run.task.id) if e.type == "status"]


def _delivered(run: TaskRun) -> dict:
    msgs = [
        e for e in run.store.read_events(run.task.id)
        if e.type == "message" and e.payload.get("role") == "assistant"
    ]
    return dict(msgs[-1].payload) if msgs else {}


# ---------------- P0-5 交付结果语义 ----------------


def test_default_outcome_is_success(tmp_path):
    run = _run(tmp_path, [_done(message="做完了")])
    assert run.task.status == "done"
    assert "idle" in _states(run)


def test_outcome_failed_marks_task_failed(tmp_path):
    """视频生成那种"如实汇报没做成"的任务，不能再显示绿点。"""
    run = _run(tmp_path, [_done(message="没能生成视频，原因是……", outcome="failed")])
    assert run.task.status == "failed"
    assert "failed" in _states(run)
    assert "idle" not in _states(run)


def test_outcome_partial_marks_task_partial(tmp_path):
    run = _run(tmp_path, [_done(message="只做了一半", outcome="partial")])
    assert run.task.status == "partial"
    assert "partial" in _states(run)


def test_invalid_outcome_is_never_treated_as_success(tmp_path):
    run = _run(tmp_path, [_done(message="？", outcome="totally-fine")])
    assert run.task.status == "partial", "非法 outcome 不能被当成成功"


# ---------------- P0-6 来源核实 ----------------


def test_sources_are_filtered_by_actual_evidence(tmp_path):
    run = _mk_run(tmp_path, [])
    run.web_calls = 2
    run.web_evidence = {_normalize_url(REAL_URL)}
    kept, dropped = run._verify_sources(
        [{"title": "真", "url": REAL_URL}, {"title": "假", "url": FAKE_URL}]
    )
    assert [s["url"] for s in kept] == [REAL_URL]
    assert dropped == [FAKE_URL]


def test_fabricated_source_is_dropped_end_to_end(tmp_path):
    """先联网（桩），再交付：编造的来源必须被丢掉，真实的留下。"""
    run = _run(
        tmp_path,
        [
            AssistantTurn(tool_call=ToolCall(name="web_fetch", arguments={"url": REAL_URL})),
            _done(
                message="调研完成",
                outcome="success",
                sources=[{"title": "真", "url": REAL_URL}, {"title": "假", "url": FAKE_URL}],
            ),
        ],
    )
    payload = _delivered(run)
    assert [s["url"] for s in payload.get("sources", [])] == [REAL_URL]
    obs = [str(e.payload.get("result", "")) for e in run.store.read_events(run.task.id) if e.type == "observation"]
    assert any("无法核实" in o for o in obs), "被丢弃的来源要写进观察结果，模型才知道"


def test_verified_sources_emit_knowledge_event(tmp_path):
    run = _run(
        tmp_path,
        [
            AssistantTurn(tool_call=ToolCall(name="web_fetch", arguments={"url": REAL_URL})),
            _done(message="报告", sources=[{"title": "真", "url": REAL_URL}]),
        ],
    )
    knows = [
        str(e.payload.get("title", ""))
        for e in run.store.read_events(run.task.id)
        if e.type == "knowledge"
    ]
    assert any("来源清单" in k for k in knows), f"应发出来源清单事件，实际：{knows}"


def test_researched_but_uncited_is_downgraded_to_partial(tmp_path):
    """P0-6 的核心防线：联网检索过却一条来源都不给 → 不许算成功。"""
    run = _run(
        tmp_path,
        [
            AssistantTurn(tool_call=ToolCall(name="web_fetch", arguments={"url": REAL_URL})),
            _done(message="我调研完了", outcome="success"),
        ],
    )
    assert run.task.status == "partial"
    obs = [str(e.payload.get("result", "")) for e in run.store.read_events(run.task.id) if e.type == "observation"]
    assert any("未提供 sources" in o for o in obs)


def test_no_web_activity_is_not_penalized(tmp_path):
    """没联网的普通任务（写文件、闲聊）不该被这条规则误伤。"""
    run = _run(tmp_path, [_done(message="写好了")])
    assert run.task.status == "done"
