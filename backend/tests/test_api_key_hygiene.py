"""二十六轮第 7 批第 11 处：API Key 卫生（strip + 拒非 ASCII）。

事故来源（2026-10-04，用户报"发消息不回复"）：
  日志里反复出现
    [记忆] 提取失败（不影响任务）：UnicodeEncodeError: 'ascii' codec can't encode
    characters in position 7-25: ordinal not in range(128)
  `position 7-25` 里的 7 正是 `"Bearer "` 的长度 ⇒ 报错对象是请求头
  `Authorization: Bearer <key>`，即**那一刻进程里的 Key 前 18 个字符全是非 ASCII**
  （复制 Key 时带进了全角空格/中文标点这类字符）。

  为什么难查：构造 provider 时**只检查"非空"**，所以填进去时一切正常；
  偏偏只在【真正发起调用】时才炸，而且报错是一句"position 7-25"，
  既不说哪个变量、也不说该怎么办 —— 用户只能看到"发消息不回复"。

本文件钉住三层的修复：
  ① 入口（`POST /api/v1/settings/model`、视频设置）→ strip + 非 ASCII 直接 422；
  ② 构造（openai_compat / anthropic）→ strip + 非 ASCII 抛带位置的 ValueError
     （这样"系统环境变量里塞了坏 Key"也能在启动自检时给出人话）；
  ③ 值本身能编进 HTTP 头（回归判据：`"Bearer " + key` 必须能 encode("ascii")）。
"""
from __future__ import annotations

import os
import pytest
from fastapi.testclient import TestClient

from app import main as m
from app.providers.openai_compat import OpenAICompatProvider

# 全角空格（U+3000）、全角破折号、中文句号 —— 复制粘贴时最常混进来的三个
FULLWIDTH_SPACE = "\u3000"
BAD_KEYS = [
    "sk-abc" + FULLWIDTH_SPACE + "def123456",   # 全角空格
    "sk-abc好def123456",                        # 中文字
    "ｓｋ－ａｂｃ１２３４５６",                    # 全角字母数字
    "sk-abc123456。",                            # 中文句号
]
GOOD_KEY = "sk-abc1234567890ABCDEF"


@pytest.fixture()
def isolated_env():
    """用【临时环境变量名】做实验，绝不碰真实的 XIAOMI_MIMO_API_KEY。"""
    names = ("AS_TEST_KEY_A", "AS_TEST_KEY_B")
    for n in names:
        os.environ.pop(n, None)
    yield names
    for n in names:
        os.environ.pop(n, None)


@pytest.fixture()
def guarded_app(monkeypatch):
    """保护全局状态：不写 config.json、不改动内存里的 cfg.model。"""
    monkeypatch.setattr(m, "_save_config", lambda: None)
    for attr in ("provider", "model_name", "base_url", "api_key_env"):
        monkeypatch.setattr(m.cfg.model, attr, getattr(m.cfg.model, attr))
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        yield c


# ---------- ③ 回归判据：为什么"非 ASCII"必然会炸 ----------


@pytest.mark.parametrize("key", BAD_KEYS)
def test_bad_key_really_breaks_http_header(key):
    """机制锚点：非 ASCII 的 Key 放进请求头**确实**编不出来。

    而且报错位置满足一个精确关系：`start == 7 + (Key 里第一个非 ASCII 字符的下标)`
    ——7 就是 `"Bearer "` 的长度。日志里那条 `position 7-25` 正是由此而来：
    它指的是**Key 自己的第 1~18 个字符**。这就是"为什么必须拦"的可执行证据。
    """
    hdr = "Bearer " + key
    with pytest.raises(UnicodeEncodeError) as ei:
        hdr.encode("ascii")
    first_bad = next(i for i, ch in enumerate(key) if ord(ch) > 127)
    assert ei.value.start == 7 + first_bad, (
        f"报错位置应 = 7（'Bearer ' 的长度）+ Key 内首个非 ASCII 的下标 {first_bad}"
        f" = {7 + first_bad}，实际 {ei.value.start}"
    )


def test_good_key_encodes():
    ("Bearer " + GOOD_KEY).encode("ascii")   # 不抛即通过


# ---------- ② 构造层 ----------


@pytest.mark.parametrize("key", BAD_KEYS)
def test_provider_rejects_non_ascii_key(monkeypatch, key):
    monkeypatch.setenv("AS_TEST_KEY_A", key)
    cfg = type("C", (), {
        "api_key_env": "AS_TEST_KEY_A", "model_name": "m",
        "base_url": "http://x/v1", "temperature": 0.7, "max_tokens": 8,
    })()
    with pytest.raises(ValueError) as ei:
        OpenAICompatProvider(cfg)
    msg = str(ei.value)
    assert "非 ASCII" in msg, msg
    assert "AS_TEST_KEY_A" in msg, "报错必须点名是哪个环境变量"
    assert "第" in msg, "报错必须给出位置"


def test_provider_strips_key(monkeypatch):
    """首尾空白（复制粘贴最常见）要自动去掉，不能算坏 Key。"""
    monkeypatch.setenv("AS_TEST_KEY_A", "  " + GOOD_KEY + "  \n")
    cfg = type("C", (), {
        "api_key_env": "AS_TEST_KEY_A", "model_name": "m",
        "base_url": "http://x/v1", "temperature": 0.7, "max_tokens": 8,
    })()
    p = OpenAICompatProvider(cfg)
    assert p.api_key == GOOD_KEY


def test_provider_still_rejects_empty_key(monkeypatch):
    """反回归：空值仍报"未设置"（原有口径不许被这次改动改掉）。"""
    monkeypatch.setenv("AS_TEST_KEY_A", "   ")
    cfg = type("C", (), {
        "api_key_env": "AS_TEST_KEY_A", "model_name": "m",
        "base_url": "http://x/v1", "temperature": 0.7, "max_tokens": 8,
    })()
    with pytest.raises(ValueError, match="未设置"):
        OpenAICompatProvider(cfg)


# ---------- ① 入口层 ----------


def test_settings_model_422_on_non_ascii_key(guarded_app, isolated_env):
    r = guarded_app.post("/api/v1/settings/model", json={
        "provider": "mimo", "api_key_env": "AS_TEST_KEY_B",
        "api_key": "sk-abc" + FULLWIDTH_SPACE + "def",
    })
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "非 ASCII" in detail, detail
    assert "AS_TEST_KEY_B" in detail, "要告诉用户是哪个变量"
    assert "AS_TEST_KEY_B" not in os.environ, "坏 Key 绝不能被写进环境变量"


def test_settings_model_strips_key(guarded_app, isolated_env):
    r = guarded_app.post("/api/v1/settings/model", json={
        "provider": "mimo", "api_key_env": "AS_TEST_KEY_B",
        "api_key": "  " + GOOD_KEY + "  \n",
    })
    assert r.status_code == 200, r.text
    assert os.environ.get("AS_TEST_KEY_B") == GOOD_KEY


def test_clean_api_key_is_the_single_implementation():
    """元锚点：入口层必须走同一个 `_clean_api_key`（防有人另写一份、口径漂移）。"""
    from app.main import _clean_api_key
    assert _clean_api_key("  " + GOOD_KEY + "  ", "X") == GOOD_KEY
    with pytest.raises(Exception) as ei:
        _clean_api_key("sk-中" + FULLWIDTH_SPACE, "X")
    assert "非 ASCII" in str(ei.value)
