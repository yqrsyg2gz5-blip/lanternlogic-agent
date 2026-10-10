"""流式用量统计 —— P2-17。

**修复前**：流式请求不带 `stream_options`，上游默认不返回 usage，
于是 `input_tokens` 恒为 0、`output_tokens` 只是按长度粗估 —— 用户看到「输入 0 tok」，
**成本完全不可见**（商用场景这是硬伤）。

**修复后**：
1. 流式请求显式带 `stream_options: {"include_usage": true}`
2. 上游返回真实 usage → 用它（并**不打估算标记**）
3. 上游没返回 → **输入输出都按长度估算**，并在事件里标注「（估算）」
4. 上游**不认这个参数**（部分国产/自建网关会 400）→ 去掉它重试，**宁可少拿用量也不能让流式整个失败**

用 httpx.MockTransport 模拟上游，不碰真实网络。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx

from app.providers import openai_compat as oc


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        base_url="http://upstream/v1",
        model_name="m",
        temperature=0.1,
        max_tokens=16,
        api_key_env=None,
    )


def _patch(monkeypatch, handler) -> None:
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):  # noqa: ANN001, ANN002
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(oc.httpx, "AsyncClient", fake_client)


def _sse(*chunks: str) -> bytes:
    body = "".join(f"data: {c}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return body.encode("utf-8")


def test_stream_requests_usage_from_upstream(monkeypatch):
    """★ 必须显式带 stream_options，否则上游不返回用量。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, content=_sse('{"choices":[{"delta":{"content":"你好"}}]}'))

    _patch(monkeypatch, handler)
    provider = oc.OpenAICompatProvider(_cfg())
    asyncio.run(provider.next_turn("hi", [{"role": "user", "content": "hi"}], [], lambda d: None))

    assert seen.get("stream") is True
    assert seen.get("stream_options") == {"include_usage": True}, "必须向上游要 usage"


def test_real_usage_is_used_and_not_marked_estimated(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse(
            '{"choices":[{"delta":{"content":"你好"}}]}',
            '{"choices":[],"usage":{"prompt_tokens":123,"completion_tokens":45}}',
        ))

    _patch(monkeypatch, handler)
    provider = oc.OpenAICompatProvider(_cfg())
    asyncio.run(provider.next_turn("hi", [{"role": "user", "content": "hi"}], [], lambda d: None))

    assert provider.total_usage["input_tokens"] == 123
    assert provider.total_usage["output_tokens"] == 45
    assert provider.total_usage["estimated"] is False, "真实用量不该被标成估算"


def test_falls_back_to_estimate_when_usage_missing(monkeypatch):
    """上游不返回 usage：输入输出都要有值（旧实现输入恒 0），并标记为估算。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse('{"choices":[{"delta":{"content":"一二三四五"}}]}'))

    _patch(monkeypatch, handler)
    provider = oc.OpenAICompatProvider(_cfg())
    asyncio.run(provider.next_turn("hi", [{"role": "user", "content": "一二三四五"}], [], lambda d: None))

    u = provider.total_usage
    assert u["input_tokens"] > 0, "输入不能是 0（这正是 P2-17 的症状）"
    assert u["output_tokens"] > 0
    assert u["estimated"] is True, "估算值必须被标记出来"


def test_provider_rejecting_stream_options_still_streams(monkeypatch):
    """★ 兜底：不认 stream_options 的上游（400）必须自动去掉它重试，不能整个失败。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        calls["n"] += 1
        body = json.loads(request.content.decode("utf-8"))
        if "stream_options" in body:
            return httpx.Response(400, json={"error": {"message": "unknown parameter stream_options"}})
        return httpx.Response(200, content=_sse('{"choices":[{"delta":{"content":"通了"}}]}'))

    _patch(monkeypatch, handler)
    provider = oc.OpenAICompatProvider(_cfg())
    turn = asyncio.run(
        provider.next_turn("hi", [{"role": "user", "content": "hi"}], [], lambda d: None)
    )

    assert turn.text == "通了", "去掉 stream_options 后应当成功"
    assert calls["n"] == 2
    assert provider.total_usage["estimated"] is True, "拿不到真实用量时退化为估算"
