"""开会模式（meeting）的锚点 —— 用户要的「指定一个议题，他们开会探讨，最后总结」那一半。

设计是先查后定的（2026-10-05）：
  · dsh-group-chat（DSH 插件，AI 主持人驱动的多角色群聊）：群内共享发言（【角色名】内容）、
    多轮轮流发言、**结论导出**
  · AutoGen GroupChatManager：主持人动态选人 + **终止条件** + 总结
所以这里钉的四条是：**共享上下文**、**轮次**、**每轮问主持人够不够（提前收口）**、
**收口成 结论/分歧/待办**。全部用打桩的"发言"，一次模型调用都不发。
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import main as m
from app.team import TeamStore


@pytest.fixture()
def room(tmp_path, monkeypatch):
    st = TeamStore(tmp_path)
    e1 = st.add_employee({"name": "产品", "dept": "产品部", "role": "产品经理", "persona": "盯需求", "mode": "expert"})
    e2 = st.add_employee({"name": "开发", "dept": "研发部", "role": "工程师", "persona": "盯实现", "mode": "expert"})
    e3 = st.add_employee({"name": "主持", "dept": "管理部", "role": "组长", "persona": "控节奏", "mode": "expert"})
    g = st.create_group("议事厅", [e1["id"], e2["id"], e3["id"]], leader=e3["id"], mode="leader")
    st.set_mode(g["id"], "meeting")
    monkeypatch.setattr(m, "_team_store", st)

    prompts: list[tuple[str, str]] = []      # (发言者, 提示词)
    replies: dict[str, list[str]] = {}       # 按顺序喂回的"模型输出"
    # ★ 2026-10-06：收口那次单独给一份（想验跑题就覆盖它 ✓）—— 见 _fake_say 里的说明 ✓
    room_box = {"summary_text": "1. 结论：先做最小可用版\n2. 分歧：无\n3. 待办：产品出PRD"}

    async def _fake_say(emp, prompt):
        prompts.append((emp["name"], prompt))
        # ★ 2026-10-06：**收口那次按提示词内容识别** ✓ ——
        #   原来只按"第几次调用"盲取假回复 ✗；跑题复问（新增的一次调用）一进来就全错位 ✓。
        #   想验"跑题纪要"的测试**自己覆盖** `room["summary_text"]` 即可 ✓（默认给一份接地气的 ✓）。
        if "纪要" in prompt or "收口" in prompt or "汇总" in prompt:
            return str(room_box["summary_text"])
        q = replies.get(emp["name"]) or ["（默认发言）"]
        return q.pop(0) if q else "（说完了）"

    monkeypatch.setattr(m, "_employee_say", _fake_say)
    # 后台任务**不真跑**：测试自己用 asyncio.run 驱动执行体（跑完才好断言全过程）。
    # ★ 别用 asyncio.get_event_loop()：全量跑时事件循环状态会跨测试串味
    #   （本班实测：单跑全过、全量跑 4 个挂 —— 就是它 + 下面那个直接赋值造成的污染）。
    monkeypatch.setattr(m, "_spawn_bg", lambda coro: coro.close())
    return {"store": st, "gid": g["id"], "prompts": prompts, "replies": replies,
            "summary": room_box, "names": (e1["name"], e2["name"], e3["name"])}


def _say(text: str, gid: str):
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        return c.post(f"/api/v1/team/groups/{gid}/say", json={"text": text})


def _run_meeting_sync(room, topic: str, rounds: int):
    """直接同步驱动执行体（绕开后台任务），断言整场讨论。"""
    body = _say(topic, room["gid"]).json()
    begin = room["store"].meeting_begin(room["gid"], topic, rounds)   # 重新起一场（_say 已起过）
    asyncio.run(m._run_meeting(room["gid"], begin["order"], begin["rounds"]))
    return body


def test_meeting_starts_and_opens_with_the_roster(room):
    body = _say("我们要不要做外卖小程序？ 2轮", room["gid"]).json()
    assert body["mode"] == "meeting" and body["rounds"] == 2, body
    feed = [x["text"] for x in room["store"].feed(room["gid"])]
    assert any("🗣 开会" in t for t in feed), feed
    opening = next(t for t in feed if "🗣 开会" in t)
    assert "参会 3 人" in opening and "最多 2 轮" in opening, opening
    assert "主持：主持" in opening, "有组长时该由组长主持"


def test_every_speaker_sees_what_was_said_before(room):
    """★ 开会与"各说各话"的分界：后发言的人**能看到**前面的发言（【名字】内容）。"""
    room["replies"]["产品"] = ["我建议先做最小可用版，会员体系砍掉"]
    room["replies"]["开发"] = ["同意，但订单状态机要先定"]
    room["replies"]["主持"] = ["DONE 够了", "结论：做MVP", "1. 结论：做MVP"]
    _run_meeting_sync(room, "要不要做外卖小程序", 1)
    dev_prompt = next(p for n, p in room["prompts"] if n == "开发")
    assert "【产品】我建议先做最小可用版" in dev_prompt, dev_prompt[-400:]
    assert "【议题】要不要做外卖小程序" in dev_prompt
    assert "不要复述" in dev_prompt, "提示词要让模型别复读别人的话"


def test_moderator_can_stop_early(room):
    """★ 动态调度/终止条件：主持人第 1 轮说 DONE ⇒ 不再跑第 2 轮（省钱）。

    注意：**主持自己也是参会者**，会先以参会者身份发一次言，然后才轮到"裁决"那一次调用
    —— 假回复队列必须按这个顺序排（本班第一次就排错了，跑出 3 轮才发现）。
    """
    room["replies"]["主持"] = ["（我作为参会者听着，先不插话）", "DONE 已经够清楚了", "1. 结论：按 A 方案"]
    body = _say("选 A 还是 B？ 3轮", room["gid"]).json()
    assert body["rounds"] == 3, body
    begin = room["store"].meeting_begin(room["gid"], "选 A 还是 B？", 3)
    asyncio.run(m._run_meeting(room["gid"], begin["order"], 3))
    feed = [x["text"] for x in room["store"].feed(room["gid"])]
    assert sum(1 for t in feed if t.startswith("—— 第")) == 1, f"只该跑 1 轮：{feed}"
    assert any("提前收口" in t for t in feed), feed
    assert room["store"].get_group(room["gid"])["meeting_state"] == "done"


def test_meeting_always_ends_with_a_summary(room):
    """收口必须是 结论/分歧/待办 三段（用户要的"最后总结"）。

    ★ 2026-10-06：纪要里**要引用讨论里真出现过的话** ✓ —— 现在跑题会被接地校验拦下、
    触发一次复问 ✓（那次复问会吃掉下一条假回复 ⇒ 断言就会看到兜底那份 ✗）。
    所以这里的假纪要**照实引用了发言原文**（"（默认发言）" ✓），接地 ✓ 才走得到正常路径 ✓。
    """
    room["replies"]["主持"] = [
        "CONTINUE 还缺成本估算", "DONE 够了",
        "1. 结论：先做最小可用版\n2. 分歧：无\n3. 待办：产品出PRD（原话：「（默认发言）」）",
    ]
    begin = room["store"].meeting_begin(room["gid"], "要不要做", 2)
    asyncio.run(m._run_meeting(room["gid"], begin["order"], 2))
    feed = [x["text"] for x in room["store"].feed(room["gid"])]
    summary = [t for t in feed if t.startswith("📋 会议纪要")]
    assert summary and "待办" in summary[0], summary
    g = room["store"].get_group(room["gid"])
    assert g["meeting_state"] == "done" and "结论" in g["meeting_summary"]


def test_summary_failure_falls_back_to_a_digest(room, monkeypatch):
    """★ 用户实测的真问题：收口那一步空响应 ⇒ 纪要变成一句"（收口失败：…）"，等于白开会。

    现在要求：**收口尽力拿东西** —— 先正常要，失败用更短记录再要一次，还失败就
    从发言记录里**自动整理要点**（每人最后说了什么 + 带分歧/结论字样的原话）。
    """
    room["replies"]["产品"] = ["我倾向 A 方案"]
    room["replies"]["开发"] = ["我更倾向 B，但风险是排期"]
    room["replies"]["主持"] = ["（参会）", "DONE 够了"]

    calls = {"n": 0}
    orig = m._employee_say

    async def _say_but_summary_fails(emp, prompt):
        # 纪要提示词一定包含「会议纪要」这几个字；让它失败，模拟空响应
        if "会议纪要" in prompt or "收口" in prompt:
            calls["n"] += 1
            raise RuntimeError("模型返回空结果")
        return await orig(emp, prompt)

    monkeypatch.setattr(m, "_employee_say", _say_but_summary_fails)
    begin = room["store"].meeting_begin(room["gid"], "选 A 还是 B", 1)
    asyncio.run(m._run_meeting(room["gid"], begin["order"], 1))

    feed = [x["text"] for x in room["store"].feed(room["gid"])]
    note = [t for t in feed if t.startswith("📋 会议纪要")]
    assert note, feed
    body = note[0]
    assert "自动整理" in body, f"没有兜底整理：{body[:200]}"
    assert "产品" in body and "我倾向 A 方案" in body, "兜底纪要没把各方发言列出来"
    assert "待办" in body, "兜底纪要也要有'待办'这一段（哪怕是'需你手动确认'）"
    assert calls["n"] >= 2, "应当先重试一次更短的记录，再兜底"
    g = room["store"].get_group(room["gid"])
    assert g["meeting_state"] == "done" and g["meeting_summary"], "兜底纪要也要落盘"


def test_off_topic_summary_is_replaced_by_the_digest(room):
    """★ 用户实测：收口那次模型**编了一份与议题无关的纪要**（记录里根本没有那些词）。

    "看着像纪要、其实跑题"比没有纪要更糟 —— 用户会以为大家真讨论过。
    所以要有**接地校验**：与讨论记录几乎没交集 ⇒ 当失败处理，换成自动整理那份。
    """
    room["replies"]["产品"] = ["我建议先做最小可用版，砍掉会员体系"]
    room["replies"]["开发"] = ["同意，但订单状态机要先定，否则返工"]
    # ★ 2026-10-06：跑题内容现在从 `room["summary"]` 覆盖 ✓
    #   （收口那次已按提示词识别 ✓，不再按"第几次调用"盲取 ✓）
    room["summary"]["summary_text"] = (
        "1. 结论：复购口径统一为同一客户二次下单率\n2. 分歧：无\n3. 待办：无")   # ← 与议题无关 ✗
    begin = room["store"].meeting_begin(room["gid"], "要不要做外卖小程序", 1)
    asyncio.run(m._run_meeting(room["gid"], begin["order"], 1))
    body = next(t for t in (x["text"] for x in room["store"].feed(room["gid"]))
                if t.startswith("📋 会议纪要"))
    assert "对不上" in body and "自动整理" in body, f"跑题的纪要没被换掉：{body[:200]}"
    assert "最小可用版" in body, "兜底整理应当带上真实发言"


def test_on_topic_summary_is_kept(room):
    """反过来：接地良好的纪要**必须原样保留**（别把好纪要也换成兜底）。"""
    room["replies"]["产品"] = ["建议先做最小可用版"]
    room["replies"]["开发"] = ["同意，但订单状态机要先定"]
    room["replies"]["主持"] = ["（参会）", "1. 结论：先做最小可用版，订单状态机要先定\n2. 分歧：无\n3. 待办：无"]
    begin = room["store"].meeting_begin(room["gid"], "要不要做外卖小程序", 1)
    asyncio.run(m._run_meeting(room["gid"], begin["order"], 1))
    body = next(t for t in (x["text"] for x in room["store"].feed(room["gid"]))
                if t.startswith("📋 会议纪要"))
    assert "最小可用版" in body and "自动整理" not in body, body[:200]


def test_resummarize_uses_the_stored_transcript(room, monkeypatch):
    """★ 存量会议补纪要：讨论记录还在，就**不该**让用户重开一场（那是重烧一遍讨论的钱）。

    真实现场：用户那场 4 人会的收口撞上空响应 ⇒ 纪要变成 "（收口失败：…）"，
    讨论记录其实完整躺在群里。这个接口用记录再收一次口。
    """
    room["replies"]["产品"] = ["先做最小可用版"]
    room["replies"]["开发"] = ["同意，但状态机要先定"]
    room["summary"]["summary_text"] = "1. 结论：做MVP"
    # 先跑一场（收口用假回复里那条纪要）
    begin = room["store"].meeting_begin(room["gid"], "要不要做", 1)
    asyncio.run(m._run_meeting(room["gid"], begin["order"], 1))
    # 再"重新出纪要"：换一条回复，验证用的是**已存记录**
    room["summary"]["summary_text"] = "1. 结论：重新收口——先做最小可用版\n2. 分歧：无\n3. 待办：无"
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.post(f"/api/v1/team/groups/{room['gid']}/meeting/resummarize")
    assert r.status_code == 200, r.text
    assert "重新收口" in r.json()["summary"]
    feed = [x["text"] for x in room["store"].feed(room["gid"])]
    assert any("会议纪要（重新收口）" in t for t in feed), feed
    # 提示词里必须带着原来的发言（否则"重新收口"就变成凭空编）
    last_summary_prompt = [p for n, p in room["prompts"] if "会议纪要" in p or "收口" in p][-1]
    assert "先做最小可用版" in last_summary_prompt, "重新收口没带原讨论记录"


def test_resummarize_refuses_when_there_is_no_meeting(room):
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.post(f"/api/v1/team/groups/{room['gid']}/meeting/resummarize")
    assert r.status_code == 422, r.text
    assert "还没有会议记录" in r.json()["detail"]


def test_one_member_failure_does_not_kill_the_meeting(room, monkeypatch):
    """一个人的发言失败（网络抖动）不该让整场会停摆 —— 记一条 ⚠ 继续。"""
    room["replies"]["主持"] = ["（参会）", "DONE 够了", "结论：继续"]
    orig = m._employee_say

    async def _flaky(emp, prompt):
        if emp["name"] == "开发":
            raise RuntimeError("模型 429")
        return await orig(emp, prompt)

    # ★ 必须用 monkeypatch（别直接赋值）：直接赋值在全量跑时会**污染其它测试**
    monkeypatch.setattr(m, "_employee_say", _flaky)
    begin = room["store"].meeting_begin(room["gid"], "议题", 1)
    asyncio.run(m._run_meeting(room["gid"], begin["order"], 1))
    feed = [x["text"] for x in room["store"].feed(room["gid"])]
    assert any("没说出话" in t and "开发" in t for t in feed), feed
    assert any(t.startswith("📋 会议纪要") for t in feed), "有人失败也要照常收口"


@pytest.mark.parametrize("text,expect", [
    ("讨论一下 2轮", 2), ("讨论一下 3 轮", 3), ("讨论一下", 2),
    ("讨论一下 99轮", TeamStore.MAX_MEETING_ROUNDS), ("讨论一下 0轮", 1),
])
def test_rounds_parsing_is_clamped(room, text, expect):
    assert m._meeting_rounds_from(text) == expect


def test_transcript_is_capped_but_keeps_the_latest(room):
    """讨论很长时提示词不能无限膨胀（越贵越慢）：截断要保留**最近**的发言。"""
    st, gid = room["store"], room["gid"]
    for i in range(40):
        st.meeting_append(gid, "产品", f"第{i}条很长的发言" + "内容" * 200, 1)
    text = st.meeting_transcript_text(st.get_group(gid))
    assert len(text) <= st.MEETING_MAX_TRANSCRIPT + 40, len(text)
    assert "第39条" in text, "截断后应当保留最新发言"


# ═══ 界面接线 + 提示词契约（功能做了、界面够不着 = 等于没做）═══
import pathlib  # noqa: E402

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_TV = (_ROOT / "frontend" / "src" / "components" / "TeamView.tsx").read_text("utf-8")
_MAIN_TXT = (_ROOT / "backend" / "app" / "main.py").read_text("utf-8")
_VERIFY = (_ROOT / "frontend" / "scripts" / "verify_team_composer.mjs").read_text("utf-8")


def test_both_mode_dropdowns_offer_meeting():
    assert _TV.count('value="meeting"') == 2, \
        f"「开会」应当出现在两个下拉里（新建群 + 群内改模式），实际 {_TV.count('value=\"meeting\"')} 处"
    assert "meeting: '开会'" in _TV, "群卡片上的模式名没加开会"
    assert "开会：围绕议题讨论 → 出会议纪要" in _TV, "下拉里没说明这一项是干什么的"


def test_group_card_shows_meeting_progress():
    assert "meeting_state === 'running'" in _TV, "没显示「讨论中」"
    assert "第 {g.meeting_round ?? 0}/{g.meeting_rounds ?? 0} 轮" in _TV, "没显示讨论到第几轮"
    assert "已出纪要" in _TV, "结束后没给状态"
    assert "给个议题，他们开会讨论" in _TV, "输入框提示没随模式变化（用户不知道怎么用）"


def test_meeting_loop_is_wired_and_has_an_early_stop():
    assert "_spawn_bg(_run_meeting(" in _MAIN_TXT, "开会没接到后台任务上（阻塞 HTTP 必超时）"
    assert "_team_store.meeting_moderator_prompt(g, r)" in _MAIN_TXT, "没有每轮问主持人（少了动态收口）"
    assert "_team_store.meeting_summary_prompt(g)" in _MAIN_TXT, "没有收口那一步"
    assert "这轮没说出话" in _MAIN_TXT, "单人失败没兜住"
    prompt = TeamStore.meeting_summary_prompt({"meeting_topic": "t", "meeting_transcript": []})
    for k in ("结论", "分歧", "待办"):
        assert k in prompt, f"纪要提示词缺「{k}」"
