# -*- coding: utf-8 -*-
"""技能自带的「BGM 合成器」—— 不用下载素材，直接**生成**一条可卡点的音乐床。

## 为什么有它（用户实测提问）

用户问：「那个视频里没有音乐，他音乐是不是弄不了」

查下去**是真的没有**：
  · 技能里**处处要求音乐**（第 3 步节拍检测、第 4 步复核、混音 ducking 4–6 dB）
  · 但工作区里**一个音乐文件都没有**（只有配音 mp3），本机也**没有音乐素材库**
  · 技能的 `make_click_track.py` 还**写死**了那次 18 秒小样的路径
    `assets\\audio\\music\\bgm.wav`（那个目录在本机根本不存在）
  ⇒ 模型**无米下锅**，只好做了一条**纯人声**的片子 ✗

## 两条硬约束（都是实测得来）

1. **只能用标准库**：便携版运行时（`runtime\\python.exe`）里
   **没有 numpy / scipy / soundfile / pydub**（实测 `ModuleNotFoundError`）
   ⇒ 用 `wave` + `math` + `array` 写，产品里才跑得起来 ✓
2. **节拍必须是真的**：卡点视频的整个前提是"画面切在拍上"。
   本脚本的重音**落在 BPM 网格上**，并且**文件名带 BPM**，让网格在流水线里看得见。
   实测：合成 120 BPM → 技能的 `beatmap.mjs` 检出 **120 BPM、漂移 10 ms** ✓

## 用法

    & <python> tools\\make_bgm.py data\\bgm-120bpm.wav --seconds 32 --bpm 120 --style beat

    --style beat      卡点用（重 kick + hat，节奏明显）
    --style voiceover 口播用（垫底更轻，不抢人声）
    --style warm      温柔（长音铺底，少打击）

★ **它只造"能卡点的音乐床"，不碰创作**：什么风格配什么片子由你（AI）定 ✓
"""
from __future__ import annotations

import argparse
import math
import random as _random
import sys
import wave
from array import array
from pathlib import Path

#: 采样率（44.1k 通用；beatmap 会自己重采样）
SAMPLE_RATE = 44100

#: 风格档位 —— **是参数，不是写死的题材**
#:
#: ★★ 关于 `accent_every`（2026-10-13 实测新增，防"八度误判"）：
#:   节拍检测器（`beatmap.mjs`）有个已知毛病 —— BPM 会**差一倍**（八度错误）：
#:   实测本脚本 160 BPM 的轨被检成 **79.73 BPM**（正好一半），120 BPM 的轨检对了。
#:   而技能文档里也早记着参考项目的同类事故（**120 BPM 被检成 181.82**）。
#:   ⇒ 应对：**每 4 拍压一个更重的重音**（= 一小节一个强拍），
#:     让"小节"在音频里**听得出来** ⇒ 检测器有更明确的层级可用，少犯八度错 ✓
STYLES: dict[str, dict] = {
    # 卡点：重 kick + hat，节奏感强，适合快剪
    "beat": {
        "kick_gain": 0.30, "kick_decay": 26.0, "hat_gain": 0.07,
        "pad_gain": 0.10, "pad_decay": 2.0, "sub_gain": 0.05,
        "kick_every": 1, "hat_every": 2, "accent_every": 4,
    },
    # 口播：垫底更轻、打击更少，**不抢人声**（后面还要 ducking）
    "voiceover": {
        "kick_gain": 0.15, "kick_decay": 30.0, "hat_gain": 0.03,
        "pad_gain": 0.12, "pad_decay": 1.4, "sub_gain": 0.04,
        "kick_every": 2, "hat_every": 4, "accent_every": 8,
    },
    # 温柔：长音铺底，几乎不打击
    "warm": {
        "kick_gain": 0.08, "kick_decay": 34.0, "hat_gain": 0.015,
        "pad_gain": 0.16, "pad_decay": 0.9, "sub_gain": 0.03,
        "kick_every": 4, "hat_every": 8, "accent_every": 8,
    },
}

#: 默认风格（参数拼错时退回它，不许崩）
DEFAULT_STYLE = "beat"

#: ★★ 节拍检测器（`beatmap.mjs`）的 **BPM 上限 ≈ 155**（2026-10-13 实测扫出来的）
#:
#: 实测（本脚本合成的轨，逐个目标扫）：
#:     100 → 100 ✓   120 → 120 ✓   130 → 129.73 ✓   140 → 139.53 ✓
#:     150 → 150 ✓   155 → 154.84 ✓
#:     160 → 79.73 ✗  165 → 82.26 ✗  180 → 89.89 ✗   ← **全部折半（八度误判）**
#: ⇒ 超过上限时会检成**一半**。**卡点仍然能用**（拍点还在，只是每拍变半拍），
#:   但**报告里会写错的 BPM** ⇒ 所以这里主动警告，让调用方心里有数 ✓
DETECT_BPM_CEILING = 155.0

#: 和弦进行 —— A 小调 Am–F–C–G：通用、不抢戏、情绪中性偏积极
_CHORDS = (
    (220.00, 261.63, 329.63),   # Am
    (174.61, 220.00, 261.63),   # F
    (261.63, 329.63, 392.00),   # C
    (196.00, 246.94, 293.66),   # G
)

#: 峰值留多少余量（后面要压人声进来；顶满就没法混）
_PEAK_TARGET = 0.82


def suggest_filename(*, bpm: float, style: str = DEFAULT_STYLE) -> str:
    """建议文件名 —— **必须带 BPM**，让"节拍网格"在流水线里看得见（技能约定）。"""
    return f"bgm-{int(round(bpm))}bpm-{style}.wav"


def _env_attack_decay(t: float, decay: float, attack: float = 0.004) -> float:
    """软起音 + 指数衰减包络（起音很短，避免"啪"的爆音）。"""
    a = 1.0 - math.exp(-t / max(1e-6, attack))
    return a * math.exp(-t * decay)


def synthesize(path: str | Path, *, seconds: float, bpm: float,
               style: str = DEFAULT_STYLE, sample_rate: int = SAMPLE_RATE) -> Path:
    """合成一条**可卡点**的音乐床，写成 16bit 单声道 WAV。

    ★ 节拍落在 BPM 网格上：第 k 拍的时刻 = `k * 60/bpm`（从 0 开始）。
    ★ 未知风格退回 `DEFAULT_STYLE`（模型常拼错参数，不许因此崩）✗
    """
    cfg = STYLES.get(str(style)) or STYLES[DEFAULT_STYLE]
    sr = int(sample_rate)
    n = max(1, int(round(float(seconds) * sr)))
    bpm = max(30.0, min(300.0, float(bpm)))
    beat_s = 60.0 / bpm
    bar_s = beat_s * 4.0

    buf = [0.0] * n

    # ── 和弦垫底（每小节换一个和弦）──
    i = 0
    while i * bar_s < seconds:
        t0 = i * bar_s
        ch = _CHORDS[i % len(_CHORDS)]
        k0 = int(t0 * sr)
        k1 = min(n, int((t0 + bar_s) * sr))
        for k in range(k0, k1):
            tt = (k - k0) / sr
            e = _env_attack_decay(tt, cfg["pad_decay"], 0.02)
            v = 0.0
            for f in ch:
                v += math.sin(2.0 * math.pi * f * tt)
                v += 0.28 * math.sin(2.0 * math.pi * f * 2.0 * tt)   # 一点泛音
            buf[k] += cfg["pad_gain"] * e * v / len(ch)
        i += 1

    # ── 每拍 kick（低频，给卡点一个"心跳"）★ 落在网格上 ──
    #    ★★ `accent_every` 拍压一个更重的重音（= 小节强拍）—— 防检测器的"八度误判"，
    #       见 `STYLES` 上方关于 accent_every 的实测说明 ✓
    step = int(cfg["kick_every"]) or 1
    accent_every = max(1, int(cfg.get("accent_every") or 4))
    b = 0
    while b * beat_s < seconds:
        if b % step == 0:
            is_accent = (b % accent_every == 0)
            # 强拍更响、衰减更慢（听感上"这是小节头"）
            kg = cfg["kick_gain"] * (1.9 if is_accent else 1.0)
            kd = cfg["kick_decay"] * (0.72 if is_accent else 1.0)
            t0 = b * beat_s
            k0 = int(t0 * sr)
            k1 = min(n, int((t0 + 0.24) * sr))
            for k in range(k0, k1):
                tt = (k - k0) / sr
                e = _env_attack_decay(tt, kd, 0.002)
                # 频率下滑 90 → 48 Hz，kick 的经典形态
                f = 48.0 + 42.0 * math.exp(-tt * 24.0)
                buf[k] += kg * e * math.sin(2.0 * math.pi * f * tt)
        b += 1

    # ── hat（高频，给节奏"颗粒"）★ 也落在网格上 ──
    #
    #   ★★ 2026-10-15 修（用户反馈「音乐不好听」，实测抓到根因）：
    #      原来 hat 用的是**未滤波的白噪声**直接乘衰减 ⇒ 满频"嘶嘶"声 ✗
    #      真机实测（60 秒片那条 bgm.wav）：频谱重心 **9642 Hz**、高频占比 **0.205**
    #      而修好后：重心 **697 Hz**、高频占比 **0.079** —— 差 2.6 倍 ✗→✓
    #      ⇒ 加一个**一阶低通**（截止约 7 kHz）：真 hat 也不是全频噪声，
    #        滤掉 7k 以上的"沙"之后，颗粒感还在，但不刺耳 ✓
    hstep = int(cfg["hat_every"]) or 1
    CUT_HZ = 7000.0                      # hat 的低通截止
    a_lp = math.exp(-2.0 * math.pi * CUT_HZ / sr)   # 一阶 IIR 系数
    b = 0
    while b * beat_s < seconds:
        if b % hstep == 0:
            t0 = b * beat_s
            k0 = int(t0 * sr)
            k1 = min(n, int((t0 + 0.05) * sr))
            # 固定种子的伪噪声（不用 random 模块，保证**可复现**：同参数出同一条轨）
            seed = 12345 + b * 7919
            lp = 0.0                      # 低通状态（每记 hat 重置）
            for k in range(k0, k1):
                tt = (k - k0) / sr
                seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
                noise = (seed / 0x3FFFFFFF) - 1.0
                lp = (1.0 - a_lp) * noise + a_lp * lp       # 一阶低通
                # ★ 力度变化：每记 hat 的轻重不同（治"打字机"）
                vel = 0.82 + 0.18 * (((seed >> 7) & 0xFF) / 255.0)
                buf[k] += cfg["hat_gain"] * vel * _env_attack_decay(tt, 110.0, 0.0005) * lp
        b += 1

    # ── 归一化到目标峰值（留 headroom 给混音）──
    peak = 0.0
    for v in buf:
        a = v if v >= 0 else -v
        if a > peak:
            peak = a
    if peak > 1e-9:
        gain = _PEAK_TARGET / peak
        for k in range(n):
            buf[k] *= gain

    # ════════════════════════════════════════════════════════════════════════
    # ★★★ 2026-10-15：**音色处理** —— 治「音乐不好听」（用户原话）
    #
    #   现场实测（60 秒片 task_20261014_d459cbb4）：模型自己合的曲子只用了
    #   release / pan / arp 三样手法 ⇒ 用户反馈「音乐不好听」✗
    #   缺的十样里，**最致命的两样是**：
    #     · reverb（混响）—— 缺了就是"干、贴脸、像玩具琴"
    #     · velocity（力度变化）—— 缺了就是"每个音一样响 ⇒ 打字机"
    #
    #   ⇒ 这里在**写盘之前**统一加三道：**轻饱和 → 延迟 → 混响** ✓
    #     全部用固定种子 ⇒ **同一组参数每次输出逐字节相同**（可复现，渲染才稳）✓
    # ════════════════════════════════════════════════════════════════════════
    rng = _random.Random(20261015)      # ★ 固定种子：可复现

    # ① 轻饱和（tanh）—— 给一点暖度，同时把峰值压得服帖
    for k in range(n):
        buf[k] = math.tanh(buf[k] * 1.5) * 0.92

    # ② 力度变化：把整体分成小段，每段随机微调 ±7%（模拟"人弹的轻重"）
    seg = max(1, int(sr * 0.25))        # 每 0.25s 一个"力度段"
    k = 0
    while k < n:
        g = 0.93 + 0.14 * rng.random()
        for j in range(k, min(n, k + seg)):
            buf[j] *= g
        k += seg

    # ③ 延迟（半拍，反馈 2 次）—— 空间感
    delay_n = max(1, int(beat_s * sr * 0.5))
    wet_d = 0.18
    for k in range(n):
        src = buf[k]
        if src == 0.0:
            continue
        for tap, g in ((1, wet_d), (2, wet_d * wet_d)):
            j = k + delay_n * tap
            if j < n:
                buf[j] += src * g

    # ④ 混响（指数衰减噪声，衰减 0.9s）—— 治"干"
    rt = 0.9
    rn = int(sr * rt)
    ir = [(_random.Random(1000 + i).random() * 2.0 - 1.0) * math.exp(-3.5 * i / sr)
          for i in range(rn)]
    ir_e = sum(x * x for x in ir) or 1.0
    wet_r = 0.22
    #   ★★ 性能坑（本班踩过）：**直接卷积是 O(n×rn)** —— 20 秒 / 44.1k 就是
    #      882000 × 39690 ≈ 350 亿次乘法 ⇒ 跑几分钟都出不来 ✗
    #   ⇒ 用**稀疏卷积**：每 STRIDE 个采样算一次锚点，中间线性插值。
    #      混响本身是"模糊的尾巴"，插值听不出来，但快 32 倍 ✓
    STRIDE = 32
    dry = buf[:]                        # 留一份干声
    anchors: list[tuple[int, float]] = []
    for k in range(0, n, STRIDE):
        acc = 0.0
        limit = min(rn, n - k)
        for i in range(0, limit, 4):    # 脉冲响应自身也稀疏取（再快 4 倍）
            acc += dry[k + i] * ir[i]
        anchors.append((k, acc / (ir_e ** 0.5)))
    for idx in range(len(anchors) - 1):
        k0, v0 = anchors[idx]
        k1, v1 = anchors[idx + 1]
        span = max(1, k1 - k0)
        for k in range(k0, min(k1, n)):
            w = (k - k0) / span
            buf[k] = dry[k] * (1.0 - wet_r) + (v0 * (1.0 - w) + v1 * w) * wet_r
    if anchors:
        k_last, v_last = anchors[-1]
        for k in range(k_last, n):
            buf[k] = dry[k] * (1.0 - wet_r) + v_last * wet_r

    # ⑤ 处理后再归一化一次（加了效果会改变峰值，不归一化会削顶）
    peak = 0.0
    for v in buf:
        a = v if v >= 0 else -v
        if a > peak:
            peak = a
    if peak > 1e-9:
        gain = _PEAK_TARGET / peak
        for k in range(n):
            buf[k] *= gain

    # ── 写 16bit 单声道 WAV ──
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    samples = array("h", (max(-32768, min(32767, int(v * 32767))) for v in buf))
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(samples.tobytes())
    return p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="合成一条可卡点的音乐床（纯标准库，不下载任何素材）")
    ap.add_argument("out", nargs="?", default="", help="输出 wav（默认按 BPM/风格自动命名）")
    ap.add_argument("--seconds", type=float, default=32.0, help="时长（秒，默认 32）")
    ap.add_argument("--bpm", type=float, default=120.0, help="BPM（默认 120）")
    ap.add_argument("--style", default=DEFAULT_STYLE,
                    choices=sorted(STYLES) + ["__other__"],
                    help=f"风格（默认 {DEFAULT_STYLE}；{', '.join(sorted(STYLES))}）")
    ap.add_argument("--sample-rate", type=int, default=SAMPLE_RATE)
    args = ap.parse_args(argv)

    # 未知风格不许崩：退回默认（模型常拼错）
    style = args.style if args.style in STYLES else DEFAULT_STYLE
    out = args.out or suggest_filename(bpm=args.bpm, style=style)
    p = synthesize(out, seconds=args.seconds, bpm=args.bpm, style=style,
                   sample_rate=args.sample_rate)
    print(f"BGM 已生成：{p}")
    print(f"  时长 {args.seconds:.2f}s   BPM {args.bpm:g}   风格 {style}")
    print(f"  采样率 {args.sample_rate}  16bit 单声道")
    print(f"  ★ 节拍网格 = 每 {60.0 / max(30.0, args.bpm):.4f}s 一拍（重音落在网格上）")
    print(f"  ★ 下一步：node tools\\render\\beatmap.mjs {p} data\\beats.json")
    if style != args.style:
        print(f"  ⚠️ 风格 {args.style!r} 不存在，已退回 {style!r}")
    if args.bpm > DETECT_BPM_CEILING:
        print(f"  ⚠️ BPM {args.bpm:g} 超过节拍检测上限（≈{DETECT_BPM_CEILING:g}）——")
        print("     实测：检测器会把它检成**一半**（八度误判）。两个选择：")
        print(f"       ① 把 BPM 降到 {DETECT_BPM_CEILING:g} 以下（推荐，检测准）；")
        print("       ② 保持这个 BPM，但**明知**检测报告会把 BPM 写成一半 ——")
        print("          拍点仍落在正确位置，可用 --trim 后按 beats 数组切，别用常量 BPM 映射。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
