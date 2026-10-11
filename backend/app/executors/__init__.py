"""执行器注册表 —— local 现役；docker/wsl2/e2b 未来按同接口注册。"""
from __future__ import annotations

from ..config import ExecutorCfg
from .base import ExecResult, Executor
from .local import LocalExecutor

__all__ = ["ExecResult", "Executor", "create_executor"]

_REGISTRY: dict[str, type[Executor]] = {
    "local": LocalExecutor,
}


def create_executor(cfg: ExecutorCfg) -> Executor:
    cls = _REGISTRY.get(cfg.type)
    if cls is None:
        raise ValueError(f"未知 executor.type：{cfg.type}（已注册：{sorted(_REGISTRY)}）")
    return cls(cfg)
