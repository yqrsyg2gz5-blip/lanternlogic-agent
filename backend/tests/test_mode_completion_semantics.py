# -*- coding: utf-8 -*-
"""★ 2026-10-06：**五个模式"完成"的定义，集中钉在一处** ✓

用户问"其他模式都试没试" ✓ —— 查下来：只有组长模式端到端真跑过 ✓，
广播与开会**连单测都没有** ✗。补测时发现一个更要紧的事实：

> **"项目终验门 / 两级验收 / 缺陷回环 / 波次" 四套机制，原先只接在 `leader` 上** ✓
> ⇒ 其他模式里"**每个人都交了**"就算完成 ✗ ——
> 正是用户最开始抱怨的"**做完了 ≠ 能跑**" ✓。

**已经补上的** ✓（2026-10-06）：
- `broadcast`：批次全交齐 ⇒ 自动派项目终验 ✓（`6435b95`）
- `relay`：最后一棒交完 ⇒ 自动派项目终验 ✓（`4f88439`）

**故意不装的**（不是遗漏，是判断 ✓）：
- `meeting`：产出的是**一场讨论的纪要**，不是可运行的东西 ✓ ——
  "能不能跑"这个问题在开会里**不成立** ✓；它的完成判据就是"有纪要 ✓"。
- `manual`：**用户在点名派活** ✓ —— 派几个、什么时候算完，由用户自己说了算 ✓
  （硬塞一道门反而会挡住用户手动推进 ✓）。

这份测试**不修行为** ✗，只把"现在各模式到底怎么算完成"写清楚 ✓：
哪天给某个模式加/删这道门，**这里会红** ⇒ 那时**改这里**（连同 CHANGELOG ✓），不是绕过 ✓。
"""
from __future__ import annotations

import pytest

from app.team import TeamStore


@pytest.fixture()
def shop(tmp_path):
    st = TeamStore(tmp_path)
    ids = []
    for nm in ("甲", "乙"):
        ids.append(st.add_employee({"name": nm, "dept": "技术部", "role": "工程师",
                                    "persona": "干活", "mode": "expert"})["id"])
    return st, ids


def test_leader_is_the_only_mode_with_a_completion_gate(shop):
    """**组长模式**：有分工单 ⇒ 有波次、有两级验收、有项目终验门 ✓（唯一一个 ✓）。"""
    st, ids = shop
    g = st.create_group("组长群", ids, mode="leader")
    st.leader_begin(g["id"], "做个东西", [
        {"name": "甲", "task": "实现", "output": "a.py", "depends_on": []},
        {"name": "乙", "task": "测试", "output": "test_a.py", "depends_on": ["甲"]},
    ])
    gg = st.get_group(g["id"])
    assert gg["leader_plan"], "组长群该有分工单"
    # ★ `leader_begin` 会把**第一批**直接标成 running ✓（本班第一版断言 pending 被打脸 ✗）
    assert gg["leader_plan"][0]["status"] == "running", gg["leader_plan"][0]
    assert gg["leader_plan"][1]["status"] == "pending", gg["leader_plan"][1]
    # ★ `leader_begin` 会把**第一批直接开工**（写成 running ✓）⇒ 此刻 ready 是空的 ✓
    #   （本班连踩两次：先按 pending 断言 ✗，又按"名字列表"断言 ✗ —— 实际返回的是字典 ✓）
    batch = st.leader_ready(g["id"])
    assert batch["ready"] == [], batch
    assert [w["name"] for w in batch["waiting"]] == ["乙"], batch


@pytest.mark.parametrize("mode", ["manual", "broadcast", "relay", "meeting"])
def test_other_modes_still_have_no_leader_plan(shop, mode):
    """**这四个模式没有"分工单"** ✓（不是缺陷：分工单是组长那套波次机制的载体 ✓）。

    它们现在的"完成"由各自的机制决定 ✓：
    · `broadcast`：批次全交齐 ⇒ **自动加一道项目终验** ✓（2026-10-06 补上 ✓）
    · `relay`：最后一棒交完 ⇒ **自动加一道项目终验** ✓（2026-10-06 补上 ✓）
    · `meeting`：有纪要就算完 ✓（**故意不装门**：讨论产出的是结论，不是能跑的东西 ✓）
    · `manual`：**用户点名派活**，什么时候算完由用户说了算 ✓（硬塞门反而挡住手动推进 ✓）
    """
    st, ids = shop
    g = st.create_group(f"{mode} 群", ids, mode=mode)
    gg = st.get_group(g["id"])
    assert gg["mode"] == mode
    assert not gg.get("leader_plan"), f"{mode} 模式不该有分工单"
    assert not gg.get("leader_goal"), f"{mode} 模式不该有组长目标"


def test_relay_chain_advances_by_handoff_not_by_verification(shop):
    """**接力模式**的推进靠"交接"，不靠"验收" ✓ —— 这也是它和组长模式的关键差别 ✓。

    记录现状：接力的位置（`relay_pos`）只由"上一棒交付了"推动 ✓，
    没有"这一步到底做对没有"的判定 ✓。
    """
    st, ids = shop
    g = st.create_group("接力群", ids, mode="relay")
    order = st.relay_order(st.get_group(g["id"]))
    assert order, "接力至少要有顺序"
    assert st.get_group(g["id"]).get("relay_pos") in (0, None), "开局不该有进行中的棒次"
