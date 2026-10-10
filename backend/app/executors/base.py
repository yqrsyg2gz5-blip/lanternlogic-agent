"""执行器接口 —— 预留接口二（local / docker / wsl2 / e2b 可换）。

执行器是唯一的"动手"边界：文件操作和子进程都从这里过，沙箱护栏也在这里。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ExecResult:
    ok: bool
    output: str
    duration_ms: int


class Executor:
    type_name = "base"

    async def run_tool(self, tool: str, args: dict[str, Any], workdir: Path) -> ExecResult:
        raise NotImplementedError
