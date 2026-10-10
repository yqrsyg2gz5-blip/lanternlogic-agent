"""提供者注册表 —— 加新 provider = 加一个实现 + 一行注册，核心零改动（契约三规则 2）。"""
from __future__ import annotations

from typing import Any

from .base import AssistantTurn, ModelProvider, PlanUpdate, ToolCall
from .anthropic import AnthropicProvider
from .mock import MockProvider
from .openai_compat import OpenAICompatProvider

__all__ = [
    "AnthropicProvider",
    "AssistantTurn",
    "ModelProvider",
    "PlanUpdate",
    "ToolCall",
    "create_provider",
]

# 别名都指向 OpenAI 兼容实现（base_url 由配置决定）
_REGISTRY: dict[str, type[ModelProvider]] = {
    "mock": MockProvider,
    "openai_compatible": OpenAICompatProvider,
    "anthropic": AnthropicProvider,
    "claude": AnthropicProvider,
    "deepseek": OpenAICompatProvider,
    "qwen": OpenAICompatProvider,
    "glm": OpenAICompatProvider,
    "ollama": OpenAICompatProvider,
    "kimi": OpenAICompatProvider,
    "mimo": OpenAICompatProvider,
    # ★ 2026-10-10（用户点名要接的那个本地模型 ✓）：
    #   Bonsai 2 27B —— llama.cpp 的 `llama-server.exe` 起在 http://127.0.0.1:8080/v1
    #   （D:\Bonsai-demo\启动-Bonsai-单槽32K.bat ✓ 桌面快捷方式「Bonsai2 本地模型」✓）
    #   它本身就是 OpenAI 兼容接口 ⇒ 别名指向同一个实现即可 ✓ 核心零改动 ✓
    #   ★ 无需 Key：预设里 `key_env: ''` ⇒ OpenAICompatProvider 走"本地服务不用 key"那条 ✓
    #   ★ 带视觉（--mmproj 加载了 Qwen-VL 投影器 ✓）⇒ image_read 也能用它 ✓
    "bonsai": OpenAICompatProvider,
}


def create_provider(model_cfg: Any) -> ModelProvider:
    cls = _REGISTRY.get(model_cfg.provider)
    if cls is None:
        raise ValueError(
            f"未知的 model.provider：{model_cfg.provider}（已注册：{sorted(_REGISTRY)}）"
        )
    return cls(model_cfg)
