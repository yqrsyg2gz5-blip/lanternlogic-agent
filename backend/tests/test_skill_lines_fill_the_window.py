# -*- coding: utf-8 -*-
"""★★ 台词要**填满场景窗口**、而且要像人话（2026-10-14 用户反馈）。

## 用户原话

> 「画面行，内容不行。内容哎呀……这 20 秒感觉就是说话的内容不行啊，
>   画面什么都行」

## 现场数字（`task_20261014_332781e8`，20 秒卡点片）

```
5 条台词合计只有 41 个非空字符 / 12.2 秒  ⇒ 3.4 字/秒
中文自然语速实测 5.2~5.5 字/秒
⇒ 每场都短一截、20 秒里空掉 3 秒 ⇒ 观众在等下一句 ✗
```

画面是好的（用户认可），**台词拖了后腿** ⇒ 病根在"没人规定台词要填满窗口" ✗

## 本文件钉什么

1. 技能里必须有**字数公式**（每场该多少字，能算不用猜）
2. 技能里必须列出**五条病根**（没钩子 / 没动词 / 没转折 / 互不相干 / 太短太空）
3. 技能里必须说明**太长也不行**（超窗口会被切）—— 不许只写上限不写下限
4. 五条病根要**逐条给出反例与改法**（反例用真机产出那几句）
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
SKILL = ROOT / "backend" / "skills" / "卡点视频" / "SKILL.md"


def _md() -> str:
    assert SKILL.exists(), f"技能不在：{SKILL} ✗"
    return SKILL.read_text("utf-8", errors="replace")


def test_skill_states_the_line_length_formula():
    """★ 必须有**能算的**字数公式（不是"「看着差不多」"）✗"""
    t = _md()
    assert "5.4" in t, (
        "技能里没有中文自然语速基准（5.4 字/秒）⇒ 模型只能拍脑袋定台词长度 ✗\n"
        "  真机后果：41 字撑 20 秒（3.4 字/秒），每场都短一截 ✗"
    )
    assert re.search(r"≈\s*可放秒数\s*×\s*5\.4", t), (
        "没有「字数 ≈ 可放秒数 × 5.4」这条公式 ⇒ 台词长度定不准 ✗"
    )
    assert "0.2" in t, (
        "没写「场长 − 0.2s」的余量口径 ⇒ 配音起止会贴边 ✗"
    )


def test_skill_lists_the_five_narration_defects():
    """★ 五条病根都要在（这是真机反馈的根因分类）✓"""
    t = _md()
    for flaw in ("没钩子", "没动词", "没转折", "互不相干", "太短太空"):
        assert flaw in t, f"技能里没写「{flaw}」这条病根 ⇒ 模型还会犯 ✗"


def test_skill_gives_before_after_examples():
    """★ 光说病名没用 —— 必须给**反例 → 改法**（反例用真机产出的那几句）✓"""
    t = _md()
    assert "单次输出，从六万四到一百万" in t, (
        "没把真机的反例写进去 ⇒ 模型不知道自己写的算不算「平铺」✗"
    )
    assert "老板，看这个数" in t or "老板" in t, (
        "没给「有钩子」的正例 ⇒ 模型改不对方向 ✗"
    )
    assert "干到" in t, "没给「有动词」的对照（从…到… vs 干到）✗"


def test_skill_says_too_long_is_also_bad():
    """★ 不许只写上限不写下限 —— **太长会被切**，字幕就跟画面错位了 ✗"""
    t = _md()
    assert re.search(r"太长(也)?不行|超窗口", t), (
        "没写「台词太长也不行」⇒ 模型以为越长越好，超窗口被切、字幕错位 ✗"
    )


def test_skill_gives_a_computable_self_check():
    """★ 交付前要能**自己算一遍**（落 90%~100% 才算填满）✓"""
    t = _md()
    assert "0.9" in t and "1.0" in t, (
        "没给「落 90%~100% 才算填满」的判据 ⇒ 自检没法做 ✗"
    )
    assert "非空字符" in t or "len([" in t, (
        "没说清「字数」怎么数（非空字符）⇒ 中英混排时算不准 ✗"
    )
