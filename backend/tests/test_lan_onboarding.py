"""第 8 批 问题4：手机直连（局域网）一键开通 + 后端托管界面。

用户原话：「手机直连局域网这个功能吧，感觉是有，但对小白来说他根本就不会设置——
你写一个什么 backend config.json，他们根本不懂、不知道搁哪找」。

★ 实测发现比"难设置"更严重 —— **今天根本连不上**：
  · 后端 API 能设 0.0.0.0，但**后端不托管界面**（此前只托管了一个 favicon）；
  · 界面在 vite:5173，而 `vite.config.ts` 没设 `server.host` ⇒ 只绑 localhost；
  · 旧说明还教人打开 `http://电脑IP:5173` —— 那个端口外部本来就进不来。

★★ 测试安全（本文件所有用例都必须遵守）：
   `_patch_config_file` 写的是**真实的 config.json**。所以每个碰写接口的用例都
   必须先把 `m._CONFIG_PATH` 指到 tmp_path —— 否则测试会改掉用户真实的配置。
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app import main as m


def _client() -> TestClient:
    return TestClient(m.app, base_url="http://127.0.0.1:8642")


def _seed_cfg(tmp_path, host: str = "127.0.0.1", token: str = "") -> "object":
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"server": {"host": host, "port": 8642, "access_token": token}}),
                 encoding="utf-8")
    return p


# ══════════════════ 状态接口 ══════════════════


def test_lan_status_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "_CONFIG_PATH", _seed_cfg(tmp_path))
    with _client() as c:
        d = c.get("/api/v1/lan/status").json()
    for k in ("enabled", "pending_lan", "needs_restart", "host", "port", "ip",
              "token", "token_set", "url", "ui_ready"):
        assert k in d, f"缺字段 {k}：{d}"
    assert d["enabled"] is False, "默认不该是局域网模式"
    assert "://" in d["url"] and str(d["port"]) in d["url"]


# ══════════════════ 一键开通 / 收回 ══════════════════


def test_lan_enable_writes_file_and_live_cfg(tmp_path, monkeypatch):
    """★ 核心锚点（8f 之后的新契约）：一键开通要 ① 写进 config.json ② 自动生成强密码
    ③ **内存 cfg 也一起改**（防被后续设置保存覆盖） ④ 但"是否已生效"仍为 false。

    第 ③④ 条看似矛盾，其实是同一件事的两面：
      · 内存 cfg 是"意图"，必须跟着改 —— 否则 `_save_config()` 一次覆盖写就把 host 打回去；
      · 是否生效看 `_BOUND_HOST`（进程启动时真正绑的地址）—— 没重启就不能说通了，
        否则用户会拿一个连不上的二维码。
    """
    cpath = _seed_cfg(tmp_path)
    monkeypatch.setattr(m, "_CONFIG_PATH", cpath)
    monkeypatch.setattr(m, "_BOUND_HOST", "127.0.0.1")     # 本进程仍绑本机

    with _client() as c:
        d = c.post("/api/v1/lan/enable").json()

    saved = json.loads(cpath.read_text("utf-8"))["server"]
    assert saved["host"] == "0.0.0.0", saved
    assert len(str(saved["access_token"])) >= 24, f"应自动生成强访问密码：{saved}"
    assert m.cfg.server.host == "0.0.0.0", "★ 内存 cfg 没跟着改 ⇒ 一次设置保存就会把 host 打回去"
    assert m.cfg.server.access_token == saved["access_token"], "内存与文件里的密码必须一致"
    assert d["pending_lan"] is True and d["needs_restart"] is True, d
    assert d["enabled"] is False, "还没重启，不能报成已生效"
    assert "token=" in d["url"], f"链接里应带密码（手机点开即登录）：{d['url']}"


def test_lan_enable_is_idempotent_for_token(tmp_path, monkeypatch):
    """已有访问密码时再开一次 **不换密码**（否则已配对的手机全部失效）。"""
    keep = "EXISTING-TOKEN-abcdefghijklmnop"
    cpath = _seed_cfg(tmp_path, token=keep)
    monkeypatch.setattr(m, "_CONFIG_PATH", cpath)
    monkeypatch.setattr(m, "_BOUND_HOST", "127.0.0.1")
    monkeypatch.setattr(m.cfg.server, "access_token", keep)   # 内存里已有密码（真实场景就是这样）

    with _client() as c:
        d = c.post("/api/v1/lan/enable").json()

    assert json.loads(cpath.read_text("utf-8"))["server"]["access_token"] == keep
    assert d["token"] == keep


def test_lan_disable_keeps_token(tmp_path, monkeypatch):
    """收回局域网绑定，但**保留访问密码**（下次开通不用重新配对手机）。"""
    cpath = _seed_cfg(tmp_path, host="0.0.0.0", token="KEEPME-0123456789abcdefghij")
    monkeypatch.setattr(m, "_CONFIG_PATH", cpath)
    with _client() as c:
        d = c.post("/api/v1/lan/disable").json()
    saved = json.loads(cpath.read_text("utf-8"))["server"]
    assert saved["host"] == "127.0.0.1", saved
    assert saved["access_token"] == "KEEPME-0123456789abcdefghij", "密码不该被清掉"
    assert d["pending_lan"] is False


def test_lan_patch_preserves_other_config(tmp_path, monkeypatch):
    """写配置是**定点打补丁**，不许把别的字段弄丢。"""
    cpath = tmp_path / "config.json"
    cpath.write_text(json.dumps({
        "server": {"host": "127.0.0.1", "port": 8642, "access_token": ""},
        "model": {"provider": "mimo", "model_name": "mimo-v2.6-flash"},
        "executor": {"approval_required": ["rm", "del"]},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(m, "_CONFIG_PATH", cpath)

    with _client() as c:
        c.post("/api/v1/lan/enable")

    after = json.loads(cpath.read_text("utf-8"))
    assert after["model"]["model_name"] == "mimo-v2.6-flash", "★ 开了个局域网把模型配置弄丢了"
    assert after["executor"]["approval_required"] == ["rm", "del"], "★ 审批配置丢了"
    assert after["server"]["port"] == 8642


# ══════════════════ 后端托管界面（手机能打开界面的前提） ══════════════════


def test_ui_served_from_dist(tmp_path, monkeypatch):
    """★ 核心锚点：后端要能托管构建好的界面 —— 这是"手机能连上"的前提。"""
    monkeypatch.setattr(m, "_CONFIG_PATH", _seed_cfg(tmp_path))
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>APP-SHELL</html>", encoding="utf-8")
    (dist / "assets" / "a.js").write_text("console.log('hi')", encoding="utf-8")
    monkeypatch.setattr(m, "_UI_DIST", dist)

    with _client() as c:
        assert "APP-SHELL" in c.get("/").text, "根路径应返回界面"
        assert "APP-SHELL" in c.get("/some/spa/route").text, "未知路径应回退到界面（SPA）"
        assert "console.log" in c.get("/assets/a.js").text, "静态资源应能取到"
        assert c.get("/api/v1/definitely-not-exist").status_code == 404, (
            "★ 通配路由不许吞掉 /api/*（吞了会让所有 404 变成返回 HTML）"
        )


def test_ui_traversal_cannot_escape_dist(tmp_path, monkeypatch):
    """路径穿越：不许读到 dist 之外的文件。"""
    monkeypatch.setattr(m, "_CONFIG_PATH", _seed_cfg(tmp_path))
    (tmp_path / "secret.txt").write_text("TOP-SECRET-VALUE", encoding="utf-8")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("APP-SHELL", encoding="utf-8")
    monkeypatch.setattr(m, "_UI_DIST", dist)

    with _client() as c:
        for path in ("/%2e%2e/secret.txt", "/..%2fsecret.txt", "/a/../../secret.txt"):
            r = c.get(path)
            assert "TOP-SECRET-VALUE" not in r.text, f"★ 路径穿越泄漏：{path} → {r.text[:60]}"
            assert r.status_code in (200, 404)


def test_ui_missing_dist_returns_503(tmp_path, monkeypatch):
    """界面还没构建时要给**人话**，不是 500 也不是空页面。"""
    monkeypatch.setattr(m, "_CONFIG_PATH", _seed_cfg(tmp_path))
    monkeypatch.setattr(m, "_UI_DIST", tmp_path / "nope")
    with _client() as c:
        r = c.get("/")
    assert r.status_code == 503, r.status_code
    assert "npm run build" in r.text, f"错误信息应告诉用户怎么办：{r.text[:120]}"


# ══════════════════ 第 8d 处：局域网模式下「界面公开、数据要密码」 ══════════════════


def _enter_lan_mode(monkeypatch, token: str = "LAN-TOKEN-0123456789abcdef") -> None:
    """把运行中的状态切成局域网可访问。

    ★ 第 8f 处之后要改**两处**：`_BOUND_HOST`（进程真正绑的地址，决定"是否生效"）
      与 `cfg.server.host`（配置里的意图）。只改后者是测不出真实行为的。
    """
    monkeypatch.setattr(m, "_BOUND_HOST", "0.0.0.0")
    monkeypatch.setattr(m.cfg.server, "host", "0.0.0.0")
    monkeypatch.setattr(m.cfg.server, "access_token", token)


def test_settings_save_must_not_revert_lan_host(tmp_path, monkeypatch):
    """★★ 第 8f 处回归锚点：开通手机直连后，**任何一次设置保存都不许把 host 打回去**。

    这是本班自查出的严重潜伏 bug，真实发生过：
      · `lan_enable` 当初只写文件、**不动内存 cfg**（怕本机突然被要密码）；
      · 而 `_save_config()`（设置页任何一次保存都会调）是拿**内存 cfg 整体覆盖写文件**；
      · ⇒ 用户点一次设置，config.json 的 host 就被静默打回 127.0.0.1；
      · ⇒ 当时手机还能用（进程启动时就绑好了 0.0.0.0），**但下次重启手机就连不上**。
    修法：内存 cfg 一起改；"是否已生效"另看 `_BOUND_HOST`。
    """
    cpath = _seed_cfg(tmp_path)
    monkeypatch.setattr(m, "_CONFIG_PATH", cpath)
    monkeypatch.setattr(m, "_BOUND_HOST", "127.0.0.1")   # 本次进程还是本机绑定

    with _client() as c:
        c.post("/api/v1/lan/enable")

    # 模拟"设置页保存了一次设置"：这正是当初把 host 打回去的那条路
    m._save_config()

    saved = json.loads(cpath.read_text("utf-8"))["server"]
    assert saved["host"] == "0.0.0.0", (
        f"★ 一次设置保存就把 host 打回去了 —— 重启后手机直连会静默失效：{saved}"
    )
    assert saved["access_token"], "密码也不许被覆盖掉"

    # 而"是否已生效"必须仍报 false（还没重启）——不能假装通了
    d = _lan_view_of(c)
    assert d["enabled"] is False, "还没重启就报成已生效，会让用户拿一个连不上的二维码"
    assert d["pending_lan"] is True and d["needs_restart"] is True


def _lan_view_of(c: TestClient) -> dict:
    return c.get("/api/v1/lan/status").json()


def test_lan_mode_gates_api_but_serves_ui(tmp_path, monkeypatch):
    """★ 核心锚点（8d）：局域网模式下 **/api/* 必须 401，界面资源必须 200**。

    为什么这条必须有：手机扫码打开页面后，浏览器要去取 `/assets/index-xxx.js`。
    此前是"除 auth/check 外全都校验" ⇒ 那个 JS 请求被 401 挡掉 ⇒ **网页白屏**
    （真局域网实测：body 只有 448 字节＝index.html 本身，React 根本没跑）。
    而这个 bug 走 vite 开发服务器**永远测不到**（静态资源由 vite 出，只有 /api 落到后端）
    —— 所以必须在这里钉死。

    同时钉住"放开界面"**没有**连带放开数据面。
    """
    monkeypatch.setattr(m, "_CONFIG_PATH", _seed_cfg(tmp_path))
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>APP-SHELL</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('app')", encoding="utf-8")
    (dist / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    monkeypatch.setattr(m, "_UI_DIST", dist)
    _enter_lan_mode(monkeypatch)

    with _client() as c:
        # ① 界面资源：不带密码也必须能取（否则手机白屏）
        assert c.get("/").status_code == 200, "局域网模式下首页必须能打开"
        assert "APP-SHELL" in c.get("/").text
        js = c.get("/assets/app.js")
        assert js.status_code == 200, "★ 不带密码取不到 JS ⇒ 手机白屏"
        assert "console.log" in js.text
        assert c.get("/favicon.ico").status_code == 200
        assert "APP-SHELL" in c.get("/some/spa/route").text, "SPA 回退也要能打开"

        # ② 数据面：不带密码一律 401（这是安全底线）
        for path in ("/api/v1/tasks", "/api/v1/settings", "/api/v1/files?path=x",
                     "/api/v1/lan/status"):
            assert c.get(path).status_code == 401, f"★ {path} 没带密码竟然能访问"

        # ③ 带上密码就该通
        assert c.get("/api/v1/tasks?token=LAN-TOKEN-0123456789abcdef").status_code == 200
        assert c.get("/api/v1/lan/status",
                     headers={"x-auth-token": "LAN-TOKEN-0123456789abcdef"}).status_code == 200

        # ④ 错的密码仍然 401
        assert c.get("/api/v1/tasks?token=wrong").status_code == 401


def test_non_lan_mode_keeps_loopback_open(tmp_path, monkeypatch):
    """反向：**没开**局域网时行为不变（本机不需要密码）——别把日常用法搞坏了。"""
    monkeypatch.setattr(m, "_CONFIG_PATH", _seed_cfg(tmp_path))
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("APP", encoding="utf-8")
    monkeypatch.setattr(m, "_UI_DIST", dist)
    monkeypatch.setattr(m.cfg.server, "host", "127.0.0.1")

    with _client() as c:
        assert c.get("/api/v1/tasks").status_code == 200, "本机模式下不该要密码"
        assert c.get("/").status_code == 200
