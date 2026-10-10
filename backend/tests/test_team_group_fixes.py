"""二十六轮第 7 批第 7a / 7b 处：群任务看门逻辑 + 团队/群聊上限拆分。

用户原话：「任务超过 30 秒未结束，交付不再自动回流。这些东西能不能就是他就不停的
提醒这个东西，能不能就是不让他这么提醒？就是提醒一条就行了」+「员工卡是不是就
可以无限添加，群聊限制 12 人」。

—— 实测发现比他说的更严重：
  ① 提醒不是"30 秒/30 分钟才开始"，而是 **`timed_out = True` 一开始就是真**
     ⇒ 任务刚派出去 5 秒就喊「超过 30 分钟未结束」，此后**每 5 秒一条**，
     30 分钟能刷 360 条；
  ② 同一段逻辑在 `_dispatch_to_employee` 与 `POST groups/{gid}/say` 里**各抄了一份**，
     所以 bug 也是两份；
  ③ "团队成员上限"与"单个群成员上限"**共用同一个 MAX_MEMBERS=12**
     ⇒ 加第 13 个员工就被拒（而员工数跟"一个群坐得下几个人"没关系）。

修复：合并成唯一的 `_watch_group_delivery`（超时只提醒一次、结束后安静回流），
      并把 `MAX_EMPLOYEES`(200) 与 `MAX_MEMBERS`(12) 拆开。
"""
from __future__ import annotations

import asyncio
import pathlib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main as m
from app.team import MAX_EMPLOYEES, MAX_MEMBERS, TeamStore


@pytest.fixture()
def group_sink(monkeypatch):
    """把群聊追加收集起来（不碰真实群数据）。"""
    got: list[tuple[str, dict]] = []
    monkeypatch.setattr(m, "_team_store", SimpleNamespace(
        append=lambda gid, **kw: got.append((gid, kw)),
        # ★ 2026-10-05：超时提示改成**落盘去重**（同一任务只发一条）——
        #   这个假 store 也得提供 has_notice，否则看门逻辑一调就 AttributeError。
        has_notice=lambda gid, task_id, needle: any(
            kw.get("task_id") == task_id and needle in str(kw.get("text") or "") for _g, kw in got
        ),
    ))
    return got


# ══════════════════ 7a：看门逻辑 ══════════════════


def test_timeout_notice_fires_exactly_once(monkeypatch, group_sink):
    """★ 核心锚点：任务一直不结束 ⇒ 满 timeout 后【恰好一条】提醒。

    老逻辑在同一条件下会产出多条（每 5 秒一条）—— 这是用户报的那个刷屏。
    """
    class _T:
        status = "running"

    monkeypatch.setattr(m, "tasks", {"t1": _T()})
    monkeypatch.setattr(m, "store", SimpleNamespace(read_events=lambda tid: []))

    asyncio.run(m._watch_group_delivery("t1", "甲", "g1", timeout_s=0.06, poll_s=0.01))

    notices = [kw for _, kw in group_sink if "超过 30 分钟" in str(kw.get("text", ""))]
    assert len(notices) == 1, f"超时提醒必须恰好 1 条，实际 {len(notices)} 条"
    assert notices[0]["from"] == "sys:甲"
    assert "只提醒一次" in str(notices[0]["text"])


def test_delivery_returned_and_no_timeout_notice(monkeypatch, group_sink):
    """任务正常结束 ⇒ 交付回流一次，且**不发**超时提醒。"""
    class _T:
        status = "done"

    monkeypatch.setattr(m, "tasks", {"t1": _T()})
    monkeypatch.setattr(m, "store", SimpleNamespace(read_events=lambda tid: [
        SimpleNamespace(type="message", payload={"role": "assistant", "text": "活干完了"}),
    ]))

    asyncio.run(m._watch_group_delivery("t1", "甲", "g1", timeout_s=5, poll_s=0.01))

    texts = [str(kw.get("text", "")) for _, kw in group_sink]
    assert len(texts) == 1, f"只应回流一条交付，实际 {len(texts)}：{texts}"
    assert "活干完了" in texts[0]
    assert not any("超过 30 分钟" in t for t in texts), "正常结束不该发超时提醒"


def test_rearmed_watcher_keeps_the_leader_name():
    """★★ 2026-10-06 根因修复（现场："终验交付了，群里没反应，我发一句话才补上" ✗）。

    看门者满 30 分钟会**续挂**一轮 ✓ —— 但续挂时**漏传了 `leader_name`** ✗
    ⇒ 续挂后的看门者交付时走"没有组长"那条分支 ⇒ **不验收、不推进波次** ✗✗，
    那一项就一直停在 running，直到有人再往群里说一句话触发对账才被补认领 ✓
    （用户看到的就是"偶尔认领不到"）。这条元锚点把"续挂要带 leader_name"钉死 ✓。
    """
    import inspect

    src = inspect.getsource(m._watch_group_delivery)
    assert "leader_name=leader_name" in src, (
        "续挂看门者时没传 leader_name ⇒ 交付不会被验收/推进（就是这个 bug）✗"
    )
    tail = src.split("_GROUP_WATCH_MAX_REARMS")[-1]        # 只看"续挂"那一段
    assert "leader_name=leader_name" in tail, tail[:300]


def test_no_inline_duplicate_watch_remains():
    """元锚点：两处派发必须都走同一个 `_watch_group_delivery`，不许再内联抄一份。

    老 bug 之所以有两份，就是因为内联复制。这条把"唯一实现"钉死：
    源码里应恰好出现 3 次（1 处定义 + 2 处调用），且老的 `timed_out = True` 不得复活。
    """
    src = pathlib.Path(m.__file__).read_text(encoding="utf-8")
    # ★ 2026-10-05：3 → 4 —— 第三处调用是**「接着跑」（P0-4）**：续跑后必须把看门重新挂上，
    #   否则续跑出来的交付没人认领（波次/接力的推进就断了）。
    # ★ 2026-10-05 晚：4 → 5 —— 第四处调用是**看门器续挂**（全量试跑抓到的真 bug：
    #   任务超过 30 分钟后群里不再提示审批 ⇒ 它卡在审批上时用户看起来就是"它停了"）。
    # ★ 2026-10-06：5 → 7 —— 第五、六处调用是**非组长模式的「项目终验门」** ✓：
    #   广播批次全交齐、以及接力最后一棒交完，都会自动派一道"项目验收" ✓
    #   （"做完 ≠ 能跑"与模式无关 ✓）。
    assert src.count("_watch_group_delivery(") == 7, (
        f"应为 1 处定义 + 6 处调用（派发 / 点名广播 / 接着跑 / 超时续挂 / "
        f"广播终验门 / 接力终验门），实际 {src.count('_watch_group_delivery(')} 次"
    )
    # ★ 补这一条是因为本班真的踩过：合并时漏删了一行遗留的 `_spawn_bg(_watch(...))`，
    #   指向已删除的函数 ⇒ 那段代码一跑就 NameError。**pytest 全绿、pyflakes 才抓到**
    #   （所以守门里 pyflakes 不是可选项）。这里把它钉死。
    assert "_watch(" not in src, "还有指向已删除 `_watch` 的调用 —— 那段代码会 NameError"
    # 允许出现在解释 bug 的注释/docstring 里，但不允许出现在可执行代码里
    code_lines = [
        ln for ln in src.splitlines()
        if "timed_out" in ln and not ln.lstrip().startswith("#") and "←" not in ln
    ]
    assert not code_lines, f"老的 timed_out 写法复活了：{code_lines[:3]}"


# ══════════════════ 7b：两个上限拆开 ══════════════════


def test_employee_limit_is_not_group_limit(tmp_path):
    """★ 核心锚点：加第 13 个员工必须成功；但 13 人的群仍要被拒。"""
    ts = TeamStore(tmp_path / "team")
    assert MAX_EMPLOYEES > MAX_MEMBERS, "团队总人数上限必须大于单个群的上限"

    ids = []
    for i in range(MAX_MEMBERS + 1):        # 13 人 —— 老代码在这里就抛了
        ids.append(ts.add_employee({"name": f"员工{i}"})["id"])

    assert len(ts.employees()) == MAX_MEMBERS + 1, (
        f"团队应允许超过 {MAX_MEMBERS} 名员工（专家模式会持续加人），"
        f"实际只有 {len(ts.employees())} 人"
    )

    with pytest.raises(ValueError, match="群成员最多"):
        ts.create_group("大群", ids)        # 群聊仍限 12 ✓


def test_employee_hard_cap_still_exists(tmp_path):
    """防呆上限仍在：不可能无限加（防误操作把内存/文件撑爆）。"""
    ts = TeamStore(tmp_path / "team")
    ts._write(ts.root / "employees.json",
              [{"id": f"emp_{i}", "name": f"e{i}"} for i in range(MAX_EMPLOYEES)])
    with pytest.raises(ValueError, match="最多 200 人"):
        ts.add_employee({"name": "再来一个"})


def test_endpoints_expose_two_different_caps():
    """接口层分别暴露两个上限 —— 前端才能正确显示/限制。"""
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        emp = c.get("/api/v1/team/employees").json()
        grp = c.get("/api/v1/team/groups").json()
    assert emp["max"] == MAX_EMPLOYEES, f"员工接口的 max 应为 {MAX_EMPLOYEES}，实际 {emp['max']}"
    assert grp["max_members"] == MAX_MEMBERS, (
        f"群聊接口应暴露 max_members={MAX_MEMBERS}，实际 {grp.get('max_members')}"
    )


# ══════════════════ 7c：建群后也能改派发模式 ══════════════════
# 用户原话：「这几个模式（点名派发/全员广播/组长拆解）……如果创建群聊之后，
#            然后在群聊里都可以选择可不可以」—— 此前 mode 只能在建群那一刻定死。


def test_set_mode_after_creation(tmp_path):
    """★ 核心锚点：建群后能改模式，claim_enabled 跟着走，且真落盘。"""
    ts = TeamStore(tmp_path / "team")
    a = ts.add_employee({"name": "甲"})
    b = ts.add_employee({"name": "乙"})
    g = ts.create_group("小群", [a["id"], b["id"]], mode="manual")
    assert g["mode"] == "manual" and g["claim_enabled"] is False

    g2 = ts.set_mode(g["id"], "broadcast")
    assert g2["mode"] == "broadcast"
    assert g2["claim_enabled"] is True, "广播模式必须开认领"
    assert ts.get_group(g["id"])["mode"] == "broadcast", "必须落盘，不能只改内存"

    g3 = ts.set_mode(g["id"], "MANUAL")        # 大小写不敏感
    assert g3["mode"] == "manual" and g3["claim_enabled"] is False


def test_set_mode_leader_requires_leader(tmp_path):
    """切 leader 模式但没有组长 ⇒ 明确报错，不留"组长模式却没组长"的空壳。

    空壳最危险：派发时会静默走 manual 分支，用户以为在用组长拆解。
    """
    ts = TeamStore(tmp_path / "team")
    a = ts.add_employee({"name": "甲"})
    g = ts.create_group("小群", [a["id"]], mode="manual")
    with pytest.raises(ValueError, match="先给这个群指定一名组长"):
        ts.set_mode(g["id"], "leader")
    ts.set_leader(g["id"], a["id"])
    assert ts.set_mode(g["id"], "leader")["mode"] == "leader"


def test_set_mode_rejects_bad_values(tmp_path):
    ts = TeamStore(tmp_path / "team")
    a = ts.add_employee({"name": "甲"})
    g = ts.create_group("小群", [a["id"]])
    with pytest.raises(ValueError, match="mode 必须是"):
        ts.set_mode(g["id"], "whatever")
    with pytest.raises(ValueError, match="群不存在"):
        ts.set_mode("grp_nope", "manual")


def test_group_update_endpoint(tmp_path, monkeypatch):
    """接口层：PUT /team/groups/{gid} 改模式。

    ★ 用临时 TeamStore 替换 _team_store —— 绝不碰用户真实的 groups.json。
    """
    ts = TeamStore(tmp_path / "team")
    a = ts.add_employee({"name": "甲"})
    g = ts.create_group("端点群", [a["id"]], mode="manual")
    monkeypatch.setattr(m, "_team_store", ts)

    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        ok = c.put(f"/api/v1/team/groups/{g['id']}", json={"mode": "broadcast"})
        assert ok.status_code == 200, ok.text
        assert ok.json()["mode"] == "broadcast"

        bad = c.put(f"/api/v1/team/groups/{g['id']}", json={"mode": "nope"})
        assert bad.status_code == 422, bad.text

        miss = c.put("/api/v1/team/groups/grp_nope", json={"mode": "manual"})
        assert miss.status_code == 422, miss.text

        # 空 body = 只读回当前群（幂等，便于前端刷新）
        cur = c.put(f"/api/v1/team/groups/{g['id']}", json={})
        assert cur.status_code == 200 and cur.json()["mode"] == "broadcast"
