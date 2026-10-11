# -*- coding: utf-8 -*-
"""★ 第 3 项 · 版本检查 + 升级提示 —— 2026-10-07。

## 加之前是什么样（查出来的现状 ✓）

| 哪里 | 版本 |
|---|---|
| `frontend/package.json` | `0.1.0` ✓ |
| 关于页 | **硬编码的**「LanternLogic Agent v0.1.0」✗ |
| **后端** | **压根没有** ✗（没有任何接口能回答"我是哪个版本"✓）|

⇒ 三处各说各的 ✓ 改一处另两处不会跟着动 ✗ —— 本项目栽过五次的"**多处口径打架**"（这次是版本号 ✓）

## 三条设计（每条都在防"最坏的那种提示"✗）

1. **默认不查** ✓ —— 没配地址就**一个字节都不往外发** ✓（本项目是本地优先 ✓）
2. **只查不装** ✗ —— 查到新版只告诉你**去哪下** ✓ 绝不自动下载/替换程序 ✓
   （自动替换自己 = 谁改了那个地址谁就能给你换程序 ✓ 那是供应链口子 ✓）
3. **宁可保守** ✓ —— 断网/格式怪/版本号比不出来 ⇒ 一律判"没发现新版本" ✓
   **绝不**因为解析失败就弹一个假的"有新版本" ✗（让用户去下一个不存在的东西 = 最糟 ✓）
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from app import main as m
from app import version as V

ROOT = pathlib.Path(m.__file__).resolve().parents[2]
SETTINGS_TSX = ROOT / "frontend" / "src" / "components" / "SettingsPanel.tsx"
PKG = ROOT / "frontend" / "package.json"


# ═══ ① 版本号：**一处声明，处处读它** ═══

def test_version_has_exactly_one_source():
    """★★ 版本号只能有一处 ✓ —— 关于页**不许再硬编码** ✗（回滚实验：写回去 ⇒ 本组必红 ✓）。

    ★ 比较前必须**剔掉注释** ✓ —— 注释里也会提到那个旧字符串 ✓
      直接全文找会被**注释抢先命中** ✗（本仓栽过不止一次：见 test_voice_input.py 的同款教训 ✓）
      ⚠️ 而且**只剔行首 `//` 不够** ✗ —— 这段说明是 JSX 的**块注释** `{/* … */}` ✓
        它的中间几行不以 `//` 开头 ✓ 第一版就栽在这儿（本班实测 ✓）⇒ 先整块去掉再比 ✓
    """
    assert V.__version__ and V.__version__.count(".") >= 1, V.__version__
    raw = re.sub(r"/\*.*?\*/", "", SETTINGS_TSX.read_text("utf-8"), flags=re.S)
    code = "\n".join(ln for ln in raw.splitlines() if not ln.strip().startswith("//"))
    assert f"LanternLogic Agent v{V.__version__}" not in code, \
        "关于页又把版本号写死了 ✗（改一处另两处不会动 ✓ 这正是要修的毛病 ✓）"
    assert "api.getVersion()" in code, "关于页没从后端读版本 ✗"
    assert "ver.version" in code, "读了却没用它渲染 ✗"


def test_frontend_package_version_matches_the_backend():
    """★ `package.json` 那份要与后端**一致** ✓ —— 靠测试钉住，不靠自觉 ✗。

    （两处版本号天生会漂 ✓ 一边发版一边忘 ✓ ⇒ 与其"记得改两处"，不如让门盯着 ✓）
    """
    pkg = json.loads(PKG.read_text("utf-8"))
    assert pkg["version"] == V.__version__, \
        f"package.json={pkg['version']} 与后端={V.__version__} 不一致 ✗（发版时两处都要改 ✓）"


def test_version_endpoint_answers_the_question():
    """★ 后端必须能回答"我是哪个版本" ✓（加之前它答不出来 ✗）。"""
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        d = c.get("/api/v1/version").json()
    assert d["version"] == V.__version__ and d["name"] == V.APP_NAME, d


# ═══ ② 版本比较：宁可保守，绝不弹假消息 ═══

@pytest.mark.parametrize("latest,current,want", [
    ("0.1.0", "0.1.0", False),      # 相等 ⇒ 没有新版 ✓
    ("0.1.1", "0.1.0", True),
    ("0.2.0", "0.1.9", True),
    ("1.0.0", "0.9.9", True),
    ("0.0.9", "0.1.0", False),      # 比当前**旧** ⇒ 绝不能说"有新版本" ✗
    ("v0.2.0", "0.1.0", True),      # 带 v 前缀也要认 ✓
    ("0.2.0-beta", "0.1.0", True),  # 带后缀也要认 ✓
])
def test_is_newer(latest, current, want):
    assert V.is_newer(latest, current) is want, (latest, current)


def test_garbage_version_never_claims_an_update():
    """★★ **解析不出来就不许说"有新版本"** ✗ —— 让用户去下一个不存在的东西是最糟的提示 ✓。"""
    for junk in ("", "  ", "最新版", "abc", "...", "-"):
        assert V.is_newer(junk, "0.1.0") is False, f"「{junk}」竟然判成有新版本 ✗"


# ═══ ③ 检查更新：只查不装、查不到就说查不到 ═══

def test_no_url_means_no_network_call_at_all(monkeypatch):
    """★★ **默认不查** ✓ —— 没配地址就一个字节都不往外发 ✓（本地优先 ✓）。

    回滚实验：把"没配地址就早退"那段去掉 ⇒ 它会去请求一个空地址 ⇒ 本组必红 ✓
    """
    monkeypatch.setattr(m.cfg, "update", type("U", (), {"check_url": ""})(), raising=False)
    called: list[str] = []

    import httpx

    class _Boom:
        def __init__(self, *a, **k):        # noqa: ARG002
            called.append("client")

    monkeypatch.setattr(httpx, "AsyncClient", _Boom)
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        d = c.post("/api/v1/version/check").json()
    assert called == [], "没配地址竟然还是发了请求 ✗（本项目默认不往外发东西 ✓）"
    assert d["ok"] is False and d["has_update"] is False, d
    assert "还没配" in d["note"], d["note"]


def test_non_http_url_is_refused(monkeypatch):
    monkeypatch.setattr(m.cfg, "update", type("U", (), {"check_url": "file:///etc/passwd"})(), raising=False)
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        d = c.post("/api/v1/version/check").json()
    assert d["ok"] is False and "http" in d["note"], d


def test_localhost_url_is_blocked_by_the_ssrf_guard(monkeypatch):
    """★ 复用项目里那套 SSRF 防护 ✓（自己再写一套只会更弱 ✗）—— 指向本机一律拒 ✓。"""
    monkeypatch.setattr(m.cfg, "update", type("U", (), {"check_url": "http://127.0.0.1:9/x.json"})(), raising=False)
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        d = c.post("/api/v1/version/check").json()
    assert d["ok"] is False and "不允许" in d["note"], d


def _fake_fetch(monkeypatch, payload: dict | None, status: int = 200, boom: bool = False):
    """把"去网上取 JSON"这一步换掉 ✓（单元测试不许碰真网络 ✓ 本仓的老规矩 ✓）。"""
    from app import ssrf

    async def _ok(url):                                     # noqa: ARG001
        return {"1.2.3.4"}

    monkeypatch.setattr(ssrf, "assert_public_url", _ok)

    class _Resp:
        status_code = status

        def json(self):
            return payload

    class _Client:
        def __init__(self, *a, **k):                        # noqa: ARG002
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):                      # noqa: ARG002
            return False

        async def get(self, *a, **k):                       # noqa: ARG002
            if boom:
                raise RuntimeError("断网了")
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)


def test_newer_version_is_reported_with_a_download_link(monkeypatch):
    monkeypatch.setattr(m.cfg, "update", type("U", (), {"check_url": "https://example.com/u.json"})(), raising=False)
    _fake_fetch(monkeypatch, {"version": "9.9.9", "notes": "修了很多", "url": "https://example.com/dl"})
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        d = c.post("/api/v1/version/check").json()
    assert d["has_update"] is True and d["latest"] == "9.9.9", d
    assert d["url"] == "https://example.com/dl" and "修了很多" in d["notes"], d
    assert "不会自动替换" in d["note"] or "不自动" in d["note"], f"没说清「只查不装」✗：{d['note']}"


def test_same_version_says_up_to_date(monkeypatch):
    monkeypatch.setattr(m.cfg, "update", type("U", (), {"check_url": "https://example.com/u.json"})(), raising=False)
    _fake_fetch(monkeypatch, {"version": V.__version__})
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        d = c.post("/api/v1/version/check").json()
    assert d["ok"] is True and d["has_update"] is False and "已是最新" in d["note"], d


def test_network_failure_is_reported_honestly(monkeypatch):
    """★ 断网 ⇒ 如实说"查不到" ✓ **绝不说"有新版本"** ✗ 也绝不说"已是最新" ✗（那是假装查过了 ✓）。"""
    monkeypatch.setattr(m.cfg, "update", type("U", (), {"check_url": "https://example.com/u.json"})(), raising=False)
    _fake_fetch(monkeypatch, None, boom=True)
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        d = c.post("/api/v1/version/check").json()
    assert d["ok"] is False and d["has_update"] is False, d
    assert "查不到" in d["note"], d["note"]


def test_bad_payload_is_not_guessed(monkeypatch):
    """★ 对方返回的东西里没有 version ⇒ **不猜** ✓（"我认不出来，就不猜"✓）。"""
    monkeypatch.setattr(m.cfg, "update", type("U", (), {"check_url": "https://example.com/u.json"})(), raising=False)
    _fake_fetch(monkeypatch, {"随便": "什么"})
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        d = c.post("/api/v1/version/check").json()
    assert d["ok"] is False and d["has_update"] is False and "不猜" in d["note"], d


def test_update_config_section_exists_and_defaults_to_off():
    """★ 默认**关着** ✓（不填地址 = 不检查 ✓ 与 simple_model / 花费上限同规矩 ✓）。"""
    from app.config import AppConfig
    c = AppConfig(version=1).model_dump()
    assert c["update"]["check_url"] == "", c["update"]
