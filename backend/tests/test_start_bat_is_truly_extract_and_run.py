# -*- coding: utf-8 -*-
"""★ 全新解压的包，双击 `start.bat` 就必须能起来（不许要用户先手动做一步）。

## 现场（2026-10-14，第三方只读测评 + 我用 v0.1.1 包复现）

修完 P0（preflight 技能名单）之后，我拿**新打的包**模拟用户解压，preflight 仍然退出 1：

    [OK] 技能库完整（9 个技能，名字正确）
    [X]  缺 config.json（第一次用要复制一份模板）
         → copy contracts\\config.example.json config.json
    ⇒ 退出码 1 ⇒ start.bat 第 49 行 `if errorlevel 1` 直接退出 ✗

**而 README 对 `-full.zip` 的承诺是「解压即用，什么都不用装」** ✗
⇒ 实际是"解压 → 看到报错 → 手动复制 → 再启动"，与承诺不符。

## 根因

`install.bat` **有**自动生成逻辑（第 140–142 行 `if not exist config.json ( copy … )`），
但 **`start.bat` 完全不管 config** —— 只双击 start.bat 的用户必然被拦死 ✗

## 本文件钉什么

1. `start.bat` 必须在 preflight **之前**补上缺失的 `config.json`（从模板复制）
2. 不许改坏 entry bat 的两条硬规矩：**纯 ASCII** + **CRLF 无 BOM**（见 test_bat_encoding.py）
3. 只能**缺了才补**（已有 config.json 绝不许覆盖 —— 那是用户的配置）
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
START = ROOT / "start.bat"
INSTALL = ROOT / "install.bat"
EXAMPLE = ROOT / "contracts" / "config.example.json"


def _text(p: pathlib.Path) -> str:
    return p.read_text("utf-8", errors="replace")


def test_config_template_ships_in_the_repo():
    """先确认模板在（start.bat 要复制它）。"""
    assert EXAMPLE.exists(), f"模板不在：{EXAMPLE} ✗"


def test_start_bat_creates_a_missing_config():
    """★★ 核心：`start.bat` 必须自己补上缺失的 config.json。

    没有这一步，「解压即用」就是假的 —— 用户双击只会看到报错然后窗口关掉 ✗
    """
    src = _text(START)
    assert re.search(r"if\s+not\s+exist\s+.*config\.json", src, re.I), (
        "start.bat 里没有「缺 config.json 就补」的判断 ⇒ "
        "全新解压的用户双击必然被 preflight 拦死（实测退出码 1）✗"
    )
    assert re.search(r"copy\s+.*config\.example\.json", src, re.I), (
        "start.bat 里没有从 contracts\\config.example.json 复制的动作 ✗"
    )


def test_start_bat_copies_before_preflight():
    """★ 顺序要紧：补配置必须在 preflight **之前** —— 否则 preflight 照样判 FAIL。

    ★ 判据只认**真正的调用行**（含 `"%PY%"` 与 `preflight.py`，且不是 REM 注释）——
      第一版只找 "preflight.py" 字样，结果匹配到了文件头部注释里的
      "from scripts\\preflight.py"（第 7 行）⇒ 误判顺序反了 ✗（本班踩过）
    """
    src = _text(START)
    lines = src.splitlines()
    i_copy = next((i for i, l in enumerate(lines)
                   if re.search(r"copy\s+.*config\.example\.json", l, re.I)), None)
    i_pf = next((i for i, l in enumerate(lines)
                 if "preflight.py" in l and "--print-url" not in l
                 and not l.strip().upper().startswith("REM")), None)
    assert i_copy is not None, "没找到复制动作 ✗"
    assert i_pf is not None, "没找到 preflight 调用 ✗"
    assert i_copy < i_pf, (
        f"复制配置在第 {i_copy + 1} 行、preflight 调用在第 {i_pf + 1} 行 ⇒ 顺序反了，"
        "preflight 仍会判「缺 config.json」✗"
    )


def test_start_bat_never_overwrites_an_existing_config():
    """★ 已有 config.json **绝不许覆盖** —— 那是用户配好的（含他的 provider/模型）。"""
    src = _text(START)
    # 复制动作必须被 `if not exist` 包住（同一行或紧邻上一行）
    lines = src.splitlines()
    for i, l in enumerate(lines):
        if re.search(r"copy\s+.*config\.example\.json", l, re.I):
            ctx = "\n".join(lines[max(0, i - 3):i + 1])
            assert re.search(r"if\s+not\s+exist", ctx, re.I), (
                f"第 {i + 1} 行的复制没被 'if not exist' 保护 ⇒ 会覆盖用户配置 ✗"
            )


def test_entry_bats_keep_ascii_and_crlf():
    """★ 不许改坏 entry bat 的两条硬规矩（2026-10-10 事故：中文让 .bat 彻底起不来）。

    这条与 `test_bat_encoding.py` 同口径 —— 我改了 start.bat，必须仍然过。
    """
    for p in (START, INSTALL):
        raw = p.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), f"{p.name} 带了 BOM ✗"
        try:
            raw.decode("ascii")
        except UnicodeDecodeError as e:
            raise AssertionError(
                f"{p.name} 不是纯 ASCII（第 {e.start} 字节）⇒ zh-CN cmd 会读成乱码、"
                f"脚本可能整个起不来 ✗（中文一律交给 preflight.py 输出）"
            ) from None
        assert b"\r\n" in raw, f"{p.name} 不是 CRLF ✗"
        # 不许出现"裸 LF"（除 CRLF 里的那个）
        assert raw.replace(b"\r\n", b"").count(b"\n") == 0, f"{p.name} 里有裸 LF ✗"
