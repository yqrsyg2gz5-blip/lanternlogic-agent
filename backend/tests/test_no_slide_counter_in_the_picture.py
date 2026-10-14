# -*- coding: utf-8 -*-
"""★★ 画面里不许出现「第 N 拍 / 120」这种计数文字（2026-10-15 用户反馈）。

## 用户原话

> 「你看看他那底下有个什么什么拍，那个能不能去掉？
>   这一看就像**拿图拼拼凑起来的**那个，到时候不要。」

## 为什么这句话是对的

观众看到「第 43 拍 / 120」会**立刻意识到"这是拼的"**，而不是一条成片 ✗
时间码（`21.0s`）是同一个毛病（旁白式计数器）✗

## 但**进度条要留**

进度条靠"每拍跳一格"把卡点做在画面里 —— 那是"看得见卡点"的**正当实现**，
不是计数器 ✓（用户也没说要去掉那条）

## 本文件钉什么

1. 起手模板里**没有**「第 … 拍」这种插值文本
2. 起手模板里**没有**时间码（`toFixed` 出来的 `s`）
3. 但**进度条还在**（`hud` 函数还在画条）
4. `TOTAL_BEATS` 这类变量**必须定义过**再用（本班踩过：引用了没定义的变量）
"""
from __future__ import annotations

import pathlib
import re

BACKEND = pathlib.Path(__file__).resolve().parents[1]
STARTER = BACKEND / "skills" / "卡点视频" / "tools" / "render" / "starter.html"


def _src() -> str:
    assert STARTER.exists(), f"起手模板不在：{STARTER} ✗"
    return STARTER.read_text("utf-8", errors="replace")


def test_starter_has_no_beat_counter_text():
    """★★ 不许有「第 N 拍」这种计数文字 —— 一看就像拼的 ✗"""
    t = _src()
    bad = re.findall(r"`第\s*\$\{[^`]*\}\s*拍[^`]*`", t)
    assert not bad, (
        f"模板里还有拍数计数文字：{bad} ✗\n"
        f"  （用户原话：「这一看就像拿图拼拼凑起来的」）"
    )


def test_starter_has_no_timecode_text():
    """★ 时间码（`21.0s`）是同一个毛病 —— 旁白式计数器 ✗"""
    t = _src()
    hud = t[t.find("function hud("):]
    hud = hud[:hud.find("\n}") + 2] if "\n}" in hud else hud
    assert "toFixed" not in hud, (
        "hud 里还有 toFixed（时间码文字）✗ —— 跟拍数计数是同一个毛病"
    )
    assert 'fillText' not in hud, (
        "hud 里还在 fillText 画文字 ✗ —— 底部应该只有进度条，没有文字"
    )


def test_starter_keeps_the_progress_bar():
    """★ 进度条要留 —— 那是"卡点看得见"的正当实现 ✓"""
    t = _src()
    assert "function hud(" in t, "hud 函数没了 ⇒ 进度条也丢了 ✗"
    hud = t[t.find("function hud("):]
    assert "fillRect" in hud, "hud 里没有 fillRect ⇒ 进度条没了 ✗"


def test_total_beats_is_defined_before_use():
    """★★ 引用过的常量必须定义过（本班踩过：用 TOTAL_BEATS 却没定义）✗"""
    t = _src()
    if "TOTAL_BEATS" in t:
        # 必须有 `const TOTAL_BEATS = ...` 的定义
        assert re.search(r"const\s+TOTAL_BEATS\s*=", t), (
            "用了 TOTAL_BEATS 却没定义 ⇒ 渲染时 ReferenceError ✗"
        )
        # 定义必须出现在第一次使用之前
        d = t.find("const TOTAL_BEATS")
        u = t.find("TOTAL_BEATS", d + 10)
        assert d < u, "TOTAL_BEATS 的定义在使用之后 ✗"


def test_starter_still_renders_without_js_errors():
    """★ 用 node 真跑一遍渲染（catch 掉 ReferenceError 这类只在运行时炸的错）✓

    ★★ 本班踩过：这条测试第一次跑就红了 —— 因为测试进程的 PATH 上是
       **残版 ffmpeg（没有 libx264）** ⇒ 渲染在编码那步才崩 ✗
       ⇒ 先探一下有没有 libx264：没有就**跳过渲染**（只跑上面那些静态检查），
         别让"环境缺编码器"伪装成"模板坏了" ✗
    """
    import shutil
    import subprocess
    import tempfile

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("环境里没 node")
    ff = shutil.which("ffmpeg")
    if ff:
        probe = subprocess.run([ff, "-hide_banner", "-encoders"],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
        if "libx264" not in (probe.stdout or ""):
            pytest.skip("PATH 上的 ffmpeg 没有 libx264（残版）")
    render = STARTER.parent / "render.mjs"
    if not render.exists():
        pytest.skip("找不到 render.mjs")
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        page = td / "page.html"
        page.write_text(_src(), encoding="utf-8")
        r = subprocess.run(
            [node, str(render), str(page), str(td / "t.mp4"),
             "--seconds", "1", "--fps", "30", "--scale", "0.3"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=300, cwd=str(STARTER.parent))
        assert r.returncode == 0, (
            f"模板渲染失败（很可能是 JS 运行时报错）✗\n"
            f"  stdout: {(r.stdout or '')[-500:]}\n"
            f"  stderr: {(r.stderr or '')[-500:]}"
        )
        assert (td / "t.mp4").exists(), "没渲出文件 ✗"
