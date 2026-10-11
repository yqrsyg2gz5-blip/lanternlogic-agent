# -*- coding: utf-8 -*-
"""会议模式：**总结指令**的两条规矩 —— 2026-10-07 用户实测报的 ✗✗。

## 用户遇到的事（看真事件流查出来的 ✓ 不是猜 ✓）

```
任务 task_20261007_927e2791「会议纪要」→ failed ✗
① Agent 老实回答："工作区是空的、没有素材，请把会议内容发给我" ✓（答对了 ✓）
② 系统自己追加："会议结束了。请用 file_read 读取工作区的 meeting_notes.md…" ✗
   —— 而**没人创建过那个文件** ✗（只有**录了音**才会有 ✓）
③ Agent："没有这个文件，我不编造" ✓（又答对了 ✓）
④ 同一句**又发了第 2、3 遍** ✗ → 最后 failed ✗
```

## 两条根因（各有一条测试钉住 ✓）

1. **指令写死假设有 `meeting_notes.md`** ✗ ⇒ 打字开的会必然失败 ✗
2. **同一条总结指令会被重复发** ✗（切页面/卸载也会触发停止会议 ✓）
"""
from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_TV = (_ROOT / "frontend" / "src" / "components" / "TaskView.tsx").read_text("utf-8")


def test_meeting_summary_does_not_assume_a_notes_file():
    """★★ **不许写死"去读 meeting_notes.md"** ✗ —— 那条路只在"录过音"时才成立 ✓。

    用户这次就是**打字开的会**（输入框里写了"会议纪要"四个字 ✓）
    ⇒ 工作区里根本没有那个文件 ✗ ⇒ 指令不认这个事实 ⇒ 反复失败 ✗
    """
    assert "meeting_notes.md" in _TV, "会议那条指令不见了？✗"
    # 必须把"没有文件"这条也写进指令 ✓
    assert "如果没有那个文件" in _TV, "没告诉 Agent「没有那个文件时怎么办」✗"
    assert "当作会议内容" in _TV, "没告诉它可以拿对话本身当会议内容 ✗"
    # 而且必须明说**不许编造** ✓（与项目一贯口径一致 ✓）
    assert "不要编造" in _TV, "没写「不要编造会议内容」✗"


def test_meeting_summary_is_sent_only_once():
    """★ **同一场会议只发一条总结指令** ✓ —— 用户那次发了 3 遍 ✗。"""
    assert "meetingSummarySentRef" in _TV, "没有「只发一次」的闸 ✗（会重复发 ✓）"
    # 闸必须在**发送之前**判断 ✓（放在后面等于没拦 ✗）
    idx_guard = _TV.find("if (meetingSummarySentRef.current) return;")
    idx_send = _TV.find("void api.sendMessage(", idx_guard if idx_guard >= 0 else 0)
    assert idx_guard > 0, "没找到只发一次的判断 ✗"
    assert idx_send > idx_guard, "判断放在发送之后了 ⇒ 拦不住 ✗"
