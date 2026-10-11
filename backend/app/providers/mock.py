"""Mock 提供者 —— 无需 API Key 的确定性演示，节奏与真实 loop 完全一致。

三步剧本：看目录 → 写文件 → 交付。用来端到端验证
REST/SSE/JSONL/审批外的整条链路，也是新 provider 的参考实现。
"""
from __future__ import annotations

from typing import Any

from .base import AssistantTurn, ModelProvider, PlanUpdate, ToolCall


def _plan(steps_status: list[str], current: int, reflection: str | None = None) -> PlanUpdate:
    texts = ["查看工作区目录", "写入说明文件 result.md", "汇总交付"]
    steps = [
        {"no": i + 1, "text": t, "status": s}
        for i, (t, s) in enumerate(zip(texts, steps_status))
    ]
    return PlanUpdate(steps=steps, current_step=current, reflection=reflection)


class MockProvider(ModelProvider):
    name = "mock"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # 注册表统一用 cls(model_cfg) 实例化，mock 不需要配置但签名必须兼容
        self._step = 0

    async def describe_image(self, image_b64: str, mime: str, question: str) -> str:
        return "（mock 提供者无视觉能力——这是占位描述：图片内容略）"

    async def next_turn(
        self, task_input: str, history: list[dict[str, Any]], tools: list[dict[str, Any]], on_delta: Any = None
    ) -> AssistantTurn:
        self._step += 1
        if self._step == 1:
            return AssistantTurn(
                text="收到！我先制定计划，看看工作区里有什么。",
                plan=_plan(["in_progress", "pending", "pending"], 1,
                           "Mock 提供者：固定三步，演示真实 agent loop 节奏。"),
                tool_call=ToolCall(name="list_dir", arguments={"path": "."}),
            )
        if self._step == 2:
            return AssistantTurn(
                text="目录为空，写入说明文件。",
                plan=_plan(["done", "in_progress", "pending"], 2, "工作区是空的，直接创建产物。"),
                tool_call=ToolCall(
                    name="file_write",
                    arguments={
                        "path": "result.md",
                        "content": f"# {task_input}\n\n由 LanternLogic Agent 后端（mock provider）生成。\n",
                    },
                ),
            )
        return AssistantTurn(
            text="收尾中。",
            plan=_plan(["done", "done", "done"], 3, "任务完成。"),
            final_message=f"已完成「{task_input}」：写入 result.md。",
            attachments=["result.md"],
        )
