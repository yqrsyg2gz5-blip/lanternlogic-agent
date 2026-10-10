# -*- coding: utf-8 -*-
"""示例数据 —— 用**真 FsStore**当考卷 ✓（不 mock ✗：mock 掉的正是最危险的那部分 ✓）

三条要害：
  ① **空库才种** ✓
  ② ★ **有任何任务（含回收站）⇒ 一个字都不许写** ✗ —— 这是保护用户真实数据的那条 ✓
  ③ 种出来的每条都要带「【示例】」标记 ✓（用户一眼能认出、能直接删 ✓）
"""
from __future__ import annotations

import pathlib

from app.demo_seed import MARK, seed
from app.store import FsStore


def _store(tmp_path) -> FsStore:
    return FsStore(tmp_path)


def test_seeds_into_an_empty_store(tmp_path):
    """空库 ⇒ 种得进去 ✓ 且每条都带【示例】标记 ✓"""
    st = _store(tmp_path)
    got = seed(st)
    assert got["ok"] is True, got
    assert len(got["created"]) == 4, got
    tasks = st.load_index()
    assert len(tasks) == 4
    for t in tasks:
        assert t.title.startswith(MARK), f"缺示例标记（用户就认不出是假数据 ✗）：{t.title}"
        assert "示例" in t.title


def test_refuses_when_the_user_has_any_task(tmp_path):
    """★ 最重要的一条：**用户已有任务 ⇒ 一个字都不许写** ✗"""
    st = _store(tmp_path)
    before = st.load_index()
    from app.schemas import TaskSummary
    st.save_index([TaskSummary(id="task_real_1", title="用户自己的真任务",
                               status="done", created_at="2026-10-09T00:00:00",
                               updated_at="2026-10-09T00:00:00")])
    got = seed(st)
    assert got["ok"] is False, f"居然往有数据的库里种了 ✗：{got}"
    assert got["created"] == []
    titles = [t.title for t in st.load_index()]
    assert titles == ["用户自己的真任务"], f"用户数据被改动了 ✗：{titles}"
    assert before == []          # 前置条件复核 ✓


def test_refuses_when_only_trash_exists(tmp_path):
    """★ 连回收站里有东西也拒绝 ✗（用户删过东西 ⇒ 说明这不是新装 ✓）"""
    st = _store(tmp_path)
    trash = pathlib.Path(st.tasks_dir) / "_deleted_" / "20261009-1200" / "task_old"
    trash.mkdir(parents=True, exist_ok=True)
    (trash / "events.jsonl").write_text("{}\n", encoding="utf-8")
    got = seed(st)
    assert got["ok"] is False, got
    assert st.load_index() == []


def test_seed_is_idempotent_by_refusal(tmp_path):
    """种过一次之后再种 ⇒ 拒绝（第二次就不是空库了 ✓ 不会变成 8 条 ✓）"""
    st = _store(tmp_path)
    assert seed(st)["ok"] is True
    second = seed(st)
    assert second["ok"] is False, second
    assert len(st.load_index()) == 4, "被种了两遍 ✗"


def test_api_endpoint_refuses_on_a_non_empty_store(tmp_path, monkeypatch):
    """★ 接口层也要守同一条边界 ✗（薄包装也测 ✓ —— 不然将来有人绕过模块直接写 ✓）

    做法：把 app 的 store 换成临时 FsStore ✓ 先放一条"真任务" ⇒ 接口必须拒绝 ✓
    """
    from fastapi.testclient import TestClient

    from app import main as m

    st = _store(tmp_path)
    from app.schemas import TaskSummary
    st.save_index([TaskSummary(id="task_real_1", title="真任务", status="done",
                               created_at="2026-10-09T00:00:00", updated_at="2026-10-09T00:00:00")])
    monkeypatch.setattr(m, "store", st, raising=False)
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.post("/api/v1/demo/seed")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False, f"有真数据的库居然被种了 ✗：{body}"
    assert [t.title for t in st.load_index()] == ["真任务"], "用户数据被动了 ✗"


def test_api_endpoint_seeds_into_empty_store(tmp_path, monkeypatch):
    """空库 ⇒ 接口种入 4 条 ✓"""
    from fastapi.testclient import TestClient

    from app import main as m

    st = _store(tmp_path)
    monkeypatch.setattr(m, "store", st, raising=False)
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.post("/api/v1/demo/seed")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert len(st.load_index()) == 4


def test_writes_a_readable_note_in_each_workspace(tmp_path):
    """每条示例的**工作区**里放一份说明 ✓（用户点进去不是一片空白 ✓）"""
    st = _store(tmp_path)
    seed(st)
    for t in st.load_index():
        note = pathlib.Path(st.workspace_dir(t.id)) / "说明.txt"
        assert note.is_file(), f"{t.id} 的工作区里没有说明 ✗"
        body = note.read_text(encoding="utf-8")
        assert MARK in body and "回收站" in body, "说明里要告诉用户'这是示例、能删、可恢复' ✓"
