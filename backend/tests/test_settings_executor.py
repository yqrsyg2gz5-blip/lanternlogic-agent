"""执行环境设置的写接口 —— 第 30 班：设置面板从"只读展示"变"可编辑"。

**为什么**：第 29 班把执行环境（授权目录/审批清单/超时）显示出来了，
但只能看不能改，用户还得去翻 `config.json`。

**注意**：这些值在启动时被 executor 实例读走 → 保存后需**重启后端**才完全生效。
本文件同时验证"归一化"（用户填 `RM` 也要能用）。
"""
from __future__ import annotations

import pytest
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


@pytest.fixture(autouse=True)
def _no_disk_write(monkeypatch):
    """★ 绝不能让测试真的写用户的 config.json —— 拦掉落盘，并在用例后还原内存配置。"""
    ex = m.cfg.executor
    snapshot = (
        list(ex.allowed_dirs),
        list(ex.approval_required),
        ex.timeout_seconds,
    )
    monkeypatch.setattr(m, "_save_config", lambda: None)
    yield
    ex.allowed_dirs = snapshot[0]
    ex.approval_required = snapshot[1]
    ex.timeout_seconds = snapshot[2]


def test_update_allowed_dirs():
    r = CLIENT.post("/api/v1/settings/executor", json={"allowed_dirs": ["workspace", "C:/tmp/demo"]})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    # Windows 下 Path("C:/tmp/demo") 会被规范成反斜杠 —— 断言要跨平台
    got = [str(p).replace("\\", "/") for p in m.cfg.executor.allowed_dirs]
    assert got == ["workspace", "C:/tmp/demo"]


def test_approval_list_is_normalized():
    """用户填 `RM` / 带空格，必须归一化成小写去空白 —— 否则审批判定（小写比较）不生效。"""
    r = CLIENT.post("/api/v1/settings/executor", json={"approval_required": ["RM", " del ", ""]})
    assert r.status_code == 200
    assert m.cfg.executor.approval_required == ["rm", "del"], "空项要丢掉、大小写要归一"


def test_timeout_is_bounded():
    ok = CLIENT.post("/api/v1/settings/executor", json={"timeout_seconds": 120})
    assert ok.status_code == 200 and m.cfg.executor.timeout_seconds == 120.0
    for bad in (0, -5, 99999):
        r = CLIENT.post("/api/v1/settings/executor", json={"timeout_seconds": bad})
        assert r.status_code == 422, f"{bad} 应当被拒绝"
    assert m.cfg.executor.timeout_seconds == 120.0, "非法值不能改动配置"


def test_partial_update_keeps_other_fields():
    before = list(m.cfg.executor.approval_required)
    r = CLIENT.post("/api/v1/settings/executor", json={"timeout_seconds": 30})
    assert r.status_code == 200
    assert m.cfg.executor.approval_required == before, "只改超时，不该动审批清单"


def test_note_mentions_restart():
    r = CLIENT.post("/api/v1/settings/executor", json={"timeout_seconds": 61})
    assert "重启" in r.json()["note"], "必须如实告诉用户要重启才完全生效"
