# -*- coding: utf-8 -*-
"""请求体检：畸形消息组合不许发出去（2026-10-13 用户真机 400 事后加固）。

## 事故

用户真机 `task_20261012_d0edc269`（GLM5.3 视频任务）运行中失败：

    上游 400：Cannot have 2 or more assistant messages at the end of the list.
    type: invalid_request_error

**这是应用自己拼出来的畸形组合，不是模型的问题** —— 上游直接拒收，任务判 failed ✗

事后查磁盘历史：63 条、末尾 6 条是 `assistant/tool` 交替、**悬空 tool_calls = 0**
⇒ **坏掉的那一版只存在于运行内存里，现场已随进程消失** ✗（这也是为什么必须加体检：
   下次再出现时，**要么被本地拦下并说清哪里畸形，要么压根发不出去**，不再是一句上游 400）

## 上游为什么拒（语义）

OpenAI 兼容协议里，`assistant(tool_calls)` 与紧随其后的 `tool` 回执是**一组**。
末尾若出现**两组 assistant**（或一条 assistant 后面没有它的回执），严格网关就拒收。

## 本文件钉什么

`app/providers/openai_compat.py` 发请求前必须做**体检 + 修复**：

  1. 末尾不能有 ≥2 条连续 assistant（合并成一条）
  2. `assistant(tool_calls)` 声明了工具却没有对应 `tool` 回执的，**补一条说明性回执**
     （仓里已有同款处理：`_RECEIPT_PLACEHOLDER`「该工具调用未产生回执…」）
  3. 体检只改**结构**，不该动正常内容（正常历史必须原样通过）
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def _prov():
    from app.providers.openai_compat import OpenAICompatProvider

    class Cfg:
        base_url = "http://127.0.0.1:8080/v1"
        model_name = "t"
        api_key_env = ""
        max_tokens = 64
        temperature = 0.7

    return OpenAICompatProvider(Cfg())


def _roles(msgs):
    return [m.get("role") for m in msgs]


# ═══ 1) 末尾两条连续 assistant 必须被合并 ═══

def test_two_trailing_assistant_messages_get_merged():
    """★ 复现上游那句 400：末尾两条 assistant ⇒ 必须合成一条再发。"""
    p = _prov()
    history = [
        {"role": "user", "content": "任务书"},
        {"role": "assistant", "content": "第一步的想法"},
        {"role": "assistant", "content": "还没收到工具回执就又想了一句"},
    ]
    out = p._assemble(history)
    tail = _roles(out)[-3:]
    assert tail.count("assistant") < 2 or tail[-1] != "assistant" or tail[-2] != "assistant", (
        f"末尾仍是两条连续 assistant ⇒ 上游会 400 ✗  实际={tail}"
    )
    # 内容不许丢
    joined = "\n".join(str(m.get("content") or "") for m in out)
    assert "第一步的想法" in joined and "还没收到工具回执就又想了一句" in joined


def test_many_trailing_assistants_collapse_to_one():
    """连续 N 条 assistant 都要能收成一条（不是只处理两条）。"""
    p = _prov()
    history = [{"role": "user", "content": "go"}] + [
        {"role": "assistant", "content": f"想法{i}"} for i in range(5)
    ]
    out = p._assemble(history)
    rs = _roles(out)
    assert rs[-1] == "assistant"
    assert rs[-2] != "assistant", f"末尾仍有连续 assistant ✗ {rs[-4:]}"
    for i in range(5):
        assert any(f"想法{i}" in str(m.get("content") or "") for m in out), f"想法{i} 丢了 ✗"


# ═══ 2) 悬空 tool_calls 必须补回执 ═══

def test_dangling_tool_call_gets_a_receipt():
    """assistant 声明了工具、后面没有回执 ⇒ 补一条说明性 tool 回执（否则严格网关拒收）。"""
    p = _prov()
    history = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "call_1", "type": "function",
                         "function": {"name": "web_search", "arguments": "{}"}}]},
    ]
    out = p._assemble(history)
    assert _roles(out)[-1] == "tool", f"悬空 tool_calls 没补回执 ✗ 末尾={_roles(out)[-2:]}"
    last = out[-1]
    assert last.get("tool_call_id") == "call_1", f"回执的 tool_call_id 不对：{last.get('tool_call_id')}"
    assert str(last.get("content") or "").strip(), "回执内容不能是空的 ✗"


def test_dangling_tool_call_before_another_assistant_is_handled():
    """悬空回执后面又跟了 assistant（就是本次事故的形态）—— 既要补回执、又不能留连续 assistant。"""
    p = _prov()
    history = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "web_search", "arguments": "{}"}}]},
        {"role": "assistant", "content": "我又想了一句"},
    ]
    out = p._assemble(history)
    rs = _roles(out)
    # 末尾不能是两条 assistant
    assert not (len(rs) >= 2 and rs[-1] == "assistant" and rs[-2] == "assistant"), \
        f"末尾连续 assistant ✗ {rs[-4:]}"
    # c1 必须有回执
    receipts = [m for m in out if m.get("role") == "tool"]
    assert any(m.get("tool_call_id") == "c1" for m in receipts), "c1 没有回执 ✗"


# ═══ 3) 正常历史必须原样通过（别把好数据改坏） ═══

def test_normal_history_passes_through_unchanged():
    """★ 护栏不能误伤：正常的 assistant/tool 交替历史必须**一字不改**。"""
    p = _prov()
    history = [
        {"role": "user", "content": "任务书"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "搜索结果"},
        {"role": "assistant", "content": "看完了"},
        {"role": "user", "content": "继续"},
    ]
    out = p._assemble(history)
    # 去掉开头 system 后应与输入完全一致
    assert out[1:] == history, f"正常历史被改动了 ✗\n出={out[1:]}\n入={history}"


def test_real_failed_history_still_passes():
    """用户那条失败任务的真实历史（63 条）经体检后**结构合法**。"""
    path = pathlib.Path(r"D:\测试-Flash对比\LanternLogic-Agent\backend\data\tasks"
                        r"\task_20261012_d0edc269\history.json")
    if not path.exists():
        pytest.skip(f"拿不到那条真事故的 history（{path}）—— 该面无法验证 ✓")
    import json as _json

    h = _json.loads(path.read_text("utf-8", errors="replace"))
    msgs = h if isinstance(h, list) else h.get("messages", [])
    p = _prov()
    out = p._assemble(msgs)
    rs = _roles(out)
    # ① 末尾不许两条 assistant
    assert not (len(rs) >= 2 and rs[-1] == "assistant" and rs[-2] == "assistant"), \
        f"体检后末尾仍有连续 assistant ✗ {rs[-4:]}"
    # ② 不许悬空 tool_calls
    for i, m in enumerate(out):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            ids = [c.get("id") for c in m["tool_calls"]]
            got = {x.get("tool_call_id") for x in out[i + 1:i + 4] if x.get("role") == "tool"}
            assert all(x in got for x in ids), f"第 {i} 条仍有悬空 tool_calls {ids} ✗"
    # ③ 任务书还在
    assert any(m.get("role") == "user" for m in out), "体检把任务书弄丢了 ✗"


# ═══ 4) 静默修复要有据可查（别偷偷改） ═══

def test_repair_is_reported_not_silent():
    """★ 体检**改了什么要能说出来** —— 静默改用户数据是更坏的做法 ✗"""
    p = _prov()
    history = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "a"},
        {"role": "assistant", "content": "b"},
    ]
    notes = p.sanitize_notes(history) if hasattr(p, "sanitize_notes") else None
    assert notes, ("没有暴露『体检改了什么』的接口 ⇒ 修了也查不到 ✗"
                   "（需要 openai_compat 提供一个能返回修复说明的方法）")
