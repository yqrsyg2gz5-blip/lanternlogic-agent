# -*- coding: utf-8 -*-
"""保存 Key 的两道闸 + 一本账（★ 2026-10-10 用户实测那件事的产物）。

现场（用户原话）："我每切换另一个 API…它就不好用了"／"保存了就是保存了…不能这么做"
  查实：MiMo 那格被写成了别家的 Key ✗（现在 35 位，今早还是 51 位）
        ⇒ 之后每次任务 `上游 401 {"type": "invalid_key"}` ✗
        ⇒ **而界面上看不出任何异常** ✗
          （能力表只查"变量有没有值"✓；模型列表接口把 401 吞了、还回 200 ✗）

本文件钉五件事：
  ① 要求校验时：服务商**明确拒收**（401/403）⇒ 一个字都不许存 ✗（配置也不许动 ✓）
  ② **网络不通**／其它错误 ⇒ 照常保存 ✓（不能因为网不好就不让用户存 Key ✗）
  ③ 每次写 Key 都**记账** ✓ —— 账上只有**指纹**（长度+头尾 4 位）✗ 没有全值 ✓
  ④ ★ 兼容性：**没要求校验时，行为与以前一模一样** ✓（不许多打一次网络请求 ✗）
  ⑤ 前端接线：换服务商必须清空 Key 输入框 ✓；"Key 存放位置"是只读 ✓
"""
from __future__ import annotations

import os
import pathlib

import pytest
from fastapi.testclient import TestClient

from app import audit
from app import main as m

PROBE_ENV = "DSH_KEY_GUARD_PROBE"
PROBE_KEY = "sk-guard-probe-1234567890abcdef"
TSX = (pathlib.Path(__file__).resolve().parents[2]
       / "frontend" / "src" / "components" / "SettingsPanel.tsx")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """与 test_key_persist 同款防护：不碰真注册表 / 不写配置 / 不留内存改动 / 账本写临时目录。"""
    for _k, _v in m.cfg.model.model_dump().items():
        monkeypatch.setattr(m.cfg.model, _k, _v, raising=False)
    monkeypatch.setattr(m, "_save_config", lambda: None)
    monkeypatch.setattr(m, "write_user_env", lambda n, v: (True, "（测试：假装写成功）"))
    monkeypatch.setattr(audit, "_PATH", tmp_path / "audit.jsonl")
    monkeypatch.delenv(PROBE_ENV, raising=False)
    yield
    os.environ.pop(PROBE_ENV, None)          # 端点会写进程环境，测完清掉 ✓


def _post(payload: dict):
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        return c.post("/api/v1/settings/model", json=payload)


# ═══ ① 拒收 ⇒ 一个字都不许存 ═══

def test_rejected_key_is_not_saved_at_all(monkeypatch):
    async def _reject(provider, base_url, key):
        return False, "服务商拒收这把 Key（HTTP 401）"

    monkeypatch.setattr(m, "_probe_key", _reject)
    m.cfg.model.provider = "deepseek"                 # 先站一个已知状态
    r = _post({"provider": "mimo", "base_url": "https://api.xiaomimimo.com/v1",
               "api_key": PROBE_KEY, "api_key_env": PROBE_ENV, "verify_key": True})

    assert r.status_code == 400, r.text
    assert "没有保存" in r.json()["detail"], r.text
    assert not os.environ.get(PROBE_ENV), "拒收了却还是把 Key 写进进程环境了 ✗"
    assert m.cfg.model.provider == "deepseek", "拒收了却把配置改了 ✗（要求：一个字都不动）"


def test_rejection_is_written_to_the_ledger(monkeypatch):
    async def _reject(provider, base_url, key):
        return False, "服务商拒收这把 Key（HTTP 403）"

    monkeypatch.setattr(m, "_probe_key", _reject)
    _post({"provider": "mimo", "api_key": PROBE_KEY, "api_key_env": PROBE_ENV, "verify_key": True})
    rows = audit.tail(10, kind="key_write")
    assert rows and rows[0]["result"] == "rejected", rows


# ═══ ② 网络不通 ⇒ 照常保存（不许因为网不好拦人 ✗）═══

def test_network_error_still_saves(monkeypatch):
    async def _net_err(provider, base_url, key):
        return True, "没验成（ConnectError）—— 网络问题不拦你，已照常保存"

    monkeypatch.setattr(m, "_probe_key", _net_err)
    r = _post({"provider": "mimo", "api_key": PROBE_KEY, "api_key_env": PROBE_ENV,
               "verify_key": True, "persist": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert os.environ.get(PROBE_ENV) == PROBE_KEY, "网络不通时应当照常保存 ✓"
    assert "没验成" in body["note"], body["note"]


# ═══ ③ 记账只记指纹 ═══

def test_saved_key_is_audited_with_fingerprint_only(monkeypatch):
    async def _ok(provider, base_url, key):
        return True, "Key 校验通过（HTTP 200）"

    monkeypatch.setattr(m, "_probe_key", _ok)
    _post({"provider": "mimo", "api_key": PROBE_KEY, "api_key_env": PROBE_ENV,
           "verify_key": True, "persist": True})
    rows = audit.tail(10, kind="key_write")
    assert rows and rows[0]["result"] == "saved", rows
    row = rows[0]
    assert row["env"] == PROBE_ENV and row["provider"] == "mimo", row
    assert f"len={len(PROBE_KEY)}" in row["fingerprint"], row
    assert PROBE_KEY not in str(row), f"账本里出现了 Key 全值 ✗：{row}"


def test_fingerprint_never_returns_the_whole_key():
    fp = m._key_fingerprint(PROBE_KEY)
    assert PROBE_KEY not in fp and "len=" in fp, fp
    assert m._key_fingerprint("short") == "len=5（太短，不打头尾）", m._key_fingerprint("short")


# ═══ ④ 兼容性：没要求校验 ⇒ 一次网络都不打 ═══

def test_no_probe_when_caller_does_not_ask(monkeypatch):
    calls: list[str] = []

    async def _spy(provider, base_url, key):
        calls.append(provider)
        return False, "如果被调到，本用例就该红"

    monkeypatch.setattr(m, "_probe_key", _spy)
    r = _post({"provider": "mimo", "api_key": PROBE_KEY, "api_key_env": PROBE_ENV,
               "persist": True})                     # ← 没有 verify_key
    assert r.status_code == 200, r.text
    assert calls == [], f"没要求校验却打了网络：{calls}"
    assert os.environ.get(PROBE_ENV) == PROBE_KEY


# ═══ ⑤ 前端接线（源码锚点）═══

def test_frontend_clears_key_box_when_switching_provider():
    src = TSX.read_text("utf-8")
    start = src.index("const pickPreset =")
    body = src[start:src.index("};", start)]
    assert "setApiKey('')" in body, (
        "换服务商没清空 Key 输入框 ✗ —— 上一家还没保存的 Key 会跟着走，"
        "一点保存就写进这一家的环境变量（2026-10-10 实测事故）")


def test_frontend_key_location_field_is_readonly():
    src = TSX.read_text("utf-8")
    assert "Key 存放位置（只读）" in src, "那个「Key 环境变量名」输入框还让人随便改 ✗"
    assert "readOnly" in src, "只读没加上 ✗"


# ═══ ⑥ 换到"本地无需 Key"的服务时，那一格必须能清空 ═══
#   ★ 2026-10-10（接本地 Bonsai 时发现的缺口 ✗）：此前只在**非空**时才写
#     ⇒ 从云端切到本地后，api_key_env 还留着上一家的变量名 ✗（语义错 ✓）

def test_switching_to_local_clears_the_key_env(monkeypatch):
    for _k, _v in m.cfg.model.model_dump().items():
        monkeypatch.setattr(m.cfg.model, _k, _v, raising=False)
    monkeypatch.setattr(m, "_save_config", lambda: None)
    m.cfg.model.api_key_env = "DEEPSEEK_API_KEY"
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.post("/api/v1/settings/model", json={
            "provider": "bonsai", "model_name": "bonsai-2-27b",
            "base_url": "http://127.0.0.1:8080/v1", "api_key_env": ""})
    assert r.status_code == 200, r.text
    assert m.cfg.model.api_key_env == "", (
        f"切到本地服务却没清掉变量名 ✗：{m.cfg.model.api_key_env!r}（会残留上一家的 ✓）")


def test_omitting_key_env_keeps_the_old_value(monkeypatch):
    """★ 向后兼容：**不传**这个字段（None）= 这次不动它 ✓（现有调用方全都不传 ✓）。"""
    for _k, _v in m.cfg.model.model_dump().items():
        monkeypatch.setattr(m.cfg.model, _k, _v, raising=False)
    monkeypatch.setattr(m, "_save_config", lambda: None)
    m.cfg.model.api_key_env = "DEEPSEEK_API_KEY"
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.post("/api/v1/settings/model", json={"provider": "deepseek",
                                                   "model_name": "deepseek-flash"})
    assert r.status_code == 200, r.text
    assert m.cfg.model.api_key_env == "DEEPSEEK_API_KEY", "不传却把人家改了 ✗"
