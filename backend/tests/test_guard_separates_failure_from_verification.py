# -*- coding: utf-8 -*-
"""★★ 护栏必须区分「反复失败」和「反复验证」（2026-10-14 真机误熔断）。

## 现场（`task_20261014_47344d15`）—— 这条任务是**成功的**，却被熔断

```
成片已经做出来了（out/final.mp4 4.68 MB、体检退出码 0、10 项交付物齐全）
它跑 qa_video.py 体检：
  第 1 次 → `report written, passed= False`（没过，它去修）
  第 2 次 → `全部通过` ✓
  之后又跑了几次**确认**（都是 ✓）
★ 而旧口径**把"重跑体检"全算成绕圈** ⇒ 累计 20 次熔断、3 次后 giveup
⇒ 任务停在 `partial`、**没走到 task_done** ⇒ 界面显示黄点、用户以为没交付 ✗
```

**护栏分不清"反复失败"和"反复验证"** —— 后者是负责任的正常行为，
按旧口径惩罚它，等于**惩罚它做验收** ✗（而验收正是这个技能的核心要求：
技能里明写「别把"做了一步"当成"做完了"，交付前必须跑 `tools/qa_video.py`」）

## 修法

同类计数只在**上一次真的失败了**才累加；**上一次成功就清零** ✓

## 本文件钉什么

1. `_bump_kind_count` 有 `prev_failed` 参数，且三种取值语义正确
2. 反复「上一步成功」的同类动作 ⇒ **计数永远是 0**（复跑验证不该被拦）
3. 反复「上一步失败」⇒ **照样按时熔断**（不许把护栏改瘫）
4. 「失败 5 次 → 成功 1 次」⇒ **清零**（真有进展）
5. 没信息（`None`）⇒ 保守累加（不放松护栏）
6. 循环里把「上一次成功/失败」真接上了（写了不接 = 死代码）
"""
from __future__ import annotations

import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.loop import _KIND_TRIP_AT, _KIND_WARN_AT, _bump_kind_count  # noqa: E402

KIND = "媒体转码抽帧"


def test_repeated_successful_checks_never_trip():
    """★★ 核心：**反复验证（每一步都成功）永远不该被熔断** ✓

    真机：成片已做出来，跑 qa_video 确认几次 ⇒ 旧口径把它熔断了 ✗
    """
    counts: dict[str, int] = {}
    for _ in range(40):          # 跑 40 次，远超熔断线 20
        n = _bump_kind_count(counts, KIND, prev_failed=False)
        assert n == 0, (
            f"反复验证被计数到 {n} ⇒ 会熔断 ⇒ "
            f"惩罚「做验收」（真机就是这么把一条做完的任务卡成 partial 的）✗"
        )


def test_repeated_failures_still_trip():
    """★ 别把护栏改瘫：**真的在原地失败**要照样按时熔断** ✓"""
    counts: dict[str, int] = {}
    seen = []
    for _ in range(_KIND_TRIP_AT + 2):
        seen.append(_bump_kind_count(counts, KIND, prev_failed=True))
    assert seen[-1] >= _KIND_TRIP_AT, f"反复失败没累加：{seen[-1]} ✗"
    assert _KIND_WARN_AT in seen, "提醒线（12）没到过 ⇒ 阈值变了 ✗"
    # 单调递增（前 _KIND_TRIP_AT 次）
    head = seen[:_KIND_TRIP_AT]
    assert head == sorted(head) and len(set(head)) == len(head), head


def test_success_resets_the_counter():
    """★ 「失败 N 次 → 成功 1 次」⇒ 清零（**真有进展**，不该接着算）✓"""
    counts: dict[str, int] = {}
    for _ in range(5):
        _bump_kind_count(counts, KIND, prev_failed=True)
    assert counts[KIND] == 5, counts
    _bump_kind_count(counts, KIND, prev_failed=False)
    assert counts[KIND] == 0, (
        f"成功后没清零（还是 {counts[KIND]}）⇒ 它会一直背着旧账，"
        f"再失败几次就被误熔断 ✗"
    )


def test_unknown_previous_state_stays_conservative():
    """★ 没信息（None）⇒ **照旧累加**（不放松护栏）✓"""
    counts: dict[str, int] = {}
    for i in range(1, 4):
        n = _bump_kind_count(counts, KIND, prev_failed=None)
        assert n == i, (n, i)


def test_switching_kind_still_resets_others():
    """★ 换一类动作仍要清零（老规矩不许坏）✓"""
    counts: dict[str, int] = {}
    for _ in range(3):
        _bump_kind_count(counts, KIND, prev_failed=True)
    n = _bump_kind_count(counts, "试字体/量字体", prev_failed=True)
    assert n == 1, n
    assert counts.get(KIND, 0) == 0, counts


def test_empty_kind_does_nothing():
    """★ 认不出的动作（空类别）不动任何计数 ✓"""
    counts = {KIND: 7}
    assert _bump_kind_count(counts, "", prev_failed=True) == 0
    assert counts[KIND] == 7, counts


def test_loop_wires_the_failure_flag():
    """★★ 循环里必须把「上一次成功/失败」**真接上**（写了不接 = 死代码）✗"""
    src = (BACKEND / "app" / "loop.py").read_text("utf-8", errors="replace")
    assert "_last_output_repeated_failed" in src, "没有这个状态 ⇒ 无从判断 ✗"
    # ① 初始化
    assert "self._last_output_repeated_failed: bool | None = None" in src, "没初始化 ✗"
    # ② 每次工具调用后要写
    assert src.count("self._last_output_repeated_failed = True") >= 1, "失败时没置位 ✗"
    assert src.count("self._last_output_repeated_failed = False") >= 1, "成功时没置位 ✗"
    # ③ 护栏里要读它
    assert "prev_failed=_prev_failed" in src, (
        "护栏没把「上一次成功/失败」传进去 ⇒ 这个修复是死的 ✗"
    )
    assert "_prev_failed = self._last_output_repeated_failed" in src, "没从状态里取 ✗"
