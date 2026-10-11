"""二十六轮第 7 批第 7d 处：删除任务必须**真正删掉落盘文件**。

真实现状（改前）：
    `DELETE /api/v1/tasks/{id}` 只 `tasks.pop()` + 存索引，**一个文件都不删**。
    实测后果：`data/tasks/` 里积了 **213 个"界面上已删、硬盘还在"的目录共 470.5 MB**，
    其中有一个明文出现过 API Key 的任务（用户以为删了，其实原封不动躺在盘上）。

本文件把三件事钉住：
    ① 删除**真的删文件**（并且回报释放了多少字节）；
    ② **不可逆操作的护栏**逐条生效（路径穿越 / 符号链接 / 越界 / 幂等）；
    ③ 误伤防护：删一个任务不许碰到别的任务。

护栏比功能重要 —— 这是本文件里测试最多的一类。
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app import main as m
from app.schemas import TaskSummary
from app.store import FsStore

TID = "task_20261004_d3l7"


def _mk_task_dir(store: FsStore, tid: str = TID, n_bytes: int = 1200) -> None:
    d = store.tasks_dir / tid
    (d / "workspace").mkdir(parents=True, exist_ok=True)
    (d / "snapshots").mkdir(parents=True, exist_ok=True)
    (d / "events.jsonl").write_text('{"seq":1}\n', encoding="utf-8")
    (d / "history.json").write_text("[]", encoding="utf-8")
    (d / "workspace" / "big.bin").write_bytes(b"x" * n_bytes)


# ══════════════════ ① 真的删文件 ══════════════════


def test_delete_task_files_removes_directory(tmp_path):
    store = FsStore(tmp_path / "data")
    _mk_task_dir(store)
    assert (store.tasks_dir / TID).is_dir()

    r = store.delete_task_files(TID)

    assert r["deleted"] is True, r
    assert not (store.tasks_dir / TID).exists(), "目录还在 —— 文件没被删掉"
    assert r["bytes"] >= 1200, f"应回报释放的字节数，实际 {r['bytes']}"


def test_delete_endpoint_really_deletes(tmp_path, monkeypatch):
    """★ 接口层：DELETE 之后磁盘上必须真的没有这个任务目录。"""
    store = FsStore(tmp_path / "data")
    _mk_task_dir(store)
    task = TaskSummary(id=TID, title="待删", created_at="2026-10-04T00:00:00Z",
                       updated_at="2026-10-04T00:00:00Z")
    monkeypatch.setattr(m, "store", store)
    monkeypatch.setattr(m, "tasks", {TID: task})
    monkeypatch.setattr(m, "runs", {})
    monkeypatch.setattr(m, "_save_index", lambda: None)   # 不碰真实索引

    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        resp = c.delete(f"/api/v1/tasks/{TID}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert "files" in body, f"删除回执必须含 files 字段（本次到底删没删文件）：{body}"
    assert body["files"]["deleted"] is True, body
    assert not (store.tasks_dir / TID).exists(), "接口返回成功，但文件还在"


def test_delete_while_running_keeps_files(tmp_path, monkeypatch):
    """任务仍在跑 ⇒ 只取消、**不删文件**（正在写盘，删了会打架），并如实说明原因。"""
    store = FsStore(tmp_path / "data")
    _mk_task_dir(store)
    task = TaskSummary(id=TID, title="运行中", created_at="2026-10-04T00:00:00Z",
                       updated_at="2026-10-04T00:00:00Z")

    class _FakeAio:
        def done(self) -> bool:
            return False

    class _FakeRun:
        aio_task = _FakeAio()
        seq = 0                      # 接口链路上会读 run.seq（事件序号游标）
        cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

    fake = _FakeRun()
    monkeypatch.setattr(m, "store", store)
    monkeypatch.setattr(m, "tasks", {TID: task})
    monkeypatch.setattr(m, "runs", {TID: fake})
    monkeypatch.setattr(m, "_save_index", lambda: None)

    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        body = c.delete(f"/api/v1/tasks/{TID}").json()

    assert fake.cancelled is True, "仍在运行的任务必须先取消"
    assert body["files"]["deleted"] is False, body
    assert "仍在运行" in body["files"]["reason"], body
    assert (store.tasks_dir / TID).is_dir(), "运行中的任务文件不该被删"


# ══════════════════ ② 不可逆操作的护栏 ══════════════════


@pytest.mark.parametrize("bad", ["../evil", "..\\evil", "a/b", "a\\b", ".", "..", ""])
def test_delete_refuses_traversal(tmp_path, bad):
    """路径穿越 / 空名 / 点号 —— 一律拒绝（绝不允许删到 tasks_dir 之外）。"""
    store = FsStore(tmp_path / "data")
    with pytest.raises(ValueError):
        store.delete_task_files(bad)


def test_delete_refuses_symlink(tmp_path):
    """符号链接/Junction 一律拒绝删除（宁可报错，也不顺着链接删到别处）。

    Windows 上建符号链接可能要权限 ⇒ 建不了就跳过（跳过也要说明原因）。
    """
    store = FsStore(tmp_path / "data")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "important.txt").write_text("别删我", encoding="utf-8")
    link = store.tasks_dir / "task_20261004_lnk0"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"本环境建不了符号链接（{type(e).__name__}）——护栏逻辑仍由其它用例覆盖")

    with pytest.raises(ValueError, match="符号链接"):
        store.delete_task_files("task_20261004_lnk0")
    assert (outside / "important.txt").exists(), "★ 链接目标被删了 —— 护栏失效"


def test_delete_missing_dir_is_idempotent(tmp_path):
    """目录不存在 ⇒ deleted=False，不抛错（重复删除不该报 500）。"""
    store = FsStore(tmp_path / "data")
    r = store.delete_task_files(TID)
    assert r["deleted"] is False and r["bytes"] == 0


def test_delete_other_task_untouched(tmp_path):
    """★ 误伤防护：删 A 不许碰到 B。"""
    store = FsStore(tmp_path / "data")
    other = "task_20261004_k33p"
    _mk_task_dir(store, TID)
    _mk_task_dir(store, other)

    store.delete_task_files(TID)

    assert not (store.tasks_dir / TID).exists()
    assert (store.tasks_dir / other / "workspace" / "big.bin").exists(), "误删了别的任务"


def test_delete_never_removes_tasks_root(tmp_path):
    """任何情况下都不许删掉 tasks_dir 本身。"""
    store = FsStore(tmp_path / "data")
    assert store.tasks_dir.is_dir()
    with pytest.raises(ValueError):
        store.delete_task_files(".")
    assert store.tasks_dir.is_dir(), "★ tasks 根目录被删了 —— 灾难级"


# ══════════════════ ③ 孤儿清单（只列不删） ══════════════════


def test_orphan_listing_only_lists(tmp_path):
    """孤儿清单只列不删；且必须排除"仍在索引里"的任务。"""
    store = FsStore(tmp_path / "data")
    keep = "task_20261004_k33p"
    _mk_task_dir(store, keep, n_bytes=10)
    _mk_task_dir(store, TID, n_bytes=2000)
    store.save_index([TaskSummary(id=keep, title="在用", created_at="2026-10-04T00:00:00Z",
                                  updated_at="2026-10-04T00:00:00Z")])

    orphans = store.orphan_task_dirs()

    ids = [o["id"] for o in orphans]
    assert TID in ids, f"不在索引里的目录应被列为孤儿：{ids}"
    assert keep not in ids, "★ 仍在索引里的任务被误列为孤儿（照此清理会删掉在用的任务）"
    assert (store.tasks_dir / TID).is_dir(), "清单只读，不许删任何东西"
    assert next(o["bytes"] for o in orphans if o["id"] == TID) >= 2000


def test_orphan_listing_refuses_when_index_empty(tmp_path, capsys):
    """★★ 关键护栏锚点：索引为空却有任务目录 ⇒ **拒绝产出清单**。

    为什么必须这样：`load_index()` 在 index.json 缺失/损坏时会**静默返回 []**
    （损坏文件被改名）。若照"不在索引里就是孤儿"去算，此时**所有任务都会被判成孤儿**
    —— 拿这份清单清理等于**删光全部任务**。所以宁可什么都不列。
    """
    store = FsStore(tmp_path / "data")
    _mk_task_dir(store, TID, n_bytes=500)
    _mk_task_dir(store, "task_20261004_k33p", n_bytes=500)
    # index.json 不存在（= load_index() 返回 []），但盘上有 2 个任务目录

    orphans = store.orphan_task_dirs()

    assert orphans == [], f"★ 索引为空时不许产出孤儿清单（会误判为全量孤儿），实际列出 {len(orphans)} 个"
    out = capsys.readouterr().out
    assert "拒绝生成孤儿清单" in out, f"应留下告警，实际输出：{out[:200]}"
    assert (store.tasks_dir / TID).is_dir(), "只读操作不许删东西"
