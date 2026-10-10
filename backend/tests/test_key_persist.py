"""Phase 1 ① 后半截：把 Key 一键写进 **User 级环境变量**（重启后仍然有效）。

背景（2026-10-04 血案的根）：
  设置页填的 Key 只写进**当前进程**的 `os.environ` ⇒ 后端一重启就没了 ⇒
  "界面能开、HTTP 200、发消息永远不回复"。正确落点是 User 级环境变量
  （`HKCU\\Environment`）——`restart-backend.ps1` 正是从那里读出来注入子进程的。

本文件钉五件事（**全部用假注册表，绝不碰用户真实的 HKCU\\Environment**）：
  ① 默认**不写**：请求里没显式 `persist=True` 时，写入钩子一次都不许被调
  ② 显式 `persist=True` 时按【清洗后的 Key】写，且**响应里不许出现 Key 明文**
  ③ 写入失败要**如实回报**（persisted=False + 原因进 note），不许假装成功
  ④ 低层 `write_user_env` 自带**回读校验**：读回不一致就报失败（诚实优先）
  ⑤ 非 Windows 如实说"不支持"并给手工做法（不猜用户的 shell 配置文件）
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main as m
from app import uservenv

PROBE_ENV = "DSH_PERSIST_PROBE_KEY"
PROBE_KEY = "sk-persist-probe-1234567890"


def _walk(node, path="$"):
    if isinstance(node, dict):
        for k, v in node.items():
            yield f"{path}.{k}[键名]", str(k)
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, str(node)


@pytest.fixture(autouse=True)
def _no_config_write(monkeypatch):
    """★ 本文件只测"写不写环境变量"，**不许碰配置落盘、也不许留下内存改动**。

    端点里的 `_save_config()` 会写 `_CONFIG_PATH`（测试下 = conftest 的临时配置）。
    不拦的话：① 污染共享的临时配置；② 万一哪天写隔离又被改坏，本文件的测试会跟着把
    **用户真实 config.json** 写坏 —— 与 tests/test_api_key_hygiene.py 同款防护。

    ★★ 本班实测的第二个坑：光拦落盘还不够 —— 端点还会改**内存里的 cfg**。
      本文件把 provider 改成 mimo（而临时配置的 base_url 是空的）⇒ 同一进程里
      后面的用例 `create_provider(cfg.model)` 直接抛 `model.base_url 未配置`
      ⇒ `test_webhook_security` / `test_startup_selfcheck` 共 3 条红。
      所以这里把 cfg.model 的每个字段都纳入 monkeypatch（用例结束自动还原）。
    """
    for _k, _v in m.cfg.model.model_dump().items():
        monkeypatch.setattr(m.cfg.model, _k, _v, raising=False)
    monkeypatch.setattr(m, "_save_config", lambda: None)


def _post(payload: dict):
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.post("/api/v1/settings/model", json=payload)
        assert r.status_code == 200, r.text
        return r.json()


# ═══ ① 默认不写 ═══

def test_no_persist_by_default(monkeypatch):
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(m, "write_user_env", lambda n, v: (calls.append((n, v)), (True, "x"))[1])
    _post({"provider": "mimo", "model_name": "mimo-v2.6-flash", "api_key_env": PROBE_ENV,
           "api_key": PROBE_KEY})
    assert calls == [], f"没要求持久化却动了注册表：{calls}"


# ═══ ② 显式 persist：写清洗后的 Key、响应不回显 ═══

def test_persist_writes_cleaned_key_and_never_echoes_it(monkeypatch):
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(m, "write_user_env",
                        lambda n, v: (calls.append((n, v)), (True, "已写入用户级环境变量"))[1])
    body = _post({"provider": "mimo", "model_name": "mimo-v2.6-flash", "api_key_env": PROBE_ENV,
                  "api_key": f"  {PROBE_KEY}  ", "persist": True})   # 故意带空白 → 必须被 strip
    assert calls == [(PROBE_ENV, PROBE_KEY)], f"写入的不是清洗后的 Key：{calls}"
    leaks = [p for p, s in _walk(body) if PROBE_KEY in s]
    assert not leaks, f"响应里回显了 Key 明文：{leaks}"
    assert body["persisted"] is True and body["key_env"] == PROBE_ENV
    assert "用户级环境变量" in body["note"], body["note"]


def test_persist_failure_reported_honestly(monkeypatch):
    monkeypatch.setattr(m, "write_user_env",
                        lambda n, v: (False, "写入用户级环境变量失败：PermissionError: 被策略锁"))
    body = _post({"provider": "mimo", "api_key_env": PROBE_ENV, "api_key": PROBE_KEY,
                  "persist": True})
    assert body["persisted"] is False, body
    assert "PermissionError" in body["note"] and "重启后会丢" in body["note"], body["note"]


def test_no_api_key_means_no_write(monkeypatch):
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(m, "write_user_env", lambda n, v: (calls.append((n, v)), (True, "x"))[1])
    _post({"provider": "mimo", "api_key_env": PROBE_ENV, "persist": True})   # 没带 Key
    assert calls == [], "没带 Key 却写了注册表"


# ═══ ④ 低层：回读校验（假注册表）═══

def test_write_user_env_verifies_readback(monkeypatch):
    fake: dict[str, str] = {}
    monkeypatch.setattr(uservenv, "IS_WINDOWS", True)
    monkeypatch.setattr(uservenv, "_win_set", lambda n, v: fake.__setitem__(n, v))
    monkeypatch.setattr(uservenv, "_win_get", lambda n: fake.get(n))
    ok, why = uservenv.write_user_env(PROBE_ENV, PROBE_KEY)
    assert ok and PROBE_ENV in why and PROBE_KEY not in why, why
    assert fake[PROBE_ENV] == PROBE_KEY


def test_write_user_env_reports_mismatch(monkeypatch):
    """★ 诚实优先：写进去、读回来不一致（被策略/安全软件改掉）必须报失败，不许报成功。"""
    monkeypatch.setattr(uservenv, "IS_WINDOWS", True)
    monkeypatch.setattr(uservenv, "_win_set", lambda n, v: None)
    monkeypatch.setattr(uservenv, "_win_get", lambda n: "SOMETHING-ELSE")
    ok, why = uservenv.write_user_env(PROBE_ENV, PROBE_KEY)
    assert ok is False and "回读不一致" in why, why


def test_write_user_env_rejects_empty(monkeypatch):
    monkeypatch.setattr(uservenv, "IS_WINDOWS", True)
    assert uservenv.write_user_env("", "x")[0] is False
    assert uservenv.write_user_env(PROBE_ENV, "")[0] is False


# ═══ ⑤ 非 Windows：如实说 + 给手工做法 ═══

def test_non_windows_is_honest(monkeypatch):
    monkeypatch.setattr(uservenv, "IS_WINDOWS", False)
    ok, why = uservenv.write_user_env(PROBE_ENV, PROBE_KEY)
    assert ok is False and "export" in why and PROBE_KEY not in why, why


def test_user_env_status_never_returns_value(monkeypatch):
    monkeypatch.setattr(uservenv, "IS_WINDOWS", True)
    monkeypatch.setattr(uservenv, "_win_get", lambda n: PROBE_KEY)
    st = uservenv.user_env_status(PROBE_ENV)
    assert st["set"] is True and st["length"] == len(PROBE_KEY)
    assert PROBE_KEY not in str(st), f"状态接口泄露了 Key：{st}"
