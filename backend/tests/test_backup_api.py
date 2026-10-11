# -*- coding: utf-8 -*-
"""备份/恢复的**接口层** —— 薄包装也要测 ✓（不然将来谁绕过模块直接写 ✓ 就没人拦了 ✓）

三条要害在接口层的体现：
  ① 备份能下载 ✓ 且是**有效 zip + 有清单** ✓
  ② ★ 有任务在跑 ⇒ **恢复必须拒绝** ✗（409 ✓）—— 一边跑一边换数据目录必然写坏 ✓
  ③ 恢复成功后**原数据挪走的那份还在** ✓（这是"恢复失败也不丢数据"的底线 ✓）
"""
from __future__ import annotations

import io
import json
import zipfile

from fastapi.testclient import TestClient

from app import main as m
from app.backup import MANIFEST
from app.store import FsStore


def _client(tmp_path, monkeypatch) -> TestClient:
    """把 app 的 store 与 _DATA_DIR 都指向临时目录 ✓（绝不碰真数据 ✓）"""
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    st = FsStore(data)
    monkeypatch.setattr(m, "store", st, raising=False)
    monkeypatch.setattr(m, "_DATA_DIR", data, raising=False)
    return TestClient(m.app, base_url="http://127.0.0.1:8642")


def test_backup_download_returns_a_valid_zip_with_manifest(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    (tmp_path / "data" / "memory").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "memory" / "m.json").write_text("[]", encoding="utf-8")

    r = c.get("/api/v1/backup")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/zip"), r.headers.get("content-type")
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        names = z.namelist()
        assert MANIFEST in names, f"包里没有清单 ✗：{names[:5]}"
        assert "memory/m.json" in names
        json.loads(z.read(MANIFEST).decode("utf-8"))


def test_restore_refuses_while_a_task_is_running(tmp_path, monkeypatch):
    """★ 硬规矩 ②：有任务在跑 ⇒ 409 拒绝 ✗（并且**一个字都不许动** ✓）"""
    from app.schemas import TaskSummary

    c = _client(tmp_path, monkeypatch)
    m.store.save_index([TaskSummary(id="task_run_1", title="正在跑的任务", status="running",
                                    created_at="2026-10-09T00:00:00",
                                    updated_at="2026-10-09T00:00:00")])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(MANIFEST, "{}")
    r = c.post("/api/v1/backup/restore", files={"file": ("b.zip", buf.getvalue())})
    assert r.status_code == 409, f"跑着任务居然允许恢复 ✗：{r.status_code} {r.text[:120]}"
    assert "正在运行" in r.json()["detail"]
    assert len(m.store.load_index()) == 1, "拒绝时必须一个字不动 ✗"


def test_restore_end_to_end_keeps_the_old_data_aside(tmp_path, monkeypatch):
    """★ 硬规矩 ①③：恢复成功 ✓ 且原数据**挪走那份还在** ✓"""
    c = _client(tmp_path, monkeypatch)
    data = tmp_path / "data"
    (data / "memory").mkdir(parents=True, exist_ok=True)
    (data / "memory" / "old.json").write_text('{"old":1}', encoding="utf-8")

    # 先备份（此时 old.json 在包里）
    zip_bytes = c.get("/api/v1/backup").content
    # 备份之后又产生了新数据（恢复会换掉它 ✓ 但必须留住 ✓）
    (data / "memory" / "new.json").write_text('{"new":1}', encoding="utf-8")

    r = c.post("/api/v1/backup/restore", files={"file": ("b.zip", zip_bytes)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    import pathlib
    moved = pathlib.Path(body["moved_aside"])
    assert moved.is_dir() and (moved / "memory" / "new.json").is_file(), \
        "恢复把备份之后的新数据弄丢了 ✗（恢复不该等于丢数据 ✓）"
    assert (data / "memory" / "old.json").is_file(), "恢复后的数据不全 ✗"


def test_restore_rejects_a_foreign_zip(tmp_path, monkeypatch):
    """不是本应用的备份 ⇒ 400 拒绝 ✓"""
    c = _client(tmp_path, monkeypatch)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hello.txt", "hi")
    r = c.post("/api/v1/backup/restore", files={"file": ("x.zip", buf.getvalue())})
    assert r.status_code == 400, r.text
    assert MANIFEST in r.json()["detail"]
