# -*- coding: utf-8 -*-
"""技能自带的「BGM 合成器」`make_bgm.py` 的验收测试。

## 为什么有它（用户实测提问）

用户问：「那个视频里没有音乐，他音乐是不是弄不了」

查下去发现**是真的没有**：
  · 技能里**处处要求音乐**（第 3 步节拍检测、第 4 步复核、ducking 4–6 dB 混音平衡）
  · 但工作区里**一个音乐文件都没有**（只有配音 mp3），本机也**没有音乐素材库**
  · 技能的 `make_click_track.py` 还**写死**了那次 18 秒小样的路径 `assets\\audio\\music\\bgm.wav`
  ⇒ 模型**无米下锅**，只好做了一条**纯人声**的片子 ✗

## 关键约束（实测）

**便携版运行时里没有 numpy / scipy / soundfile / pydub**（`ModuleNotFoundError`）
⇒ 合成器**只能用标准库**（`wave` + `math` + `array`）—— 否则产品里根本跑不起来 ✗

## 实测验证过的能力

纯标准库合成 8 秒 120 BPM 音乐床 → 喂给技能的 `beatmap.mjs`：
    检出 tempo = 120 BPM、16 拍、漂移仅 10 ms ✓
⇒ 「合成的音乐能被本技能的节拍检测认出来」—— 这是本脚本存在的意义 ✓

## 本文件钉什么

1. **能用**：合成的 WAV 是合法 44.1k/16bit 单声道，时长符合要求
2. **节拍可信**：文件名/元数据里带 BPM，且**强拍真的落在 BPM 网格上**（能量峰值位置）
3. **不抢人声**：默认输出给混音留 headroom（峰值不逼近满刻度）
4. **无第三方依赖**：**不许 import numpy/scipy/soundfile/pydub**（便携运行时没有）
5. **不写死题材**：风格是参数（卡点/口播/温柔…），不许把某种视频写死进去
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib
import struct
import types
import wave

import pytest

SKILL = pathlib.Path(__file__).resolve().parents[2] / "backend" / "skills" / "卡点视频"
BGM = SKILL / "tools" / "make_bgm.py"


def _load() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("skill_make_bgm", BGM)
    assert spec and spec.loader, f"加载不了 {BGM}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_bgm_script_ships_with_the_skill():
    """脚本必须随技能包分发（否则模型无米下锅）。"""
    assert BGM.exists(), f"技能里没有 BGM 合成器：{BGM} ✗"


# ─────────────────────────────────────────────────────────────────────────────
# 一、无第三方依赖（★ 便携运行时没有 numpy —— 这是硬约束）
# ─────────────────────────────────────────────────────────────────────────────

def test_no_third_party_imports():
    """★★ **不许** import numpy / scipy / soundfile / pydub。

    实测：便携版运行时（`runtime\\python.exe`）里这些**全都没有**
    ⇒ 用了就 `ModuleNotFoundError`，产品里根本跑不起来 ✗
    """
    tree = ast.parse(BGM.read_text("utf-8"))
    banned = {"numpy", "scipy", "soundfile", "pydub", "librosa", "torch"}
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad += [a.name for a in node.names if a.name.split(".")[0] in banned]
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in banned:
                bad.append(node.module)
    assert not bad, (
        f"BGM 合成器用了第三方库 {bad} ✗ —— 便携运行时里没有它们（实测 ModuleNotFoundError）"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 二、能用：产出合法 WAV
# ─────────────────────────────────────────────────────────────────────────────

def test_synthesizes_a_valid_wav(tmp_path):
    """产出必须是合法 WAV，采样率/位深/声道符合约定，时长对。"""
    mod = _load()
    out = tmp_path / "bgm.wav"
    mod.synthesize(out, seconds=4.0, bpm=120.0, style="beat")
    assert out.exists() and out.stat().st_size > 1000, "没产出音频 ✗"
    with wave.open(str(out), "rb") as w:
        assert w.getframerate() == mod.SAMPLE_RATE, f"采样率 {w.getframerate()} ✗"
        assert w.getsampwidth() == 2, "位深不是 16bit ✗"
        assert w.getnchannels() == 1, "不是单声道 ✗"
        dur = w.getnframes() / w.getframerate()
        assert abs(dur - 4.0) < 0.05, f"时长 {dur:.2f}s（要 4.0）✗"


def test_duration_and_bpm_are_respected(tmp_path):
    """不同时长/BPM 都要能出（不许写死一种）。"""
    mod = _load()
    for secs, bpm in ((2.0, 90.0), (6.5, 150.0)):
        out = tmp_path / f"b_{int(secs)}_{int(bpm)}.wav"
        mod.synthesize(out, seconds=secs, bpm=bpm, style="beat")
        with wave.open(str(out), "rb") as w:
            got = w.getnframes() / w.getframerate()
        assert abs(got - secs) < 0.05, f"{secs}s/{bpm}bpm → 出了 {got:.2f}s ✗"


# ─────────────────────────────────────────────────────────────────────────────
# 三、★ 节拍真的落在 BPM 网格上（否则卡点视频对不上拍）
# ─────────────────────────────────────────────────────────────────────────────

def _read_mono(path: pathlib.Path) -> list[float]:
    with wave.open(str(path), "rb") as w:
        raw = w.readframes(w.getnframes())
    n = len(raw) // 2
    return [v / 32768.0 for v in struct.unpack(f"<{n}h", raw[: n * 2])]


def test_beat_grid_is_real(tmp_path):
    """★★ 最强的判据：**低频能量峰值必须落在 BPM 网格上**。

    为什么钉这条：卡点视频的整个前提是"画面切在拍上"。如果合成出来的"120 BPM"
    其实重音落在别处，那 beatmap 检出的 120 是**巧合**，切点会全错 ✗
    做法：按 10 ms 分窗算低频能量包络，找峰值时刻，验证它们与 60/BPM 网格的偏差。
    """
    mod = _load()
    out = tmp_path / "beat.wav"
    mod.synthesize(out, seconds=6.0, bpm=120.0, style="beat")
    s = _read_mono(out)
    sr = mod.SAMPLE_RATE

    # 10ms 窗的能量
    W = sr // 100
    env = [sum(x * x for x in s[i:i + W]) for i in range(0, len(s) - W, W)]
    if not env:
        pytest.fail("音频太短，量不出包络 ✗")
    peak = max(env)
    assert peak > 0, "全静音 ✗"
    # 取超过 60% 峰值的窗作为"重音所在"
    hot = [i for i, v in enumerate(env) if v >= peak * 0.6]
    assert hot, "找不到任何重音 ✗"

    beat_s = 60.0 / 120.0                       # 0.5 s
    # 每个重音窗到最近拍点的偏差（秒）
    devs = []
    for i in hot:
        t = i * 0.01
        k = round(t / beat_s)
        devs.append(abs(t - k * beat_s))
    worst = max(devs)
    # 允许 90ms（10ms 窗 + 起音包络的固有延迟）
    assert worst <= 0.09, (
        f"重音与 120BPM 网格最大偏差 {worst * 1000:.0f}ms ⇒ 节拍是假的，切点会全错 ✗"
    )


def test_different_bpm_moves_the_grid(tmp_path):
    """BPM 是参数：120 → 160，拍距必须跟着从 0.5s 变到 0.375s。"""
    mod = _load()
    slow = tmp_path / "slow.wav"
    fast = tmp_path / "fast.wav"
    mod.synthesize(slow, seconds=4.0, bpm=120.0, style="beat")
    mod.synthesize(fast, seconds=4.0, bpm=160.0, style="beat")
    a, b = _read_mono(slow), _read_mono(fast)
    assert a != b, "换了 BPM 却出了完全一样的音频 ⇒ BPM 没生效 ✗"

    def count_hits(sig: list[float], sr: int) -> int:
        W = sr // 100
        env = [sum(x * x for x in sig[i:i + W]) for i in range(0, len(sig) - W, W)]
        if not env:
            return 0
        pk = max(env)
        hits, last = 0, -99
        for i, v in enumerate(env):
            if v >= pk * 0.6 and i - last > 20:      # 至少间隔 200ms 才算一次重音
                hits += 1
                last = i
        return hits

    hs, hf = count_hits(a, mod.SAMPLE_RATE), count_hits(b, mod.SAMPLE_RATE)
    assert hf > hs, f"160BPM({hf} 次重音) 没有比 120BPM({hs} 次) 更密 ⇒ BPM 没生效 ✗"


# ─────────────────────────────────────────────────────────────────────────────
# 四、混音 headroom（不许把音轨顶满 —— 后面还要压人声进去）
# ─────────────────────────────────────────────────────────────────────────────

def test_leaves_headroom_for_the_voice(tmp_path):
    """★ 峰值不许逼近满刻度：这条轨要垫在人声**下面**，顶满了就没法混 ✗"""
    mod = _load()
    out = tmp_path / "h.wav"
    mod.synthesize(out, seconds=4.0, bpm=120.0, style="beat")
    s = _read_mono(out)
    pk = max(abs(v) for v in s)
    assert pk <= 0.92, f"峰值 {pk:.2f} 太满（留给混音的余量不足）✗"
    assert pk >= 0.15, f"峰值 {pk:.2f} 太小（等于没声音）✗"


def test_no_clipping(tmp_path):
    """不许削波（样本打到 ±1.0 会失真）。"""
    mod = _load()
    out = tmp_path / "c.wav"
    mod.synthesize(out, seconds=4.0, bpm=120.0, style="beat")
    s = _read_mono(out)
    assert max(abs(v) for v in s) < 0.999, "削波了 ✗"


# ─────────────────────────────────────────────────────────────────────────────
# 五、风格是参数（不写死题材）
# ─────────────────────────────────────────────────────────────────────────────

def test_styles_are_parameters_not_hardcoded():
    """风格必须是**可选参数**，且至少提供两档（卡点/口播）—— 技能要做任意风格。"""
    mod = _load()
    styles = getattr(mod, "STYLES", None)
    assert isinstance(styles, dict) and len(styles) >= 2, (
        f"STYLES 至少要有两档（实际 {styles}）✗ 技能要做各种视频，不许写死一种"
    )
    for name in styles:
        assert isinstance(name, str) and name, "风格名必须是非空字符串 ✗"


def test_unknown_style_falls_back_instead_of_crashing(tmp_path):
    """传了不认识的风格不许崩（模型经常拼错参数）—— 退回默认档。"""
    mod = _load()
    out = tmp_path / "x.wav"
    mod.synthesize(out, seconds=2.0, bpm=110.0, style="这个风格不存在")
    assert out.exists() and out.stat().st_size > 500, "未知风格时应退回默认而不是崩 ✗"


def test_output_filename_says_the_bpm():
    """★ 文件名要带 BPM —— 让"节拍网格"这件事在流水线里**看得见**（skill 的约定）。"""
    mod = _load()
    name = mod.suggest_filename(bpm=120.0, style="beat")
    assert "120" in name, f"文件名 {name} 没带 BPM ⇒ 后面容易对错拍 ✗"
    assert name.endswith(".wav"), f"文件名 {name} 不是 .wav ✗"


def test_cli_runs(tmp_path, capsys):
    """命令行可用（模型是通过 shell 调它的）。"""
    mod = _load()
    out = tmp_path / "cli.wav"
    rc = mod.main([str(out), "--seconds", "2", "--bpm", "120", "--style", "beat"])
    assert rc == 0, f"CLI 退出码 {rc} ✗"
    assert out.exists(), "CLI 没产出文件 ✗"
    printed = capsys.readouterr().out
    assert "120" in printed, "CLI 没打印 BPM（模型要靠它确认）✗"


def test_cli_warns_above_the_detector_ceiling(tmp_path, capsys):
    """★★ 超过检测上限的 BPM 必须**主动警告**（实测：会被检成一半，报告不可信）。

    实测扫描（本脚本合成的轨 → `beatmap.mjs`）：
        100/120/130/140/150/155 → 检对 ✓
        160/165/180            → 检成一半 ✗
    ⇒ 调用方得知道这件事，否则会把"79.73 BPM"当成真的写进报告 ✓
    """
    mod = _load()
    ceiling = getattr(mod, "DETECT_BPM_CEILING", None)
    assert isinstance(ceiling, (int, float)) and 100 <= ceiling <= 200, (
        f"没有 DETECT_BPM_CEILING 或值不合理：{ceiling} ✗"
    )
    out = tmp_path / "hi.wav"
    rc = mod.main([str(out), "--seconds", "2", "--bpm", str(int(ceiling) + 20),
                   "--style", "beat"])
    text = capsys.readouterr().out
    assert rc == 0, "超上限不该失败（只是要警告）✗"
    assert "上限" in text or "一半" in text, f"没警告八度误判风险 ✗\n{text}"
    # 上限之内不该打扰
    out2 = tmp_path / "ok.wav"
    mod.main([str(out2), "--seconds", "2", "--bpm", "120", "--style", "beat"])
    text2 = capsys.readouterr().out
    assert "上限" not in text2, "120 BPM（安全值）不该警告 ✗"


def test_skill_md_tells_the_agent_to_synthesize_music_when_missing():
    """★ SKILL.md 必须告诉模型：**没有音乐素材就自己造**（这是用户那次纯人声片的根因）。

    真机事实：技能处处要求音乐，但工作区里一个音乐文件都没有、本机也没有素材库
    ⇒ 模型无米下锅，交了一条纯人声片 ✗
    """
    md = (SKILL / "SKILL.md").read_text("utf-8", errors="replace")
    assert "make_bgm.py" in md, "SKILL.md 里没提 make_bgm.py ⇒ 模型仍然无米下锅 ✗"
    # 必须落在**第 3 步的章节里**（音乐是节拍检测的输入）
    #   ★ 判据要认标题（`### 第 N 步`），不能只找"第 N 步"字样 ——
    #     正文里也有"必须进第 4 步"这种句子，会误判（本班踩过）
    i_bgm = md.find("make_bgm.py")
    i_step3 = md.find("### 第 3 步")
    i_step4 = md.find("### 第 4 步")
    assert 0 < i_step3 < i_bgm < i_step4, (
        f"合成器的说明没写在「### 第 3 步」章节里"
        f"（第3步@{i_step3} 合成器@{i_bgm} 第4步@{i_step4}）✗"
    )
    # 上限这件事也要让模型知道
    assert "155" in md, "没告诉模型节拍检测的 BPM 上限（超过会检成一半）✗"
