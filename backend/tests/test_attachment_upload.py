"""附件上传接口 —— 输入区"拖文件进去"的后端半边。

**为什么**：对照 Codex / Manus 2.0 这一代，"能拖图片/文件进任务"属于标配；
本项目此前只有语音输入（`/voice`），没有通用附件上传。

**本文件重点验证安全**：文件名里带 `../` 不能写到工作区外面去。

注：任务基础设施（index / 事件）用 monkeypatch 隔离 —— 本文件测的是
**文件落盘与路径安全**，不需要真跑一个任务。
"""
from __future__ import annotations

import shutil
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.main as m
from app.main import app

CLIENT: TestClient | None = None  # fixture 内 global 赋值


@pytest.fixture(scope="module", autouse=True)
def _lifespan_client():
    """审计 §7.5：裸 TestClient 不触发 lifespan——看门狗/自动化/群回流装配路径
    完全没进测试。改为 module 级 with 上下文，启动/关闭钩子真实执行。"""
    global CLIENT
    with TestClient(app, base_url="http://127.0.0.1:8642") as c:
        CLIENT = c
        yield
TID = "task_20260930_upld"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    def fake_get(tid: str):
        if tid != TID:
            raise HTTPException(404, f"任务不存在：{tid}")
        return SimpleNamespace(id=tid, updated_at="")

    monkeypatch.setattr(m, "_get_task", fake_get)
    monkeypatch.setattr(m, "_save_index", lambda: None)
    monkeypatch.setattr(m, "_emit_standalone", lambda *a, **k: None)

    # ★ 每个用例前后都清空工作区：否则上轮残留的 note.txt / evil.txt 会让
    #   本轮的"同名改名"逻辑生效，断言随之失败 —— 测试必须能**重复运行**。
    #   （2026-09-30：第一次跑绿、第二次跑红，就是这个原因。）
    shutil.rmtree(m.store.workspace_dir(TID), ignore_errors=True)
    yield
    shutil.rmtree(m.store.workspace_dir(TID), ignore_errors=True)


def _ws():
    return m.store.workspace_dir(TID)


def test_upload_lands_in_task_workspace():
    r = CLIENT.post(f"/api/v1/tasks/{TID}/files", files={"file": ("note.txt", b"hello", "text/plain")})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "note.txt" and body["size"] == 5
    assert (_ws() / "note.txt").read_bytes() == b"hello"


def test_path_traversal_in_filename_is_neutralized():
    """★ 核心安全用例：`../../evil.txt` 必须只留文件名，落在工作区内。"""
    r = CLIENT.post(
        f"/api/v1/tasks/{TID}/files",
        files={"file": ("../../evil.txt", b"x", "text/plain")},
    )
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "evil.txt"
    assert (_ws() / "evil.txt").exists()
    assert not (_ws().parent / "evil.txt").exists(), "路径穿越没被拦住！"


def test_duplicate_name_does_not_overwrite():
    a = CLIENT.post(f"/api/v1/tasks/{TID}/files", files={"file": ("dup.txt", b"first", "text/plain")})
    b = CLIENT.post(f"/api/v1/tasks/{TID}/files", files={"file": ("dup.txt", b"second", "text/plain")})
    assert a.status_code == 201 and b.status_code == 201
    assert b.json()["name"] != "dup.txt", "同名应改名（dup_1.txt），不能覆盖"
    assert (_ws() / "dup.txt").read_bytes() == b"first", "原文件被覆盖了"


def test_missing_file_field_is_422():
    r = CLIENT.post(f"/api/v1/tasks/{TID}/files", data={"x": "1"})
    assert r.status_code == 422


def test_unknown_task_is_404():
    r = CLIENT.post(
        "/api/v1/tasks/task_20260930_nope/files",
        files={"file": ("a.txt", b"a", "text/plain")},
    )
    assert r.status_code == 404
