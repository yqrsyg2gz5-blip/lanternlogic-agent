# -*- coding: utf-8 -*-
"""★ 硬门：任务书里给了完整交付清单时，**没做出 mp4 不许 task_done**。

## 现场（2026-10-14 真机，用户 task_20261014_7f1bb07b）

用户按「文章 → 提示词模板」的路子派了一条 20 秒卡点视频任务书，
交付清单里写着 10 个文件（`out/final.mp4`、`data/script.md`、`data/bgm.wav`、
`data/beats.json`、`data/vo_full.json`、`out/douyin/*`、`out/qa/qa-report.md`）。

**模型只做了配音就交了** ✗

    09:01:00  交付完成（已忽略不存在或不在工作区的附件：[…16 个…]）
    09:01:00  任务完成，回到待命
    最终消息：「已完成 5 段配音与词级时间戳生成。」

    工具分布：load_skill 6 · update_plan 4 · shell_exec 23 · file_write 14 · code_check 11 · task_done 1
    23 条命令里 **22 条都在搞配音**（反复调 edge-tts / make_vo.py / test_fast_tts.py）
    计划只写了 5 步、**全围着配音** ⇒ 它把"做配音"当成了整个任务

    工作区实测：`data/bgm.wav` 有（1.7MB）、5 段 vo 有 —— **但 `*.mp4` 一个都没有** ✗

技能里明明写着「第 0–8 步顺序不可改」「第 8 步不可跳过」，它照样在第 2 步就
`task_done` 了 —— 光靠文档里的"必须"拦不住，**要有一步能机器判的硬门**。

## 规矩（本文件钉死）

任务书里出现 `out/final.mp4` 这类**成片交付物**时，技能必须**明确写出**：

  · **只有生成了成片、且第 8 步体检退出码 0，才允许 `task_done`**
  · 反过来也要写明：**只做了配音 / 只做了音乐 ⇒ 不算完成**（列出这些"半成品"清单）
  · 交付附件路径**必须相对工作区根**，且要**先确认文件真的在那**再写进交付
    （这次 16 个附件全被忽略，因为它把 `assets/audio/vo/vo_01.wav`
     写成了 `assets/audio/vo_01.wav` —— 少了一层目录 ✗）
"""
from __future__ import annotations

import pathlib
import re

SKILL = pathlib.Path(__file__).resolve().parents[2] / "backend" / "skills" / "卡点视频"


def _docs() -> str:
    out = []
    for f in sorted(SKILL.rglob("*.md")):
        if "node_modules" in str(f):
            continue
        out.append(f.read_text("utf-8", errors="replace"))
    return "\n".join(out)


def test_docs_forbid_done_without_a_finished_video():
    """★ 必须明确写出：**没出成片不许 task_done**。"""
    txt = _docs()
    assert re.search(r"没.{0,6}(出|生成|做出).{0,10}(成片|mp4|final\.mp4).{0,20}(不许|不得|禁止).{0,10}task_done",
                     txt), (
        "技能里没有「没出成片不许 task_done」这条硬门 ⇒ 模型会在做完配音后直接交付 ✗\n"
        "  实测（2026-10-14）：它 23 条命令全在搞配音、mp4 一个没有，就 task_done 了 ✗"
    )


def test_docs_list_the_half_done_states_as_not_done():
    """★ 要正面列出"哪些只能算半成品" —— 模型分不清"做了一步"和"做完了"。"""
    txt = _docs()
    # 至少点出「只有配音 / 只有音乐」这两种半成品
    assert re.search(r"(只有|仅).{0,8}配音", txt), "没写「只有配音不算完成」✗"
    assert re.search(r"(只有|仅).{0,8}音乐", txt), "没写「只有音乐不算完成」✗"


def test_docs_require_checking_attachment_paths_before_delivering():
    """★ 交付附件要先确认存在，且路径相对工作区根（这次 16 个附件全被忽略 ✗）。"""
    txt = _docs()
    assert re.search(r"(附件|交付).{0,40}(先|要).{0,10}(确认|核对|检查).{0,10}(存在|在)", txt) or \
        re.search(r"(确认|核对).{0,6}(附件|文件).{0,6}(存在|真的在那)", txt), (
        "没写「交付前先确认附件真的存在」⇒ 路径写错也不知道，全被忽略 ✗\n"
        "  实测：它把 assets/audio/vo/vo_01.wav 写成 assets/audio/vo_01.wav，16 个全丢 ✗"
    )


def test_the_iron_rules_section_mentions_the_video_gate():
    """★ 这条要放在开头「铁律」里 —— 放太后面模型读不到（它只读了前几步就动手了）。"""
    src = (SKILL / "SKILL.md").read_text("utf-8", errors="replace")
    i = src.find("## 0.")
    j = src.find("## 1.")
    head = src[i:j] if 0 <= i < j else ""
    assert re.search(r"task_done|成片", head), (
        "开头铁律那段没提「成片 / task_done」⇒ 模型读完前几步就开干、一路做到交付才想起 ✗"
    )
