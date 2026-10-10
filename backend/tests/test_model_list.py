"""Phase 2 ⑥（模型名下拉 + 一键刷新）的锚点 —— **全程离线**（假 HTTP，不碰真网络）。

钉住四件事：
  ① 三家响应形态都能解析：OpenAI 兼容 / Ollama / Anthropic
  ② 失败**不是错误**：没 Key、服务商不提供、超时、返回空 —— 一律 source="preset"
     + 一句人话原因（界面才好退预设），绝不抛 500
  ③ ★ 安全：拒绝非 http(s)、拒绝云元数据地址（169.254.169.254 这类 SSRF 头号目标）；
     但**允许本地回环**（本地 Ollama 是正当用法 —— 这是与 web_fetch 守卫有意为之的差别）
  ④ 缓存：5 分钟内不重复打服务商；`refresh=1` 必须真的重拉
"""
from __future__ import annotations

import json
import pathlib

import httpx
import pytest

from app import main as m
from app import model_list


def _transport(status: int = 200, body: object = None, seen: list | None = None) -> httpx.MockTransport:
    """假 HTTP：记录请求、回固定响应。绝不联外网。"""
    def handler(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append({"url": str(req.url), "headers": dict(req.headers)})
        payload = body if body is not None else {"data": [{"id": "m-1"}, {"id": "m-2"}]}
        return httpx.Response(status, json=payload)
    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _clean_cache():
    model_list.clear_cache()
    yield
    model_list.clear_cache()


# ═══ ① 三家形态 ═══

async def test_parses_openai_shape():
    out = await model_list.fetch_models("mimo", "https://api.example.com/v1", "sk-x",
                                        transport=_transport(body={"data": [{"id": "b"}, {"id": "a"}]}))
    assert out["source"] == "live" and out["models"] == ["a", "b"], out


async def test_parses_ollama_shape():
    out = await model_list.fetch_models("ollama", "http://127.0.0.1:11434", None,
                                        transport=_transport(body={"models": [{"name": "qwen3:0.6b"}]}))
    assert out["models"] == ["qwen3:0.6b"], out


async def test_parses_anthropic_shape_and_sends_its_own_headers():
    seen: list = []
    out = await model_list.fetch_models("anthropic", "https://api.anthropic.com/v1", "sk-ant",
                                        transport=_transport(body={"data": [{"id": "claude-x"}]}, seen=seen))
    assert out["models"] == ["claude-x"], out
    assert seen[0]["headers"].get("x-api-key") == "sk-ant", "Anthropic 要用 x-api-key"
    assert seen[0]["headers"].get("anthropic-version"), "Anthropic 必须带 anthropic-version"
    assert "authorization" not in {k.lower() for k in seen[0]["headers"]}, "不该同时带 Bearer"


# ═══ ② 失败降级（绝不 500）═══

@pytest.mark.parametrize("status,body", [(401, {"error": "bad key"}), (500, {}), (200, {"data": []})])
async def test_failures_degrade_to_preset(status, body):
    out = await model_list.fetch_models("deepseek", "https://api.example.com/v1", "sk-x",
                                        transport=_transport(status=status, body=body))
    assert out["source"] == "preset" and out["models"] == [], out
    assert out["note"], "降级时必须说明原因（不然用户以为本来就这些）"


async def test_network_error_degrades():
    def boom(req):
        raise httpx.ConnectError("网络不通")
    out = await model_list.fetch_models("kimi", "https://api.example.com/v1", "sk-x",
                                        transport=httpx.MockTransport(boom))
    assert out["source"] == "preset" and "ConnectError" in out["note"], out


async def test_missing_base_url_degrades():
    out = await model_list.fetch_models("openai_compatible", "", "sk-x")
    assert out["source"] == "preset" and "base_url" in out["note"], out


# ═══ ③ 地址规矩（含与 web_fetch 守卫的有意差别）═══

async def test_metadata_address_is_refused():
    out = await model_list.fetch_models("openai_compatible", "http://169.254.169.254/latest", "sk-x")
    assert out["source"] == "preset" and "元数据" in out["note"], out


@pytest.mark.parametrize("url", ["file:///C:/x", "ftp://example.com/v1", "not-a-url"])
async def test_non_http_scheme_is_refused(url):
    out = await model_list.fetch_models("openai_compatible", url, "sk-x")
    assert out["source"] == "preset", out


async def test_localhost_is_allowed_on_purpose():
    """★ 本地 Ollama / 局域网自建服务是正当用法 —— 这里**不许**照搬 web_fetch 的 fail-closed。"""
    seen: list = []
    out = await model_list.fetch_models("ollama", "http://127.0.0.1:11434", None,
                                        transport=_transport(body={"models": [{"name": "x"}]}, seen=seen))
    assert out["source"] == "live" and seen, "回环地址被拦了 —— 本地 Ollama 会彻底不可用"
    assert "/api/tags" in seen[0]["url"], "Ollama 的列表接口是 /api/tags"


# ═══ ④ 缓存 ═══

async def test_cache_avoids_second_call_but_refresh_forces_one():
    seen: list = []
    t = _transport(body={"data": [{"id": "m"}]}, seen=seen)
    await model_list.fetch_models("mimo", "https://api.example.com/v1", "sk-x", transport=t)
    again = await model_list.fetch_models("mimo", "https://api.example.com/v1", "sk-x", transport=t)
    assert len(seen) == 1 and again["cached"] is True, f"缓存没生效：{len(seen)} 次请求"
    await model_list.fetch_models("mimo", "https://api.example.com/v1", "sk-x", refresh=True, transport=t)
    assert len(seen) == 2, "refresh=1 必须真的重拉"


# ═══ 端点接线 + 不回显 Key ═══

def test_endpoint_rejects_unknown_provider():
    from fastapi.testclient import TestClient
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.get("/api/v1/models", params={"provider": "不存在的提供者"})
    assert r.status_code == 422, r.text


def test_endpoint_never_echoes_the_key(monkeypatch):
    from fastapi.testclient import TestClient
    secret = "sk-models-leak-probe-1234567890"
    monkeypatch.setenv("XIAOMI_MIMO_API_KEY", secret)
    # ★ 逐字段 monkeypatch（别直接 m.cfg.model.provider = ...）：
    #   直接改共享 cfg 会污染同进程的后续用例 —— 本班实测：改了 provider/base_url 之后
    #   `test_webhook_security` 等 3 条红（报 model.base_url 未配置）。
    #   这条教训（N15）之前已经写进总表备忘，这里第一版又犯了一次。
    monkeypatch.setattr(m.cfg.model, "provider", "mimo", raising=False)
    monkeypatch.setattr(m.cfg.model, "api_key_env", "XIAOMI_MIMO_API_KEY", raising=False)
    monkeypatch.setattr(m.cfg.model, "base_url", "https://api.example.com/v1", raising=False)

    async def fake(p, base, key, refresh=False):
        assert key == secret, "端点没把环境变量里的 Key 传下去（那就永远拉不到）"
        return {"provider": p, "models": ["m1"], "source": "live", "note": "ok", "cached": False}

    monkeypatch.setattr(model_list, "fetch_models", fake)
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.get("/api/v1/models")
    assert r.status_code == 200, r.text
    assert secret not in json.dumps(r.json()), "响应里回显了 Key"
    assert r.json()["models"] == ["m1"]


# ═══ 界面接线（脚本存在 ≠ 接上了）═══
# 前端没有单测 runner，沿用本仓既有做法：钉源码接线（与 test_frontend_guards.py 同款）。

_SETTINGS = (pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"
             / "components" / "SettingsPanel.tsx").read_text("utf-8")
_API = (pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "api.ts").read_text("utf-8")


def test_settings_panel_wires_the_model_list():
    assert ".listModels(" in _SETTINGS, "设置页没调 listModels —— 下拉永远是空的"
    assert "select" in _SETTINGS and "model-select" in _SETTINGS, "没有模型下拉框"
    assert "刷新列表" in _SETTINGS, "没有一键刷新按钮"
    assert "model-source" in _SETTINGS, "没有来源提示（用户分不清真拉到的还是预设）"
    assert "'live'" in _SETTINGS and "preset" in _SETTINGS, "没按 source 区分真实/预设"


def test_settings_panel_auto_fetches_on_entering_section():
    """进「模型设置」就该自动拉一次 —— 不能让用户先猜着按刷新。"""
    assert "loadModels()" in _SETTINGS, "没有自动拉取调用"
    assert "section !== 'model'" in _SETTINGS, "没有进入模型设置才拉的判断"


def test_refresh_button_actually_bypasses_cache():
    assert "loadModels(true)" in _SETTINGS, "刷新按钮没带 refresh=true（那就只是读缓存）"


def test_offline_mock_api_also_has_list_models():
    """离线演示模式也要有这个接口，否则一进设置页就报错。"""
    assert _API.count("listModels") >= 3, "api.ts 里 listModels 的声明/实现/离线实现不齐"
    assert "source: 'preset'" in _API, "离线实现的返回值不是 preset（演示模式会显示成拉到了）"
