"""Webhook 安全回归 —— 审计"无轮换/无重放保护"补齐。

· 轮换：POST /automations/{id}/rotate 生成新 secret，旧 secret 立即失效
· 强签名：X-AgentShell-Timestamp + X-AgentShell-Sign（HMAC-SHA256(secret, "ts.body")）
  时间戳偏差 >300s → 401（重放的请求会过期）
· 爆破限流：错 5 次后 429
"""
from __future__ import annotations

import hashlib
import hmac
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app, automations, _save_automations


@pytest.fixture()
def client():
    with TestClient(app, base_url="http://127.0.0.1:8642") as c:
        yield c


@pytest.fixture()
def hook(client):
    aid = f"auto_hooktest{int(time.time())}"
    a = {
        "id": aid, "name": "wh", "kind": "hook", "task_input": "say hi",
        "enabled": True, "secret": "abcdef1234567890", "created_at": "2026-10-02T00:00:00Z",
        "last_run": None, "last_task_id": None,
    }
    automations[a["id"]] = a
    _save_automations()
    yield dict(a)  # 快照：rotate 会原地改 automations 里的同一对象
    automations.pop(a["id"], None)
    _save_automations()


def test_hook_path_is_exempt_from_the_global_access_password():
    """★★ 2026-10-06（用户实测："**Webhook 这个能触发吗？**" ⇒ 真去试 ⇒ **根本触发不了** ✗✗）

    现象：带上**正确的 HMAC 签名**去 POST `/api/v1/hooks/<id>/<secret>` ⇒
    照样 `401 需要访问密码（token 不匹配）`✗ —— 被**大门的密码**挡在外面了 ✓。
    而 Webhook 的调用方是**外部系统** ✓ 它**不可能知道**你本机的访问密码 ✓
    ⇒ 这条路等于永远打不开 ✗（"定时 / Webhook 触发"里的后一半是摆设 ✓）。

    修法：全局鉴权里**只放行 `/api/v1/hooks/`** 这一条 ✓ —— 它自带更严的鉴权
    （URL 里的 secret ✓ + 可选强签名 HMAC ✓ + 时间戳防重放 ✓ + 连错 5 次限流 ✓）。

    ★ 这条测试钉两件事：**hook 放行** ✓ 而且**别的路径一条都没多放** ✗。
    """
    import pathlib
    import re

    src = (pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py").read_text("utf-8")
    assert 'not path_lan.startswith("/api/v1/hooks/")' in src, \
        "全局鉴权没有放行 hooks 路径 ✗ ⇒ 外部系统永远触发不了 Webhook ✓"
    exempt = re.findall(r'not path_lan\.startswith\("([^"]+)"\)', src)
    assert sorted(exempt) == ["/api/v1/auth/check", "/api/v1/hooks/"], \
        f"放行名单被改动了 ✗（多放一条都是风险 ✓）：{exempt}"


def test_hook_still_requires_the_right_secret(client):
    """**放行 ≠ 不设防** ✓：密钥不对照样 404 ✓（外部拿到 URL 但没 secret 也没用 ✓）。"""
    r = client.post("/api/v1/hooks/auto_nope/wrong-secret")
    assert r.status_code == 404, r.text


def test_rotate_invalidates_old_secret(client, hook):
    aid = hook["id"]
    r = client.post(f"/api/v1/automations/{aid}/rotate")
    assert r.status_code == 200
    new_secret = r.json()["secret"]
    assert new_secret != hook["secret"]

    # 旧 secret 立即失效
    r_old = client.post(f"/api/v1/hooks/{aid}/{hook['secret']}")
    assert r_old.status_code == 404
    # 新 secret 可用
    r_new = client.post(f"/api/v1/hooks/{aid}/{new_secret}")
    assert r_new.status_code == 201


def test_signed_hook_rejects_stale_timestamp(client, hook):
    aid, secret = hook["id"], hook["secret"]
    stale_ts = str(int(time.time()) - 3600)
    sign = hmac.new(secret.encode(), f"{stale_ts}.".encode(), hashlib.sha256).hexdigest()
    r = client.post(
        f"/api/v1/hooks/{aid}/{secret}",
        headers={"X-AgentShell-Timestamp": stale_ts, "X-AgentShell-Sign": sign},
    )
    assert r.status_code == 401, "过期时间戳必须被拒（防重放）"


def test_signed_hook_rejects_bad_signature(client, hook):
    aid, secret = hook["id"], hook["secret"]
    ts = str(int(time.time()))
    bad = hmac.new(b"wrong-key", f"{ts}.".encode(), hashlib.sha256).hexdigest()
    r = client.post(
        f"/api/v1/hooks/{aid}/{secret}",
        headers={"X-AgentShell-Timestamp": ts, "X-AgentShell-Sign": bad},
    )
    assert r.status_code == 401


def test_signed_hook_accepts_valid_signature(client, hook):
    aid, secret = hook["id"], hook["secret"]
    ts = str(int(time.time()))
    sign = hmac.new(secret.encode(), f"{ts}.".encode(), hashlib.sha256).hexdigest()
    r = client.post(
        f"/api/v1/hooks/{aid}/{secret}",
        headers={"X-AgentShell-Timestamp": ts, "X-AgentShell-Sign": sign},
    )
    assert r.status_code == 201, r.text


def test_brute_force_rate_limited(client, hook):
    aid = hook["id"]
    codes = []
    for _ in range(7):
        codes.append(client.post(f"/api/v1/hooks/{aid}/wrong-secret-wrong").status_code)
    assert codes.count(429) >= 1, f"连错后必须限流：{codes}"
