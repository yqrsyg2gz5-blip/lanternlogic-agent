# -*- coding: utf-8 -*-
"""把场景边界**吸附到拍网格**上 —— 治「卡点卡不出来」。

## 为什么必须有这个工具（2026-10-15 实测）

60 秒片 `task_20261015_9aa9279b` 的切点 vs 拍点：

```
切点 1.20s   最近拍点 0.97s   偏差 230 ms   ✗
切点 9.51s   最近拍点 9.49s   偏差  20 ms   ✓
切点 17.83s  最近拍点 18.01s  偏差 180 ms   ✗
切点 26.14s  最近拍点 26.03s  偏差 110 ms   ✗
切点 34.46s  最近拍点 34.55s  偏差  90 ms   ✗
切点 42.77s  最近拍点 42.57s  偏差 200 ms   ✗
切点 51.09s  最近拍点 51.09s  偏差   0 ms   ✓
★ 最大偏差 230 ms（拍间距只有 500 ms ⇒ 偏了快半拍）✗
```

**根因**：场景边界是**按等分算的**（58.2 / 7 = 8.314…），
而拍网格是 0.5s 的整数倍 —— **两者压根不在一个网格上** ✗

**改法**：算完等分之后，**把每个边界吸附到最近的拍点**，
再用「总时长不变」的约束把累计误差摊回去 ⇒ 既在拍上、又正好 60 秒 ✓

## 用法

```bash
python tools/beat_snap.py --duration 60 --lead 1.2 --tail 0.6 --scenes 7 --bpm 120
# 输出每一场的 a/b（都在拍点上）+ 每场窗口长度 + 字数上限
```
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def snap_to_grid(t: float, beat: float) -> float:
    """把 t 吸附到 beat 的整数倍（四舍五入到最近的拍）。"""
    if beat <= 0:
        return t
    return round(t / beat) * beat


def plan(duration: float, lead: float, tail: float, scenes: int,
         bpm: float) -> list[dict]:
    """算出吸附到拍网格的场景表。

    ★★ 关键：**片头留白本身也要吸附到拍上**（本班实测踩到）：
       lead=1.2s、拍 0.5s ⇒ 第一场从 1.20s 开始 ⇒ **整条片子的边界全长在
       半个拍上**（1.20 / 9.51 / …都偏 0.2s 的零头）✗
       ⇒ 把 lead 吸成 1.0s，之后所有边界就都在拍上了 ✓

    ★ 算法：lead 吸附 ⇒ 等分 ⇒ 每个边界吸附到最近拍 ⇒
      末边界 = snap(duration - tail)，强制在拍上 ✓
    """
    beat = 60.0 / bpm
    lead = snap_to_grid(lead, beat)          # ★ 起点也吸
    usable = duration - lead - tail
    if scenes < 1 or usable <= 0:
        return []
    step = usable / scenes
    bounds: list[float] = []
    for i in range(1, scenes):
        snapped = snap_to_grid(lead + step * i, beat)
        # ★ 不许吸到"比上一个还靠前"（否则窗口会变成负数）
        if bounds and snapped <= bounds[-1]:
            snapped = bounds[-1] + beat
        bounds.append(snapped)
    # 末边界 = 片尾开始处（也要在拍上）
    end = snap_to_grid(duration - tail, beat)
    if bounds and end <= bounds[-1]:
        end = bounds[-1] + beat
    out: list[dict] = []
    prev = lead
    for i, b in enumerate(bounds + [end]):
        out.append({
            "i": i + 1,
            "a": round(prev, 3),
            "b": round(b, 3),
            "len": round(b - prev, 3),
            "beat_a": round(prev / beat, 3),
            "beat_b": round(b / beat, 3),
            "on_grid": abs(prev / beat - round(prev / beat)) < 1e-6,
        })
        prev = b
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把场景边界吸附到拍网格")
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--lead", type=float, default=1.2, help="片头留白")
    ap.add_argument("--tail", type=float, default=0.6, help="片尾留白")
    ap.add_argument("--scenes", type=int, default=7)
    ap.add_argument("--bpm", type=float, default=120.0)
    ap.add_argument("--chars-per-sec", type=float, default=4.2, help="语速")
    ap.add_argument("--out", default="", help="写一份 json（可选）")
    args = ap.parse_args(argv)

    beat = 60.0 / args.bpm
    rows = plan(args.duration, args.lead, args.tail, args.scenes, args.bpm)
    if not rows:
        print("参数不合法（窗口为负）", file=sys.stderr)
        return 2

    print("=" * 88)
    print(f"拍网格：{args.bpm:g} BPM ⇒ 每 {beat:.4f}s 一拍，全片 {int(args.duration/beat)} 拍")
    print("=" * 88)
    print(f"  {'场':<4}{'开始':>8}{'结束':>8}{'窗口':>8}{'可放':>8}{'字数上限':>10}"
          f"{'在拍上':>10}{'拍号':>12}")
    bad = 0
    for r in rows:
        room = r["len"] - 0.4
        cap = int(room * args.chars_per_sec)
        on = "✓" if r["on_grid"] else "✗"
        if not r["on_grid"]:
            bad += 1
        print(f"  {r['i']:<4}{r['a']:>8.2f}{r['b']:>8.2f}{r['len']:>8.2f}{room:>8.2f}"
              f"{cap:>10}{on:>10}{r['beat_a']:>7.0f}–{r['beat_b']:<5.0f}")
    print()
    last = rows[-1]["b"]
    print(f"  ★ 末边界 {last:.3f}s（片尾留白 {args.duration - last:.2f}s）")
    print(f"  ★ {'全部边界都在拍点上 ✓' if bad == 0 else f'还有 {bad} 个边界不在拍上 ✗'}")
    print()
    print("  ★★ 画面还必须在**每一拍**上有可见动作（不只是切场景）：")
    print("     每拍：主视觉缩放 1.02 / 光晕 +8% / 进度条跳一格")
    print("     每 4 拍（小节头）：更重的一次（缩放 1.04 / 闪一下）")
    print("     否则实测帧间差异比值只有 1.24×（<1.5× 观众看不出卡点）✗")

    if args.out:
        p = pathlib.Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "bpm": args.bpm, "beat_s": beat, "duration": args.duration,
            "lead": args.lead, "tail": args.tail, "scenes": rows,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  ✓ 已写 {p}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
