# -*- coding: utf-8 -*-
"""阶段 1：包里的**便携 Python 运行时**（★ 2026-10-11）。

## 要解决什么

  · 阶段 0 之后，门槛剩一条：**用户得自己装 Python 3.11+ 并勾 Add to PATH** ✗
  · 阶段 1 把官方 **embeddable Python**（10.6MB ✓）+ 预装依赖装进包里的 `runtime/`
    ⇒ 用户**什么都不用装** ✓ 解压双击就用 ✓
  · 依赖用**随包的 wheels** 离线装 ✓（8.2MB ✓）⇒ 连网都不用 ✓

## 本文件钉五件事

  ① `install.bat` **优先用随包的 `runtime\\python.exe`** ✓
     找不到才退回 AGENT_SHELL_PYTHON / py -3 / python ✓
  ② 走随包这条路时**不建 venv、不联网** ✓（依赖已经在包里 ✓ 探一下 import 就行 ✓）
  ③ 依赖万一缺了 ⇒ 用 `wheels\\` **离线**重装 ✓（`--no-index --find-links` ✓）
  ④ `start.bat` 也**优先用随包运行时** ✓（然后才是 backend\\.venv、PATH ✓）
  ⑤ ★ **源码树那条路一个字没变** ✗：没有 runtime/ 的目录照样建 venv + 从镜像装 ✓

★ 实测（不是只跑测试 ✓）：把包解到空目录、**把 Python 与 Node 都从 PATH 里拿掉**、
  跑 install.bat ⇒ 退出码 0 ✓ 再用包里的运行时跑 preflight ⇒「一切就绪」✓
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
INSTALL = (ROOT / "install.bat").read_text("utf-8", errors="replace")
START = (ROOT / "start.bat").read_text("utf-8", errors="replace")


# ═══ ① install.bat 优先随包运行时 ═══

def test_installer_prefers_the_shipped_runtime():
    assert 'if exist "%~dp0runtime\\python.exe" set "PYEXE=%~dp0runtime\\python.exe"' in INSTALL, \
        "install.bat 没有优先用随包的 runtime\\python.exe ✗（那用户还是得自己装 Python ✓）"
    assert 'set "SHIPPED=1"' in INSTALL, "没有标记'这次用的是随包运行时' ✗"


# ═══ ② 随包这条路：不建 venv、不联网 ═══

def test_shipped_path_skips_venv_and_network():
    assert "if defined SHIPPED goto :deps_shipped" in INSTALL, "没有分流到'随包依赖'那条路 ✗"
    assert ":deps_shipped" in INSTALL and ":deps_done" in INSTALL, "分流的落点不在了 ✗"
    shipped = INSTALL.split("\n:deps_shipped")[1].split("\n:deps_done")[0]
    assert "venv" not in shipped, "随包那条路还在建 venv ✗（没必要 ✓ 而且嵌入式 Python 建 venv 会多一层坑 ✓）"
    assert "--no-index" in shipped, "随包那条路没有 --no-index ⇒ 会去联网 ✗"
    assert "already bundled" in shipped, "依赖已随包时没有明说 ✓（用户会以为在装东西 ✓）"


# ═══ ③ 缺依赖时用 wheels 离线重装 ═══

def test_offline_wheel_fallback_exists():
    assert '--find-links "%~dp0wheels"' in INSTALL, "缺依赖时没有指向随包的 wheels\\ ✗"
    assert "import fastapi, uvicorn, httpx" in INSTALL, "没有先探一下依赖在不在 ✗"


# ═══ ④ start.bat 也优先随包运行时 ═══

def test_start_prefers_the_shipped_runtime():
    i_runtime = START.index('set "PY=%ROOT%runtime\\python.exe"')
    i_venv = START.index('set "PY=%ROOT%backend\\.venv\\Scripts\\python.exe"')
    i_path = START.index('set "PY=python"')
    assert i_runtime < i_venv < i_path, "start.bat 的 Python 查找顺序不对 ✗（应先 runtime ✓ 再 venv ✓ 最后 PATH ✓）"


# ═══ ⑤ 源码树那条路没被动过 ✗ ═══

def test_source_checkout_path_is_untouched():
    assert '-m venv "%~dp0backend\\.venv"' in INSTALL, "源码树那条建 venv 的路被删了 ✗（开发者会装不起来 ✓）"
    assert "PIP_INDEX_URL" in INSTALL, "源码树那条走镜像源的路没了 ✗"
    # 分流必须**在**建 venv 之前 ✓ 否则随包的包也会去建 venv ✓（上面 ② 已细看，这里再钉顺序 ✓）
    assert INSTALL.index("if defined SHIPPED goto :deps_shipped") < INSTALL.index('-m venv "%~dp0backend\\.venv"'), \
        "分流位置在'建 venv'之后 ✗ ⇒ 随包的包照样会去建 venv ✓"
