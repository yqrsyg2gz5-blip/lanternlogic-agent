# -*- coding: utf-8 -*-
"""★ 第 1 项「统计口径三条」查出并修掉的两个真毛病 —— 2026-10-07 真数据量出来的。

## 毛病 A：同一个"花了多少"，**有两本账、口径不一样** ✗

| 哪里 | 原来怎么算 | 对不对 |
|---|---|---|
| 「使用统计」页（`/usage`） | 把该任务**所有**用量记录**加起来** | ✓ 对 |
| 群里那行账单 / 整批账单 | **只取最后一条** ✗ | ✗ **少报** |

实测（真数据 ✓ 202 个任务）：
```
「介绍一下你自己」：求和 输入 4,676,537 ｜ 取末条 3,031,020 ⇒ 少报 35% ✗
全体：求和 34,649,204 ｜ 取末条 32,477,504 ⇒ 少 217 万（6%）✗
```
⇒ 你在群里看到的数字，和使用统计页**对不上** ✗ —— 本项目最忌讳的"多处口径打架"（第五处 ✓）。

## 毛病 B：被**强杀**的任务，**账会丢** ✗

用量原来只在**跑完那一刻**记一笔 ✗ 而进程被强杀（`restart-backend.ps1` 就是强杀 ✓）⇒
那一笔**永远发不出来** ✗ 实测 **12 个任务**这么死的 ✓ 跑了 **175 次动作**却**没留下账** ✗
（粗估丢约 230 万 tok ✓）—— 而**重启后端是这个项目的日常动作** ✓ 这个洞会一直漏 ✓

修法：**边跑边记**（每调一次模型就落一份快照 ✓）+ **同 run_id 去重**（绝不重复计 ✓）。

本文件钉住这两条，外加一条**最要紧**的：**不能重复计** ✗
（边跑边记 + 跑完记账，两条都在 ⇒ 一不小心就算两遍 ✓ 那比少报还糟 ✓）
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main as m
from app import pricing as P
from app.schemas import TaskSummary
from app.store import FsStore

TID = "task_20261007_abcd"


@pytest.fixture(autouse=True)
def _prices(monkeypatch):
    monkeypatch.setattr(P, "PRICES", {"mimo-v2.6-flash": {"in": 1.0, "out": 2.0, "cached": 0.02}})


_SEQ = [0]


def _ev(usage: dict):
    """★ 假事件要**照真接口写** ✓ —— 第一版我漏了 `seq`，
    结果 lifespan/接口那边 `'SimpleNamespace' object has no attribute 'seq'` 直接炸 ✗
    （本仓的老教训又验了一次：假对象不照真接口写，测出来的绿是假的 ✓）。"""
    _SEQ[0] += 1
    return SimpleNamespace(
        id=f"evt_{_SEQ[0]:06d}", seq=_SEQ[0], task_id=TID, type="knowledge",
        version=1, ts="2026-10-07T03:00:00+00:00",
        payload={"title": "📊 用量（mimo-v2.6-flash）", "content": "", "usage": usage},
    )


def _u(calls: int, tin: int, tout: int, run_id: str = "", cached: int = 0) -> dict:
    d = {"model": "mimo-v2.6-flash", "calls": calls, "input_tokens": tin,
         "output_tokens": tout, "cached_tokens": cached}
    if run_id:
        d["run_id"] = run_id
    return d


# ═══ 毛病 A：口径必须是"求和" ═══

def test_task_usage_sums_every_run(monkeypatch):
    """★★ **多跑过的任务必须求和** ✓ —— 原来只取最后一条 ⇒ 少报（实测最多少 35% ✗）。

    回滚实验：把 `_task_usage` 改回 `for e in reversed(evs)` 取第一条 ⇒ 本组必红 ✓
    """
    monkeypatch.setattr(m.store, "read_events", lambda tid: [       # noqa: ARG005
        _ev(_u(3, 1_000_000, 10_000)),
        _ev(_u(2, 500_000, 5_000)),
        _ev(_u(4, 200_000, 2_000)),
    ])
    monkeypatch.setattr(m.store, "read_usage_inflight", lambda tid: {})   # noqa: ARG005
    u = m._task_usage(TID)
    assert u["input_tokens"] == 1_700_000, f"没求和（{u}）—— 这正是'群里少报'的根因 ✗"
    assert u["output_tokens"] == 17_000 and u["calls"] == 9, u


def test_group_bill_matches_the_stats_page(monkeypatch):
    """★★ **同一件事只能有一个数** ✓ —— 群里那行与使用统计页**必须一致** ✓。

    这条是这次修复的**验收标准**：不是"改成了求和"，而是"**两处一样**"✓。
    """
    evs = [_ev(_u(3, 900_000, 9_000)), _ev(_u(2, 300_000, 3_000))]
    monkeypatch.setattr(m.store, "read_events", lambda tid: evs)         # noqa: ARG005
    monkeypatch.setattr(m.store, "read_usage_inflight", lambda tid: {})   # noqa: ARG005
    line = m._usage_line(TID, "@程序员 这一步")
    assert "1200000" in line, f"群里那行没算全（应 120 万输入）✗：{line}"

    # 使用统计页那边：同一个任务也要是 120 万
    monkeypatch.setattr(m, "tasks", {TID: TaskSummary(
        id=TID, title="对账", created_at="2026-10-07T00:00:00Z", updated_at="2026-10-07T00:00:00Z")})
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        api = c.get("/api/v1/usage").json()
    assert api["input_tokens"] == 1_200_000, f"两处对不上 ✗：{api}"


# ═══ 毛病 B：被强杀也要留下账 ═══

def test_inflight_snapshot_is_counted_when_the_run_never_committed(monkeypatch):
    """★★ "跑到一半被掐死"的那一份**也要算进账** ✓ —— 这正是 12 个任务丢掉的那部分 ✓。"""
    monkeypatch.setattr(m.store, "read_events", lambda tid: [])          # noqa: ARG005
    monkeypatch.setattr(m.store, "read_usage_inflight", lambda tid: {     # noqa: ARG005
        "run_id": "deadrun123", "model": "mimo-v2.6-flash",
        "calls": 7, "input_tokens": 91_000, "output_tokens": 4_000, "cached_tokens": 0,
    })
    u = m._task_usage(TID)
    assert u and u["input_tokens"] == 91_000 and u["calls"] == 7, u
    assert u.get("inflight") is True, "没标出'这里面有一段还在跑'✗"


def test_no_double_count_when_the_run_already_committed(monkeypatch):
    """★★★ **最要紧的一条**：边跑边记 + 跑完记账，**绝不能算两遍** ✗✗

    （少报只是数字小一点 ✓ 多报会让用户以为花了更多钱 ✓ 那更糟 ✓）
    规则：快照带 run_id ✓ 正式事件带同一个 run_id ✓ ⇒ 有正式事件就**忽略**快照 ✓。
    """
    monkeypatch.setattr(m.store, "read_events", lambda tid: [_ev(_u(5, 100_000, 8_000, run_id="r1"))])   # noqa: ARG005
    monkeypatch.setattr(m.store, "read_usage_inflight", lambda tid: {     # noqa: ARG005
        "run_id": "r1", "calls": 5, "input_tokens": 100_000, "output_tokens": 8_000,
    })
    u = m._task_usage(TID)
    assert u["calls"] == 5 and u["input_tokens"] == 100_000, f"**算了两遍** ✗✗（{u}）"
    assert not u.get("inflight"), "已记账的那份还被当成'还在跑'✗"


def test_other_runs_snapshot_still_counts(monkeypatch):
    """★ 快照属于**这一次**运行 ✓ 与**别的**运行的正式事件无关 ⇒ 该算还得算 ✓。"""
    monkeypatch.setattr(m.store, "read_events", lambda tid: [_ev(_u(2, 10_000, 1_000, run_id="old"))])   # noqa: ARG005
    monkeypatch.setattr(m.store, "read_usage_inflight", lambda tid: {     # noqa: ARG005
        "run_id": "new", "calls": 3, "input_tokens": 30_000, "output_tokens": 2_000,
    })
    u = m._task_usage(TID)
    assert u["calls"] == 5 and u["input_tokens"] == 40_000, u


# ═══ 真跑一遍 loop：边跑边记、跑完清掉 ═══

def _mk_run(tmp_path, provider):
    from app.approval import ApprovalManager
    from app.bus import EventBus
    from app.executors.local import LocalExecutor
    from app.loop import TaskRun
    store = FsStore(tmp_path / "data")
    task = TaskSummary(id=TID, title="边跑边记", created_at="2026-10-07T00:00:00Z",
                       updated_at="2026-10-07T00:00:00Z")
    ex_cfg = SimpleNamespace(workspace_root=tmp_path / "ws", timeout_seconds=5.0, shell="",
                             search_url="", browser_channel="", comfyui_url="",
                             image_checkpoint="", allowed_dirs=[])
    return TaskRun(task, "干点活", store=store, bus=EventBus(), provider=provider,
                   executor=LocalExecutor(ex_cfg), approval=ApprovalManager(), tools=[],
                   max_iterations=3, timeout_seconds=5.0, approval_required=[],
                   on_finish=lambda r: None)


class _WatchProvider:
    """第 2 次调用时，去读一眼"快照文件在不在、里面有没有数" ✓（= 证明边跑边记 ✓）。"""

    name = "watch"
    model_name = "mimo-v2.6-flash"
    api_key_env = "XIAOMI_MIMO_API_KEY"

    def __init__(self, store) -> None:
        self.store = store
        self.calls = 0
        self.total_usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0, "estimated": False}
        self.seen_mid_run: dict | None = None
        self.seen_after: dict | None = None

    async def next_turn(self, task_input, history, tools, on_delta=None):  # type: ignore[override]
        from app.providers.base import AssistantTurn, ToolCall
        self.calls += 1
        self.total_usage["calls"] += 1
        self.total_usage["input_tokens"] += 1000
        if self.calls == 1:
            # ★ 第 1 轮**必须调个工具** ✓ —— 直接给纯文本的话，loop 会当场收尾交付、
            #   **根本不会有第 2 轮** ✗ ⇒ 也就看不到"边跑边记" ✓
            #   （第一版就是这么写的 ⇒ 测出来一片空 ✗ 又是探针的错 ✓）
            return AssistantTurn(tool_call=ToolCall(name="list_dir", arguments={"path": "."}))
        # ★ 第 2 轮开始时读一眼快照 ✓ —— 那时第 1 轮已经跑完、快照已经落过 ✓
        self.seen_mid_run = self.store.read_usage_inflight(TID)
        return AssistantTurn(text="干完了")


def test_loop_writes_a_snapshot_while_running_and_clears_it_after(tmp_path):
    """★★ **边跑边记**（不然被强杀就丢账 ✗）+ **跑完清掉**（不然会被当成还在跑 ✗）。

    回滚实验：把 `self._flush_usage_inflight()` 那一行去掉 ⇒ 本组必红 ✓
    """
    prov = _WatchProvider(FsStore(tmp_path / "data"))
    run = _mk_run(tmp_path, prov)
    prov.store = run.store
    asyncio.run(run._run())
    mid = prov.seen_mid_run or {}
    assert mid.get("calls") == 1, f"跑到一半时**没有留下账** ✗（被掐死就丢了 ✓）：{mid}"
    assert mid.get("run_id") == run.run_id, "快照没带 run_id ⇒ 会和正式账重复计 ✗"
    assert run.store.read_usage_inflight(TID) == {}, "跑完了快照还留着 ⇒ 会被当成还在跑 ✗"


def test_committed_event_carries_the_run_id(tmp_path):
    """★ 正式事件必须带**同一个 run_id** ✓ —— 去重全靠它 ✓（不带就必然重复计 ✗）。"""
    prov = _WatchProvider(FsStore(tmp_path / "data"))
    run = _mk_run(tmp_path, prov)
    prov.store = run.store
    asyncio.run(run._run())
    us = [e.payload["usage"] for e in run.store.read_events(TID)
          if e.type == "knowledge" and isinstance((e.payload or {}).get("usage"), dict)]
    assert us and us[-1].get("run_id") == run.run_id, us
