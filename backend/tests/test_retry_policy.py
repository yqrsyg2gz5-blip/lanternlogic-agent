"""上游限流的重试策略 —— 真实任务暴露出来的缺口。

发现路径：2026-09-30 用真实任务（扫描 Downloads，158 个文件）验收时，
**第一个请求就撞上 `429 Too Many Requests`，任务 3 秒内 failed** ——
因为旧实现只重试 5xx，429 被当成"配置错误"直接抛出。

本文件用 httpx.MockTransport 模拟上游，不碰真实网络。
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
        api_key_env=None,  # 本地服务不需要 Key，避免读环境变量
    )


def _patch(monkeypatch, handler) -> None:
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):  # noqa: ANN001, ANN002
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(oc.httpx, "AsyncClient", fake_client)


def test_is_retryable_covers_429_and_5xx():
    assert oc._is_retryable(429) is True, "429 必须可重试（真实任务就是死在这）"
    assert oc._is_retryable(503) is True
    assert oc._is_retryable(500) is True
    assert oc._is_retryable(400) is False, "请求本身有问题，重试没意义"
    assert oc._is_retryable(401) is False
    assert oc._is_retryable(404) is False


def test_retry_delay_honors_retry_after():
    resp = httpx.Response(429, headers={"retry-after": "7"})
    assert oc._retry_delay(resp, 0) == 7.0
    # 上限 60s，且不因上游乱填而无限等
    assert oc._retry_delay(httpx.Response(429, headers={"retry-after": "9999"}), 0) == 60.0


def test_retry_delay_falls_back_to_backoff():
    assert oc._retry_delay(None, 0) == 2.0
    assert oc._retry_delay(None, 1) == 3.0
    assert oc._retry_delay(None, 2) == 5.0


def test_429_then_success_is_retried(monkeypatch):
    """★ 核心回归：第一次 429、第二次 200 —— 任务必须活下来。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429,
                json={"error": {"code": "429", "message": "Too Many Requests"}},
                headers={"retry-after": "1"},
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "活下来了"}}], "usage": {}},
        )

    _patch(monkeypatch, handler)
    provider = oc.OpenAICompatProvider(_cfg())
    turn = asyncio.run(provider.next_turn("hi", [{"role": "user", "content": "hi"}], []))
    assert turn.text == "活下来了"
    assert calls["n"] == 2, "应当重试一次后成功"


def test_client_error_is_not_retried(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": {"message": "bad request"}})

    _patch(monkeypatch, handler)
    provider = oc.OpenAICompatProvider(_cfg())
    try:
        asyncio.run(provider.next_turn("hi", [{"role": "user", "content": "hi"}], []))
        raise AssertionError("400 应当直接抛错")
    except RuntimeError as e:
        assert "400" in str(e)
    assert calls["n"] == 1, "4xx（非限流）不该重试"


def test_429_exhausted_reports_actionable_hint(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "1"}, json={"error": {"code": "429"}})

    _patch(monkeypatch, handler)
    provider = oc.OpenAICompatProvider(_cfg())
    try:
        asyncio.run(provider.next_turn("hi", [{"role": "user", "content": "hi"}], []))
        raise AssertionError("重试耗尽后应当抛错")
    except RuntimeError as e:
        msg = str(e)
        assert "429" in msg and "限流" in msg, f"错误信息要能指导用户：{msg}"
