# -*- coding: utf-8 -*-
"""技能自带的「找 ffmpeg」共用工具 —— 找到**真正能用**的那个，并给出可直接复制的设置命令。

## 为什么有它（2026-10-13 真机事故）

应用执行命令用的是 **git bash**（实测 `bash 5.2.37(1)-release`），而在 bash 里：

    which ffmpeg  → /d/Software/GPT-SoVITS/GPT-SoVITS-v2pro-20250604/runtime/ffmpeg
    libx264 条数  → 0        ← **残缺版，编不了 H.264** ✗

完整那个在 `D:\\MiniMax Design\\current\\resources\\ffmpeg`（libx264 = 2 ✓），
但它在 bash 的 PATH 里**排在最后**，被前面那个盖住了（改 PATH 顺序也未必生效，
因为 bash 与 Windows 的 PATH 拼接规则不同）。

**而且技能原来只给 PowerShell 语法**（`$env:PATH = ...`）——
在 bash 里直接报 `:PATH: command not found`，**照做反而掉坑** ✗

⇒ 本模块做两件事：
  1. **找出真正能用的 ffmpeg/ffprobe**（**真的跑 `-encoders` 验 libx264**，
     不是只看文件存在 —— PATH 上那个也存在，但它是残的 ✗）
  2. 给出 **bash 与 PowerShell 两种**可直接复制的设置命令（**bash 优先**）

## 用法

    from ffmpeg_path import find_ffmpeg, env_snippets, shell_hint
    ff = find_ffmpeg()                 # → 'D:\\MiniMax Design\\...\\ffmpeg.exe'
    print(env_snippets(ff)['bash'])    # → export PATH="/d/MiniMax Design/...:$PATH"

命令行：
    & <python> tools/ffmpeg_path.py            # 打印诊断 + 两种 shell 的设置命令
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

#: 已知可用的 ffmpeg 所在目录（放在最前：本机实测这里的带 libx264）
KNOWN_GOOD_DIRS = (
    r"D:\MiniMax Design\current\resources\ffmpeg",
)

#: 显式指定的环境变量（优先级最高）
ENV_KEY = "AGENT_FFMPEG"

#: 探测超时（秒）—— `-encoders` 很快，超时说明这个二进制有问题
_PROBE_TIMEOUT = 30.0


def has_libx264(ffmpeg: str) -> bool:
    """这个 ffmpeg 到底带不带 libx264？

    ★ **必须真的跑一次**，不能只看文件存在 ——
      真机上 PATH 那个也存在、也是 ffmpeg，但 `libx264` 条数是 **0** ✗
      渲染会在**编码那一步**才崩（画面都画完了才失败，白跑一趟）。
    """
    if not ffmpeg:
        return False
    try:
        if not Path(ffmpeg).exists():
            return False
    except OSError:
        return False
    try:
        p = subprocess.run([ffmpeg, "-hide_banner", "-encoders"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=_PROBE_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return False
    out = (p.stdout or "") + (p.stderr or "")
    return "libx264" in out


def _iter_candidates() -> list[str]:
    """按优先级列出候选：显式指定 → 已知可用目录 → PATH。"""
    out: list[str] = []
    env = os.environ.get(ENV_KEY)
    if env:
        out.append(env)
    for d in KNOWN_GOOD_DIRS:
        for name in ("ffmpeg.exe", "ffmpeg"):
            cand = str(Path(d) / name)
            if cand not in out:
                out.append(cand)
    on_path = shutil.which("ffmpeg")
    if on_path and on_path not in out:
        out.append(on_path)
    return out


def find_ffmpeg() -> str | None:
    """找到**第一个真能用（带 libx264）**的 ffmpeg；找不到返回 None。

    ★ 找不到就返回 None，**不许退而求其次返回残版** ——
      残版会让渲染在最后一步崩，比早点报错更浪费 ✗
    """
    for cand in _iter_candidates():
        if has_libx264(cand):
            return cand
    return None


def find_ffprobe(ffmpeg: str | None = None) -> str | None:
    """找与 ffmpeg **同目录**的 ffprobe（版本配套，避免奇怪的不兼容）。"""
    base = Path(ffmpeg or find_ffmpeg() or "")
    if not base.name:
        return shutil.which("ffprobe")
    for name in ("ffprobe.exe", "ffprobe"):
        cand = base.parent / name
        if cand.exists():
            return str(cand)
    return shutil.which("ffprobe")


def to_posix(path: str) -> str:
    """Windows 盘符路径 → bash 认的 `/d/...` 形式（已经 posix 的原样返回）。"""
    s = str(path or "").strip().replace("\\", "/")
    if len(s) >= 2 and s[1] == ":" and s[0].isalpha():
        return "/" + s[0].lower() + s[2:]
    return s


def env_snippets(ffmpeg: str) -> dict[str, str]:
    """给出**两种 shell** 的可复制设置命令。

    ★ 路径含空格（本机就是 `MiniMax Design`）**必须加引号** ——
      实测不加引号的 `export PATH=/d/MiniMax Design/...:$PATH` 直接失败 ✗
    ★ `export` 必须是**同一行前置**：环境变量只影响当前 shell，
      下一个 `shell_exec` 是新进程、拿不到 ⇒ 文档里要把这行和命令写在**一起** ✓
    """
    d_win = str(Path(ffmpeg).parent)
    d_posix = to_posix(d_win)
    return {
        "bash": f'export PATH="{d_posix}:$PATH"',
        "powershell": f"$env:PATH = '{d_win};' + $env:PATH",
        "ffmpeg": ffmpeg,
        "ffprobe": find_ffprobe(ffmpeg) or "",
        "dir_posix": d_posix,
        "dir_windows": d_win,
    }


def shell_hint() -> str:
    """默认用哪种 shell 说明 —— **bash**。

    真机实测：应用（`app/executors/local.py::_resolve_bash`）执行命令走
    **git bash**，所以文档默认要给 bash 语法 ✓
    """
    return ("bash（本应用执行命令用它 —— 实测 `bash 5.2.37`）。"
            "若你确实在 PowerShell 里手工跑，用文末的 PowerShell 写法。")


def diagnose() -> int:
    """命令行诊断：打印现状 + 两种 shell 的设置命令。"""
    ff_all = _iter_candidates()
    print("=" * 74)
    print("ffmpeg 定位")
    print("=" * 74)
    print("候选（按优先级）：")
    for c in ff_all:
        ok = has_libx264(c)
        exists = Path(c).exists() if c else False
        mark = "✓ 可用" if ok else ("✗ 无 libx264" if exists else "✗ 不存在")
        print(f"  {mark:<14} {c}")
    good = find_ffmpeg()
    print()
    if not good:
        print("结论：✗ **找不到带 libx264 的 ffmpeg** —— 渲染到编码那步会崩。")
        print("      请装一个完整的 ffmpeg，或设 AGENT_FFMPEG 指向它。")
        return 1
    print(f"结论：✅ 用这个 → {good}")
    sn = env_snippets(good)
    print()
    print("把下面**任意一行**和你真正要跑的命令写在**同一条命令里**")
    print("（环境变量只影响当前 shell，下一条命令是新进程、拿不到）：")
    print()
    print("  【bash —— 本应用用这个】")
    print(f"    {sn['bash']}")
    print()
    print("  【PowerShell —— 手工在 PS 里跑时用】")
    print(f"    {sn['powershell']}")
    print()
    print(f"  ffprobe 配套：{sn['ffprobe']}")
    print()
    print("  ★ 路径含空格 ⇒ **引号不能省**（本机目录名就是 `MiniMax Design`）")
    return 0


def main(argv: list[str] | None = None) -> int:
    return diagnose()


if __name__ == "__main__":
    sys.exit(main())
