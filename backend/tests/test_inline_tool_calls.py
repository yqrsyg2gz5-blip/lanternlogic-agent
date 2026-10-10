"""把"模型写成文本的工具调用"救回成真调用（2026-10-05 用户任务实测的那类）。

真样本（task_20261005_25f7c5d9 事件 #13–#16，从事件流里原样抄下来的）：
    <tool_call><function=update_plan><parameter=steps>[{"text": "梳理自我介绍要点（身份、能力、纪律、环境边界）", "status": "in_progress"}, …]</parameter></function></tool_call>
    要点已梳理完成，准备输出介绍

后果两层（都要治）：
  ① 界面把 XML 当"助手说的话"显示（前端已拦，但**事件/history 里仍是脏的**）
  ② 更贵的一层：那一轮**白跑** —— 真正下发的调用参数不全（缺少必填参数 steps），还在再烧一轮补救
"""
from __future__ import annotations

import json

import pytest

from app.providers import openai_compat as oc
from app.providers.base import AssistantTurn, ToolCall
from app.providers.inline_tool import parse_inline_tool_calls, recover_inline_tool_call

# 真样本：注意参数值本身是 JSON、且中间夹着换行
REAL = (
    '<tool_call><function=update_plan><parameter=steps>[{"text": "梳理自我介绍要点（身份、能力、纪律、环境边界）",'
    ' "status": "in_progress"}, {"text": "输出结构化自我介绍", "status": "pending"}]</parameter></function></tool_call>'
    "\n要点已梳理完成，准备输出介绍"
)


def test_real_sample_is_parsed_into_a_real_tool_call():
    cleaned, calls = parse_inline_tool_calls(REAL)
    assert len(calls) == 1, calls
    assert calls[0].name == "update_plan"
    steps = calls[0].arguments.get("steps")
    assert isinstance(steps, list) and len(steps) == 2, calls[0].arguments
    assert steps[0]["text"].startswith("梳理自我介绍要点"), steps
    assert "要点已梳理完成" in cleaned, "正文里那句真话必须留着"
    assert "<tool_call>" not in cleaned and "<parameter=" not in cleaned, "标记要清干净"


def test_recover_fills_missing_arguments_of_the_structured_call():
    """真样本的病情：结构化调用在、但缺 steps ⇒ 用文本里的参数补齐（那一轮就不白跑了）。"""
    turn = AssistantTurn(text=REAL)
    turn.tool_call = ToolCall(name="update_plan", arguments={})          # 缺参数
    out = recover_inline_tool_call(turn)
    assert out.tool_call.name == "update_plan"
    assert isinstance(out.tool_call.arguments.get("steps"), list), out.tool_call.arguments
    assert out.text == "要点已梳理完成，准备输出介绍"


def test_structured_call_wins_when_name_differs():
    turn = AssistantTurn(text=REAL)
    turn.tool_call = ToolCall(name="task_done", arguments={"message": "x"})
    out = recover_inline_tool_call(turn)
    assert out.tool_call.name == "task_done", "名字不一致时结构化调用更权威"
    assert "<tool_call>" not in (out.text or ""), "文本照样要清干净"


@pytest.mark.parametrize("text", [
    "<tool_call>{\"name\": \"shell_exec\", \"arguments\": {\"command\": \"echo hi\"}}</tool_call>",
    "<function=shell_exec><parameter=command>echo hi</parameter></function>",
])
def test_other_written_forms_are_supported(text):
    _, calls = parse_inline_tool_calls(text)
    assert len(calls) == 1 and calls[0].name == "shell_exec", calls
    assert calls[0].arguments.get("command") == "echo hi", calls[0].arguments


def test_plain_text_is_untouched():
    txt = "我先看一下目录里有什么，然后按类型分组统计。这里没有任何工具调用标记。"
    cleaned, calls = parse_inline_tool_calls(txt)
    assert cleaned == txt and calls == [], (cleaned, calls)


def test_prose_is_preserved_around_markup():
    cleaned, calls = parse_inline_tool_calls("好的，我这就去办。\n" + REAL + "\n办完了告诉你。")
    assert len(calls) == 1
    assert cleaned.startswith("好的，我这就去办。") and cleaned.endswith("办完了告诉你。")


def test_unclosed_markup_does_not_crash_and_is_stripped():
    """流式截断时常留半截标记 —— 不许崩，也不许把 XML 留给用户看。"""
    cleaned, _ = parse_inline_tool_calls('<tool_call><function=shell_exec><parameter=command>echo')
    assert "<tool_call>" not in cleaned and "<parameter=" not in cleaned, cleaned


def test_html_ish_normal_text_is_not_mistaken():
    """只有明确标记才认 —— 普通尖括号内容不许被当工具调用（宁可少认不可错认）。"""
    _, calls = parse_inline_tool_calls("这段是文档里的示例：<div class=\"x\">内容</div>，不是调用。")
    assert calls == []


# ═══ provider 两条路径都要接上（不然"某条路径白跑"只在线上偶发时才暴露）═══

def _provider(monkeypatch):
    monkeypatch.setenv("DSH_INLINE_PROBE_KEY", "sk-probe-1234567890")   # 构造 provider 需要 Key
    cfg = type("C", (), {"provider": "mimo", "model_name": "mimo-v2.6-flash",
                         "base_url": "https://api.example.com/v1",
                         "api_key_env": "DSH_INLINE_PROBE_KEY",
                         "temperature": 0.7, "max_tokens": 100, "max_iterations": 3})()
    return oc.OpenAICompatProvider(cfg)


def test_streaming_path_recovers_inline_call(monkeypatch):
    """假 SSE：delta 里把工具调用当正文吐出来（真样本形状）→ turn 里必须是**真调用**。"""
    chunks = [
        {"choices": [{"delta": {"content": "<tool_call><function=update_plan>"}}]},
        {"choices": [{"delta": {"content": "<parameter=steps>[{\"text\": \"a\", \"status\": \"pending\"}]</parameter>"}}]},
        {"choices": [{"delta": {"content": "</function></tool_call>\n打算先梳理要点"}}]},
    ]
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            raise AssertionError("流式路径不该整体 json()")

        async def aiter_lines(self):
            for line in body.splitlines():
                yield line

    class _Stream:
        async def __aenter__(self):
            return _Resp()

        async def __aexit__(self, *a):
            return False

    class _Cli:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, *a, **kw):
            return _Stream()

    import asyncio
    monkeypatch.setattr(oc.httpx, "AsyncClient", _Cli)
    turn = asyncio.run(_provider(monkeypatch).next_turn(
        "hi", [{"role": "user", "content": "hi"}], [],
        on_delta=lambda _s: None))       # 给了 on_delta 才走**流式**路径
    assert turn.tool_call is not None and turn.tool_call.name == "update_plan", turn
    assert isinstance(turn.tool_call.arguments.get("steps"), list), turn.tool_call.arguments
    assert "<tool_call>" not in (turn.text or ""), turn.text


def test_non_streaming_path_recovers_inline_call(monkeypatch):
    payload = {"choices": [{"message": {"content": REAL}}]}

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return payload

    class _Cli:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            return _Resp()

    import asyncio
    monkeypatch.setattr(oc.httpx, "AsyncClient", _Cli)
    turn = asyncio.run(_provider(monkeypatch).next_turn("hi", [{"role": "user", "content": "hi"}], [], on_delta=None))
    assert turn.tool_call is not None and turn.tool_call.name == "update_plan", turn
    assert "<parameter=" not in (turn.text or ""), turn.text
