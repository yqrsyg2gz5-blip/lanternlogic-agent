# -*- coding: utf-8 -*-
"""★ 第 10 项 · 多审批汇总（后端那一半）—— 2026-10-07。

## 要解决的真问题（用户在真群里遇到过）

一个群同时开几个任务时，审批卡片会**一条条刷屏** ✗：
  · 他得**一个个点** ✓
  · **看不出还有几条在等** ✗（点完一条才发现下面还有 ✓）
  · 更糟：**几个任务各自卡着** ✓ 他以为"就这一个" ✓ 其实后面排着仨 ✓

## 这个接口给的是"还差几个决定"

`GET /api/v1/team/groups/{gid}/approvals` ⇒ `{count, approvals:[{task_id, call_id, title, command}]}` ✓

★ 两条口径：
  · 群里的任务 = 这个群 feed 里出现过的 `task_id` ✓（与 `_gid_of_task` **同源、反着走** ✓
    不另立一套 ✗ —— 本仓栽过多次的"两处口径打架"✓）
  · 命令取自 `approval.command_of` ✓ = **服务端自己记的那份** ✓（不是界面传的 ✓ 所以可信 ✓）
    —— 这一条正好用上了本轮刚补的"账本缺命令"那个修复 ✓
"""
from __future__ import annotations

import asyncio
import pathlib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main as m


@pytest.fixture()
def fake_group(monkeypatch):
    """造一个只有两个任务的群 ✓（不碰真数据 ✓ 用 monkeypatch 挡掉存储 ✓）。"""
    feed = [
        {"task_id": "task_A", "text": "甲开工"},
        {"task_id": "task_B", "text": "乙开工"},
        {"text": "没有 task_id 的系统消息（不该被算成任务 ✓）"},
    ]
    # ★ 假函数要**接得住任意参数** ✓ —— 第一版写死 `lambda gid:` ✗
    #   而 app 的**启动流程**也会来调它们（还可能带 limit 之类的参数 ✓）
    #   ⇒ 启动阶段直接炸 ⇒ `with TestClient(...)` 连门都进不去 ✓（本班实测 ✓）
    monkeypatch.setattr(m._team_store, "get_group",
                        lambda gid, *a, **k: {"id": gid} if gid == "grp_t" else None)
    monkeypatch.setattr(m._team_store, "feed",
                        lambda gid, *a, **k: feed if gid == "grp_t" else [])
    return "grp_t"


def _pending(ids: list[tuple[str, str]]) -> None:
    """把 (task, call) 塞进"正在等审批"那份表里 ✓（真表 ✓ 不是假的 ✓）。"""
    loop = asyncio.new_event_loop()
    try:
        for tid, cid in ids:
            fut = loop.create_future()
            m.approval._pending[(tid, cid)] = fut
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _clean_pending():
    yield
    m.approval._pending.clear()
    m.approval._cmds.clear()


def test_it_counts_every_waiting_approval(fake_group):
    """★★ **必须把"还差几个决定"数对** ✓ —— 这正是用户看不到的那个数 ✓
    （回滚实验：把汇总改成只看第一个任务 ⇒ 本组必红 ✓）"""
    _pending([("task_A", "call1"), ("task_A", "call2"), ("task_B", "call3")])
    # ★ 假任务要给足字段 ✓ —— app 启动时看门狗会扫 `tasks` ✓ 少了 `status` 就炸 ✓（本班实测 ✓）
    m.tasks["task_A"] = SimpleNamespace(id="task_A", title="整理下载目录", status="done")
    m.tasks["task_B"] = SimpleNamespace(id="task_B", title="写周报", status="done")
    try:
        with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
            r = c.get("/api/v1/team/groups/grp_t/approvals")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["count"] == 3, f"数是错的 ✗（应该 3 条，得到 {body['count']}）"
        assert {a["task_id"] for a in body["approvals"]} == {"task_A", "task_B"}, body
        assert {a["call_id"] for a in body["approvals"]} == {"call1", "call2", "call3"}, body
    finally:
        m.tasks.pop("task_A", None)
        m.tasks.pop("task_B", None)


def test_the_command_comes_along(fake_group):
    """★ 汇总里要带**那条命令** ✓ —— 不然用户还是得点进去才知道要批什么 ✓
    （命令来自服务端记的那份 ✓ 见 `note_command` ✓）。"""
    _pending([("task_A", "call1")])
    m.approval.note_command("task_A", "call1", "Remove-Item D:\\旧备份\\* -Recurse")
    m.tasks["task_A"] = SimpleNamespace(id="task_A", title="清旧备份", status="done")
    try:
        with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
            body = c.get("/api/v1/team/groups/grp_t/approvals").json()
        assert body["count"] == 1
        assert "旧备份" in body["approvals"][0]["command"], f"没带命令 ⇒ 还是得点进去看 ✗：{body}"
    finally:
        m.tasks.pop("task_A", None)


def test_empty_group_is_honest(fake_group):
    """★ 没人在等 ⇒ 返回 0 条 ✓（不是报错 ✗ 也不是假装有 ✓）。"""
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        body = c.get("/api/v1/team/groups/grp_t/approvals").json()
    assert body["count"] == 0 and body["approvals"] == []


def test_unknown_group_is_404(fake_group):
    """★ 群不存在就说 404 ✓（与其它群接口一致 ✓）。"""
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        assert c.get("/api/v1/team/groups/grp_没有/approvals").status_code == 404


def test_it_uses_the_same_task_source_as_the_reverse_lookup():
    """★ 口径**同源** ✓ —— 群的成员任务用 feed 里的 `task_id`（与 `_gid_of_task` 反着走 ✓）
    不另立一套 ✗（本仓栽过多次的"两处口径打架"✓）。"""
    src = pathlib.Path(m.__file__).read_text("utf-8")
    i = src.find("async def group_pending_approvals")
    assert i > 0
    seg = src[i : i + 1800]
    assert "_team_store.feed(gid)" in seg, "没从 feed 取任务 ⇒ 与 `_gid_of_task` 不是同一口径 ✗"
    assert "get(\"task_id\")" in seg, "没按 task_id 归组 ✗"
    assert "approval.pending_for" in seg, "没走审批管理器那份待批表 ✗（自己另记一份必然打架 ✓）"
