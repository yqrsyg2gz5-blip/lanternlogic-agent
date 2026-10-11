# -*- coding: utf-8 -*-
"""本地出片的超时（用户 2026-10-10 实测引出来的）。

实测（同一台机器：Wan2.1 14B int8，12GB 笔记本卡）：
    · 480×272 × 33 帧（≈2 秒） → **90 秒** ✓
    · 832×480 × 81 帧（5 秒）  → **30 分钟还没跑完** ✗
而本地引擎原来把超时**写死 900 秒** ⇒ 上面第二条必然被误杀：界面报"生成超时"，
其实 ComfyUI 那边还在算 ✓ —— 用户会以为"本地出视频坏了" ✗（真实现场就是这句：
"我 Docker 和 ComfyUI 本地都有，出视频为什么用不了"）。

判据：
    ① 本地引擎默认超时 ≥ 30 分钟（与云端那档同一个口径 ✓）
    ② **配置里的 `video.timeout_seconds` 要真的传进本地引擎** ✓（用户设了就得听他的）
"""
from __future__ import annotations

from types import SimpleNamespace


def test_local_engine_default_timeout_is_not_15_minutes():
    from app.video import ComfyUIVideoEngine
    eng = ComfyUIVideoEngine(SimpleNamespace(comfyui_url="http://127.0.0.1:9"))
    assert eng.timeout_hint >= 1800.0, (
        f"本地出片超时是 {eng.timeout_hint} 秒 —— 实测 5 秒片就要十几分钟起，写死 900 必然误杀 ✗")


def test_local_engine_honors_configured_timeout():
    from app.video import ComfyUIVideoEngine, get_video_engine
    ecfg = SimpleNamespace(comfyui_url="http://127.0.0.1:9")
    eng = get_video_engine(ecfg, {"timeout_seconds": 2400})
    assert isinstance(eng, ComfyUIVideoEngine), eng
    assert eng.timeout_hint == 2400.0, \
        "配置里的 video.timeout_seconds 没传进本地引擎（用户设了 40 分钟却还按默认走 ✗）"
