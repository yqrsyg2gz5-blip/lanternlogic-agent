# -*- coding: utf-8 -*-
"""启动入口 .bat 的编码红线（2026-10-10 线上事故换来的）。

事故经过：
    `start.bat` 头部写着「本文件故意只用 ASCII」，但正文被塞进了中文 echo。
    zh-CN 的 cmd 默认按 GBK 读批处理，UTF-8 的多字节中文会让它的行计数错位 ——
    双击后不是启动，而是一屏
        'ight.py' 不是内部或外部命令
        'YROOTbackend_restart.log" 2>&1"' 不是内部或外部命令
    （`preflight.py` 被从中间切开、`%ROOT%` 被吃掉字符），一键启动直接不可用。
    而这份文件同时躺在开发仓、`D:\\LanternLogicAgent-public`、GitHub 与 Gitee 的
    main 上（blob da762923）—— 用户和评委双击一样炸。

红线（本文件盯的就是这三条）：
    1. 纯 ASCII —— 中文提示一律交给 Python 打印（`scripts/preflight.py`，chcp 65001 之后）；
    2. CRLF —— 不许出现裸 LF；
    3. 无 BOM —— BOM 会让第一行变成 BOM+`@echo`。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _entry_bats() -> list[Path]:
    """用户真正会双击的那几个 .bat（不含 _attic / data / .venv 里的历史件）。"""
    return sorted([p for p in ROOT.glob("*.bat")] + [p for p in (ROOT / "scripts").glob("*.bat")])


def test_entry_bats_are_pure_ascii():
    for f in _entry_bats():
        raw = f.read_bytes()
        bad = [i for i, b in enumerate(raw) if b > 0x7F]
        assert not bad, (
            f"{f.name} 里出现了非 ASCII 字节（首个在第 {bad[0]} 字节）—— "
            "zh-CN 的 cmd 按 GBK 读会把行切断，双击会变成一屏『不是内部或外部命令』；"
            "中文提示请交给 scripts/preflight.py 打印"
        )


def test_entry_bats_use_crlf_without_bom():
    for f in _entry_bats():
        raw = f.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), f"{f.name} 带了 UTF-8 BOM（首行会读成 BOM+@echo）"
        assert raw.count(b"\n") == raw.count(b"\r\n"), f"{f.name} 里有裸 LF —— 批处理必须整份 CRLF"
