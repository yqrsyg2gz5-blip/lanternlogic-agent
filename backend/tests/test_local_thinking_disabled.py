# -*- coding: utf-8 -*-
"""本地模型"吐空回复"的根治 —— 关掉聊天模板的思考模式（2026-10-12 实测）。

## 事故原貌（用户真机 · Bonsai 27B · task_20261012_3aaa5fc1）

任务卡死两次，每次都停在同一个地方：

```
10:22:55  web_fetch 抓到了文章 ✓
10:22:55  ……… 10 分钟没有任何动静 ………
10:33:05  cancelled —— 任务超过 10 分钟没有任何进展，已自动取消
（用户发「继续」后）一模一样又卡 607 秒
```

## 根因（不是模型不行、不是量化坏了、不是温度）

| 测什么 | 结果 |
|---|---|
| raw `/completion`（绕过聊天模板） | **空 0/5** ✅ 一直正常 |
| chat `/v1/chat/completions`（用模板） | **空 4/5** ✗ 大多吐空 |
| prompt 处理速度 | 5.6 万 token 只要 **77 秒** ⇒ **不是慢** |
| 换温度 0.3–1.0 | 都 2–3/5 空 ⇒ **温度无关** |

服务端 `/props` 里那份 Jinja 模板的末尾：

```jinja
{%- if enable_thinking is defined and enable_thinking is false %}
    {{- '<think>\n\n</think>\n\n' }}      ← 显式关思考
{%- else %}
    {{- '<think>\n' }}                     ← ★ 默认**开着**思考
{%- endif %}
```

⇒ 该模型在 ` thinking` 块里**绕圈绕到 token 用光**（实测输出正好顶到 max_tokens、
`finish_reason=length`），**正式回复是空的** ⇒ 应用收到空回复 ⇒ 判定"没有进展" ⇒ 看门狗取消 ✗

## 修法（实测有效）

请求里带 `chat_template_kwargs: {"enable_thinking": false}`：

    默认（思考开）  → 空 3/5 ✗
    显式关掉思考    → 空 0/5 ✓（内容正常、耗时更短、token 更省）

★ 服务端不认这个字段时**会忽略它**（llama.cpp 对未知模板变量就是 undefined）⇒ 对云端 API 安全 ✓
"""
from __future__ import annotations

import sys
from typing import Any

ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def _provider(**over):
    from app.providers.openai_compat import OpenAICompatProvider

    class Cfg:
        base_url = over.get("base_url", "http://127.0.0.1:8080/v1")
        model_name = over.get("model_name", "bonsai-2-27b")
        api_key_env = ""
        max_tokens = 256
        temperature = 0.7

    return OpenAICompatProvider(Cfg())


def _body_for(provider, history=None, tools=None, **over) -> dict[str, Any]:
    """拿 provider 准备好的请求体（不真发请求）。"""
    return provider._build_body(  # type: ignore[attr-defined]
        history if history is not None else [{"role": "user", "content": "你好"}],
        tools if tools is not None else [],
        **over)


def test_request_disables_thinking_for_local_templates():
    """★ 核心：请求体里必须带 `chat_template_kwargs.enable_thinking = False`。

    不带 ⇒ 本地模板默认开思考 ⇒ 模型绕圈吐空 ⇒ 任务被看门狗取消（事故原貌）✗
    """
    p = _provider()
    body = _body_for(p)
    kw = body.get("chat_template_kwargs")
    assert isinstance(kw, dict), (
        "请求体里没有 chat_template_kwargs ⇒ 本地模板会默认打开思考模式，"
        "模型会绕圈吐空回复（就是那条任务卡死的原因）✗"
    )
    assert kw.get("enable_thinking") is False, (
        f"enable_thinking 不是 False（实际 {kw.get('enable_thinking')!r}）"
        "⇒ 思考模式仍开着 ✗"
    )


def test_thinking_flag_is_opt_in_for_cloud():
    """云端 API 不该收到这个字段（虽然服务端会忽略，但别给云端发无用字段）。"""
    p = _provider(base_url="https://api.deepseek.com/v1", model_name="deepseek-flash")
    body = _body_for(p)
    assert "chat_template_kwargs" not in body, (
        "给云端 API 也发了 chat_template_kwargs ✗ —— 它用不上，属于污染请求"
    )


def test_thinking_flag_can_be_re_enabled_for_a_model_that_needs_it():
    """留一个开关：万一某个本地模型**必须**开思考，能被显式打开（别把路堵死）。"""
    p = _provider()
    body = _body_for(p, enable_thinking=True)
    kw = body.get("chat_template_kwargs") or {}
    assert kw.get("enable_thinking") is True, "显式要求开思考时没能开 ✗"


def test_body_still_has_the_normal_fields():
    """别修坏正常字段：model / messages / tools / max_tokens 该在的都在。"""
    p = _provider()
    tools = [{"type": "function", "function": {"name": "t", "description": "d",
                                              "parameters": {"type": "object", "properties": {}}}}]
    body = _body_for(p, tools=tools)
    assert body.get("model") == "bonsai-2-27b"
    assert isinstance(body.get("messages"), list) and body["messages"]
    assert body.get("tools") == tools
    assert body.get("max_tokens")


def test_local_detection_covers_common_local_hosts():
    """本地判定要能认出常见本地地址（127.0.0.1 / localhost / [::1]），云端不算本地。"""
    for url in ("http://127.0.0.1:8080/v1", "http://localhost:1234/v1",
                "http://[::1]:8080/v1", "http://0.0.0.0:8080/v1"):
        p = _provider(base_url=url)
        body = _body_for(p)
        assert body.get("chat_template_kwargs", {}).get("enable_thinking") is False, \
            f"{url} 没被当成本地 ⇒ 没关思考 ✗"
    for url in ("https://api.deepseek.com/v1", "https://api.moonshot.cn/v1"):
        p = _provider(base_url=url)
        assert "chat_template_kwargs" not in _body_for(p), f"{url} 被误判成本地 ✗"


