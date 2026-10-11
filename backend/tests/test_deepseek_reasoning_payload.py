# -*- coding: utf-8 -*-
"""★ 真验：**实际发出去的请求体**里到底有没有 reasoning_content（不猜 ✓ 截下来看 ✓）

方法：把 httpx 的 post 打桩 ✓ 截住 provider 真要发的那份 body ✓ 逐条检查 ✓
—— 这才是"验过了"✗，而不是"代码里有这个字段"✓（今天就是栽在这个区别上 ✓）

依据：DeepSeek 官方《思考模式》原文 ✓
  「携带了 tools 参数的请求，在后续所有请求中，必须**完整回传** reasoning_content 给 API
    ——即使该轮模型未实际进行工具调用。若未正确回传，API 会返回 400 报错。」
  「**历史轮次的** reasoning_content 均应回传」✗（不只最后一条 ✓）
  https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.providers.openai_compat import OpenAICompatProvider


def _provider(base_url: str, model: str = "deepseek-flash") -> OpenAICompatProvider:
    """按**真实签名**造 ✓（它收 model_cfg ✓ 不是散参数 ✗ —— 我第一版就写错了 ✓）"""
    return OpenAICompatProvider(SimpleNamespace(
        base_url=base_url, model_name=model, api_key_env="", temperature=0.3,
        max_tokens=1024, api_key="k"))


class _FakeResp:
    status_code = 200

    def json(self):
        return {
            "choices": [{"message": {"content": "好的", "reasoning_content": "我先想了想"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        }


def _capture(monkeypatch, captured):
    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):  # noqa: A002
            captured.append(json)
            return _FakeResp()

    monkeypatch.setattr("app.providers.openai_compat.httpx.AsyncClient", _FakeClient)


def _history_with_tool_round():
    """一次真实形态的历史：user → assistant(带 tool_calls) → tool 回执 ✓"""
    return [
        {"role": "user", "content": "帮我看看天气"},
        {"role": "assistant", "content": "", "reasoning_content": "我得调用工具",
         "tool_calls": [{"id": "call_001", "type": "function",
                         "function": {"name": "get_weather", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_001", "content": "晴"},
    ]


def _run(provider, history):
    return asyncio.run(provider.next_turn("继续", history, tools=[{"type": "function"}]))


def test_deepseek_messages_all_carry_reasoning_content(monkeypatch):
    """★ DeepSeek + 带 tools ⇒ **每条 assistant 消息都必须带上它** ✗（官方硬要求 ✓）"""
    captured: list[dict] = []
    _capture(monkeypatch, captured)
    _run(_provider("https://api.deepseek.com/v1"), _history_with_tool_round())
    assert captured, "没截到请求体 ✗ 测试本身有问题 ✓"
    assistants = [m for m in captured[-1]["messages"] if m.get("role") == "assistant"]
    assert assistants, "历史里应该有 assistant 消息 ✓"
    for m in assistants:
        assert "reasoning_content" in m, f"有 assistant 消息没带 ✗：{m}"
    assert any(m.get("reasoning_content") == "我得调用工具" for m in assistants), \
        "原有的思考内容被弄丢了 ✗（要**原样**回传 ✓）"


def test_old_history_without_reasoning_is_repaired(monkeypatch):
    """★ 旧历史（报错前存的 ✓ 没这个字段）⇒ 发送前**补空串** ✓ 这样卡住的任务也能救活 ✓"""
    captured: list[dict] = []
    _capture(monkeypatch, captured)
    _run(_provider("https://api.deepseek.com/v1"),
         [{"role": "user", "content": "x"},
          {"role": "assistant", "content": "旧回复",
           "tool_calls": [{"id": "call_009", "type": "function",
                           "function": {"name": "f", "arguments": "{}"}}]}])
    m = [x for x in captured[-1]["messages"] if x.get("role") == "assistant"][0]
    assert "reasoning_content" in m, "旧历史没被补上 ⇒ 用户那个卡住的任务还是 400 ✗"


def test_other_providers_are_not_touched(monkeypatch):
    """★ 别家**不许被补空串** ✗（严格网关见到陌生字段会 400 ✓ 不能为了 DeepSeek 害了别人 ✓）

    ★ 我第一版这条写错了 ✗：拿的历史里**本来就带** reasoning_content ✓
      那是 loop 存进去的 ✓ 透传它没错 ✓ ⇒ 断言该盯的是"**有没有被主动补上**"✗
    """
    captured: list[dict] = []
    _capture(monkeypatch, captured)
    hist = [{"role": "user", "content": "x"},
            {"role": "assistant", "content": "旧回复",
             "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "f", "arguments": "{}"}}]}]
    _run(_provider("https://api.xiaomimimo.com/v1", "mimo-v2.6-flash"), hist)
    for m in captured[-1]["messages"]:
        assert "reasoning_content" not in m, f"别家被主动补了字段 ✗：{m}"


def test_no_tools_means_no_rewrite(monkeypatch):
    """★ 不带 tools ⇒ 官方说"无需回传"✓ ⇒ 我们也不动 ✓（不多事 ✗）"""
    captured: list[dict] = []
    _capture(monkeypatch, captured)
    asyncio.run(_provider("https://api.deepseek.com/v1").next_turn(
        "你好", [{"role": "user", "content": "你好"}], tools=[]))
    for m in captured[-1]["messages"]:
        assert "reasoning_content" not in m, "没带 tools 却改了消息 ✗"
