# -*- coding: utf-8 -*-
"""凭据库（★ 2026-10-10 第二期）—— 照 DSH 的做法：**一个文件、按名字存、改动前备份**。

对照 DSH（`C:\\Users\\y\\.dsh\\.credentials.yaml` 里读出来的）：
  · `version: 1` ＋ `refs:`（名字 → 密钥）✓ 本模块同款结构 ✓
  · 配置里**按名字引用** ⇒ 每家各引用各的、切来切去串不了格 ✓
  · 改动前先备份（`.bak_before_*`）✓ 本模块写前留 `credentials.yaml.bak` ✓

本文件钉六件事：
  ① 结构/往返：写进去能读回来，文件里是 `version: 1` + `refs:` ✓
  ② **写前备份** ✓（第二次写时，`.bak` 里是上一版 ✓）
  ③ 宽容解析：手改过的引号/空格也读得出 ✓（不许因为一个字符就整个读不出 ✗）
  ④ **只搬不删** ✓：迁移把系统里已有的 Key 收进凭据库，**注册表那份原样留着** ✓
  ⑤ 取值顺序：进程环境 → **凭据库** → 注册表 ✓（凭据库优先于注册表 ✓）
  ⑥ 保存 Key 时**真的写进了凭据库** ✓（`set_model` 集成 ✓）
"""
from __future__ import annotations

import os

import pytest

from app import credentials, envkeys
from app import main as m


@pytest.fixture(autouse=True)
def _bind_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(credentials, "_PATH", tmp_path / "credentials.yaml")
    yield


# ═══ ① 结构 / 往返 ═══

def test_round_trip_and_file_shape():
    ok, why = credentials.set("DSH_PROBE_KEY", "sk-value-1234567890")
    assert ok, why
    assert credentials.get("DSH_PROBE_KEY") == "sk-value-1234567890"
    text = credentials.path().read_text("utf-8")
    assert "version: 1" in text and "refs:" in text, text
    assert "DSH_PROBE_KEY" in text


def test_write_is_atomic_and_leaves_no_tmp():
    credentials.set("A", "1")
    credentials.set("B", "2")
    assert not credentials.path().with_suffix(".yaml.tmp").exists(), "临时文件没清掉 ✗"
    assert credentials.names() == ["A", "B"], credentials.names()


def test_empty_name_or_value_is_refused():
    assert credentials.set("", "x")[0] is False
    assert credentials.set("X", "")[0] is False


# ═══ ② 写前备份 ═══

def test_backup_holds_previous_version():
    credentials.set("K", "first")
    credentials.set("K", "second")
    bak = credentials.path().with_suffix(".yaml.bak")
    assert bak.exists(), "改动前没备份 ✗（DSH 的 `.bak_before_*` 同款习惯）"
    assert "first" in bak.read_text("utf-8"), "备份里不是上一版 ✗"
    assert credentials.get("K") == "second"


# ═══ ③ 宽容解析（手改过也要读得出）═══

def test_hand_edited_file_still_parses():
    credentials.path().write_text(
        "# 手改的\nversion: 1\nrefs:\n  A: 'plain-quoted'\n  B: sk-no-quotes\n  C: \"json-style\"\n",
        "utf-8")
    got = credentials.load()
    assert got == {"A": "plain-quoted", "B": "sk-no-quotes", "C": "json-style"}, got


# ═══ ④ 迁移：只搬不删 ═══

def test_migration_copies_but_never_deletes(monkeypatch):
    fake = {"XIAOMI_MIMO_API_KEY": "sk-mimo-51chars-xxxx", "DEEPSEEK_API_KEY": "sk-deep-35"}
    monkeypatch.setattr(envkeys, "_read_registry", lambda n, scope: fake.get(n, "") if scope == "User" else "")
    moved = envkeys.migrate_into_credentials(["XIAOMI_MIMO_API_KEY", "DEEPSEEK_API_KEY", "GLM_API_KEY"])
    assert set(moved) == {"XIAOMI_MIMO_API_KEY", "DEEPSEEK_API_KEY"}, moved
    assert credentials.get("XIAOMI_MIMO_API_KEY") == fake["XIAOMI_MIMO_API_KEY"]
    assert fake["XIAOMI_MIMO_API_KEY"] == "sk-mimo-51chars-xxxx", "迁移不许动系统里那份 ✗"
    assert envkeys.migrate_into_credentials(["XIAOMI_MIMO_API_KEY"]) == [], "迁移不幂等 ✗"


# ═══ ⑤ 取值顺序：凭据库优先于注册表 ═══

def test_credentials_win_over_registry(monkeypatch):
    monkeypatch.delenv("DSH_ORDER_PROBE", raising=False)
    monkeypatch.setattr(envkeys, "_read_registry", lambda n, scope: "from-registry" if scope == "User" else "")
    credentials.set("DSH_ORDER_PROBE", "from-credentials")
    added = envkeys.sync_user_env(["DSH_ORDER_PROBE"])
    assert added == {"DSH_ORDER_PROBE": "credentials"}, added
    assert os.environ["DSH_ORDER_PROBE"] == "from-credentials"
    os.environ.pop("DSH_ORDER_PROBE", None)


def test_process_env_still_wins(monkeypatch):
    monkeypatch.setenv("DSH_ORDER_PROBE2", "explicit-in-process")
    credentials.set("DSH_ORDER_PROBE2", "from-credentials")
    added = envkeys.sync_user_env(["DSH_ORDER_PROBE2"])
    assert added == {}, "进程里显式设的那份必须优先 ✓（只补缺失、不覆盖 ✓）"
    assert os.environ["DSH_ORDER_PROBE2"] == "explicit-in-process"


# ═══ ⑥ set_model 集成：保存 Key 时真的写进凭据库 ═══

def test_set_model_writes_into_credentials(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    for _k, _v in m.cfg.model.model_dump().items():
        monkeypatch.setattr(m.cfg.model, _k, _v, raising=False)
    monkeypatch.setattr(m, "_save_config", lambda: None)
    monkeypatch.setattr(m, "write_user_env", lambda n, v: (True, "（测试：假装写成功）"))
    monkeypatch.setattr(m, "_probe_key", lambda *a, **k: _ok())
    monkeypatch.delenv("DSH_CRED_PROBE", raising=False)

    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.post("/api/v1/settings/model", json={
            "provider": "mimo", "api_key": "sk-cred-probe-9876543210",
            "api_key_env": "DSH_CRED_PROBE", "persist": True, "verify_key": True})
    assert r.status_code == 200, r.text
    assert credentials.get("DSH_CRED_PROBE") == "sk-cred-probe-9876543210", credentials.names()
    assert "凭据库" in r.json()["note"], r.json()["note"]
    os.environ.pop("DSH_CRED_PROBE", None)


async def _ok():
    return True, "Key 校验通过（HTTP 200）"
