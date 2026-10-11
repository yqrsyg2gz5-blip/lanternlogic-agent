"""接力模式（relay）的锚点 —— 用户要的"开会/协作"里**串行那一半**。

与组长模式的根本区别（也是这个文件要钉住的东西）：
  · 组长 = **并行**：一个大目标切成互不重叠的子任务，各干各的
  · 接力 = **串行**：同一件事一个人做完，**把交付交给下一个人**接着做（写作→评审→定稿）
所以接力的灵魂是"**上一棒的交付必须进下一棒的工作单**" —— 这条没做到，就只是"排着队各干各的"。

一次模型调用都不发：`_leader_plan`/`_dispatch_to_employee`/`_launch_task` 全部打桩。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main as m
from app.team import TeamStore


@pytest.fixture()
def shop(tmp_path, monkeypatch):
    """真 TeamStore（落 tmp_path）+ 打桩派发：能验"第几棒、带了什么上游内容"。"""
    st = TeamStore(tmp_path)
    e1 = st.add_employee({"name": "写手", "dept": "内容部", "role": "撰稿", "persona": "写初稿", "mode": "expert"})
    e2 = st.add_employee({"name": "评审", "dept": "质量部", "role": "评审", "persona": "挑毛病", "mode": "expert"})
    e3 = st.add_employee({"name": "定稿", "dept": "内容部", "role": "编辑", "persona": "润色定稿", "mode": "expert"})
    g = st.create_group("文案流水线", [e1["id"], e2["id"], e3["id"]], mode="relay")
    monkeypatch.setattr(m, "_team_store", st)

    sent: list[dict] = []

    def _fake_dispatch(gid, gg, nm, emp, task_text, relay_pos=None):
        sent.append({"name": nm, "text": task_text, "pos": relay_pos})
        return type("T", (), {"id": f"task_20261005_{len(sent):04x}"})()

    monkeypatch.setattr(m, "_dispatch_to_employee", _fake_dispatch)
    monkeypatch.setattr(m, "_spawn_bg", lambda coro: coro.close())
    monkeypatch.setattr(m, "_launch_task", lambda *a, **kw: type("T", (), {"id": "task_20261005_ffff"})())
    return {"store": st, "gid": g["id"], "sent": sent, "names": (e1["name"], e2["name"], e3["name"])}


def _say(text: str, gid: str):
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        return c.post(f"/api/v1/team/groups/{gid}/say", json={"text": text})


def test_relay_end_also_triggers_the_project_acceptance_gate(shop, monkeypatch):
    """★★ 2026-10-06：**接力最后一棒交完 ≠ 这东西能跑** ✗ —— 接力也要过「项目终验门」 ✓

    和广播同一个道理（"做完 ≠ 能跑"与模式无关 ✓）。做法一致：派一个真跑终验的任务
    + 挂看门者（复用它宣布 ✅/❌ ✓）。
    """
    import asyncio as _aio

    st, gid = shop["store"], shop["gid"]
    launched: list[str] = []
    monkeypatch.setattr(m, "_launch_task",
                        lambda text, *a, **kw: (launched.append(text),
                                                type("T", (), {"id": "gate_relay"})())[1])
    _say("写一篇稿子", gid)                       # 起接力
    total = len(st.relay_order(st.get_group(gid)))
    # 把前几棒直接推过去，然后让"最后一棒"交付 ⇒ 应触发终验门
    for pos in range(1, total):
        st.relay_advance(gid, pos)
    m._relay_after_delivery(gid, "定稿", "最终稿在这", total, "done")
    assert len(launched) == 1, launched
    assert "项目验收（最后一道门）" in launched[0]
    assert "跑工作区里已有的测试" in launched[0]          # 降本硬要求带上了 ✓
    texts = [x["text"] for x in st.feed(gid)]
    assert any("最后一道门" in t and "项目验收" in t for t in texts), texts[-3:]
    assert _aio is not None


def test_relay_starts_with_the_first_member(shop):
    """不点名 + relay 模式 ⇒ 起接力：第一棒先上，群里能看到棒次与顺序。"""
    body = _say("写一篇新品稿", shop["gid"]).json()
    assert body["mode"] == "relay", body
    assert [d["name"] for d in body["dispatched"]] == [shop["names"][0]], body
    assert body["dispatched"][0]["total"] == 3, body
    assert [s["name"] for s in shop["sent"]] == [shop["names"][0]]
    assert "第 1 棒" in shop["sent"][0]["text"] or "先手" in shop["sent"][0]["text"]
    feed = [x["text"] for x in shop["store"].feed(shop["gid"])]
    assert any("接力开始" in t for t in feed), feed


def test_each_delivery_hands_the_output_to_the_next_member(shop):
    """★ 灵魂一条：上一棒的**交付内容**必须出现在下一棒的工作单里。"""
    _say("写一篇新品稿", shop["gid"])
    m._relay_after_delivery(shop["gid"], shop["names"][0], "初稿：这是写手交出来的正文内容", 1, "done")
    assert [s["name"] for s in shop["sent"]] == [shop["names"][0], shop["names"][1]], shop["sent"]
    nxt = shop["sent"][1]
    assert "初稿：这是写手交出来的正文内容" in nxt["text"], nxt["text"]
    assert nxt["pos"] == 2, nxt
    feed = [x["text"] for x in shop["store"].feed(shop["gid"])]
    assert any("接棒" in t and shop["names"][1] in t for t in feed), feed


def test_last_delivery_finishes_the_relay(shop):
    _say("写一篇新品稿", shop["gid"])
    m._relay_after_delivery(shop["gid"], shop["names"][0], "初稿", 1, "done")
    m._relay_after_delivery(shop["gid"], shop["names"][1], "评审意见：改这三处", 2, "done")
    m._relay_after_delivery(shop["gid"], shop["names"][2], "定稿", 3, "done")
    feed = [x["text"] for x in shop["store"].feed(shop["gid"])]
    assert any("接力完成" in t for t in feed), feed
    assert shop["store"].get_group(shop["gid"])["relay_pos"] == 0, "完成后位置该归零"
    # 第 3 棒之后不该再派第 4 个人
    assert len(shop["sent"]) == 3, shop["sent"]


def test_out_of_order_delivery_does_not_advance(shop):
    """★ 防串棒：拿旧棒次的回调再交一次，不许把接力推进两次（否则最后一棒会反复打转）。"""
    _say("写一篇新品稿", shop["gid"])
    g = shop["store"].get_group(shop["gid"])
    assert g["relay_pos"] == 1
    m._relay_after_delivery(shop["gid"], shop["names"][0], "乱序的旧交付", 0, "done")   # 位置对不上
    assert shop["store"].get_group(shop["gid"])["relay_pos"] == 1, "位置不该被旧回调推进"
    assert len(shop["sent"]) == 1, shop["sent"]


def test_failed_leg_stops_the_relay_instead_of_passing_garbage(shop):
    """某棒失败：**不接棒**（否则下一棒在残缺基础上干活），且群里明说停在哪一棒。"""
    _say("写一篇新品稿", shop["gid"])
    m._relay_after_delivery(shop["gid"], shop["names"][0], "（任务结束）", 1, "failed")
    assert len(shop["sent"]) == 1, f"失败后不该继续派：{shop['sent']}"
    feed = [x["text"] for x in shop["store"].feed(shop["gid"])]
    assert any("接力停在第 1 棒" in t for t in feed), feed
    assert shop["store"].get_group(shop["gid"])["relay_pos"] == 0, "停下后位置该归零"


def test_relay_prompt_differs_between_first_and_later_legs(shop):
    first = shop["store"].relay_prompt("总目标", "写手", "", 1, 3)
    later = shop["store"].relay_prompt("总目标", "写手", "上游交付正文", 2, 3)
    assert "第一棒" in first and "上游交付正文" not in first
    assert "上游交付正文" in later and "不要重复它已经做完的部分" in later


def test_relay_order_can_be_pinned(shop):
    """接力顺序可显式指定（默认按成员顺序）—— 排过的顺序里**只保留还在群里的成员**。"""
    st, gid = shop["store"], shop["gid"]
    members = st.get_group(gid)["members"]
    order = [members[2], members[0], members[1]]
    st.set_relay_order(gid, order)
    assert st.relay_order(st.get_group(gid)) == order
    st.set_relay_order(gid, [members[0], "emp_已经不在群里"])
    assert st.relay_order(st.get_group(gid)) == [members[0]], "不在群里的成员不该出现在接力顺序里"


def test_new_modes_are_accepted_and_junk_is_rejected(tmp_path):
    st = TeamStore(tmp_path)
    e = st.add_employee({"name": "甲", "dept": "开发部", "role": "开发", "persona": "写代码", "mode": "expert"})
    for mode in ("relay", "meeting"):
        g = st.create_group(f"群-{mode}", [e["id"]], mode=mode)
        assert g["mode"] == mode
        assert st.set_mode(g["id"], mode.upper())["mode"] == mode, "大小写不敏感"
    with pytest.raises(ValueError):
        st.create_group("坏群", [e["id"]], mode="relayy")
    with pytest.raises(ValueError):
        st.set_mode(st.groups()[0]["id"], "接力")      # 中文不是合法取值（避免又出现"静默不命中"）
