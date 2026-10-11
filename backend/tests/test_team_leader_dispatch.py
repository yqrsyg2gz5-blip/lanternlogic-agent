"""★ 组长拆解（leader 模式）的锚点 —— 用户问"你测没测试过这些"的直接回答。

此前团队只有 13 个测试，全是**群组增删改 + 崩溃恢复**；而用户最关心的那条主路：
**「我说个目标 → 组长拆解 → 给每人派真实任务 → 群里贴分工单」零覆盖。**
这个文件把那条路钉住（用假 provider，一次模型调用都不发）。

对照 Manus 2.0 的"拉群干活"，我们已具备的是：**组长拆解 + 逐个派真任务**；
缺的是"**接力**"（上一个的产出喂给下一个）与"**开会讨论**"——那是另一批功能，见待办总表。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main as m


@pytest.fixture()
def team(tmp_path, monkeypatch):
    """真 TeamStore（落 tmp_path）+ 打桩派发。

    ★ 2026-10-05 改：以前这里手搓了个 `_Store` 假存储，只有 append/employees 这几个方法。
      后来组长改成**依赖波次**（`leader_begin/leader_ready/leader_finish_item`…），
      假存储没有这些方法 ⇒ AttributeError 被兜住 ⇒ "一个人都没派出去"，三条老测试红。
      教训：**替身要贴着真接口，接口一长就该换成真实现**（真存储 + 打桩派发反而更省事）。
    """
    from app.team import TeamStore

    st = TeamStore(tmp_path)
    e1 = st.add_employee({"name": "小前", "dept": "开发部", "role": "前端", "persona": "写前端", "mode": "expert"})
    e2 = st.add_employee({"name": "小后", "dept": "开发部", "role": "后端", "persona": "写后端", "mode": "expert"})
    e3 = st.add_employee({"name": "组长", "dept": "管理", "role": "组长", "persona": "拆解任务", "mode": "expert"})
    g = st.create_group("外卖小程序", [e1["id"], e2["id"], e3["id"]], leader=e3["id"], mode="leader")
    # ★ 本文件的 `_say()` 硬编码了群 id `g1`（老假存储时代留下的）——
    #   真 TeamStore 的 id 是随机的，所以这里把它**固定成 g1**，免得请求打到不存在的群（本班踩过）
    gs = st.groups()
    gs[0]["id"] = "g1"
    st._write(st.root / "groups.json", gs)
    g = st.get_group("g1")
    monkeypatch.setattr(m, "_team_store", st)

    got: list[tuple[str, dict]] = []
    real_append = st.append

    def _append(gid, **kw):
        got.append((gid, kw))
        return real_append(gid, **kw)

    monkeypatch.setattr(st, "append", _append)

    async def _fake_plan(leader_emp, prompt):
        return '[{"name":"小前","task":"写点单页"},{"name":"小后","task":"写订单接口"}]'

    monkeypatch.setattr(m, "_leader_plan", _fake_plan)

    dispatched: list[tuple[str, str]] = []

    def _fake_dispatch(gid, gg, name, emp, task_text, relay_pos=None, leader_name=None):
        dispatched.append((name, task_text))
        return type("T", (), {"id": f"task_{len(dispatched):04d}"})()

    monkeypatch.setattr(m, "_dispatch_to_employee", _fake_dispatch)
    monkeypatch.setattr(m, "_spawn_bg", lambda coro: coro.close())

    # ★ 点名 / 广播走的是**另一条派发路径**：直接 `_launch_task`（不是 _dispatch_to_employee）
    #   —— 这正是台账里记过的"同一段逻辑两处实现"（现在两条路都有测试兜着了）
    def _fake_launch(text, task_id=None, **kw):
        dispatched.append(("(点名/广播)", text))
        return type("T", (), {"id": f"task_20261005_{len(dispatched):04x}"})()

    monkeypatch.setattr(m, "_launch_task", _fake_launch)
    monkeypatch.setattr(m, "create_provider", lambda mc: object())      # 别真建 provider
    return {"store": st, "members": [e1, e2, e3], "group": g, "gid": g["id"],
            "feed": got, "dispatched": dispatched}


def _say(text: str):
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        return c.post("/api/v1/team/groups/g1/say", json={"text": text})


def test_leader_mode_dispatches_real_tasks_to_each_member(team):
    """★ 核心：组长模式下，不点名的一句目标 → 给每个成员派一个**真任务**（带 task_id）。"""
    r = _say("帮我做个外卖小程序")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "leader", body
    names = [d["name"] for d in body["dispatched"]]
    assert names == ["小前", "小后"], body
    assert all(d["task_id"].startswith("task_") for d in body["dispatched"]), body
    # 群里能看到"正在拆解"与"分工完成"两条（用户要的就是这个可读的过程）
    texts = [kw.get("text", "") for _, kw in team["feed"]]
    assert any("拆解分工" in t for t in texts), texts
    done = [t for t in texts if "分工完成" in t]
    assert done and "小前" in done[0] and "小后" in done[0], texts


def test_leader_mode_sends_each_member_exactly_one_task(team):
    _say("帮我做个外卖小程序")
    assert [n for n, _ in team["dispatched"]] == ["小前", "小后"], team["dispatched"]
    assert "点单页" in team["dispatched"][0][1] and "订单接口" in team["dispatched"][1][1]


def test_at_mention_bypasses_the_leader(team):
    """点名就点名：@了一个人，就不该再触发组长全员拆解（否则一个人被派两次）。"""
    r = _say("@小前 你先出个原型")
    body = r.json()
    assert body.get("mode") != "leader", body
    # 点名走的是 _launch_task 那条路（不是 _dispatch_to_employee），所以这里看派发出去的**文本**
    texts = [t for _, t in team["dispatched"]]
    assert len(texts) == 1 and "@小前" in texts[0], team["dispatched"]


def test_unparseable_plan_says_so_instead_of_silently_dropping(monkeypatch, team):
    """组长吐了个解析不出来的东西 ⇒ 群里必须说清楚（用户要能看到"为什么没派出去"）。

    ★ 2026-10-05 用户实测后升级了这条：原来只发一句"未能产出有效的分工单"，
      用户干等一场、也不知道组长到底写了什么 ⇒ 现在**把原文贴进群**并给出手动派的出路。
    """
    monkeypatch.setattr(m._team_store, "parse_leader_plan", lambda raw: [])
    body = _say("帮我做个外卖小程序").json()
    assert body["dispatched"] == 0
    texts = [kw.get("text", "") for _, kw in team["feed"]]
    assert any("不是可解析的分工单" in t for t in texts), texts
    assert any("@某人 做什么" in t for t in texts), "没给用户手动派的出路"
    assert any("我的分工计划" in t for t in texts), "组长的原话没贴出来"


def test_missing_leader_is_reported(monkeypatch, team):
    """群上记的组长已经不存在 ⇒ 必须明说，而不是静默什么都不做。

    ★ 真存储下"改快照"没用（本班踩过）：直接把群上的 leader 写成一个不存在的 id。
      （只删员工是不够的 —— 删员工时存储会把群上的 leader 一起清掉，那样走的是
       "没设组长"的普通路径，测不到这条分支。）
    """
    gs = team["store"].groups()
    gs[0]["leader"] = "emp_已删除"
    team["store"]._write(team["store"].root / "groups.json", gs)
    body = _say("帮我做个外卖小程序").json()
    assert body["dispatched"] == 0
    texts = [kw.get("text", "") for _, kw in team["feed"]]
    assert any("组长不存在" in t for t in texts), texts


def test_broadcast_mode_sends_the_same_task_to_everyone(monkeypatch, team):
    """广播模式：全员各领同一件事（与组长的"拆分"是两种意图）。"""
    team["store"].set_mode("g1", "broadcast")
    body = _say("都去看一眼竞品").json()
    assert body.get("dispatched"), body
    assert len(team["dispatched"]) >= 2, team["dispatched"]


def test_one_member_failure_does_not_kill_the_others(monkeypatch, team):
    """★ 韧性：某个成员派发失败（模型/网络抖动）不该让整次分工归零。"""
    calls: list[str] = []

    def _flaky(gid, g, name, emp, task_text, relay_pos=None, leader_name=None):
        calls.append(name)
        if name == "小前":
            raise RuntimeError("派发失败：模型 429")
        return type("T", (), {"id": "task_20261005_beef"})()

    monkeypatch.setattr(m, "_dispatch_to_employee", _flaky)
    body = _say("帮我做个外卖小程序").json()
    assert calls == ["小前", "小后"], calls
    assert [d["name"] for d in body["dispatched"]] == ["小后"], body
