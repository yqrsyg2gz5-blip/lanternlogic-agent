"""把"模型写成**文本**的工具调用"救回成**真正的工具调用**（2026-10-05 用户任务实测）。

真实现象（task_20261005_25f7c5d9 事件 #13–#16）：
    模型把一次工具调用整段塞进了**正文**：
        <tool_call><function=update_plan><parameter=steps>[{"text": "梳理自我介绍要点（…
        要点已梳理完成，准备输出介绍</parameter></function></tool_call>
    后果有两层：
      ① 界面把这段 XML 当"助手说的话"显示（观感"乱套"）—— 前端已拦，但**事件里还是脏的**；
      ② 更贵的一层：那一轮**白跑了** —— 真正下发的那次工具调用参数不全
         （`参数校验失败：缺少必填参数：['steps']`），计划没更新，还得再烧一轮补救。

所以根治要在这里：provider 收到回复后，从正文里把这类标记**解析成 ToolCall** 并把它从正文删掉。
这样 ① 事件/history 干净 ② 参数从文本里救回来，不用再烧一轮。

支持三种写法（第一种是实测抓到的真样本）：
    A. <tool_call><function=NAME><parameter=KEY>VALUE</parameter>…</function></tool_call>
    B. <function=NAME><parameter=KEY>VALUE</parameter>…</function>      （没有外层包裹）
    C. <tool_call>{"name": "NAME", "arguments": {…}}</tool_call>        （JSON 写法）

原则：**宁可少认，不可错认** —— 只认带明确标记的整块；认不出来就把标记剥掉（别让用户看 XML），
但绝不去猜"这堆普通文字是不是工具调用"。
"""
from __future__ import annotations

import json
import re
from typing import Any

from .base import ToolCall

_OUTER = re.compile(r"<tool_call>(.*?)</tool_call>", re.S)
_UNCLOSED = re.compile(r"<tool_call>.*$", re.S)          # 流式截断时的残块
_FUNC = re.compile(r"<function=([A-Za-z_][\w\-]*)\s*>(.*?)(?:</function>|$)", re.S)
_PARAM = re.compile(r"<parameter=([A-Za-z_][\w\-]*)\s*>(.*?)(?:</parameter>|(?=<parameter=)|$)", re.S)
_JSON_PAYLOAD = re.compile(r"^\s*[\[{]")


def _coerce(value: str) -> Any:
    """参数值尽量按 JSON 解（真样本里就是一个 JSON 数组），解不出来就当字符串。"""
    v = value.strip()
    if not v:
        return ""
    if _JSON_PAYLOAD.match(v):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return v
    return v


def _parse_block(block: str) -> ToolCall | None:
    """解析一个 <tool_call> 内层（或裸 <function=…>）。"""
    # C. JSON 写法
    body = block.strip()
    if _JSON_PAYLOAD.match(body):
        try:
            obj = json.loads(body)
            name = obj.get("name") or (obj.get("function") or {}).get("name")
            args = obj.get("arguments") or (obj.get("function") or {}).get("arguments") or {}
            if isinstance(args, str):
                args = json.loads(args)
            if isinstance(name, str) and name:
                return ToolCall(name=name, arguments=args if isinstance(args, dict) else {})
        except Exception:
            return None
        return None
    # A/B. <function=…><parameter=…>
    m = _FUNC.search(block)
    if not m:
        return None
    name, inner = m.group(1), m.group(2)
    args: dict[str, Any] = {}
    for pm in _PARAM.finditer(inner):
        args[pm.group(1)] = _coerce(pm.group(2))
    return ToolCall(name=name, arguments=args)


def parse_inline_tool_calls(text: str) -> tuple[str, list[ToolCall]]:
    """→ (清洗后的正文, 解析出的工具调用列表)。正文里这类标记一律删掉。"""
    if not text or "<" not in text:
        return text or "", []
    calls: list[ToolCall] = []

    def take(m: re.Match[str]) -> str:
        c = _parse_block(m.group(1))
        if c:
            calls.append(c)
        return ""

    cleaned = _OUTER.sub(take, text)
    # 裸 <function=…>（没有外层 <tool_call>）
    if "<function=" in cleaned:
        def take_bare(m: re.Match[str]) -> str:
            c = _parse_block(m.group(0))
            if c:
                calls.append(c)
            return ""

        cleaned = re.sub(r"<function=[\s\S]*?(?:</function>|$)", take_bare, cleaned)
    # 残块（流式截断）：能救就救，救不了也把标记删掉
    if "<tool_call>" in cleaned:
        cleaned = _UNCLOSED.sub("", cleaned)
    # 兜底：孤立的收尾标记
    cleaned = re.sub(r"</?(?:parameter|function|tool_call|tool_calls)[^>]*>", "", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, calls


def recover_inline_tool_call(turn: Any) -> Any:
    """把 turn.text 里的内联工具调用救成 turn.tool_call（就地修改并返回，便于链式调用）。

    与已有结构化调用的关系：
      · 结构化调用**缺失** ⇒ 直接用文本里救回来的
      · 结构化调用**同名但参数不全** ⇒ 用文本里的参数补齐（真样本就是缺 `steps`）
      · 名字不一致 ⇒ 保留结构化的（它更权威），文本照样清掉
    一次迭代只取第一个调用（与 loop 的既有铁律一致）。
    """
    if turn is None:
        return turn
    text = getattr(turn, "text", None) or ""
    if "<" not in text:
        return turn
    cleaned, calls = parse_inline_tool_calls(text)
    if cleaned != text:
        turn.text = cleaned or None
    if not calls:
        return turn
    first = calls[0]
    cur = getattr(turn, "tool_call", None)
    if cur is None:
        turn.tool_call = first
    elif getattr(cur, "name", "") == first.name and first.arguments:
        merged = {**first.arguments, **(getattr(cur, "arguments", None) or {})}
        # 结构化那边缺失的键由文本补上（结构化已有的值优先 —— 它更权威）
        cur.arguments = merged
    return turn
