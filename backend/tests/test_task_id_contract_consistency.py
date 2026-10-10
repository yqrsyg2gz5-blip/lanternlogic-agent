"""C7 回归（★ 上一班留下的 P0）：**新任务 id 必须真的能跑起来**。

事故（本班实测，2026-10-04 18:57）：
  在真机上建任务 → `POST /api/v1/tasks` 返回 **201 Created**，但任务 4 毫秒后变
  `failed`、`events.jsonl` **根本不存在**（0 事件）。用户看到的就是"新任务全废"。
  根因：C7（`880f887`）把 `_new_task_id()` 的随机位从 4 位 hex 提到 8 位 hex 防碰撞，
  **但契约校验没跟着放开**：
      schemas.py      `_TASK_ID_RE = ^task_\\d{8}_[a-z0-9]{4}$`      ← 只认 4 位
      contracts/events.schema.json  pattern 同上
  ⇒ `TaskRun._run()` 的第一句 `emit("message", …)` 构造 `EventEnvelope` 就
    ValidationError ⇒ `set_status("failed")` ⇒ 错误事件同样构造失败 ⇒ **一个事件都写不出来**。
  （`TaskSummary` 没有这条校验 ⇒ 建任务照样 201，"建成功但立刻死"。）

★ 为什么 976→985 个用例全绿也没发现（**假绿**的典型）：
  全仓测试的 task_id **全是手写的 4 位形态**（`task_20261003_ab12` 之类），
  `_new_task_id()` 的真实产物从没被喂给 `EventEnvelope`。
  C7 自己的测试只断言"id 长这样"，**没有断言"这样的 id 能用"**。

本文件把缺的那一环钉住（4 条）：
  ① `_new_task_id()` 的产物必须被 `EventEnvelope` 接受（现状回归）
  ② 旧 4 位形态必须照样接受（盘上 217 个老任务靠它；③④ 是"下限 4"的另一半）
  ③ 契约 JSON（`contracts/events.schema.json`）的 pattern 必须接受**两种**形态
     —— 直接读文件里的 pattern 跑正则，不引 jsonschema 依赖
  ④ ★ 端到端：用**真实 `_new_task_id()`** 建一条任务跑完，必须产出事件且不 failed
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

from app import main as m
from app.bus import EventBus
from app.loop import TaskRun
from app.providers.base import AssistantTurn, ModelProvider
from app.schemas import EventEnvelope, TaskSummary
from app.store import FsStore

ROOT = Path(__file__).resolve().parents[2]


def test_new_task_id_accepted_by_event_envelope(monkeypatch):
    """① 现行 `_new_task_id()` 的产物必须能构造事件信封（**这就是那次 P0**）。"""
    monkeypatch.setattr(m, "tasks", {})
    monkeypatch.setattr(m, "store", SimpleNamespace(tasks_dir=Path("__no_such_dir__")))
    tid = m._new_task_id()
    assert re.fullmatch(r"task_\d{8}_[a-z0-9]{8}", tid), f"现行 id 形态变了：{tid}"
    EventEnvelope(id="evt_000001", seq=1, task_id=tid, type="message", version=1,
                  ts="2026-10-04T00:00:00Z", payload={"role": "user", "text": "x"})


def test_legacy_4hex_task_id_still_accepted():
    """② 盘上老任务（4 位 hex）必须继续可用——校验只能放开下限，不能改成等长。"""
    EventEnvelope(id="evt_000001", seq=1, task_id="task_20261003_ab12", type="message",
                  version=1, ts="2026-10-04T00:00:00Z", payload={"role": "user", "text": "x"})


def test_contract_schema_pattern_accepts_both_forms():
    """③ 契约 JSON 与 schemas.py 必须同步（文件头写着"契约变动 → 只改本文件 + schema"）。"""
    schema = json.loads((ROOT / "contracts" / "events.schema.json").read_text("utf-8"))
    pat = re.compile(schema["properties"]["task_id"]["pattern"])
    assert pat.match("task_20261003_ab12"), "契约 pattern 拒了 4 位形态（老任务会读不出来）"
    assert pat.match("task_20261004_c197dcdb"), "契约 pattern 拒了 8 位形态（新任务全废）"
    assert not pat.match("task_20261004_"), "空随机位不该通过"
    assert not pat.match("task_20261004_AB12"), "大写不该通过（历史口径是小写）"


class _TextProvider(ModelProvider):
    name = "text-only"

    async def next_turn(self, task_input, history, tools, on_delta=None):  # type: ignore[override]
        return AssistantTurn(text="完成")


class _StubExecutor:
    """够用的执行器桩：只要有 resolve_in_workspace / run_tool / sandbox 即可。"""

    sandbox = "off"
    sandbox_network = "none"

    def resolve_in_workspace(self, workdir, rel):  # noqa: ANN001
        return Path(workdir) / rel

    async def run_tool(self, tool, args, workdir):  # noqa: ANN001
        from app.executors.base import ExecResult
        return ExecResult(True, "stub", 0)


def test_new_id_task_runs_end_to_end(tmp_path, monkeypatch):
    """④ ★ 端到端：真 `_new_task_id()` → 真 TaskRun → 必须写出事件、不许 failed。

    这条如果不放过，C7 那种"id 形态变了、契约没跟"的改动就会当场红。
    """
    monkeypatch.setattr(m, "tasks", {})
    monkeypatch.setattr(m, "store", SimpleNamespace(tasks_dir=tmp_path / "__probe__"))
    tid = m._new_task_id()

    store = FsStore(tmp_path / "data")
    task = TaskSummary(id=tid, title="C7 回归", created_at="2026-10-04T00:00:00Z",
                       updated_at="2026-10-04T00:00:00Z")
    run = TaskRun(
        task, "跑一下", store=store, bus=EventBus(), provider=_TextProvider(),
        executor=_StubExecutor(),  # type: ignore[arg-type]
        approval=SimpleNamespace(check=lambda *a, **k: None, remember=lambda *a: None,
                                 wait_decision=None, forget_task=lambda *a: None),
        tools=[], max_iterations=2, timeout_seconds=5.0, approval_required=[],
        on_finish=lambda r: None,
    )
    asyncio.run(run._run())

    events = store.read_events(tid)
    assert events, "任务 0 事件 —— 就是那次 P0 的形态（id 与契约不一致）"
    assert events[0].type == "message", f"首条事件应为用户消息：{events[0].type}"
    assert task.status != "failed", f"任务不该 failed：{task.status}"
    assert (tmp_path / "data").rglob("events.jsonl"), "events.jsonl 没落盘"
