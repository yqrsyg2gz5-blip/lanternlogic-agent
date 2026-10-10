# -*- coding: utf-8 -*-
"""能力探针必须**便宜** —— 2026-10-08 实测抓出来的真 bug ✗

## 怎么发现的

跑全量门时像"卡死"（30 分钟才 21% ✗）⇒ 用 faulthandler 打栈 + 逐探针计时 ⇒ 真相：

    === tts ===
       edge               0.41s
       qwen3tts           4.02s
       melotts          161.28s   ← ✗✗ 2 分 41 秒
       pyttsx3            0.06s

★ 用户装了 MeloTTS 之后开始的 ✓ —— `MeloTTSBackend.available()` 每次都
  `from melo.api import TTS` ✓ 而它一 import 就拖起 torch + nltk + 词法数据 ✓
⇒ **每次读能力表都卡 2 分半** ✗ ⇒ 设置页卡 ✓ 能力总览卡 ✓ 全量测试卡 ✓（1966 条里凡探过能力的都卡 ✓）

## 修法

分两档 ✓：
  · 默认（`deep=False`）＝ `find_spec` + 词法数据目录 ✓ **毫秒级** ✓
  · `deep=True` ＝ 真 import ✓ 只给**装完那一次**（后台任务 ✓ 等得起 ✓）

★ 下面这两条测试就钉这个 ✓：**默认探针必须便宜**（谁把它改回深探 ⇒ 必红 ✓）
"""
from __future__ import annotations

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import capabilities as cap  # noqa: E402
from app import tts as tts_mod  # noqa: E402

_ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_the_probe_is_cheap_not_a_two_minute_import():
    """★★ **默认探针必须便宜** ✓ —— 实测它原来要 **161 秒** ✗✗

    为什么这条要紧：能力表是**设置页/能力总览/体检/每次测试**都要读的东西 ✓
    它慢 2 分半 ⇒ 用户以为软件卡死了 ✓（而且没人会想到是"探一下 MeloTTS"造成的 ✓）

    回滚实验：把 `available()` 改回无条件 `from melo.api import TTS` ⇒ 本条必红 ✓
    """
    t0 = time.time()
    ok = tts_mod.MeloTTSBackend.available()          # 默认档 ✓
    dt = time.time() - t0
    assert dt < 2.0, f"MeloTTS 默认探针用了 {dt:.1f} 秒 ✗（要毫秒级 ✓ 深探留给 deep=True ✓）"
    assert isinstance(ok, bool), ok


def test_the_whole_capability_table_is_fast():
    """★ 整张能力表拉一遍也得**秒级** ✓（不能因为某一个探针把整页拖死 ✓）"""
    t0 = time.time()
    cap.status(_cfg())
    dt = time.time() - t0
    assert dt < 8.0, f"能力表整体用了 {dt:.1f} 秒 ✗（设置页会卡住 ✓）"


def test_deep_probe_still_exists_for_the_installer():
    """★ 深探没被删 ✗ —— 装完那一次还得**真**确认能 import 出 TTS ✓
    （"装完再探一次"是本项目的规矩 ✓ 只是它该待在后台任务里 ✓）"""
    import inspect

    sig = inspect.signature(tts_mod.MeloTTSBackend.available)
    assert "deep" in sig.parameters, "深探参数没了 ✗（装完就没法真确认了 ✓）"
    src = inspect.getsource(tts_mod.MeloTTSBackend.available)
    assert "if not deep" in src, "没有分档 ⇒ 又变成每次都深探 ✗"
    # 装完那一步必须传 deep=True ✓（不然"装完再探"变成永远说不清 ✓）
    li = (_ROOT / "backend" / "app" / "local_install.py").read_text("utf-8")
    assert "available(deep=True)" in li, "装完的复探没走深探 ✗"


def _cfg():
    from app import main as m

    return m.cfg
