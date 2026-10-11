"""本地 API 的防跨站护栏 —— P2-6。

**修复前的真实风险**（第一轮评估发现，一直未修）：
1. **CSRF**：无 body 的 POST 属于"简单请求"，浏览器**不发预检**，
   任意网页都能 `fetch('http://127.0.0.1:8642/api/v1/tasks/x/cancel', {method:'POST'})`
   —— 取消/接管你正在跑的任务，你只会看到任务莫名失败。
2. **DNS rebinding**：攻击者把自己的域名解析到 127.0.0.1，同源策略被绕开，
   可以**读走全量数据**（任务、事件、工作区文件）。

**修法**（`main.py` 的 `guard_local_origin` 中间件）：Host 必须回环 / Sec-Fetch-Site 不能 cross-site /
Origin 若存在必须回环。**没有 Origin 的请求（curl、本地脚本）照常放行。**

用 FastAPI TestClient 直接打中间件，不需要起服务器。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import _host_of, app

# Host 必须是回环名，否则会被护栏拒掉 —— 显式指定，别用默认的 testserver
CLIENT: TestClient | None = None  # fixture 内 global 赋值（pyflakes 口径修正）


@pytest.fixture(scope="module", autouse=True)
def _lifespan_client():
    """审计 §7.5：裸 TestClient 不触发 lifespan——看门狗/自动化/群回流装配路径
    完全没进测试。改为 module 级 with 上下文，启动/关闭钩子真实执行。"""
    global CLIENT
    with TestClient(app, base_url="http://127.0.0.1:8642") as c:
        CLIENT = c
        yield


def test_host_parser_handles_all_forms():
    assert _host_of("127.0.0.1:8642") == "127.0.0.1"
    assert _host_of("localhost") == "localhost"
    assert _host_of("http://127.0.0.1:5173") == "127.0.0.1"
    assert _host_of("https://evil.example/path") == "evil.example"
    assert _host_of("[::1]:8642") == "::1"
    assert _host_of(None) == ""


def test_normal_local_request_passes():
    r = CLIENT.get("/api/v1/settings")
    assert r.status_code == 200, "本机正常请求不能被护栏挡住"


def test_dns_rebinding_host_is_rejected():
    """★ 浏览器按域名发 Host —— 非回环一律拒绝。"""
    r = CLIENT.get("/api/v1/settings", headers={"Host": "evil.example"})
    assert r.status_code == 403
    assert "Host" in r.json()["detail"]


def test_cross_site_fetch_metadata_is_rejected():
    """★ Sec-Fetch-Site 由浏览器自动带上，页面脚本改不了 —— 最硬的一条。"""
    r = CLIENT.post("/api/v1/tasks/task_20260930_aaaa/cancel", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_cross_site_error_tells_the_user_what_to_do():
    """★ 2026-10-09（用户实测撞上 ✓ 而且看不懂 ✓）：

    守卫**拦对了** ✓（外站 Origin 必须拒 ✗）—— 但原来只回一句
      「拒绝跨站请求（Sec-Fetch-Site: cross-site）」✗
    用户拿着这句话不知道该干什么 ✓（他的原话：你看看什么样的，你看看我登录网页什么 ✗）
    ⇒ 本测试锁两件事：
      ① 提示里必须给出**该用哪个地址打开** ✓（可照做 ✓）
      ② 必须说清"这不是故障、是安全设计" ✓（否则用户以为软件坏了 ✗）
    ★ 同时锁住**判据一个字没放松** ✗：外站 Origin 依然 403 ✓
    """
    r = CLIENT.post("/api/v1/tasks/task_20260930_aaaa/cancel",
                    headers={"Sec-Fetch-Site": "cross-site",
                             "Origin": "https://evil.example.com"})
    assert r.status_code == 403, f"外站居然没被拒 ✗：{r.status_code}"
    detail = r.json().get("detail", "")
    assert "127.0.0.1" in detail, f"提示里没给出该用的地址 ✗（用户照着做不了 ✓）：{detail}"
    assert "安全设计" in detail, f"没说清这不是故障 ✗：{detail}"
    assert "frontend/dist" in detail, f"没提醒'别双击 dist 里的 html' ✗：{detail}"


def test_cross_site_origin_is_rejected():
    """★ 普通跨站 CSRF：Origin 是攻击者站点 → 拒绝。"""
    r = CLIENT.post(
        "/api/v1/tasks/task_20260930_aaaa/cancel",
        headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "same-site"},
    )
    assert r.status_code == 403
    assert "跨站来源" in r.json()["detail"]


def test_null_origin_is_rejected():
    """`file://` 打开的本地 HTML 会带 `Origin: null` —— 正是攻击常见形态。"""
    r = CLIENT.post("/api/v1/tasks/task_20260930_aaaa/cancel", headers={"Origin": "null"})
    assert r.status_code == 403


def test_loopback_origin_is_allowed():
    """前端（5173）与 Tauri 之类本机页面必须能正常调。"""
    r = CLIENT.get("/api/v1/settings", headers={"Origin": "http://127.0.0.1:5173"})
    assert r.status_code == 200
    r2 = CLIENT.get("/api/v1/settings", headers={"Origin": "http://localhost:5173"})
    assert r2.status_code == 200


def test_request_without_origin_is_allowed():
    """curl / 本地脚本 / vite 代理都不带 Origin —— 不能误伤。"""
    r = CLIENT.get("/api/v1/settings")  # TestClient 默认不发 Origin
    assert r.status_code == 200
