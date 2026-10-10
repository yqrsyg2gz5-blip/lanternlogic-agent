"""A-4（查看密码 / 改密码）锚点 —— 核心是**"仅本机"这条安全线**。

为什么必须限本机：
  · 访问密码是局域网闸门。远端设备（手机）能看到密码 ⇒ 闸门形同虚设（任何同网设备一次 GET 就拿到）
  · 手机**改**密码会把自己锁在外面（下一次请求就要新密码，而手机端没有可靠的改密入口）
判据用 `request.client.host`（TCP 对端地址），**不是** Host 头 —— 后者是客户端自己写的，
手机可以把它伪造成 127.0.0.1。拿不到对端地址时保守判否（fail-closed）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main as m

LOCAL = ("127.0.0.1", 51234)
REMOTE = ("192.168.1.23", 51234)

SECRET = "TEST-TOKEN-not-a-real-secret-0001"   # ★ 2026-10-08：原来是**线上真口令**被抄进来了 ✗ 上传前换成假值 ✓（这条测试只需要一个字符串 ✓）


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """不落盘、不污染后续用例（N15 的规矩）：_patch_config_file 打桩 + 配置快照。"""
    written: list[dict] = []
    monkeypatch.setattr(m, "_patch_config_file", lambda patch: written.append(patch))
    monkeypatch.setattr(m.cfg.server, "access_token", SECRET, raising=False)
    yield written


def _client(addr):
    return TestClient(m.app, base_url="http://127.0.0.1:8642", client=addr)


# ═══ 查看：本机给，远端一律 403 ═══

def test_local_can_view_the_token():
    with _client(LOCAL) as c:
        r = c.get("/api/v1/server/token")
    assert r.status_code == 200, r.text
    assert r.json()["token"] == SECRET and r.json()["set"] is True


def test_remote_cannot_view_the_token():
    """★ 这条是这个功能的安全底线：手机/别的电脑拿不到密码。"""
    with _client(REMOTE) as c:
        r = c.get("/api/v1/server/token")
    assert r.status_code == 403, r.text
    assert SECRET not in r.text, "403 的响应体里把密码漏出去了"


def test_localhost_name_is_treated_as_local():
    with _client(("localhost", 51234)) as c:
        assert c.get("/api/v1/server/token").status_code == 200


def test_unknown_client_address_is_denied(monkeypatch):
    """拿不到对端地址（client=None）⇒ **保守判否**（fail-closed）。"""
    assert m._client_is_local(type("R", (), {"client": None})()) is False


# ═══ 改：本机可改，远端拒绝 ═══

def test_local_can_change_the_token_and_it_is_written_to_both_places(_isolate):
    with _client(LOCAL) as c:
        r = c.post("/api/v1/server/token", json={"token": "my-new-password-123"})
    assert r.status_code == 200, r.text
    assert r.json()["token"] == "my-new-password-123" and r.json()["generated"] is False
    assert m.cfg.server.access_token == "my-new-password-123", "内存没改（会被后续保存打回旧值）"
    assert _isolate and _isolate[-1] == {"server": {"access_token": "my-new-password-123"}}, \
        "文件没写（重启后还是旧密码）"
    assert "旧密码立即失效" in r.json()["note"]


def test_remote_cannot_change_the_token(_isolate):
    with _client(REMOTE) as c:
        r = c.post("/api/v1/server/token", json={"token": "hacked-hacked"})
    assert r.status_code == 403, r.text
    assert m.cfg.server.access_token == SECRET, "远端竟然改成功了"
    assert not _isolate, "远端请求竟然写了配置"


def test_empty_token_generates_a_strong_one(_isolate):
    with _client(LOCAL) as c:
        r = c.post("/api/v1/server/token", json={"token": ""})
    body = r.json()
    tok = body["token"]
    assert body["generated"] is True
    assert len(tok) >= 20 and not any(ch.isspace() for ch in tok), tok
    assert m.cfg.server.access_token == tok


@pytest.mark.parametrize("bad,why", [("short", "太短"), ("has space here", "空格"), ("tab\tinside", "空格")])
def test_weak_tokens_are_refused(bad, why, _isolate):
    with _client(LOCAL) as c:
        r = c.post("/api/v1/server/token", json={"token": bad})
    assert r.status_code == 422, r.text
    assert not _isolate, "校验失败却写了配置"
    assert m.cfg.server.access_token == SECRET


def test_lan_view_never_leaks_the_token():
    """回归：局域网状态接口本来就不该回传密码（新增接口不能把它带出来）。"""
    with _client(LOCAL) as c:
        body = c.get("/api/v1/lan/status").text
    assert SECRET not in body, "lan/status 把访问密码漏出来了"

# ═══ 界面接线 ═══
import pathlib  # noqa: E402

_SRC = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"
_PANEL = (_SRC / "components" / "SettingsPanel.tsx").read_text("utf-8")
_API_TS = (_SRC / "api.ts").read_text("utf-8")
_UI_VERIFY = (pathlib.Path(__file__).resolve().parents[2] / "frontend" / "scripts"
              / "verify_access_token_ui.mjs").read_text("utf-8")


def test_settings_page_wires_view_and_change():
    # ★ 整行匹配（不是裸子串）：注释里也写着"查看密码（仅本机）"这句字样，
    #   只查子串的话，把按钮文案改掉、注释留着，锚点照样通过 —— 本班回滚组实测过这一点。
    assert "{tokenBusy ? '读取中…' : '查看密码（仅本机）'}" in _PANEL, \
        "按钮没声明「仅本机」（那三个字是给用户的承诺：为什么手机上找不到这个入口）"
    assert "api.getAccessToken" in _PANEL, "按钮没真的去取密码"
    assert "改密码" in _PANEL and "随机生成" in _PANEL and "保存新密码" in _PANEL, "改密码那一组控件不全"
    assert "api.setAccessToken" in _PANEL, "没把新密码发出去"
    assert "api.lanStatus" in _PANEL.split("setAccessToken")[1][:600], \
        "改完密码没刷新局域网状态（设置页二维码还是旧的）"


def test_api_layer_implements_both_methods():
    assert _API_TS.count("getAccessToken") >= 3 and _API_TS.count("setAccessToken") >= 3, \
        "声明/实现/离线实现不齐"


def test_ui_verification_covers_the_denied_path():
    assert "403" in _UI_VERIFY and "只能在本机" in _UI_VERIFY, \
        "没验证手机场景会如实说明，只验证了成功路径"
    assert "随机生成的密码够强" in _UI_VERIFY, "没验证随机生成的质量"