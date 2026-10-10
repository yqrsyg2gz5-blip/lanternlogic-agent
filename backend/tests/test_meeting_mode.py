# -*- coding: utf-8 -*-
"""★ 2026-10-06：**开会模式（meeting）此前 0 覆盖** ✗ —— 这里补上真跑的单测。

用户问"其他模式都试没试" ✓ —— 查下来只有组长模式真跑过 ✓；
广播与**开会**连单测都没有 ✗（`_run_meeting` 是一条完整链路：轮流发言 → 主持人判"够了没"
→ 收口成纪要 ✓，里面还踩过 `RuntimeError: 组长大脑返回空结果` —— 开会时它是主持、不一定是组长 ✓）。

这里覆盖**不需要真模型**的那部分 ✓（状态机 + 纪要拼装 + 轮次解析 + 执行体真跑 ✓）。

★ 本班踩过的坑：群上的字段是**扁平**的（`meeting_topic` / `meeting_rounds` /
  `meeting_transcript` …），**没有** `g["meeting"]` 这个字典 ✓ —— 我第一版按后者写，全 KeyError ✗。
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
    g = st.create_group("议事群", ids, mode="meeting")
    monkeypatch.setattr(m, "_team_store", st)
    return {"store": st, "gid": g["id"]}


def test_meeting_begin_sets_roster_moderator_and_round_cap(room):
    st, gid = room["store"], room["gid"]
    b = st.meeting_begin(gid, "这周做什么", rounds=2)
    assert b["rounds"] == 2 and len(b["order"]) == 3, b
    g = st.get_group(gid)
    assert g["meeting_topic"] == "这周做什么"
    assert g["meeting_rounds"] == 2
    assert g["meeting_state"] == "running"


def test_meeting_rounds_are_clamped(room):
    """轮次要**钳制**在 1..MAX ✓（模型说"开 99 轮"不能真开 ✗）。"""
    st, gid = room["store"], room["gid"]
    assert st.meeting_begin(gid, "话题", rounds=99)["rounds"] == st.MAX_MEETING_ROUNDS
    assert st.meeting_begin(gid, "话题", rounds=0)["rounds"] >= 1
    assert m._meeting_rounds_from("开 3 轮会") == 3
    assert m._meeting_rounds_from("随便聊聊") >= 1              # 解析不出来给默认 ✓
    assert m._meeting_rounds_from("开 999 轮") == st.MAX_MEETING_ROUNDS


def test_transcript_grows_with_each_speaker(room):
    st, gid = room["store"], room["gid"]
    st.meeting_begin(gid, "选题", rounds=2)
    st.meeting_append(gid, "甲", "我建议做记账", 1)
    st.meeting_append(gid, "乙", "我建议做待办", 1)
    g = st.get_group(gid)
    assert isinstance(g["meeting_transcript"], list), g["meeting_transcript"]
    txt = st.meeting_transcript_text(g)
    assert "甲" in txt and "我建议做记账" in txt
    assert "乙" in txt and "我建议做待办" in txt
    assert txt.index("甲") < txt.index("乙"), "发言顺序应保持入账顺序"


def test_meeting_finish_records_summary_and_reason(room):
    st, gid = room["store"], room["gid"]
    st.meeting_begin(gid, "选题", rounds=2)
    st.meeting_append(gid, "甲", "说完了", 1)
    st.meeting_finish(gid, "结论：做记账", reason="主持人判够了")
    g = st.get_group(gid)
    assert g["meeting_state"] == "done"
    assert g["meeting_summary"] == "结论：做记账"


def test_off_topic_summary_is_retried_once_before_falling_back(room, monkeypatch):
    """★★ 2026-10-06 **真跑开会那轮抓到的真问题** ✗：

    正常要来的纪要被判「与讨论记录对不上（疑似跑题）」⇒ **直接落兜底整理** ✓
    （用户拿到的是"⚠️ 自动收口没成功…仅供参考" ✓ —— 一场会白开一半 ✗）。

    修法（和"验收人说不清结论"同一招 ✓）：**带着失败原因复问一次** ✓，
    这次强调"只写讨论里真出现过的东西、点名引用原话" ✓；救得回来就用真纪要 ✓。
    """
    st, gid = room["store"], room["gid"]
    st.meeting_begin(gid, "第一版做哪三件事", rounds=1)
    st.meeting_append(gid, "甲", "我主张先做核心闭环，只跑一条主线", 1)
    st.meeting_append(gid, "乙", "我反对，先要有可读的错误提示，不然没法排查", 1)

    calls: list[str] = []
    sums = {"n": 0}

    async def _say(emp, prompt):                       # noqa: ARG001
        calls.append(prompt[:20])
        # ★ 只有"收口"那次才返回跑题 ✓ —— 本班第一版按"第几次调用"判断 ✗，
        #   结果第一次调用其实是**甲的发言** ✓ ⇒ 那次跑题的成了发言、纪要反而接地 ✓，
        #   于是复问分支根本没触发（测试假红 ✓）。按**提示词内容**判断才准 ✓。
        if "纪要" in prompt or "汇总" in prompt or "收口" in prompt:
            sums["n"] += 1
            if sums["n"] == 1:
                return "本产品应聚焦用户体验与生态建设，建议采用敏捷迭代方法论。"   # 跑题 ✗
            return "结论：先做核心闭环。分歧：甲主张闭环优先，乙主张可读错误提示优先。"  # 接地 ✓
        return "我主张先做核心闭环" if emp["name"] == "甲" else "我主张先做可读的错误提示"

    monkeypatch.setattr(m, "_employee_say", _say)
    asyncio.run(m._run_meeting(gid, [e["id"] for e in st.employees()][:2], 1))

    g = st.get_group(gid)
    assert len(calls) >= 2, f"应该复问一次（实际 {len(calls)} 次）"
    assert "核心闭环" in str(g.get("meeting_summary") or ""), g.get("meeting_summary")
    assert "自动收口没成功" not in str(g.get("meeting_summary") or ""), "救回来了就不该走兜底 ✗"
    texts = [x["text"] for x in st.feed(gid)]
    assert any("复问一次拿到了" in t for t in texts), texts[-3:]


def test_run_meeting_really_runs_with_a_fake_brain(room, monkeypatch):
    """★ **真跑一遍执行体** ✓：每个人都发言、主持人判停、收口成纪要并回流到群里 ✓。

    假大脑：普通发言返回"我是<名字>的看法"；主持人那句返回"够了" ⇒ 只跑一轮 ✓（省时间 ✓）。
    """
    st, gid = room["store"], room["gid"]
    spoke: list[str] = []

    async def _fake_say(emp, prompt):                       # noqa: ARG001
        spoke.append(emp["name"])
        # ★ 故意**不返回 JSON** ✓ —— 顺带验证"大脑不听话时会议不会崩"这条兜底 ✓
        #   （真实结果：主持人判停解析失败 ⇒ 跑满轮次 ⇒ **自动收口也失败** ⇒
        #     落到 `_meeting_fallback_digest` 出一份摘要 ✓ 并如实写明"自动收口失败" ✓）
        return f"我是{emp['name']}的看法"

    monkeypatch.setattr(m, "_employee_say", _fake_say)
    b = st.meeting_begin(gid, "选个方向", rounds=3)
    asyncio.run(m._run_meeting(gid, b["order"], b["rounds"]))

    assert len(spoke) >= 3, spoke                            # 三个人都说过
    g = st.get_group(gid)
    assert g["meeting_state"] == "done", g["meeting_state"]
    assert g.get("meeting_summary"), "没有收口摘要（哪怕是兜底摘要也得有 ✓）"
    text = " ".join(str(x.get("text") or "") for x in st.feed(gid))
    assert "轮" in text, f"群里看不到这场会的任何痕迹：{text[:120]}"
    for nm in ("甲", "乙", "丙"):
        assert nm in text, f"群里没有 {nm} 的发言痕迹"
