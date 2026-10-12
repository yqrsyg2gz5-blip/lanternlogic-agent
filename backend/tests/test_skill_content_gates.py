# -*- coding: utf-8 -*-
"""「卡点视频」技能的三条内容硬门必须写在技能里，且判据要能执行（★ 2026-10-12）。

## 为什么有这个文件

技能里那三条（8.11 尾部空转 / 8.12 逐字高亮 / 8.13 字幕覆盖率）是**实测踩出来的**，
每一条都对应一次真实翻车：

  · 8.11 —— 10 秒小样尾部 2.5 s 五个拍点全空转（占全片 25%）
  · 8.12 —— 10 秒小样高亮是**定时匀速动画**，人声停顿里照常前进；
            ★ 30 秒片又暴露**第二种**：驱动源对了，但动画窗口用了词边界 `we`，
              比实际发声尾长 ⇒ **41% 的词过冲 >40ms、最大 109ms（字幕慢半拍）**
  · 8.13 —— 10 秒小样字幕只覆盖 44%

**这些坑的共同点：技术指标全绿也照样是废片**（响度/黑场/冻帧/确定性都合格）。
所以它们必须**留在技能里**，而且**判据必须能执行**（写清口径 + 给可跑的量法），
否则下一个人会重新踩一遍 ✗。

本文件钉四件事：
  ① 三条都在 SKILL.md 里，且都进了返工判定名单
  ② 每条的判据都**写了口径**（不许只写"要注意"这种没法定量的话）
  ③ 8.12 必须包含「词尾过冲」这一条（本轮新增的那个坑）
  ④ 验收清单里要有**可执行**的量法（代码块），不是只有描述
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SKILL = (ROOT / "backend" / "skills" / "卡点视频" / "SKILL.md").read_text("utf-8", errors="replace")
CHECK = (ROOT / "backend" / "skills" / "卡点视频" / "references" / "验收清单.md").read_text(
    "utf-8", errors="replace")


# ═══ ① 三条硬门都在，且都进返工判定 ═══

def test_three_content_gates_exist_and_are_in_the_rework_rule():
    for n in ("8.11", "8.12", "8.13"):
        assert n in SKILL, f"SKILL.md 里找不到 {n} —— 这条内容硬门被删了 ✗"
    # 返工判定那一行必须点名这三条（只写 8.3–8.9 等于放它们过去）
    line = next((l for l in SKILL.splitlines() if l.startswith("**返工判定（硬规则）**")), "")
    assert line, "找不到返工判定那行 ✗"
    for n in ("8.11", "8.12", "8.13"):
        assert n in line, f"返工判定里没点名 {n} ⇒ 不过也不会返工 ✗"


# ═══ ② 每条都要写清「口径」，不许只写"要注意" ═══

def test_each_gate_declares_its_measurement_caliber():
    assert "逐帧平均灰度差" in SKILL and "0.5 s" in SKILL, \
        "8.11 没写清帧差口径与分桶粒度 ⇒ 两次测量不可比 ✗（本轮实测踩过）"
    assert "−35 dB" in SKILL or "-35 dB" in SKILL, \
        "8.12 没写清相对阈值 ⇒ 换阈值结论就反（本轮先用 −30dB 量得中位 4ms 差点放过）✗"
    assert "85%" in SKILL or "y 85" in SKILL, \
        "8.13 没写清字幕行横带位置 ⇒ 会拿画面中部大字当字幕 ✗"


# ═══ ③ 8.12 必须包含「词尾过冲」这一条 ═══

def test_gate_8_12_covers_the_word_tail_overrun():
    """本轮新发现的坑：驱动源对了不等于对齐了。"""
    assert "过冲" in SKILL, (
        "SKILL.md 里没有「词尾过冲」✗\n"
        "  ⇒ 只查驱动源会漏掉「动画窗口比实际发声长 ⇒ 字幕慢半拍」这一类 ✓\n"
        "  ⇒ 30 秒片实测：41% 的词过冲 >40ms、最大 109ms ✓"
    )
    assert "实际发声尾" in SKILL, "没写清修法（窗口尾要收到「实际发声尾」）✗"
    assert "中位" in SKILL, (
        "没提醒「别只看中位数」✗ —— 那条片子过冲中位仅 4ms，问题全在尾部四成词上 ✓"
    )


# ═══ ④ 验收清单里要有可执行量法（代码块） ═══

def test_checklist_has_runnable_measurement_code():
    assert "stem-voice.wav" in CHECK, \
        "验收清单没点明要用**人声干声**量 ✗（用混音轨测静音会失效 —— 本轮踩过）"
    assert "np.nonzero" in CHECK and "we_" in CHECK, \
        "验收清单里没有可跑的量过冲代码 ✗（只有描述的话下一个人还得自己摸索）"
    for n in ("18.1", "18.2", "18.3"):
        assert n in CHECK, f"验收清单里缺 {n} 小节 ✗"
