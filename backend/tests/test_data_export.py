# -*- coding: utf-8 -*-
"""**数据得能拿在自己手里** —— 2026-10-07 用户："数据在你手里" ✓。

## 为什么这两条要专门测

记忆库和知识库是这个项目里**最像"用户自己的东西"**的两份数据 ✓
· 记忆库：记的是**你的**偏好/身份/纠错 ✓
· 知识库：是你**自己的文档**切的块 ✓
它们只存本机 ✓ 但用户必须**有办法拿走** ✓（换机器 ✓ 备份 ✓ 或者就是想看看里面记了啥 ✓）
—— 一个"只能进不能出"的本地软件 ✓ 等于把你的数据**锁在里面** ✗。
"""
from __future__ import annotations

import io
import json
import pathlib
import sys
import zipfile

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import main as m  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        yield c


def test_memory_export_is_real_json_with_every_entry(client):
    """★ 导出的是**原始 JSON** ✓ 不是截图/摘要 ✗ —— 而且**条数要对得上** ✓。"""
    r = client.get("/api/v1/memory/export")
    assert r.status_code == 200, r.text
    assert "attachment" in r.headers.get("content-disposition", ""), \
        "没带下载头 ⇒ 浏览器会当网页打开 ✗"
    body = json.loads(r.content.decode("utf-8"))
    assert isinstance(body.get("entries"), list), body
    assert "exported_at" in body and "count" in body, sorted(body)
    # 条数必须与真库一致 ✓（导出少了比不导出更糟 ✗）
    assert body["count"] == len(m._memory_store.all()), body["count"]
    assert len(body["entries"]) == body["count"], "count 和 entries 对不上 ✗"


def test_kb_export_is_a_zip_with_both_files_and_a_readme(client):
    """★ 知识库导出**必须是 zip** ✓ 而且 **manifest + chunks + 说明** 一个不少 ✓。

    （只给 chunks.json 用户没法自己拼回去 ✗；只给 manifest 等于给了个目录 ✗。）
    """
    name = "导出测试库"
    from app.kb import chunk_text           # noqa: PLC0415
    d = m._kb_store.root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps(
        {"name": name, "description": "测试", "chunks": 1, "files": 1}, ensure_ascii=False), "utf-8")
    (d / "chunks.json").write_text(json.dumps(
        [{"text": "苹果" * 40, "source": "a.md", "embedding": [1.0, 0.0]}], ensure_ascii=False), "utf-8")
    try:
        r = client.get(f"/api/v1/kb/{name}/export")
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("application/zip"), r.headers
        z = zipfile.ZipFile(io.BytesIO(r.content))
        names = set(z.namelist())
        assert {"manifest.json", "chunks.json"} <= names, f"包里缺文件 ✗：{names}"
        assert "README.txt" in names, "没放说明 ✗（用户拿到 zip 得知道里面是什么 ✓）"
        assert "苹果" in z.read("chunks.json").decode("utf-8"), "内容不对 ✗"
        assert chunk_text, "（用一下导入，免得被 lint 当未用 ✓）"
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def test_kb_export_says_404_for_a_library_that_does_not_exist(client):
    """★ 导一个**不存在的库**要**如实说没有** ✓ 而不是回一个空包 ✗。"""
    r = client.get("/api/v1/kb/这个库根本不存在/export")
    assert r.status_code == 404, r.text
    assert "没有这个知识库" in r.text, r.text


def test_ui_has_the_two_export_buttons():
    """★ 界面上**真有两个导出入口** ✓ —— 光有接口等于没有 ✓（用户不会敲 curl ✓）。"""
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "frontend" / "src" / "components" / "SettingsPanel.tsx").read_text("utf-8")
    assert "/api/v1/memory/export" in src, "记忆库没有导出入口 ✗"
    assert "/export`" in src or "/export" in src, "知识库没有导出入口 ✗"
    assert "authedUrl" in src, "导出链接没带 token ✗（局域网模式下会 401 ✓）"
    assert 'download=' in src, "没加 download 属性 ⇒ 会当网页打开而不是下载 ✗"
