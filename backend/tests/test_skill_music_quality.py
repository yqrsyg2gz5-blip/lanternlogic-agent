# -*- coding: utf-8 -*-
"""★★ 音乐质量：自合成的曲子必须"像人弹的"，不是"电子打字机"（2026-10-15 用户反馈）

## 用户原话

> 「音乐不好听，然后还有断句儿，做得不太好，感觉这整个那天这种片子不太顺畅」

## 现场（60 秒片 `task_20261014_d459cbb4`，实测）

```
合成手法清单：
  有（3 样）：release · pan · arp
  ★ 缺（10 样）：reverb · delay · chorus · detune · sidechain
                compress · saturat · swing · velocity · humanize
```

**每个缺失对应一种「不好听」：**

| 缺什么 | 听感 |
|---|---|
| `reverb` 混响 | **干、贴脸、像玩具琴** ← 最致命 |
| `delay` 延迟 | 没有空间感和尾韵 |
| `chorus` 合唱 | 不宽、不厚 |
| `detune` 微失谐 | 每个音色太「纯」⇒ 电子味重 |
| `sidechain` 侧链 | 鼓和旋律不互相让位 ⇒ 糊成一团 |
| `compress` 压缩 | 忽大忽小、没有整体推力 |
| `saturat` 饱和 | 没有暖度和质感 |
| `swing` 摇摆 | 节奏死板（每个音都卡在整拍） |
| `velocity` 力度 | **每个音力度一样 ⇒ 像打字机** |
| `humanize` 人性化 | 完全机械，没有人类演奏的微小偏差 |

## 本文件钉什么

技能里必须写明这 10 样**至少要做哪几样**，以及每样的"最小做法"（能照着写代码的那种）。
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SKILL = ROOT / "backend" / "skills" / "卡点视频" / "SKILL.md"


def _md() -> str:
    assert SKILL.exists(), f"技能不在：{SKILL} ✗"
    return SKILL.read_text("utf-8", errors="replace")


def test_skill_has_a_music_quality_section():
    """★ 技能里必须有「音乐质量」这一节 —— 不能只说"合成一段 BGM"就完事 ✗"""
    t = _md()
    assert "音乐质量" in t or "像人弹的" in t, (
        "技能里没有音乐质量要求 ⇒ 模型只会合成「能响的 midi」（真机就是这么被用户批的）✗"
    )


def test_skill_lists_the_ten_missing_techniques():
    """★★ 那 10 样必须逐条写进去（真机缺的正是这 10 样）✓"""
    t = _md()
    NEED = ("reverb", "delay", "chorus", "detune", "sidechain",
            "compress", "saturat", "swing", "velocity", "humanize")
    miss = [k for k in NEED if k not in t]
    assert not miss, (
        f"技能里没写这些手法：{miss} ⇒ 模型还会合成「电子打字机」✗\n"
        f"  （真机实测：60 秒片只用了 release/pan/arp 三样）"
    )


def test_skill_says_reverb_and_velocity_are_mandatory():
    """★ 至少这两样要标成"必须"：混响（治"干"）+ 力度变化（治"打字机"）✓"""
    t = _md()
    # 找那一节里的"必须/必做"标记
    i = t.find("音乐质量")
    if i < 0:
        i = t.find("像人弹的")
    seg = t[i:i + 4000] if i >= 0 else t
    assert "必须" in seg, "音乐质量那节没有「必须」的标定 ⇒ 全是建议等于没有 ✗"
    for k in ("reverb", "velocity"):
        assert k in seg, f"{k} 不在音乐质量那一节里 ✗"


def test_skill_gives_a_minimal_implementation_for_each():
    """★ 光列名词没用 —— 每样要给**最小做法**（能照着写代码）✓"""
    t = _md()
    # 至少要有"怎么做"的迹象：卷积/衰减/延迟时间/随机 ±/低频压缩 之类
    marks = ("衰减", "延迟时间", "随机", "微失谐", "低频", "半拍", "阈值", "反馈比")
    hit = [m for m in marks if m in t]
    assert len(hit) >= 4, (
        f"只有名词没有做法（只命中 {hit}）⇒ 模型还是写不出来 ✗"
    )
