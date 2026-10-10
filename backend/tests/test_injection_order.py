"""运行中插话与上下文压缩的护栏 —— 审计 §3.1 / §3.2。

§3.1（26% 异常收尾的主因）：工具执行期（assistant(tool_calls) 已入历史、tool 回执
  未入）直插 user/system 消息，拼出 [assistant(tool_calls), user, tool] 非法序列，
  严格上游接口直接 400 → 任务 failed。修复：插话进队列，在"回执已入账"的
  安全点（每轮迭代开头）统一入历史；任务收尾时兜底冲刷。

§3.2：压缩保护集此前只保护"第一条 user 消息"，续聊形态下**当前任务书**
  （最后一条非摘要 user）会被裁掉；3 条 system 注入只剩 1 条。
  修复：保护集加入当前任务书与所有 role=="system"。

审计原文（17-复评v4）：`grep inject_user backend/tests` = 0 命中；压缩的
resume 形态恰好测不到 —— 本文件补上这两块零覆盖。
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


class _RecordingScripted(ModelProvider):
    """按脚本返回 turn，同时**记录每次调用收到的 history 快照**（协议断言用）。"""

    name = "recording-scripted"

    def __init__(self, turns: list[AssistantTurn]) -> None:
        self._turns = turns
        self.n = 0
        self.received: list[list[dict]] = []

    async def next_turn(self, task_input, history, tools, on_delta=None):  # type: ignore[override]
        self.received.append([dict(m) for m in history])
        i = min(self.n, len(self._turns) - 1)
        self.n += 1
        return self._turns[i]


class _InjectingExecutor:
    """工具执行期间模拟"用户插话"——正是审计 §3.1 的触发窗口。"""

    def __init__(self, holder: dict) -> None:
        self._holder = holder

    def resolve_in_workspace(self, workdir: Path, rel: str) -> Path:
        return Path(workdir) / rel

    async def run_tool(self, tool: str, args: dict, workdir: Path) -> ExecResult:
        run = self._holder.get("run")
        if run is not None and not self._holder.get("injected"):
            self._holder["injected"] = True
            run.inject_user("用户插话：顺便把结果也导出成 CSV")
        return ExecResult(True, "ok output", 1)


def _protocol_violations(history: list[dict]) -> list[tuple]:
    """校验 assistant(tool_calls) 后必须紧跟同 id 的 tool 回执（OpenAI 配对铁律）。"""
    bad: list[tuple] = []
    for i, m in enumerate(history):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            ids = [tc["id"] for tc in m["tool_calls"]]
            follow = history[i + 1 : i + 1 + len(ids)]
            got = [x.get("tool_call_id") for x in follow]
            if got != ids or any(x.get("role") != "tool" for x in follow):
                bad.append((i, ids, got))
    return bad


def _mk_run(tmp_path: Path, turns: list[AssistantTurn], executor) -> TaskRun:
    return TaskRun(
        TaskSummary(id="task_20261002_inj1", title="插话", created_at="2026-10-02T00:00:00Z",
                    updated_at="2026-10-02T00:00:00Z"),
        "调研一下",
        store=FsStore(tmp_path / "data"),
        bus=EventBus(),
        provider=_RecordingScripted(turns),
        executor=executor,  # type: ignore[arg-type]
        approval=ApprovalManager(),
        tools=[],
        max_iterations=5,
        timeout_seconds=5.0,
        approval_required=[],
        on_finish=lambda r: None,
    )


def _tool(name: str = "shell_exec") -> AssistantTurn:
    return AssistantTurn(tool_call=ToolCall(name=name, arguments={"command": "echo hi"}))


# ---------------- §3.1 插话排队 ----------------


def test_inject_during_tool_execution_keeps_protocol(tmp_path):
    """★ §3.1 回归：工具执行期插话 → 模型收到的每一帧 history 配对必须合法。"""
    holder: dict = {}
    ex = _InjectingExecutor(holder)
    run = _mk_run(tmp_path, [_tool(), _tool(), _done_turn()], ex)
    holder["run"] = run
    asyncio.run(run._run())

    provider = run.provider  # type: ignore[attr-defined]
    assert holder.get("injected"), "测试前提：插话必须真的发生在工具执行期"
    assert provider.received, "测试前提：模型至少被调用一次"
    for k, snap in enumerate(provider.received):
        assert _protocol_violations(snap) == [], f"第 {k+1} 次调用收到的 history 配对非法：{_protocol_violations(snap)}"


def _done_turn() -> AssistantTurn:
    return AssistantTurn(tool_call=ToolCall(name="task_done", arguments={"message": "完成"}))


def test_inject_user_queues_instead_of_touching_history(tmp_path):
    """悬空 tool_calls 状态下 inject_user 不得直写 history。"""
    holder: dict = {}
    run = _mk_run(tmp_path, [_tool()], _InjectingExecutor(holder))
    run.history.append({
        "role": "assistant", "content": "",
        "tool_calls": [{"id": "call_001", "type": "function",
                        "function": {"name": "shell_exec", "arguments": "{}"}}],
    })
    run.inject_user("插话")
    assert run.history[-1]["role"] == "assistant", "插话直写进了历史（§3.1 复发）"
    assert run._pending_injections == [{"role": "user", "content": "插话"}]


def test_injection_flushed_after_receipt_and_visible_to_model(tmp_path):
    """安全点冲刷：插话出现在 tool 回执**之后**，且模型下一轮真的看到它。"""
    holder: dict = {}
    ex = _InjectingExecutor(holder)
    run = _mk_run(tmp_path, [_tool(), _done_turn()], ex)
    holder["run"] = run
    asyncio.run(run._run())

    final = run.history
    assert any(m.get("role") == "user" and "导出成 CSV" in str(m.get("content")) for m in final), \
        "插话消息在收尾冲刷后仍不在历史里"
    # 位置断言：插话必须在它前面那对 assistant(tool_calls)/tool 之后
    idx_inj = next(i for i, m in enumerate(final)
                   if m.get("role") == "user" and "导出成 CSV" in str(m.get("content")))
    assert idx_inj >= 2 and final[idx_inj - 1].get("role") == "tool", \
        f"插话落在回执之前（位置 {idx_inj}，前一条 {final[idx_inj-1].get('role')}）"
    assert _protocol_violations(final) == []


def test_inject_system_also_queued(tmp_path):
    """身份切换的 system 注入走同一队列（send_message 身份路径 §3.1 同根因）。"""
    holder: dict = {}
    run = _mk_run(tmp_path, [_tool()], _InjectingExecutor(holder))
    run.history.append({
        "role": "assistant", "content": "",
        "tool_calls": [{"id": "call_001", "type": "function",
                        "function": {"name": "shell_exec", "arguments": "{}"}}],
    })
    run.inject_system("[身份切换] 你现在以「测试员」的身份执行。")
    assert run.history[-1]["role"] == "assistant", "system 注入直写进了历史"
    assert run._pending_injections[0]["role"] == "system"


# ---------------- §3.2 压缩保护（续聊形态） ----------------

SYS_PROJECT = {"role": "system", "content": "[项目指令]\n始终使用中文回复。"}
SYS_SKILLS = {"role": "system", "content": "[可用技能]\n- 桌面整理：整理、归类文件"}
SYS_MEMORY = {"role": "system", "content": "[长期记忆]\n用户偏好 Markdown 交付。"}
OLD_TASK = "旧任务书：把 C:\\Users\\y\\Downloads 里的图片按类型分类整理"
CUR_TASK = "当前任务书：基于上面整理结果写一份竞品分析报告"


def _resume_form_history(run) -> None:
    """审计 §3.2 的实测形态：旧任务书 + 40 轮工具往返 + 当前任务书 + 10 轮。"""
    run.history = [dict(SYS_PROJECT), dict(SYS_SKILLS), dict(SYS_MEMORY), {"role": "user", "content": OLD_TASK}]
    for i in range(40):
        run.history.append({
            "role": "assistant", "content": "",
            "tool_calls": [{"id": f"call_{i:03d}", "type": "function",
                            "function": {"name": "shell_exec", "arguments": "{}"}}],
        })
        run.history.append({"role": "tool", "tool_call_id": f"call_{i:03d}", "content": "x" * 600})
    run.history.append({"role": "user", "content": CUR_TASK})
    for i in range(40, 50):
        run.history.append({
            "role": "assistant", "content": "",
            "tool_calls": [{"id": f"call_{i:03d}", "type": "function",
                            "function": {"name": "shell_exec", "arguments": "{}"}}],
        })
        run.history.append({"role": "tool", "tool_call_id": f"call_{i:03d}", "content": "y" * 600})


def test_compaction_resume_form_keeps_current_task_and_systems(tmp_path):
    """★ §3.2 回归：续聊形态压缩后，当前任务书 + 3 条 system 全部保留。"""
    run = _mk_run(tmp_path, [], _InjectingExecutor({}))
    run.max_context_tokens = 3000  # 小预算，逼它真的裁剪
    _resume_form_history(run)
    before = len(run.history)

    asyncio.run(run._compact_history())

    assert len(run.history) < before, "测试前提：历史必须真的被裁掉了"
    assert _protocol_violations(run.history) == [], "压缩后配对被破坏"
    assert any(m.get("role") == "user" and CUR_TASK in str(m.get("content")) for m in run.history), \
        "当前任务书被压缩删掉了（§3.2 复发）"
    assert any(m.get("role") == "user" and OLD_TASK in str(m.get("content")) for m in run.history), \
        "首条任务书被压缩删掉了（P1-7 复发）"
    for s in (SYS_PROJECT, SYS_SKILLS, SYS_MEMORY):
        assert s in run.history, f"system 注入被压缩删掉了：{s['content'][:12]}…（§3.2 复发）"
