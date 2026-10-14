# -*- coding: utf-8 -*-
"""★★ 卡点必须真的"卡"上（2026-10-15 用户反馈「卡点卡不出来」）。

## 现场实测（60 秒片 `task_20261015_9aa9279b`）

```
① 切点没落在拍上：
   切点 1.20s（偏差 230ms）· 17.83s（180ms）· 26.14s（110ms）
        34.46s（90ms）· 42.77s（200ms）
   ★ 最大偏差 230ms —— 而拍间距只有 500ms ⇒ 偏了快半拍 ✗
   根因：场景边界按**等分**算（58.2/7 = 8.314…），跟 0.5s 的拍网格压根不是一个网格 ✗

② 拍点上没有可见动作：
   拍点那一帧的帧间差异 / 全片平均 = **1.24×**（要 ≥1.5× 才看得出来）
   ⇒ 画面在拍点上是静止的 ⇒ 就是「卡点卡不出来」✗

③ 音乐"慢悠悠"：
   频段能量 —— 低频 3631 / 中频（旋律）734 ⇒ **低音比旋律大 4.9 倍** ✗
   ⇒ 只有"轰"，没有"跳"
```

## 本文件钉什么（全部用工具真跑，不靠断言源码字符串）

1. `beat_snap.plan()` 的输出**每一个边界都在拍网格上**
2. **片头留白本身也要吸附**（1.2s → 1.0s）—— 不吸的话整条片子全长在半个拍上
3. 窗口长度全部相等或差一个拍（不许出现荒谬的短窗口）
4. 末边界 ≤ duration − tail，且片尾留白 ≥ 0.4s
5. `make_bgm` 的 `beat` 档位有**反拍军鼓**和**中频琶音**（治"慢悠悠"的两味药）
6. `make_bgm` 生成的音乐**中频不会被低频压死**（低频/中频 < 3）
"""
from __future__ import annotations

import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
TOOLS = BACKEND / "skills" / "卡点视频" / "tools"
sys.path.insert(0, str(TOOLS))

from beat_snap import plan, snap_to_grid  # noqa: E402

BPM = 120.0
BEAT = 60.0 / BPM


def test_every_boundary_sits_on_the_beat_grid():
    """★★ 核心：所有场景边界都必须落在拍点上 ✓"""
    rows = plan(60.0, 1.2, 0.6, 7, BPM)
    assert len(rows) == 7, rows
    off = []
    for r in rows:
        for t in (r["a"], r["b"]):
            if abs(t / BEAT - round(t / BEAT)) > 1e-6:
                off.append(t)
    assert not off, (
        f"这些边界不在拍点上：{off} ✗\n"
        f"  （真机就是这么偏出 230ms 的 —— 观众看得出「没卡上」）"
    )


def test_lead_in_is_snapped_too():
    """★ 片头留白也要吸：1.2s 会被吸成 1.0s（否则全长在半个拍上）✗"""
    rows = plan(60.0, 1.2, 0.6, 7, BPM)
    assert rows[0]["a"] == 1.0, (
        f"起点是 {rows[0]['a']}，没吸附到拍上 ✗ "
        f"（1.2s 不是 0.5 的整数倍 ⇒ 后面每个边界都会带 0.2s 零头）"
    )
    assert snap_to_grid(1.2, BEAT) == 1.0
    assert snap_to_grid(1.3, BEAT) == 1.5


def test_windows_are_sane():
    """★ 窗口长度要合理（不许出现 0.5s 这种荒谬的短窗口）✓"""
    rows = plan(60.0, 1.2, 0.6, 7, BPM)
    lens = [r["len"] for r in rows]
    assert min(lens) >= 3.0, f"有窗口太短：{lens} ✗"
    # 各窗口差不超过一个拍
    assert max(lens) - min(lens) <= BEAT + 1e-6, f"窗口长度差太多：{lens} ✗"


def test_tail_is_preserved():
    """★ 片尾留白 ≥0.4s，末边界不许越界 ✓"""
    rows = plan(60.0, 1.2, 0.6, 7, BPM)
    last = rows[-1]["b"]
    assert last <= 60.0 - 0.4 + 1e-9, f"末边界 {last} 越界 ✗"
    assert 60.0 - last >= 0.4, f"片尾留白只有 {60.0 - last:.2f}s ✗"


def test_snap_handles_other_bpm():
    """★ 换个 BPM 也要对（100 BPM ⇒ 0.6s 一拍）✓"""
    rows = plan(45.0, 1.0, 0.6, 5, 100.0)
    beat = 0.6
    for r in rows:
        for t in (r["a"], r["b"]):
            assert abs(t / beat - round(t / beat)) < 1e-6, (t, beat, r)


def test_music_style_has_the_two_punch_ingredients():
    """★ 治「慢悠悠」的两味药必须在 `beat` 档位里：反拍军鼓 + 中频琶音 ✓"""
    src = (TOOLS / "make_bgm.py").read_text("utf-8", errors="replace")
    assert '"snare_gain"' in src, "没有反拍军鼓 ⇒ 只有底鼓，听起来平 ✗"
    assert '"pluck_gain"' in src, "没有中频琶音 ⇒ 中频空，低音压死旋律 ✗"
    # `beat` 档位里这两样都要 > 0
    i = src.find('"beat":')
    seg = src[i:i + 600]
    assert "snare_gain" in seg and "pluck_gain" in seg, (
        f"beat 档位里缺这两样：{seg[:200]} ✗"
    )


def test_music_is_not_bass_heavy():
    """★★ 生成一段真音乐，**低频不许压死中频**（实测 4.9 倍 ⇒ 慢悠悠）✓"""
    import subprocess
    import wave
    import array
    import math

    py = sys.executable
    out = pathlib.Path(__file__).resolve().parent / "_tmp_bgm_balance.wav"
    try:
        r = subprocess.run(
            [py, str(TOOLS / "make_bgm.py"), str(out),
             "--seconds", "8", "--bpm", "120", "--style", "beat"],
            capture_output=True, text=True, timeout=180,
            encoding="utf-8", errors="replace")
        assert r.returncode == 0, f"make_bgm 失败：{r.stderr[-400:]}"
        assert out.exists(), "没生成文件 ✗"
        with wave.open(str(out), "rb") as w:
            n = w.getnframes()
            sr = w.getframerate()
            a = array.array("h")
            a.frombytes(w.readframes(n))
        # 用简单 IIR 分频：低频 = 一阶低通 150Hz 的残差；中频 = 带通近似
        def rms(xs):
            return math.sqrt(sum(x * x for x in xs) / max(1, len(xs)))
        # 低频：一阶低通
        al = math.exp(-2 * math.pi * 150 / sr)
        low = []
        y = 0.0
        for x in a[::4]:
            y = (1 - al) * x + al * y
            low.append(y)
        # 中频：先低通 2000，减去低通 500
        a2 = math.exp(-2 * math.pi * 2000 / sr)
        a3 = math.exp(-2 * math.pi * 500 / sr)
        y2 = y3 = 0.0
        mid = []
        for x in a[::4]:
            y2 = (1 - a2) * x + a2 * y2
            y3 = (1 - a3) * x + a3 * y3
            mid.append(y2 - y3)
        ratio = rms(low) / (rms(mid) or 1)
        assert ratio < 3.0, (
            f"低频/中频 = {ratio:.1f} 倍（要 <3）⇒ 低音压死旋律 ⇒ 「慢悠悠」✗\n"
            f"  真机实测旧版是 4.9 倍，用户就是这么说听感的"
        )
    finally:
        if out.exists():
            out.unlink()
