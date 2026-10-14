# -*- coding: utf-8 -*-
"""★★ 用户两条硬要求（2026-10-14）：

    ① 不许老是重复（同一个动作反复干）
    ② 不许偷懒 —— 必须认真做完，不许拿"被拦住"当台阶下

## 这两条是**一对**，不能只做一半

### ① 已经不重复了，但代价是它"提前收工" ✗

现有护栏熔断时直接 `return`（status=partial 收工）。真机后果：

```
task_20261014_345d630c：熔断后交出成片，但 5 个交付文档（douyin 文案 ×4 + 验收报告）
**没做** ⇒ 状态 partial
★ 它停，不是因为做完了，是因为"被拦了"
⇒ 这**恰恰就是偷懒**：活没干完，拿「被拦」当台阶下 ✗
```

### ② 所以规则必须分开写死

| 情况 | 该怎么处理 |
|---|---|
| 卡在**同一个动作**上 | **打断它**，让它看清单 —— 但**任务继续**，换条路接着干 |
| **交付清单没做完** | **不许收工** —— 无论在哪个环节 |
| 换了动作、真的有进展 | 计数**清零**，让它接着干（别把正在干活的也拦了）✗ |

### ③ 还要防"假进展"

模型很容易"换个几乎一样的命令"来骗过计数（改个空格、加个 echo）。
所以计数要按**语义类**（`_action_kind`）算，不是按命令行文本算 ✓

## 本文件钉什么

1. 熔断**不得**终止任务 —— 它必须给出一条"换条路"的指令后**继续下一轮**
2. 熔断消息里必须**附交付清单核对**（告诉它"这些还没做，接着做"）
3. 交付清单没做完时，**即使用户级熔断也不能收工**
4. 有**真进展**（动作类别变了）⇒ 同类计数清零
5. 最终仍然卡死 ⇒ 如实报 `partial`，但**必须把「为什么没做完」写清楚**（不是默默收工）
"""
from __future__ import annotations

import pathlib
import re
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import app.loop as loop  # noqa: E402

SRC = (BACKEND / "app" / "loop.py").read_text("utf-8")


# ═══ ① 熔断不许终止任务 ═══

def test_kind_trip_does_not_end_the_task():
    """★★ 核心：同类绕圈的熔断 **只打断，不终止** —— 换条路接着干。

    真机教训：熔断即收工 ⇒ 它交出成片就不干了，5 个文档没做 ⇒ 那就是偷懒 ✗
    """
    m = re.search(r"def _kind_trip_message.*?(?=\ndef |\nclass |\n#:|\Z)", SRC, re.S)
    assert m, "找不到 `_kind_trip_message` ✗"
    body = m.group(0)
    assert re.search(r"(继续|接着|别停|不要停|go on|keep)", body), (
        "同类熔断的提示里**没有「接着做」** ⇒ 模型会当成交差信号 ✗"
    )


def test_trip_message_carries_the_deliverable_checklist():
    """★ 熔断消息要**附交付清单** —— 让它知道"还有哪些没做"，而不是"可以下班了"。"""
    fn = getattr(loop, "_kind_trip_message", None)
    assert fn is not None, "没有 `_kind_trip_message` ✗"
    msg = fn("试字体/量字体", 20, None, missing=["out/qa/qa-report.md", "out/douyin/hook.txt"])
    assert "qa-report.md" in msg or "hook.txt" in msg, (
        f"熔断消息没带上「还缺什么」⇒ 它会以为可以收工 ✗\n  {msg[:200]}"
    )
    assert re.search(r"(继续|接着|还剩|没做完|未完成)", msg), msg[:200]


def test_the_warn_message_also_says_keep_going():
    fn = getattr(loop, "_kind_warn_message", None)
    assert fn is not None
    msg = fn("试字体/量字体", 12, missing=["out/final.mp4"])
    assert re.search(r"(继续|接着)", msg), (
        f"提醒里没说「接着做」⇒ 它可能停 ✗\n  {msg[:160]}"
    )


# ═══ ② 计数器要能"清零"（有真进展就不该继续计数）═══

def test_kind_count_resets_when_the_action_kind_changes():
    """★ 换了**另一类**动作 = 真有进展 ⇒ 同类计数清零（别拦正在干活的）。"""
    fn = getattr(loop, "_bump_kind_count", None)
    assert fn is not None, (
        "没有 `_bump_kind_count` ⇒ 计数只增不减 ⇒ 换了动作还在累加 ✗"
    )
    counts: dict[str, int] = {}
    n1 = fn(counts, "试字体/量字体")
    assert n1 == 1, n1
    n2 = fn(counts, "试字体/量字体")
    assert n2 == 2, n2
    # 换一类动作 ⇒ 上一类清零，新类从 1 开始
    n3 = fn(counts, "媒体转码抽帧")
    assert n3 == 1, f"换了动作类别，新类应从头计数，实际 {n3} ✗"
    assert counts.get("试字体/量字体", 0) == 0, (
        f"换了动作后，旧类别的计数没清零（{counts}）⇒ 后面再回来一次就被误熔断 ✗"
    )
    # 空类别：不动任何计数
    n4 = fn(counts, "")
    assert n4 == 0, f"空类别不该计数，实际 {n4} ✗"


# ═══ ③ 交付清单没做完 ⇒ 不许收工 ═══

def test_incomplete_deliverables_block_finishing():
    """★★ 交付清单没做完时，熔断也**不许**把任务停掉（只打断这一轮）。"""
    fn = getattr(loop, "_deliverables_incomplete", None)
    assert fn is not None, (
        "没有 `_deliverables_incomplete` ⇒ 无法判断「活干完没有」✗\n"
        "  真机：熔断时它只出了成片，4 个文案 + 验收报告都没做就收了 ✗"
    )
    assert fn(["out/final.mp4"]) is True, "清单里有文件没做，应判 True ✗"
    assert fn([]) is False, "空清单应判 False ✗"


# ═══ ④ 最终停不下来时，要**如实说清为什么** ═══

def test_final_partial_says_why():
    """★ 真到了必须停的时候（同类熔断反复触发），要**如实报告卡在哪**，不是默默收工。"""
    fn = getattr(loop, "_kind_giveup_message", None)
    assert fn is not None, "没有 `_kind_giveup_message` ⇒ 停的时候说不清原因 ✗"
    msg = fn("试字体/量字体", 40, ["out/qa/qa-report.md"])
    assert "试字体/量字体" in msg and "qa-report.md" in msg, msg[:220]
    assert re.search(r"(没做完|未完成|没解决|卡)", msg), (
        f"收工时没说清「为什么没做完」 ⇒ 用户不知道为什么 ✗\n  {msg[:200]}"
    )


# ═══ ⑤ 「没动手」也要拦 —— 光研究不写文件，不许交付 ═══

def test_no_work_guard_exists_and_is_wired():
    """★★ 真机事故（2026-10-14）：两条任务 **183 步 / 720 步，写文件都是 0** ✗

    对照**出过片**的任务：写文件 15 / 20 / 21 / 25 / 26 / 47 次 ✓
    ⇒ 「写文件次数」是提前看出成败的唯一硬信号 —— 所以要在交付时拦一道 ✓
    """
    src = (BACKEND / "app" / "loop.py").read_text("utf-8")
    assert "def _no_work_done" in src, "没有 _no_work_done ⇒ 光研究不动手也能交付 ✗"
    assert "self._artifact_writes: int = 0" in src, "没有产物写入计数 ✗"
    assert "self._artifact_writes += 1" in src, "file_write 没接计数 ⇒ 数不出来 ✗"
    assert "交付被拦（没动手门）" in src, "交付时没这道拦截 ✗"


def test_no_work_message_points_at_the_starter_template():
    """★ 拦截消息必须**给出直接可用的替代动作**（起手模板），不是只说别读了。"""
    src = (BACKEND / "app" / "loop.py").read_text("utf-8")
    i = src.find("交付被拦（没动手门）")
    seg = src[i:i + 700]
    assert "starter.html" in seg, (
        "拦截消息里没提 starter.html ⇒ 模型不知道该干什么，只能继续读源码 ✗"
    )
    assert "cp " in seg, "没给出 cp 的具体命令 ⇒ 它还得自己想办法 ✗"


def test_no_work_guard_gives_up_after_two_blocks():
    """★ 别把交付彻底堵死：拦 2 次后放行（那种情况交付时降级 partial 就行）。"""
    src = (BACKEND / "app" / "loop.py").read_text("utf-8")
    assert "_no_work_blocks < 2" in src, (
        "没有拦几次就放行的上限 ⇒ 会把交付彻底堵死（那是另一种失败）✗"
    )
