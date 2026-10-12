# -*- coding: utf-8 -*-
"""子进程不许闪控制台窗口（Windows 黑框闪烁）—— 2026-10-13 用户真机反馈。

## 现象（用户原话）

> 现在这个应用，我不管点哪个都感觉像闪黑屏那种感觉，就是终端就是闪烁一下子，
> 不管点，就是比如说点设置它就闪一下终端

## 根因

Windows 上从 GUI 进程起**控制台程序**（`nvidia-smi` / `where` / `node` / `cmd` / git bash …）时，
若不给 `CREATE_NO_WINDOW`（0x08000000），系统会**新建一个控制台窗口**再立刻销毁
⇒ 用户看到"黑框闪一下"，**每次调用闪一次** ✗

点「设置」就闪，是因为设置页会打 `/api/v1/capabilities` 等接口，
这些接口内部要探测显卡/环境 ⇒ 调 `nvidia-smi`、`where bash`、`node --version` 等 ✗

## 本文件钉什么

1. `app/` 下**所有** `subprocess.Popen/run/call/check_output` 调用点都必须隐藏窗口
2. **所有** `asyncio.create_subprocess_*` 调用点同理（执行器每跑一条命令都走那里）
3. 用户实测的两个触发点（capabilities 探显卡、envfacts 探环境）单独钉一遍
"""
from __future__ import annotations

import pathlib
import re

APP = pathlib.Path(__file__).resolve().parents[2] / "backend" / "app"

#: 认可的写法（都指同一件事：隐藏子进程控制台窗口）
_NO_WINDOW_NAMES = ("CREATE_NO_WINDOW", "NO_WINDOW_CREATIONFLAGS", "0x08000000")

#: 允许不隐藏的例外（每条都要写理由）。目前为空。
_EXEMPT: set[str] = set()


def _lines(rel: str) -> list[str]:
    return (APP / rel).read_text("utf-8", errors="replace").splitlines()


def _call_sites(pattern: str, window: int = 14):
    """扫出匹配 pattern 的调用点：(相对路径, 行号, 该行, 该行往后 window 行的文本)"""
    out = []
    for p in sorted(APP.rglob("*.py")):
        lines = p.read_text("utf-8", errors="replace").splitlines()
        for i, line in enumerate(lines):
            if re.search(pattern, line):
                out.append((str(p.relative_to(APP.parent.parent)), i + 1, line.strip(),
                            "\n".join(lines[i:i + window])))
    return out


def test_all_subprocess_calls_hide_the_console_window():
    """★ 所有 `subprocess.*` 调用点都必须隐藏控制台窗口，否则用户会看到黑框闪烁 ✗"""
    sites = _call_sites(r"subprocess\.(Popen|run|call|check_output|check_call)\s*\(")
    offenders = [f"  {rel}:{no}\n      {code[:88]}"
                 for rel, no, code, ctx in sites
                 if f"{rel}:{no}" not in _EXEMPT
                 and not any(n in ctx for n in _NO_WINDOW_NAMES)]
    assert len(sites) > 5, f"只扫到 {len(sites)} 个调用点 —— 选择器可能坏了（前提不成立）"
    assert not offenders, (
        "以下 subprocess 调用没隐藏控制台窗口 ⇒ 每调一次用户就看到黑框闪一下 ✗\n"
        + "\n".join(offenders)
        + "\n\n修法：`from .winconsole import NO_WINDOW_CREATIONFLAGS` 然后 **NO_WINDOW_CREATIONFLAGS"
    )


def test_all_asyncio_subprocess_calls_hide_the_console_window():
    """★ 执行器里的 `asyncio.create_subprocess_*` 同样要隐藏窗口。

    这是**最要紧的一处**：Agent 每跑一条命令（还有语法检查、docker 探测、ffmpeg 拼接）
    都会经过它 —— 不隐藏的话，一次任务里用户会看到几十次黑框闪烁 ✗
    """
    sites = _call_sites(r"create_subprocess_(exec|shell)\s*\(")
    offenders = [f"  {rel}:{no}\n      {code[:88]}"
                 for rel, no, code, ctx in sites
                 if f"{rel}:{no}" not in _EXEMPT
                 and not any(n in ctx for n in _NO_WINDOW_NAMES)]
    assert len(sites) >= 5, f"只扫到 {len(sites)} 个 async 调用点 —— 选择器可能坏了（前提不成立）"
    assert not offenders, (
        "以下 asyncio 子进程调用没隐藏控制台窗口 ⇒ 跑命令时会闪黑框 ✗\n"
        + "\n".join(offenders)
    )


def test_capabilities_probe_hides_window():
    """★ 用户实测的触发点：点「设置」会打 /capabilities ⇒ 里面探测显卡，必须隐藏窗口。"""
    src = (APP / "capabilities.py").read_text("utf-8", errors="replace")
    m = re.search(r"subprocess\.run\(\[[^\]]*nvidia-smi[^\]]*\][^)]*\)", src, re.S)
    assert m, "capabilities.py 里找不到 nvidia-smi 探测调用（选择器可能过时）"
    assert any(n in m.group(0) for n in _NO_WINDOW_NAMES), (
        "capabilities.py 的 nvidia-smi 探测没有隐藏窗口 ⇒ 打开设置会闪黑框 ✗"
    )


def test_envfacts_probe_hides_window():
    """环境探测（python / node 版本）同样要隐藏窗口。"""
    lines = _lines("envfacts.py")
    hits = 0
    for i, line in enumerate(lines):
        if "subprocess.run" in line:
            hits += 1
            ctx = "\n".join(lines[i:i + 14])
            assert any(n in ctx for n in _NO_WINDOW_NAMES), (
                f"envfacts.py 第 {i + 1} 行的子进程调用没隐藏窗口 ✗"
            )
    assert hits >= 3, f"envfacts.py 的探测调用点数量变了（只找到 {hits} 处）"


def test_executor_uses_the_shared_constant():
    """执行器要用**统一常量**（`app/winconsole.py`），别各写各的魔数。"""
    src = (APP / "executors" / "local.py").read_text("utf-8", errors="replace")
    assert "NO_WINDOW_CREATIONFLAGS" in src, (
        "executors/local.py 没引用统一常量 NO_WINDOW_CREATIONFLAGS ⇒ "
        "Agent 每跑一条命令都会闪黑框 ✗"
    )
    assert "from ..winconsole import" in src, "executors/local.py 没 import 统一常量 ✗"


def test_the_constant_module_is_cross_platform_safe():
    """`winconsole` 在非 Windows 上必须是空字典 —— 免得给平台不认的参数。"""
    import importlib
    import sys as _sys

    _sys.path.insert(0, str(APP.parent))
    try:
        mod = importlib.import_module("app.winconsole")
    finally:
        _sys.path.pop(0)
    flags = mod.NO_WINDOW_CREATIONFLAGS
    assert isinstance(flags, dict), "NO_WINDOW_CREATIONFLAGS 必须是 dict（好 `**` 展开）✗"
    if _sys.platform == "win32":
        assert flags == {"creationflags": 0x08000000}, f"Windows 上应是 CREATE_NO_WINDOW：{flags}"
    else:
        assert flags == {}, "非 Windows 上必须是空字典 ✗"
