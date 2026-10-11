# -*- coding: utf-8 -*-
"""★ 2026-10-11「平台闸」修复 ②：**步数用尽 ≠ 任务失败** —— 长任务要能自己跑到交付。

## 现场（真实任务）

一个"照技能文档做 18 秒视频"的活，跑到**步数上限**就被判 `failed` ✗；
用户手动发了一条"继续"才续上 ✓ —— 也就是说**交付本来做得到，是闸把它拦在半路** ✗✓。

而"继续"这条路**当时只有用户知道**（失败提示里一个字都没提 ✗）
⇒ 一个无人值守的夜里，任务就那么停在半路 ✓ 谁也不知道还能续 ✓。

## 这一组钉四件事（题面四条要求，一条不少）

1. **可配置** ✓ —— 续几轮 / 每轮加多少步 / 总上限，都在 `config.ModelCfg` 里
2. **有兜底上限** ✗ —— `hard_iteration_cap`（默认 400）是总天花板，**不许无限烧钱**
3. **不许静默重试** ✗ —— 每续一轮都写一条审计账（kind = `autocontinue`）+ 事件流可见
4. **失败提示要写明"可以发「继续」"** ✓ —— 那正是这次真实现场里唯一的出路

★ 关掉它（`auto_continue_rounds=0`）时，行为**必须与老代码逐字一致** ✓
  （步数用尽 ⇒ `error(code=max_iterations)` ⇒ `failed` ✓ 本仓大量测试依赖这条 ✓）。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.approval import ApprovalManager          # noqa: E402
from app.bus import EventBus                      # noqa: E402
from app.config import AppConfig, ModelCfg        # noqa: E402
from app.executors.base import ExecResult         # noqa: E402
from app.loop import TaskRun                      # noqa: E402
from app.providers.base import AssistantTurn, ModelProvider  # noqa: E402
from app.schemas import TaskSummary               # noqa: E402
from app.store import FsStore                     # noqa: E402


class _ForeverToolProvider(ModelProvider):
    """永远调同一个工具、**永不交付** —— 复刻"活干不完、步数烧光"的现场 ✓。"""

    name = "forever-tool"

    def __init__(self) -> None:
        self.calls = 0

    async def next_turn(self, task_input, history, tools, on_delta=None):  # type: ignore[override]
        from app.providers.base import ToolCall

        self.calls += 1
        return AssistantTurn(text="再查一次", tool_call=ToolCall(name="noop_tool", arguments={}))


class _NoopExecutor:
    """最小执行器：任何工具都返回一句结果（不进 shell、不碰盘 ✓）。"""

    sandbox = "off"
    sandbox_network = "none"

    def __init__(self, ws) -> None:
        self.ws = ws

    def resolve_in_workspace(self, workdir, rel):        # noqa: ANN001
        return pathlib.Path(workdir) / str(rel)

    async def run_tool(self, name, args, workdir):       # noqa: ANN001
        return ExecResult(True, "ok", 0)


def _mk_run(tmp_path, *, max_iterations=3, **kw) -> TaskRun:
    store = FsStore(tmp_path / "data")
    # ★ task_id 有格式契约（`task_<日期>_<6 位>` ✓ 见 EventEnvelope 校验 ✓）：
    #   我第一版随手写了 `task_20261011_gate02` ✗ 校验当场把我拦下 ✓（好在这条契约很硬 ✓）
    task = TaskSummary(id="task_20261011_gate02", title="长任务",
                       created_at="2026-10-11T00:00:00Z",
                       updated_at="2026-10-11T00:00:00Z")
    run = TaskRun(
        task, "做一个 18 秒视频",
        store=store, bus=EventBus(), provider=_ForeverToolProvider(),
        executor=_NoopExecutor(tmp_path / "ws"), approval=ApprovalManager(),
        tools=[{"type": "function", "function": {"name": "noop_tool", "parameters": {}}}],
        max_iterations=max_iterations, timeout_seconds=30.0,
        approval_required=[], on_finish=lambda r: None, **kw,
    )
    return run


def _errors(run: TaskRun) -> list[dict]:
    return [e.payload for e in run.store.read_events(run.task.id) if e.type == "error"]


def _statuses(run: TaskRun) -> list[dict]:
    return [e.payload for e in run.store.read_events(run.task.id) if e.type == "status"]


# ═══ ① 可配置 + 有兜底上限 ═══

def test_config_defaults_make_long_tasks_survivable():
    """★ 默认值本身就是"跑不完接着跑"✓（用户不必先去改配置 ✓）。"""
    m = ModelCfg()
    assert m.auto_continue is True, "默认应当是开的 ⇒ 否则默认行为还是'跑到一半就死' ✗"
    assert m.auto_continue_rounds >= 1, m.auto_continue_rounds
    assert m.auto_continue_extra == 0, "0 = 每轮加'跟原来一样多'（见 loop._next_extra）"
    assert m.hard_iteration_cap >= 2 * m.max_iterations, "硬上限得比一次预算宽，否则等于没开 ✗"


def test_auto_continue_settings_can_be_turned_off():
    """★ 可配置的反面：**能关** ✓（有人的场景就是要一步都不许多跑 ✓）。"""
    m = ModelCfg(auto_continue=False)
    assert m.auto_continue is False


def test_config_section_is_add_only_and_backward_compatible():
    """★ 老配置文件（没有这几个键）照常加载 ✓ —— add-only 是本仓一贯的规矩 ✓。"""
    cfg = AppConfig(version=1)
    assert cfg.model.auto_continue in (True, False)
    assert "auto_continue" in AppConfig.model_fields["model"].annotation.model_fields


# ═══ ② 续跑真的发生了（行为）═══

def test_step_budget_exhaustion_continues_instead_of_failing(tmp_path):
    """★★ 核心：步数用尽 ⇒ **自动接着跑**（同一个任务、同一份历史）✓ 而不是当场判 failed ✗。

    ★ 我第一版在这里多断言了一句"续跑之后不该再出现 max_iterations"✗ —— **那是我错了** ✓：
      这个用例配的是"永远不交付的模型 + 2 轮续跑"✓ ⇒ 两轮用完**本来就该**如实失败 ✓
      （而"续跑完之后仍然失败"正是这个用例要证明的**第二半** ✓）。
      断言改成"续跑真发生过 + 接的位置对" ✓ —— 那才是要钉的东西 ✓。
    """
    run = _mk_run(tmp_path, max_iterations=3, auto_continue_rounds=2,
                  auto_continue_extra=0, hard_iteration_cap=100)
    asyncio.run(run._run())
    assert run.provider.calls > 3, (
        f"模型只被调了 {run.provider.calls} 次 ⇒ 没有续跑 ✗（3 步就死了 ✓ 正是本次要治的病 ✓）"
    )
    assert run.max_iterations > 3, f"步数上限没有放宽：{run.max_iterations}"
    assert run._continued_rounds == 2, f"应当续满 2 轮：{run._continued_rounds}"
    assert run.provider.calls == 9, (
        f"每轮该跑满它自己的预算（3+3+3=9 步）✓ 实际 {run.provider.calls} 步 ✗"
        "（少了说明续跑接错了位置、把步数重复数了一遍 ✗）"
    )
    assert not any(e.get("code") == "loop_failed" for e in _errors(run)), \
        f"续跑过程本身出错了：{_errors(run)}"


def test_continuation_is_recorded_in_the_audit_ledger(tmp_path, monkeypatch):
    """★★ **不许静默重试** ✗ —— 每续一轮都要在审计账里留一条 ✓（可查"它自己跑了几轮"✓）。"""
    from app import audit

    rows: list[dict] = []
    monkeypatch.setattr(audit, "record", lambda kind, **f: rows.append({"kind": kind, **f}))
    run = _mk_run(tmp_path, max_iterations=2, auto_continue_rounds=3,
                  auto_continue_extra=0, hard_iteration_cap=100)
    asyncio.run(run._run())
    auto = [r for r in rows if r["kind"] == "autocontinue"]
    assert auto, "续跑没有留审计账 ⇒ 静默重试 ✗（用户事后查不到「为什么又跑了一轮」✗）"
    assert all(r.get("task") == "task_20261011_gate02" for r in auto), auto
    assert all("extra_steps" in r and "total_steps" in r for r in auto), auto
    assert len(auto) == run._continued_rounds, (len(auto), run._continued_rounds)


def test_continuation_is_visible_in_the_event_stream(tmp_path):
    """★ 用户**当场看得见** ✓ —— 事件流里要有一条 status 说明"它自己又跑了一轮"✓。"""
    run = _mk_run(tmp_path, max_iterations=2, auto_continue_rounds=2,
                  auto_continue_extra=0, hard_iteration_cap=100)
    asyncio.run(run._run())
    details = " ".join(str(s.get("detail") or "") for s in _statuses(run))
    assert "自动续" in details or "续第" in details, f"事件流里看不到续跑：{details[:300]}"


def test_hard_cap_stops_the_continuation(tmp_path):
    """★★ **有兜底上限** ✗ —— 到顶就得停（不能无限烧钱 ✓）。"""
    run = _mk_run(tmp_path, max_iterations=2, auto_continue_rounds=10,
                  auto_continue_extra=0, hard_iteration_cap=4)
    asyncio.run(run._run())
    assert run.max_iterations <= 4, f"越过了硬上限：{run.max_iterations} > 4 ✗"
    assert run._continued_rounds <= 1, f"硬上限没能拦住续跑：{run._continued_rounds} 轮 ✗"
    assert any(e.get("code") == "max_iterations" for e in _errors(run)), \
        "到顶之后要如实报 max_iterations ✗"


def test_rounds_limit_is_respected(tmp_path):
    """★ 轮次上限也要真生效 ✓（配 1 轮就不许跑 2 轮 ✓）。"""
    run = _mk_run(tmp_path, max_iterations=2, auto_continue_rounds=1,
                  auto_continue_extra=0, hard_iteration_cap=1000)
    asyncio.run(run._run())
    assert run._continued_rounds == 1, f"应恰好续 1 轮，实际 {run._continued_rounds} ✗"
    assert run.max_iterations == 4, run.max_iterations       # 2 + 2（每轮加"原来那么多"）


def test_disabled_means_exactly_the_old_behaviour(tmp_path):
    """★★ 关掉时**必须与老代码逐字一致** ✓ —— 本仓大量测试依赖"3 步跑完就 failed"✓。"""
    run = _mk_run(tmp_path, max_iterations=3, auto_continue_rounds=0)
    asyncio.run(run._run())
    assert run.provider.calls == 3, f"关掉之后多跑了：{run.provider.calls} 次 ✗"
    errs = _errors(run)
    assert errs and errs[-1].get("code") == "max_iterations", errs
    assert run.task.status == "failed", run.task.status


# ═══ ③ 失败提示要写明出路（"可以发「继续」"）═══

def test_the_failure_message_tells_the_user_they_can_say_continue(tmp_path):
    """★★ 现场唯一的出路就是"发一条继续"✓ —— 那就必须**明写在失败提示里** ✓。

    （当时的提示是一句干巴巴的"达到最大迭代次数（60）"✗
      ⇒ 用户只能自己摸索 ✓ 而无人值守的夜里根本没人摸 ✓）
    """
    run = _mk_run(tmp_path, max_iterations=2, auto_continue_rounds=0)
    asyncio.run(run._run())
    msg = str(_errors(run)[-1].get("message") or "")
    assert "继续" in msg, f"失败提示没告诉用户「可以发继续」✗：{msg}"
    assert "最大迭代次数" in msg, "原有的那句要保留（群里/测试都在认它 ✓）"


def test_group_failure_reason_also_mentions_continue(monkeypatch):
    """★ 群里那句失败原因同样要提"继续"✓（用户最先看到的就是它 ✓）。"""
    from app import main as m

    monkeypatch.setattr(m.store, "read_events", lambda tid: [
        SimpleNamespace(type="error", payload={
            "code": "max_iterations",
            "message": "达到最大迭代次数（60），任务中止。你可以：① 直接发「继续」接着跑……"}),
    ])
    why = m._task_failure_reason("t_continue")
    assert "继续" in why or "接着跑" in why, f"群里那句没给出路 ✗：{why}"
    assert "拆小" in why, f"原有的第二条出路要保留 ✗：{why}"


# ═══ ④ 不能"续跑续成假成功" ═══

def test_continuation_does_not_fake_a_delivery(tmp_path):
    """★ 续跑只是**多给步数** ✓ 不是"续到成功为止"✗ —— 真做不完就如实 failed ✓。"""
    run = _mk_run(tmp_path, max_iterations=2, auto_continue_rounds=1,
                  auto_continue_extra=0, hard_iteration_cap=100)
    asyncio.run(run._run())
    assert run.task.status == "failed", (
        f"干不完却报成了 {run.task.status} ✗ —— 续跑不许把失败粉饰成交付 ✗"
    )


def test_the_run_still_ends_with_a_normal_delivery(tmp_path):
    """★ 反面：模型**正常交付**时，续跑机制一点都不该掺和进来 ✓（不能把完成当没完成 ✗）。"""

    class _DoneProvider(ModelProvider):
        name = "done"

        async def next_turn(self, task_input, history, tools, on_delta=None):  # type: ignore[override]
            from app.providers.base import ToolCall

            return AssistantTurn(text="做完了", tool_call=ToolCall(
                name="task_done", arguments={"message": "视频已生成", "outcome": "success"}))

    run = _mk_run(tmp_path, max_iterations=5, auto_continue_rounds=3,
                  auto_continue_extra=0, hard_iteration_cap=100)
    run.provider = _DoneProvider()
    asyncio.run(run._run())
    assert run._continued_rounds == 0, "正常交付却触发了续跑 ✗"
    assert run.task.status == "done", run.task.status
    assert not _errors(run), _errors(run)


@pytest.mark.parametrize("rounds,extra,cap", [(0, 0, 0), (1, 5, 50), (3, 100, 400)])
def test_knobs_are_plain_ints_no_magic(rounds, extra, cap):
    """★ 三个旋钮都是**普通整数**（可配置 ✓ 也能一眼看懂 ✓）。"""
    cfg = ModelCfg(auto_continue_rounds=rounds, auto_continue_extra=extra,
                   hard_iteration_cap=cap)
    assert (cfg.auto_continue_rounds, cfg.auto_continue_extra, cfg.hard_iteration_cap) == \
        (rounds, extra, cap)
