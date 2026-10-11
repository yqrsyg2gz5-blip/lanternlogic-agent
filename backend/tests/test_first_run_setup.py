"""Phase 1 ①（首次上手引导 · 第一刀）：**没配 Key 时，第一眼就要看得见**。

为什么先做这一刀：
  新用户拿到的是一个能输入、能点发送的空态 —— 但如果 `model.api_key_env` 对应的
  环境变量没设置，后端 `create_provider()` 在构造时就抛，建任务直接 503
  （`main.py:_launch_task`）。用户看到的只是"发出去没动静"，没有任何指引。
  这一刀把"第一眼"补上：Hero 顶部出现一条提示，写清三步（去哪配、Key 存哪、要重启）。

  为什么 Key 是写【环境变量】而不是 config.json（产品契约，别改坏）：
  本仓的既有口径就是"Key 不落盘"（`config.json` 只存 `api_key_env` 变量名，
  `/api/v1/settings` 只回传"是否已设置"）。提示文案必须照这个说，否则等于教用户
  把密钥写进配置文件 —— 那是**倒退**。

本文件两组锚点：
  ① 后端三态契约：`key_set` = true（已配）/ false（**需要但没配**）/ None（不需要 Key）
     —— 前端判据是 `=== false`，若后端退化成"一律 bool"，本地 Ollama/Mock 用户
     会被误报"没配 Key"（假警报）⇒ 这条钉住三态。
  ② 前端接线：Hero 必须真的读 `key_set === false` 并渲染提示条，且文案必须说
     "环境变量 / 不写进配置文件"（三要素齐：去哪配、存哪、重启）。
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app import main as m

HERO = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "components" / "Hero.tsx").read_text("utf-8")


# ═══ ① 后端三态契约 ═══

def _settings_key_set(monkeypatch, key_env: str, env_value: str | None) -> object:
    monkeypatch.setattr(m.cfg.model, "api_key_env", key_env, raising=False)
    if env_value is None:
        monkeypatch.delenv(key_env, raising=False)
    else:
        monkeypatch.setenv(key_env, env_value)
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.get("/api/v1/settings")
        assert r.status_code == 200, r.text
        return r.json()["model"]["key_set"]


def test_key_set_true_when_env_present(monkeypatch):
    assert _settings_key_set(monkeypatch, "DSH_ONBOARD_PROBE_KEY", "sk-probe-1234567890") is True


def test_key_set_false_when_env_missing(monkeypatch):
    """★ 首次上手的判据就是这个 False（不是"空字符串"、不是 None）。"""
    assert _settings_key_set(monkeypatch, "DSH_ONBOARD_PROBE_KEY", None) is False


def test_key_set_none_when_provider_needs_no_key(monkeypatch):
    """本地 Ollama / Mock：key_env 为空 ⇒ None（前端用 `=== false` 判，不误报）。"""
    assert _settings_key_set(monkeypatch, "", None) is None


# ═══ ② 前端接线（静态锚：前端无单测 runner，沿用 test_frontend_guards.py 的做法）═══

def test_hero_reads_key_set_tristate():
    assert "key_set" in HERO, "Hero 没读 settings.model.key_set —— 首次上手提示无从触发"
    assert re.search(r"key_set\s*===\s*false", HERO), \
        "必须用 `=== false` 判（三态）；用 falsy 判会把 Ollama/Mock 也当成没配 Key"


def test_hero_setup_strip_has_the_three_answerables():
    """提示条必须回答三件事：去哪配 / Key 存哪 / 要不要重启。"""
    assert "去设置" in HERO, "没有可点的入口（用户得自己找设置）"
    assert "设置 → 模型设置" in HERO, "没说清去哪配"
    assert "环境变量" in HERO and "不写进配置文件" in HERO, \
        "没说清 Key 存哪 —— 本仓契约是【不落盘】，文案写反了等于教用户把密钥写进 config.json"
    assert "重启" in HERO, "没说保存后要不要重启（后端进程内环境变量不会自己刷新）"


def test_hero_setup_strip_opens_the_real_settings_entry():
    """入口必须打开**真的**设置面板（侧栏那个 title=设置 的按钮），不是摆样子。"""
    assert re.search(r"""querySelector<HTMLButtonElement>\('button\[title="设置"\]'\)""", HERO), \
        "「去设置」没有接在真实入口上"
