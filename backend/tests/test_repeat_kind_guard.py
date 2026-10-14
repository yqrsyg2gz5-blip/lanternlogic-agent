# -*- coding: utf-8 -*-
"""★★ 「换参数重试」型的绕圈也要熔断 —— 真机一次烧掉 60% 的步数。

## 现场（2026-10-14 用户真机 task_20261014_345d630c）

一条 20 秒片：**73 分钟 / 368 步 / 360 步上限熔断 / 仍未做完**。
按动作类型归并步数：

```
   88 次  ★ 试字体 / 量文字宽度      ← 全是"换个字体名/换个字号"再试
   76 次  画一帧看效果
   42 次  读图看效果
   52 次  ffmpeg 转码/抽帧
   ≈ 216 步 ≈ **60% 的步数**，全耗在"某个字符显示不出来"上 ✗
```

## 为什么现有护栏没拦住

护栏的判据是 **`_action_signature`（工具名 + 归一化参数）完全一致**，
外加"输出指纹完全一致"：

```
   第 1 次  ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 48)
   第 2 次  ImageFont.truetype('C:/Windows/Fonts/arialbd.ttf', 48)   ← 指纹不同 ✗
   第 3 次  ImageFont.truetype('C:/Windows/Fonts/simhei.ttf', 48)   ← 指纹又不同 ✗
   … 88 次，每一次"命令都不完全一样" ⇒ **护栏认为这是 88 个不同动作** ✗
```

**⇒ 这是"语义相同的重试"：手法一样（试字体）、目的没变（让某个字符显示出来），
只是参数在换。** 现存护栏对这种形态**完全无效**。

## 本文件钉什么

1. **动作要能归类**（`_action_kind`）：把"试字体"这类动作识别成同一个"类别"
2. **同类动作超过阈值 ⇒ 熔断**，并且提示词要**告诉它换个方向**（不是"别重复"，
   而是"这类试法你已经试了 N 次，换个办法或报告"）
3. **不能误伤云端**：用户明确要求"别的 API 不许降级" ⇒
   分类熔断的阈值要**明显宽于**精确重复的阈值（精确重复 5/8，同类 12/20）
4. **判据要窄**：只有**明确可归类**的动作才计入（认不出的类别 ⇒ 不参与，
   fail-open，宁可不拦也不误伤）
"""
from __future__ import annotations

import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import app.loop as loop  # noqa: E402


# ═══ ① 动作分类：同类动作要归到一起 ═══

def test_font_probing_actions_share_one_kind():
    """★ 核心：88 次"试字体"必须归成**同一类**，否则永远拦不住。"""
    kind = getattr(loop, "_action_kind", None)
    assert kind is not None, (
        "loop.py 里没有 `_action_kind` ⇒ 无法把'换参数重试'归成一类 ⇒ "
        "88 次试字体永远拦不住 ✗"
    )
    probes = [
        {"command": "python -c \"from PIL import ImageFont; "
                    "ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc', 48)\""},
        {"command": "python -c \"from PIL import ImageFont; "
                    "ImageFont.truetype('C:/Windows/Fonts/arialbd.ttf', 48)\""},
        {"command": "python -c \"from PIL import ImageFont; "
                    "ImageFont.truetype('C:/Windows/Fonts/simhei.ttf', 64)\""},
    ]
    kinds = {kind("shell_exec", a) for a in probes}
    assert len(kinds) == 1, (
        f"三次试字体归成了 {len(kinds)} 个类别（{kinds}）⇒ 熔断器数不过来 ✗"
    )
    assert kinds != {""}, "试字体没被归到任何类别 ✗"


def test_different_kinds_are_not_lumped_together():
    """★ 别乱归：配音、转码、画帧是**不同**的类别（否则会误伤正常流程）。"""
    kind = getattr(loop, "_action_kind", None)
    if kind is None:
        return
    a = kind("shell_exec", {"command": "python tools/make_vo.py --out-dir x"})
    b = kind("shell_exec", {"command": "ffmpeg -i a.mp4 -vf scale=1080:1920 b.mp4"})
    c = kind("shell_exec", {"command": "python - <<'PY'\nfrom PIL import ImageDraw\nd=ImageDraw.Draw(im)\nPY"})
    distinct = {x for x in (a, b, c) if x}
    assert len(distinct) >= 2, (
        f"配音/转码/画帧被归成了同一类（{a!r}/{b!r}/{c!r}）⇒ 正常流程会被误熔断 ✗"
    )


def test_unrecognized_actions_get_no_kind():
    """★ 窄判据：认不出的动作 ⇒ 类别为空串（不参与同类计数，fail-open）。"""
    kind = getattr(loop, "_action_kind", None)
    if kind is None:
        return
    assert kind("shell_exec", {"command": "ls -la"}) == "", (
        "普通命令被归了类 ⇒ 会拿正常操作去凑同类计数 ✗"
    )
    assert kind("update_plan", {"steps": []}) == "", "非 shell 工具不该有 shell 类别 ✗"


def test_reading_a_tool_source_is_not_counted_as_using_it():
    """★★ 真机事故（2026-10-14 第二条）：**读源码被误判成「在用那个工具」**。

    现场：模型为了搞懂渲染器怎么用，反复跑

        sed -n '130,200p' tools/render/render.mjs      ← 这是**读文件**

    旧正则 `render\\.mjs` 会匹配到**任何提到这个文件名的命令** ⇒ 归成「渲染帧」✗
    读了 21 次就被同类熔断 ⇒ **任务停在 partial、连成片都没出** ✗

    ⇒ 侦察类命令（sed/cat/grep/head/ls/find…）**一律不参与同类计数** ——
      那是**学习**，不是绕圈；技能手册本身就在教它先读工具用法 ✓
    """
    kind = getattr(loop, "_action_kind", None)
    assert kind is not None
    recon = [
        "sed -n '130,200p' tools/render/render.mjs",
        "cd /w && sed -n '1,55p' tools/render/render.mjs",
        "grep -n caption tools/qa_video.py",
        "cat skills/SKILL.md",
        "head -40 tools/render/render.mjs",
        "ls -la tools/",
        "find . -maxdepth 3 -name '*.mjs'",
    ]
    for cmd in recon:
        assert kind("shell_exec", {"command": cmd}) == "", (
            "侦察命令被归了类 ⇒ 读源码会被算成「在用那个工具」⇒ 读几次就误熔断 ✗\n"
            f"  {cmd}"
        )


def test_actually_running_the_renderer_still_counts():
    """★ 别修过头：**真的在调渲染器**必须照样归类（否则护栏形同虚设）。"""
    kind = getattr(loop, "_action_kind", None)
    assert kind is not None
    assert kind("shell_exec", {"command": "node tools/render/render.mjs index.html out.mp4"}) \
        == "渲染帧", "真渲染没被归类 ⇒ 同类护栏对渲染失效 ✗"
    assert kind("shell_exec", {"command": "python tools/render_frames.py --all"}) == "渲染帧"



# ═══ ② 阈值：同类阈值必须**明显宽于**精确重复（不误伤云端）═

def test_kind_thresholds_are_tighter_than_baseline_but_looser_than_exact():
    """★ 同类绕圈的阈值位置（三档关系要说清）：

    ```
    本地精确重复   5 /  8   ← 最紧（本地小模型绕圈纯浪费）
    同类绕圈      12 / 20   ← ★ 本档：比大预算下的基线紧，但比本地档宽
    云端精确重复  37 / 60   ← 最宽（真机预算 300 时；给"合理重试"留空间）
    ```

    **为什么同类要**比云端基线紧：它测的是"**方法层面卡死**"——
    换参数试同一件事，那不是"合理重试网络"，是真卡住了（真机 88 次）✗

    **为什么要**比本地档宽：用户在群里明确要求"别的 API 不许降级"，
    所以不能把云端收紧到本地那个量级 ✓
    """
    fn = getattr(loop, "_kind_thresholds", None)
    assert fn is not None, "没有 `_kind_thresholds` ⇒ 同类熔断没有独立阈值 ✗"
    kw, kt = fn(300)
    bw, bt = loop._base_thresholds(300)          # 大预算下的基线（很宽）
    lw, lt = loop._LOCAL_WARN_CAP, loop._LOCAL_TRIP_CAP

    assert (kw, kt) < (bw, bt), (
        f"同类阈值 {kw}/{kt} 不比大预算基线 {bw}/{bt} 紧 ⇒ 拦不住（真机 88 次才停）✗"
    )
    assert kw > lw and kt > lt, (
        f"同类阈值 {kw}/{kt} 不比本地档 {lw}/{lt} 宽 ⇒ 云端被降级，违背用户要求 ✗"
    )


def test_kind_thresholds_do_not_scale_with_a_huge_budget():
    """★ 真机教训：预算 360 步时基线阈值跟着涨 ⇒ 护栏等于没有。同类阈值必须封顶。"""
    fn = getattr(loop, "_kind_thresholds", None)
    if fn is None:
        return
    a = fn(120)
    b = fn(1000)
    assert b[1] == a[1], (
        f"预算从 120 涨到 1000，同类熔断阈值从 {a[1]} 涨到 {b[1]} ⇒ "
        "预算越大越拦不住（真机就是这样烧到 360 步的）✗"
    )


def test_kind_guard_is_actually_wired_into_the_loop():
    """★ 接线锚点：这些函数必须**真被循环调用**，否则是死代码（写了等于没写）。

    ★ 用**字面量**查（`'code": "kind_repetition"'`）而不是变量 ——
      本班踩过：把 code 提成变量后，`test_repeat_guard.py` 的接线锚点当场断掉，
      而功能其实没坏 ⇒ 锚点必须跟着实际写法走 ✓
    """
    src = (pathlib.Path(__file__).resolve().parents[1] / "app" / "loop.py").read_text("utf-8")
    assert "_action_kind(name, args)" in src, "循环里没调 `_action_kind` ⇒ 同类检测是死代码 ✗"
    assert "self._kind_counts" in src, "没有同类计数字典 ⇒ 数不过来 ✗"
    # ★ 2026-10-14 改：同类熔断**不再直接收工**（改成「打断后继续」+ 反复熔断才停），
    #   所以锚点跟着实际写法走：既要打断路径，也要有最终停下那档 ✓
    assert 'code": "kind_repetition"' in src or '_kind_giveup_message(' in src, (
        "同类熔断既没打断路径、也没有最终停下那档 ✗"
    )
    assert '_kind_trip_message(_kind_now' in src, "同类熔断提示没接上 ✗"
    assert '_bump_kind_count(' in src, "同类计数没接上（换类别要清零）✗"
    assert "_kind_thresholds(self.max_iterations)" in src, "同类阈值没接上 ✗"
    assert "_kind_trip_message(_kind_now" in src, "同类熔断提示没接上 ✗"


# ═══ ③ 提示词：要**指方向**，不是只说"别重复" ═══

def test_kind_trip_message_tells_it_to_change_approach():
    """★ 熔断提示必须告诉它**换个方向**（真机里"重复提醒"它收到了 6 次还在继续）。"""
    fn = getattr(loop, "_kind_trip_message", None)
    assert fn is not None, "没有 `_kind_trip_message` ✗"
    msg = fn("试字体", 14, None)
    assert "试字体" in msg, msg
    assert "14" in msg or "十四" in msg, f"没报出次数 ⇒ 模型不知道严重程度：{msg[:80]}"
    # 要给出"换方向"的具体建议
    assert any(k in msg for k in ("换", "改", "试别的", "换个办法", "别再用")), (
        f"熔断提示没给出「换个方向」的指引 ⇒ 模型醒了还会接着试 ✗\n  {msg[:160]}"
    )
