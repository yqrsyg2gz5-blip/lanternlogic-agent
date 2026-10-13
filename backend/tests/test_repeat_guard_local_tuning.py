# -*- coding: utf-8 -*-
"""★ 针对**本地小模型**的重复熔断收紧 —— 且**不改变云端 API 的行为**。

## 背景（真机事故，用户 task_20261013_0ea1f8de）

用户跑 30 秒视频任务，模型（bonsai 27B，本地）在一条命令上绕了 **37 次**：

    python -c "跑 ffmpeg -encoders，打印含 x264 的行"
    # 输出每次都一模一样

而护栏的阈值是**跟步数预算联动**的（我上一轮为了"别误伤长任务"改的）：

    预算 300 步 → 第 37 次才提醒、第 60 次才熔断
    ⇒ 它跑到 37 次时护栏才开口，那时已经烧掉 1/4 额度 ✗

## 用户的硬约束（原话）

「针对 27B 这这种的本地部署模型的话，必须得优化」
「其他的 API 啊什么千万别改错了，能给让其他 API 能更好，但别让他们降低功能」

⇒ 所以本文件的每一条都钉两件事：
  ① **本地模型**：收紧（更早提醒、更早熔断）
  ② **云端 API**：**行为一个字都不许变**（阈值与修复前完全相同）

## 判据设计（为什么不直接调小阈值）

朴素做法是"把 5/8 调成 3/5"，但那会**误伤云端**：云端模型重复跑同一条命令
有时是合理的（重试网络、等文件写完）。真正的区别在于**有没有进展**：

  · 每次输出**都一样** ⇒ 纯浪费，无论什么模型都该早停
  · 输出**在变**（进度在涨）⇒ 允许继续

所以判据用两条，都只在"确实浪费"时生效：
  1. **相邻重复**（这条命令刚刚才跑过）—— 只对本地端点收紧
  2. **输出没变**（这次结果和上次一字不差）—— 对所有端点加强
"""
from __future__ import annotations

from app.loop import (
    _base_thresholds,
    _looks_like_failure,
    _output_fingerprint,
    _repeat_thresholds,
    _thresholds_with_escalation,
)


# ─────────────────────────────────────────────────────────────────────────────
# 一、云端行为**一个字都不许变**（回归护栏）
# ─────────────────────────────────────────────────────────────────────────────

def test_cloud_thresholds_are_unchanged():
    """★★ 用户硬约束：非本地端点时，阈值与修复前**完全相同**。

    这条是防我自己"顺手优化"把云端改差 —— 任何对 `_repeat_thresholds`
    的默认行为改动都会让这条变红 ✗
    """
    for budget in (4, 8, 25, 60, 120, 180, 240, 300, 360, 1000):
        assert _repeat_thresholds(budget) == _base_thresholds(budget), (
            f"预算 {budget}：云端阈值被改了 ✗（用户要求其他 API 不许降级）"
        )


def test_cloud_escalation_only_when_output_is_identical():
    """云端只在"输出一字不差"时才提前一档（那确实是浪费，不是合理重试）。"""
    base_w, base_t = _base_thresholds(300)
    # 输出在变 ⇒ 云端不该被提前
    w, t = _thresholds_with_escalation(300, is_local=False, same_output=False)
    assert (w, t) == (base_w, base_t), "输出在变却把云端阈值收紧了 ✗"
    # 输出一字不差 ⇒ 可以提前（但只提前一档，别激进）
    w2, t2 = _thresholds_with_escalation(300, is_local=False, same_output=True)
    assert w2 < base_w and t2 < base_t, (
        f"输出一字不差却没提前：{base_w}/{base_t} → {w2}/{t2} ✗"
    )
    assert w2 >= base_w // 2, (
        f"云端被收得太狠（{base_w} → {w2}）⇒ 会误伤合理重试 ✗"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 二、本地模型**必须**收紧（用户诉求）
# ─────────────────────────────────────────────────────────────────────────────

def test_local_thresholds_are_tightened():
    """★ 本地端点：无论预算多大，都收紧到"很早就能拦住"。

    真机证据：预算 300 步时基线是 37/60 ⇒ 它绕了 37 次护栏才开口 ✗
    """
    for budget in (60, 120, 300, 360):
        w, t = _thresholds_with_escalation(budget, is_local=True, same_output=False)
        assert w <= 6, f"预算 {budget}：本地提醒阈值 {w} 太松（真机绕了 37 次）✗"
        assert t <= 10, f"预算 {budget}：本地熔断阈值 {t} 太松 ✗"


def test_local_with_identical_output_is_tightest():
    """本地 + 输出一字不差 = 最该早停的组合。"""
    w, t = _thresholds_with_escalation(300, is_local=True, same_output=True)
    wl, tl = _thresholds_with_escalation(300, is_local=True, same_output=False)
    assert w <= wl and t <= tl, "本地+同输出没有比本地更紧 ✗"
    assert t <= 8, f"本地+同输出的熔断阈值 {t} 还太松 ✗"


def test_local_still_never_trips_before_warning():
    """任何组合下熔断都必须**晚于**提醒（否则第一次就熔断）。"""
    for budget in (4, 8, 25, 60, 300, 360):
        for loc in (True, False):
            for same in (True, False):
                w, t = _thresholds_with_escalation(budget, is_local=loc,
                                                   same_output=same)
                assert 1 <= w < t, f"预算{budget} 本地{loc} 同输出{same} → {w}/{t} ✗"


# ─────────────────────────────────────────────────────────────────────────────
# 三、"输出有没有变"的指纹
# ─────────────────────────────────────────────────────────────────────────────

def test_output_fingerprint_detects_identical_output():
    """同样的输出 ⇒ 同样指纹；差一个字 ⇒ 不同指纹。"""
    a = "V....D libx264  libx264 H.264 / AVC"
    b = "V....D libx264  libx264 H.264 / AVC"
    c = "V....D libx265  x265 H.265"
    assert _output_fingerprint(a) == _output_fingerprint(b)
    assert _output_fingerprint(a) != _output_fingerprint(c)


def test_output_fingerprint_ignores_whitespace_noise():
    """只有空白差异 ⇒ 算"没变"（命令行输出常带多余空行）。"""
    assert _output_fingerprint("abc def") == _output_fingerprint("abc   def\n\n")
    assert _output_fingerprint("abc def") == _output_fingerprint("\n  abc def  \n")


def test_output_fingerprint_handles_empty():
    """空输出也要有稳定指纹（不许抛）。"""
    assert _output_fingerprint("") == _output_fingerprint(None)
    assert isinstance(_output_fingerprint(""), str)


def test_failure_detection_still_intact():
    """别把上一轮修的 `_looks_like_failure` 弄坏了（回归）。"""
    assert _looks_like_failure(True, "exit=1\nAttributeError: x") is True
    assert _looks_like_failure(True, "Saved OK") is False


# ─────────────────────────────────────────────────────────────────────────────
# 四、"这个 provider 是不是本地"的判据（判错会把云端阈值也收紧 ✗）
# ─────────────────────────────────────────────────────────────────────────────

def test_provider_is_local_reads_the_flag_or_method():
    """provider 上有布尔属性 / 有方法 / 只有 base_url —— 三种都要能判对。"""
    from app.loop import _provider_is_local

    class ByAttr:
        is_local_endpoint = True

    class ByMethod:
        @staticmethod
        def _is_local_endpoint() -> bool:
            return True

    class ByUrl:
        base_url = "http://127.0.0.1:8080/v1"

    class Cloud:
        base_url = "https://api.deepseek.com/v1"

    assert _provider_is_local(ByAttr()) is True
    assert _provider_is_local(ByMethod()) is True
    assert _provider_is_local(ByUrl()) is True
    assert _provider_is_local(Cloud()) is False


def test_provider_is_local_defaults_to_false_on_trouble():
    """★ 判据出错时**必须返回 False**（宁可不动，也不许误伤云端）。"""
    from app.loop import _provider_is_local

    class Boom:
        @staticmethod
        def _is_local_endpoint() -> bool:
            raise RuntimeError("炸了")

    assert _provider_is_local(Boom()) is False, "判据抛异常时没兜住 ✗"
    assert _provider_is_local(None) is False
    assert _provider_is_local(object()) is False


def test_local_url_hints_match_the_provider_rule():
    """本地地址判据要与 provider 那边**同口径**（别各判各的）。"""
    from app.loop import _url_is_local
    from app.providers.openai_compat import OpenAICompatProvider

    for u in ("http://127.0.0.1:8080/v1", "http://localhost:11434/v1",
              "http://0.0.0.0:8000/v1", "http://[::1]:8080/v1"):
        assert _url_is_local(u) is True, f"{u} 该判本地 ✗"
        assert OpenAICompatProvider._LOCAL_HINTS and any(
            h in u.lower() for h in OpenAICompatProvider._LOCAL_HINTS
        ), f"{u} 在 provider 那边判不出本地（口径不一致）✗"
    for u in ("https://api.deepseek.com/v1", "https://api.openai.com/v1",
              "https://dashscope.aliyuncs.com/compatible-mode/v1"):
        assert _url_is_local(u) is False, f"{u} 不该判本地 ✗"
