# -*- coding: utf-8 -*-
"""★「每个 Key 花费上限」—— 2026-10-07 用户点名要的那个（他的原话：

> "**每个 Key 花费上限**（现在只有成本**显示** ✗ 没有**上限** ✗）"

## 这道闸要做到的三件事（本文件逐条钉住）

1. **真拦得住** ✓ —— 花到上限就**不再往上发请求** ✗（放在发请求之后 = 没闸 ✓）
2. **账只有一份** ✓ —— 直接用任务里的用量事件（`/usage` 那份 ✓）**不另记一本** ✗
   （这个项目已经在"同一件事两处口径"上栽过四次 ✓ 再记一份必然打架 ✓）
3. **说实话** ✓ —— 上限**只管语言模型**那一档（唯一按 token 算得出钱的 ✓）；
   出图/出视频/语音是**按次或按秒**计费、后端拿不到单价 ⇒ **拦不住** ✓ 必须**写出来** ✓
   （默默不管 = 给用户**假的安心** ✗ —— 那比不做还糟 ✓）

## 还有一条**绝不能**弄错的（第 4 组钉它）

**读不到账/配置坏了 ⇒ 放行** ✓ —— 宁可漏报，也不能因为查账出错
就把用户正在干的任务**无缘无故掐掉** ✗（"宁可失败也不烧钱"只适用于**真超了**的时候 ✓）。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import budget as B
from app import main as m
from app import pricing as P
from app.approval import ApprovalManager
from app.bus import EventBus
from app.executors.local import LocalExecutor
from app.loop import TaskRun
from app.providers.base import AssistantTurn, ModelProvider
from app.schemas import TaskSummary
from app.store import FsStore


@pytest.fixture(autouse=True)
def _prices(monkeypatch):
    """★ 单价表要**显式配**才有钱可算 ✓ —— 这正是本功能的前提（见 settings 里那句
    "先填单价这项才准" ✓）：`pricing.estimate` 只认**用户填的** `PRICES` ✗ 不认内置官方价 ✓
    （官方价只是"一键查询"时用来填表的 ✓ 见 pricing.py 文件头那条规矩 ✓）。

    不配的话所有钱都是 0 ⇒ 闸门永远不拦 ⇒ 测试会变成"绿得毫无意义" ✗（本班第一版就是这样 ✓）。
    """
    monkeypatch.setattr(P, "PRICES", {
        "mimo-v2.6-flash": {"in": 1.0, "out": 2.0, "cached": 0.02},   # 元/百万 token
        "deepseek-flash": {"in": 2.0, "out": 8.0, "cached": 0.04},
    })


class _Ev:
    """一条假的事件信封（照真接口写 ✓：`tests/test_meeting_wav_upload.py` 那条教训 ✓）。"""

    def __init__(self, usage: dict, ts: str = "2026-10-07T03:00:00+00:00") -> None:
        self.type = "knowledge"
        self.payload = {"title": "📊 用量（mimo-v2.6-flash）", "usage": usage}
        self.ts = ts


def _usage(calls: int, tin: int, tout: int, key: str | None = "XIAOMI_MIMO_API_KEY",
           model: str = "mimo-v2.6-flash") -> dict:
    u = {"model": model, "calls": calls, "input_tokens": tin, "output_tokens": tout}
    if key is not None:
        u["key_env"] = key
    return u


def _cfg(caps: dict | None = None, llm_key: str = "XIAOMI_MIMO_API_KEY"):
    """一份最小配置（照真 AppConfig 的形状写 ✓）。"""
    return SimpleNamespace(
        model=SimpleNamespace(api_key_env=llm_key),
        image=SimpleNamespace(api_key_env="DASHSCOPE_API_KEY"),
        asr=SimpleNamespace(api_key_env="XIAOMI_MIMO_API_KEY"),
        video=SimpleNamespace(api_key_env="DASHSCOPE_API_KEY", engines={}),
        kb=SimpleNamespace(api_key_env="DASHSCOPE_API_KEY"),
        budget=SimpleNamespace(enabled=True, caps=caps or {}),
    )


def _reader(events: list[_Ev]):
    return lambda tid: events          # noqa: ARG005


# ═══ ① 记账：按 Key 分、按 since 截、老记录不猜 ═══

def test_spend_is_attributed_by_key():
    """★ 钱要记到**花它的那把 Key** 头上 ✓（所以 loop 落用量事件时必须带 `key_env` ✓）。"""
    evs = [_Ev(_usage(2, 1_000_000, 0, key="XIAOMI_MIMO_API_KEY")),
           _Ev(_usage(1, 0, 1_000_000, key="DEEPSEEK_API_KEY", model="deepseek-flash"))]
    got = B.spend(["t1"], _reader(evs))
    # mimo 输入 1.0 元/百万 ⇒ 1 元；deepseek-flash 输出 8 元/百万 ⇒ 8 元
    assert got["XIAOMI_MIMO_API_KEY"]["cny"] == 1.0, got
    assert got["DEEPSEEK_API_KEY"]["cny"] == 8.0, got
    assert got["XIAOMI_MIMO_API_KEY"]["calls"] == 2


def test_legacy_records_are_not_guessed():
    """★ 老记录（那时还没记 key_env）⇒ 归入「未标注」✓ **绝不猜**它属于哪把 ✗。"""
    got = B.spend(["t1"], _reader([_Ev(_usage(1, 1_000_000, 0, key=None))]))
    assert "XIAOMI_MIMO_API_KEY" not in got, "把没标的记录算到某把 Key 头上了 —— 那是**猜** ✗"
    assert got[B.UNLABELED]["cny"] == 1.0, got


def test_since_filters_old_usage():
    """★ `since` 之前的不计入 ✓ —— "清零重来"就是靠它 ✓（老账不删 ✓ 只是不算进本上限 ✓）。"""
    evs = [_Ev(_usage(1, 1_000_000, 0), ts="2026-10-07T01:00:00+00:00"),
           _Ev(_usage(1, 1_000_000, 0), ts="2026-10-07T05:00:00+00:00")]
    assert B.spend(["t1"], _reader(evs))["XIAOMI_MIMO_API_KEY"]["cny"] == 2.0
    after = B.spend(["t1"], _reader(evs), since="2026-10-07T03:00:00+00:00")
    assert after["XIAOMI_MIMO_API_KEY"]["cny"] == 1.0, after


def test_unpriced_model_is_counted_but_not_priced():
    """★ 没填单价的模型：**只记次数、不记钱** ✓（编一个数字比不报更糟 ✗ 见 pricing.py 文件头 ✓）。"""
    got = B.spend(["t1"], _reader([_Ev(_usage(3, 999, 999, model="某个没填单价的模型"))]))
    row = got["XIAOMI_MIMO_API_KEY"]
    assert row["cny"] == 0.0 and row["unpriced_calls"] == 3, row


def test_bad_caps_are_ignored():
    """★ 脏配置一律忽略 ✓（不能因为一条坏上限就把整台机器拦住 ✗ —— 与 pricing.configure 同规矩 ✓）。"""
    cfg = _cfg({"A": {"limit": "不是数"}, "B": {"limit": -5}, "C": {"limit": 3}, "D": "不是字典"})
    assert set(B.caps(cfg)) == {"C"}, B.caps(cfg)


# ═══ ② 闸门：真超了才拦，且话要能照做 ═══

def test_under_the_cap_is_not_blocked():
    cfg = _cfg({"XIAOMI_MIMO_API_KEY": {"limit": 10.0, "since": ""}})
    assert B.blocking_reason(cfg, ["t1"], _reader([_Ev(_usage(1, 1_000_000, 0))])) is None


def test_over_the_cap_is_blocked_with_actionable_words():
    """★★ 这道闸的**全部意义**：超了就停下 ✓ 而且那句话说得出**接下来怎么办** ✓。"""
    cfg = _cfg({"XIAOMI_MIMO_API_KEY": {"limit": 0.5, "since": ""}})
    why = B.blocking_reason(cfg, ["t1"], _reader([_Ev(_usage(1, 1_000_000, 0))]))   # 已花 1 元 > 0.5
    assert why, "超了却没拦 ✗"
    for must in ("已花", "上限", "提高上限", "清零重来", "换一把 Key"):
        assert must in why, f"拦下来说的话里缺「{must}」✗（用户照着做不了 ✓）：{why[:200]}"
    assert "这一步没有发出去" in why, "没说清「这一步没花钱」✗"


def test_no_cap_means_no_blocking():
    assert B.blocking_reason(_cfg(), ["t1"], _reader([_Ev(_usage(9, 9_000_000, 9_000_000))])) is None


def test_disabled_switch_never_blocks():
    cfg = _cfg({"XIAOMI_MIMO_API_KEY": {"limit": 0.01, "since": ""}})
    cfg.budget.enabled = False
    assert B.blocking_reason(cfg, ["t1"], _reader([_Ev(_usage(1, 1_000_000, 0))])) is None


def test_broken_ledger_never_blocks():
    """★★ **读不到账 ⇒ 放行** ✓ —— 查账出错就把用户的任务掐掉，比多花几毛钱糟得多 ✗。"""
    cfg = _cfg({"XIAOMI_MIMO_API_KEY": {"limit": 0.01, "since": ""}})

    def _boom(tid):        # noqa: ARG001
        raise OSError("账读不出来了")

    assert B.blocking_reason(cfg, ["t1"], _boom) is None, "读账出错竟然拦了 ⇒ 会无故掐掉用户的任务 ✗"


def test_unpriced_spend_never_blocks_by_accident():
    """★ 算不出钱（没填单价）⇒ **不拦** ✓ —— 不能靠猜数字把人拦住 ✗。"""
    cfg = _cfg({"XIAOMI_MIMO_API_KEY": {"limit": 0.01, "since": ""}})
    evs = [_Ev(_usage(50, 9_000_000, 9_000_000, model="没填单价的模型"))]
    assert B.blocking_reason(cfg, ["t1"], _reader(evs)) is None


# ═══ ③ 说实话：上限管不到的那几档必须**写出来** ═══

def test_status_says_which_keys_the_cap_cannot_govern():
    st = B.status(_cfg(), ["t1"], _reader([]))
    rows = {r["key"]: r for r in st["keys"]}
    assert rows["XIAOMI_MIMO_API_KEY"]["measurable"] is True, "语言模型那把必须能管 ✓"
    assert rows["DASHSCOPE_API_KEY"]["measurable"] is False, \
        "出图/出视频那把拿不到单价 ⇒ 必须标成「管不了」✗ 不许假装能管 ✓"
    assert "拦不住" in st["note"], "没说清「哪几档拦不住」✗（默默不管 = 假安心 ✗）"
    assert "语言模型" in st["note"], "没说清上限管的是哪一档 ✗"


def test_key_roles_tell_you_who_uses_each_key():
    """★ 一个 Key 常被好几个能力共用 ✓ 用户得看得出"设了上限会影响谁"✓。"""
    roles = B.key_roles(_cfg())
    assert "语言模型" in roles["XIAOMI_MIMO_API_KEY"]
    assert {"出图", "出视频"} <= set(roles["DASHSCOPE_API_KEY"]), roles


# ═══ ④ 接线：闸门真的接在**发请求之前**（回滚必红的那一条 ✓）═══

class _CountingProvider(ModelProvider):
    name = "counting"

    def __init__(self) -> None:
        self.calls = 0
        self.total_usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0, "estimated": False}
        self.api_key_env = "XIAOMI_MIMO_API_KEY"

    async def next_turn(self, task_input, history, tools, on_delta=None):  # type: ignore[override]
        self.calls += 1
        self.total_usage["calls"] += 1
        return AssistantTurn(text="干完了")


def _mk_run(tmp_path, provider, budget_check) -> TaskRun:
    store = FsStore(tmp_path / "data")
    # 任务 id 有格式契约（`task_<日期>_<4 位小写字母数字>` ✓ 见 schemas 的校验 ✓）
    task = TaskSummary(id="task_20261007_abcd", title="上限测试",
                       created_at="2026-10-07T00:00:00Z", updated_at="2026-10-07T00:00:00Z")
    ex_cfg = SimpleNamespace(workspace_root=tmp_path / "ws", timeout_seconds=5.0, shell="",
                             search_url="", browser_channel="", comfyui_url="",
                             image_checkpoint="", allowed_dirs=[])
    return TaskRun(task, "随便干点啥", store=store, bus=EventBus(), provider=provider,
                   executor=LocalExecutor(ex_cfg), approval=ApprovalManager(), tools=[],
                   max_iterations=3, timeout_seconds=5.0, approval_required=[],
                   on_finish=lambda r: None, budget_check=budget_check)


def test_the_gate_stops_before_any_request_is_sent(tmp_path):
    """★★ **闸门在发请求之前** ✓ —— 回滚实验：把它挪到 `next_turn` 之后 ⇒ 本组必红 ✓。

    "超了还先发一枪再停"等于没闸 ✗（钱已经花出去了 ✓）。
    """
    provider = _CountingProvider()
    run = _mk_run(tmp_path, provider, lambda: "已到上限，停下")
    asyncio.run(run._run())
    assert provider.calls == 0, f"超上限了还往上发了 {provider.calls} 次请求 ✗（钱已经花了 ✓）"
    kinds = [(e.type, (e.payload or {}).get("code")) for e in run.store.read_events("task_20261007_abcd")]
    assert ("error", "budget_cap") in kinds, f"没把「为什么停」说进事件流 ✗：{kinds}"


def test_the_gate_lets_normal_runs_through(tmp_path):
    """★ 没超就照常跑 ✓（别把闸做成"处处都拦" ✗）。"""
    provider = _CountingProvider()
    run = _mk_run(tmp_path, provider, lambda: None)
    asyncio.run(run._run())
    assert provider.calls >= 1, "没超上限却被拦住了 ✗"


def test_usage_event_records_which_key_paid(tmp_path):
    """★ 用量事件必须**带上那把 Key** ✓ —— 否则 `budget` 分不清该记谁的账 ✗。"""
    provider = _CountingProvider()
    run = _mk_run(tmp_path, provider, None)
    asyncio.run(run._run())
    usages = [e.payload["usage"] for e in run.store.read_events("task_20261007_abcd")
              if e.type == "knowledge" and isinstance((e.payload or {}).get("usage"), dict)]
    assert usages, "没落用量事件 ✗"
    assert usages[-1].get("key_env") == "XIAOMI_MIMO_API_KEY", usages[-1]


# ═══ ⑤ 接口：看 / 设 / 撤 / 清零 ═══

def test_endpoints_show_set_and_clear_a_cap(monkeypatch):
    monkeypatch.setattr(m.cfg, "budget", SimpleNamespace(enabled=True, caps={}), raising=False)
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        assert c.get("/api/v1/budget").status_code == 200
        r = c.post("/api/v1/budget", json={"key": "XIAOMI_MIMO_API_KEY", "limit": 20})
        assert r.status_code == 200, r.text
        # ★ 从**真配置**读回来（不是读我传进去那个 dict ✗ —— 端点内部是复制后整体赋回 ✓）
        caps = m.cfg.budget.caps
        assert caps["XIAOMI_MIMO_API_KEY"]["limit"] == 20.0, caps
        assert caps["XIAOMI_MIMO_API_KEY"]["since"], "没记 from-何时起算 ✗（那就没法清零重来 ✓）"
        # 清零重来：换个更晚的 since ✓ 上限不动 ✓
        first = caps["XIAOMI_MIMO_API_KEY"]["since"]
        c.post("/api/v1/budget", json={"key": "XIAOMI_MIMO_API_KEY", "reset_since": True})
        caps = m.cfg.budget.caps
        assert caps["XIAOMI_MIMO_API_KEY"]["limit"] == 20.0, "清零把上限也弄丢了 ✗"
        assert caps["XIAOMI_MIMO_API_KEY"]["since"] >= first, caps
        # 撤掉（limit=0）
        c.post("/api/v1/budget", json={"key": "XIAOMI_MIMO_API_KEY", "limit": 0})
        assert "XIAOMI_MIMO_API_KEY" not in m.cfg.budget.caps, "填 0 应该是撤掉上限 ✗"


def test_endpoint_refuses_nonsense(monkeypatch):
    monkeypatch.setattr(m.cfg, "budget", SimpleNamespace(enabled=True, caps={}), raising=False)
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        assert c.post("/api/v1/budget", json={"key": ""}).status_code == 422
        assert c.post("/api/v1/budget", json={"key": "K"}).status_code == 422          # 既没 limit 也没清零
        assert c.post("/api/v1/budget", json={"key": "K", "limit": -1}).status_code == 422


def test_settings_readback_does_not_wipe_the_budget():
    """★ 体检⑤ 那条教训的同一个形状（"存了读不回 ⇒ 一保存就重置" ✗）——
    新加的 `budget` 节也必须能在设置读回里看到 ✓ 否则界面一保存就可能把它抹掉 ✗。"""
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        s = c.get("/api/v1/settings").json()
    assert "budget" in s, "GET /settings 不回传 budget ⇒ 界面回填成空 ⇒ 一保存就把上限抹了 ✗"
    assert "caps" in s["budget"], s.get("budget")
