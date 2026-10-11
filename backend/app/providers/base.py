"""模型提供者接口 —— 预留接口一。

Provider 的职责：给定任务上下文 + 对话历史，产出"下一步"（AssistantTurn）。
  - turn.tool_call 为空  → loop 结束任务（final_message 交付）
  - turn.tool_call 非空  → loop 执行该工具并把观察结果写回 history
loop 不关心背后是脚本、DeepSeek 还是 Ollama——这就是预留。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass
class PlanUpdate:
    """契约一 plan 事件的 payload"""

    steps: list[dict[str, Any]]  # [{no, text, status}]
    current_step: int
    reflection: str | None = None


@dataclass
class AssistantTurn:
    text: str | None = None  # 迭代过程中的可见发言（可空）
    plan: PlanUpdate | None = None
    tool_call: ToolCall | None = None
    # ★ DeepSeek 思考模式：上游会回 `reasoning_content` ✓ 而**下一轮必须原样带回去** ✗
    #   （不带就 400：The `reasoning_content` in the thinking mode must be passed back）
    #   所以它得**存在 turn 上** ⇒ 由 loop 写进历史 ⇒ 下次请求再带上 ✓
    reasoning: str | None = None
    final_message: str | None = None  # 结束时的交付消息（tool_call 为空时用）
    attachments: list[str] = field(default_factory=list)


class ModelProvider:
    name = "base"

    async def next_turn(
        self,
        task_input: str,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_delta: Callable[[str], None] | None = None,  # 流式增量回调；None = 非流式
    ) -> AssistantTurn:
        raise NotImplementedError

    async def describe_image(self, image_b64: str, mime: str, question: str) -> str:
        """视觉理解（可选能力）：描述一张图片。不支持视觉的提供者保持默认实现。"""
        raise NotImplementedError("该提供者不支持视觉")
