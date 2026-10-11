# -*- coding: utf-8 -*-
"""★ 2026-10-06：**广播模式（broadcast）从来没有被验证过** ✗ —— 这里补上真跑的单测。

## 为什么要补

用户问"其他模式都试没试" ✓ —— 查下来：
· 组长模式（leader）：12 处 fixture + 端到端真跑过很多轮 ✓
· 点名派发（manual）6 处、接力（relay）1 处：只有单测 ⚠️
· **广播（broadcast）0 处、开会（meeting）0 处**：**连单测都没有** ✗✗

而"做完 ≠ 能跑"这个教训**与模式无关** ✓ —— 广播模式下没有组长 ⇒
**波次 / 项目终验门 / 两级验收 / 缺陷回环 一个都走不到** ✗。

这份测试先把**现状**钉住 ✓（写清"它现在是什么行为" ✓），
这样以后谁要给广播加验收门，就知道自己改动了什么 ✓（也防我记错 ✓）。
"""
from __future__ import annotations

import asyncio

import pytest

from app import main as m
from app.team import TeamStore


@pytest.fixture()
def room(tmp_path, monkeypatch):
    st = TeamStore(tmp_path)
    ids = []
    for nm in ("甲", "乙", "丙"):
        ids.append(st.add_employee({"name": nm, "dept": "技术部", "role": "工程师",
                                    "persona": "干活", "mode": "expert"})["id"])
    g = st.create_group("广播群", ids, mode="broadcast")
    monkeypatch.setattr(m, "_team_store", st)
    return {"store": st, "gid": g["id"]}


def test_broadcast_group_has_claim_enabled_others_do_not(tmp_path, monkeypatch):
    """认领开关跟着模式走：**只有 broadcast 开** ✓（组长模式自动关 ✓）。"""
    st = TeamStore(tmp_path)
    ids = [st.add_employee({"name": "甲", "dept": "技术部", "role": "工程师",
                            "persona": "干活", "mode": "expert"})["id"]]
    assert st.create_group("广播", ids, mode="broadcast")["claim_enabled"] is True
    assert st.create_group("组长", ids, mode="leader")["claim_enabled"] is False
    assert st.create_group("点名", ids, mode="manual")["claim_enabled"] is False


def test_broadcast_dispatches_the_same_task_to_everyone(room, monkeypatch):
    """**没有 @ 时，同一件事派给全员** ✓ —— 这是广播与点名的分界 ✓。"""
    gid = room["gid"]
    launched: list[tuple[str, str]] = []

    def _fake_launch(text, project_id=None, provider=None, system_extra=None, workdir=None, **kw):   # ★ 多了 max_iterations ✓ 用 **kw 兜住
        launched.append((str(system_extra or "")[:60], text))
        class _T:                       # 只要有个 id 就够了（看门者会去 tasks 里找）
            id = "task_fake"
        return _T()

    monkeypatch.setattr(m, "_launch_task", _fake_launch)
    monkeypatch.setattr(m, "_spawn_bg", lambda coro: coro.close())   # 关掉协程，免得刷"never awaited"警告 ✗
    out = asyncio.run(m.team_say(gid, m.TeamSayReq(text="大家一起写个 README")))
    assert len(out.get("dispatched") or []) == 3, out
    assert len(launched) == 3, launched
    # 三个人的任务**是同一件事**（广播的语义 ✓），但抬头各自署名自己 ✓
    #   （我第一版断言"正文完全相同" ✗ —— 正文里带 @署名，本来就该不同 ✓）
    assert all("大家一起写个 README" in t for _, t in launched), launched
    heads = {t.split("】")[0] for _, t in launched}
    assert len(heads) == 3, heads          # 三个人各收到一份、署名不同 ✓


def test_broadcast_with_mention_only_dispatches_that_one(room, monkeypatch):
    """**有 @ 时只派那一个** ✓（广播不等于"永远全员" ✓）。"""
    gid = room["gid"]
    launched: list[str] = []
    monkeypatch.setattr(m, "_launch_task",
                        lambda text, project_id=None, **k: (launched.append(text), type("T", (), {"id": "t"})())[1])
    monkeypatch.setattr(m, "_spawn_bg", lambda coro: coro.close())   # 关掉协程，免得刷"never awaited"警告 ✗
    out = asyncio.run(m.team_say(gid, m.TeamSayReq(text="@甲 只你做这个")))
    assert len(out.get("dispatched") or []) == 1, out
    assert len(launched) == 1


def test_broadcast_and_manual_share_the_group_workspace(room, monkeypatch):
    """★★ 2026-10-06 **真跑冒烟当场抓到的真 bug** ✗：

    点名派 / 广播走的是 `team_say` 里**另写的一份派发** ✓ —— 它漏了 `workdir` ⇒
    产物落在 `data/tasks/<任务id>/workspace` ✗ ⇒ **同群的人互相看不到对方的文件** ✓
    （"群共享工作区"名存实亡 ✗；症状：交付说"已写 hello.txt ✓"，而群工作区**是空的** ✓）。

    `_dispatch_to_employee`（组长 / 接力那条）本来是对的 ✓ —— 这条钉住"另一条也得对" ✓。
    """
    gid = room["gid"]
    got: dict = {}

    def _fake_launch(text, project_id=None, provider=None, system_extra=None,
                     workdir=None, max_iterations=None, **kw):
        got["workdir"] = workdir
        got["sys_extra"] = system_extra or ""
        return type("T", (), {"id": "t9"})()

    monkeypatch.setattr(m, "_launch_task", _fake_launch)
    monkeypatch.setattr(m, "_spawn_bg", lambda coro: coro.close())
    asyncio.run(m.team_say(gid, m.TeamSayReq(text="一起写点东西")))
    # ★ `group_workspace` 在 **FsStore**（`m.store`）上，不在 TeamStore 上 ✗（本班写错过一次 ✓）
    want = m.store.group_workspace(gid)
    assert got.get("workdir") == want, f"工作区没指到群共享目录 ✗（实际 {got.get('workdir')}）"
    # 系统提示里也要带上"这台机器的事实"（环境交底 ✓；否则它会自己瞎猜 python 在哪 ✗）
    extra = got.get("sys_extra", "").lower()
    assert "工作区" in got.get("sys_extra", "") or "python" in extra


def test_broadcast_opens_a_batch_and_announces_it(room, monkeypatch):
    """★ 2026-10-06：广播派活时**开一个批次** ✓ 并说明"都交齐会自动加一道项目验收" ✓。"""
    gid = room["gid"]
    monkeypatch.setattr(m, "_launch_task",
                        lambda text, project_id=None, **k: type("T", (), {"id": f"t{len(launched)}"})())
    monkeypatch.setattr(m, "_spawn_bg", lambda coro: coro.close())
    launched: list[str] = []
    asyncio.run(m.team_say(gid, m.TeamSayReq(text="一起写个 README")))
    b = room["store"].get_group(gid).get("broadcast_batch")
    assert b and len(b["ids"]) == 3 and len(b["left"]) == 3, b
    texts = [x["text"] for x in room["store"].feed(gid)]
    assert any("广播批次开始" in t and "项目验收" in t for t in texts), texts


def test_broadcast_gate_fires_only_when_the_last_one_delivers(room, monkeypatch):
    """★ 划掉一个不算数，**最后一个交付时**才派项目终验 ✓（并带上"能不能跑"的硬要求 ✓）。"""
    st, gid = room["store"], room["gid"]
    monkeypatch.setattr(m, "_launch_task",
                        lambda text, project_id=None, **k: type("T", (), {"id": "gate1"})())
    monkeypatch.setattr(m, "_spawn_bg", lambda coro: coro.close())
    asyncio.run(m.team_say(gid, m.TeamSayReq(text="一起写个 README")))
    b = st.get_group(gid)["broadcast_batch"]
    ids = list(b["ids"])
    launched: list[str] = []
    monkeypatch.setattr(m, "_launch_task",
                        lambda text, project_id=None, **k: (launched.append(text),
                                                            type("T", (), {"id": "gate1"})())[1])

    # 前两个交付：不该派门 ✓
    for tid in ids[:2]:
        asyncio.run(m._broadcast_after_delivery(gid, tid, "甲", "交了"))
        assert launched == [], f"还没交齐就派了终验 ✗：{launched}"
    # 最后一个：派门 ✓
    asyncio.run(m._broadcast_after_delivery(gid, ids[2], "丙", "交了"))
    assert len(launched) == 1, launched
    task = launched[0]
    assert "项目验收（最后一道门）" in task
    assert "跑工作区里已有的测试" in task and "不要新写验收脚本" in task   # 降本硬要求也在 ✓
    assert "怎么打开" in task                                          # "能不能打开"这条也在 ✓
    assert st.get_group(gid)["broadcast_batch"]["gate_task"] == "gate1", "没把门挂到批次上"


def test_broadcast_gate_delivery_announces_the_verdict(room, monkeypatch):
    """★ 门交付时**在群里宣布结论** ✓ —— 通过/不通过/没给结论，三种都要说清 ✓。"""
    st, gid = room["store"], room["gid"]
    monkeypatch.setattr(m, "_launch_task",
                        lambda text, project_id=None, **k: type("T", (), {"id": "gate9"})())
    monkeypatch.setattr(m, "_spawn_bg", lambda coro: coro.close())
    asyncio.run(m.team_say(gid, m.TeamSayReq(text="一起写个 README")))
    b = st.get_group(gid)["broadcast_batch"]
    for tid in b["ids"][:-1]:
        asyncio.run(m._broadcast_after_delivery(gid, tid, "甲", "交了"))
    asyncio.run(m._broadcast_after_delivery(gid, b["ids"][-1], "丙", "交了"))

    for reply, want in (("✅ 项目验收通过：32/32 真跑", "项目验收通过"),
                        ("❌ 项目验收不通过：甲 的 todo.py 有问题", "项目验收不通过"),
                        ("我看了一下，应该没问题", "没给出明确结论")):
        asyncio.run(m._broadcast_after_delivery(gid, "gate9", "丙", reply))
        texts = [x["text"] for x in st.feed(gid)]
        assert any(want in t for t in texts), (reply, texts[-3:])

    """★★ **现状记录**：广播模式下**没有**项目终验门 / 两级验收 / 波次 ✗

    这不是"设计如此"，而是**还没做** ✓（"做完 ≠ 能跑"的教训与模式无关 ✓）。
    哪天给广播加上验收门，这条测试会红 —— 那时**改它**就是（不是绕过它 ✓）。
    """
    st, gid = room["store"], room["gid"]
    assert st.get_group(gid).get("mode") == "broadcast"
    # 组长才有的东西，广播群上**不该有**
    assert not st.get_group(gid).get("leader_plan"), "广播群不该有分工单"
    calls: list[str] = []
    monkeypatch.setattr(m, "_verify_delivery", lambda *a, **k: calls.append("verify"))
    monkeypatch.setattr(m, "_ensure_acceptance", lambda *a, **k: calls.append("acceptance"))
    monkeypatch.setattr(m, "_launch_task",
                        lambda text, project_id=None, **k: type("T", (), {"id": "t"})())
    monkeypatch.setattr(m, "_spawn_bg", lambda coro: coro.close())   # 关掉协程，免得刷"never awaited"警告 ✗
    asyncio.run(m.team_say(gid, m.TeamSayReq(text="做点小事")))
    assert calls == [], f"广播模式下居然走了组长的那套：{calls}"


def test_broadcast_does_not_get_the_leader_machinery(room, monkeypatch):
    pass
