# -*- coding: utf-8 -*-
"""★ 环境交底（2026-10-05 第三轮真群回归暴露的真问题）。

## 现场

程序员的活烧完了 25 步预算失败，而它最后一句是：
> "code_check 显示 py_compile 通过（可能用了不同环境），**先找系统里可用的完整 Python**"

也就是说：它把整个预算花在**找一个能用的解释器**上 ——
而这件事**后端一秒钟就能查清并告诉它**（这台机器 PATH 上的 `python` 是残件：
只有 exe+DLL、没有 `Lib\\`，`python --version` 照常打印但 `-m py_compile` 当场炸；
这是台账 N19 记过的坑）。

## 结论

**环境的坑要让系统交底，不能让每个员工各自踩一遍。**
所以派活时把"本机有哪些能用的工具"写进工作单（一次探测、缓存复用）：

· 能用的 Python（`AGENT_SHELL_PYTHON` → `py -3` → `python`，且**必须能 import encodings**）
· 能不能用 node / npm
· 工作区绝对路径（省得它到处找）

探测失败或找不到，就**如实说"没有可用的 X"**（比让它瞎试强得多）。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from functools import lru_cache

_PROBE_TIMEOUT = 8


def _works(path: str | None) -> bool:
    """解释器可用性判据：跑得起来**而且**能 import encodings（残件解释器就卡在这）。"""
    if not path:
        return False
    try:
        p = subprocess.run([path, "-c", "import encodings,sys;print(sys.version.split()[0])"],
                           capture_output=True, text=True, timeout=_PROBE_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return False
    return p.returncode == 0 and bool((p.stdout or "").strip())


@lru_cache(maxsize=1)
def python_cmd() -> tuple[str | None, str]:
    """找一个**真能用**的 Python。返回 (命令, 版本或原因)。"""
    import os
    cands = []
    env = os.environ.get("AGENT_SHELL_PYTHON")
    if env:
        cands.append(env)
    if sys.executable and _works(sys.executable):
        cands.append(sys.executable)          # 后端自己那个（一定可用）
    for name in ("py", "python", "python3"):
        found = shutil.which(name)
        if found:
            cands.append(found)
    seen: set[str] = set()
    for c in cands:
        if c in seen:
            continue
        seen.add(c)
        key = c if c.endswith(("py", "py.exe")) else c
        argv = [key, "-3"] if key.endswith(("py", "py.exe")) else [key]
        try:
            p = subprocess.run(argv + ["-c", "import encodings,sys;print(sys.version.split()[0])"],
                               capture_output=True, text=True, timeout=_PROBE_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            continue
        if p.returncode == 0 and (p.stdout or "").strip():
            return (" ".join(argv), (p.stdout or "").strip())
    return (None, "没找到能用的 Python（PATH 上的可能是残件：缺 Lib\\）")


@lru_cache(maxsize=1)
def facts(workdir: str = "") -> str:
    """给工作单用的"环境交底"短文本（一次探测，之后走缓存）。"""
    lines = ["【本机实况（系统已探测，直接用，别自己找）】"]
    py, ver = python_cmd()
    if py:
        lines.append(f"· Python：用 `{py}`（{ver}）—— 本机 PATH 上的 `python` 可能是残件，"
                     "别在它上面浪费时间；跑脚本请用上面这个命令。")
    else:
        lines.append(f"· Python：**不可用**（{ver}）—— 需要 Python 的活请换工具或如实说明。")
    node = shutil.which("node")
    npm = shutil.which("npm")
    if node:
        try:
            v = subprocess.run([node, "--version"], capture_output=True, text=True,
                               timeout=_PROBE_TIMEOUT).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            v = ""
        lines.append(f"· Node：`{node}`{('（' + v + '）') if v else ''}" + (f"；npm：`{npm}`" if npm else "；**没有 npm**"))
    else:
        lines.append("· Node：**不可用**")
    if workdir:
        lines.append(f"· 工作区：`{workdir}` —— 所有产物写在这里，结尾给出相对路径。")
    lines.append("· 如果某个命令因为环境问题反复失败，**别硬试**：说明情况或换等价做法。")
    return "\n".join(lines)
