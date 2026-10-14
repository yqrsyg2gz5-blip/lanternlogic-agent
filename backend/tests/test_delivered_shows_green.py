# -*- coding: utf-8 -*-
"""★★ 交付物齐了就该显示"已完成"（绿），不管进程是怎么结束的。

## 用户原话

> 「做好了就得交互，就得显示绿点。你他妈没做好，**或者是做好你就做好了，你还显示红点，
>   那是不是就表示没交付啊？产物里什么东西也没有**」

## 现场（2026-10-14，用户截图为证）

```
task_20261014_332781e8
  交付物 10/10 齐全 ✓   成片 out/final.mp4 4.7 MB ✓   体检退出码 0 ✓
  界面上却显示 **红点**（status=failed）✗
  原因：我重启应用把它掐了 ⇒ status 写成 failed ⇒ 界面按 status 上色 ⇒ 红
```

**⇒ 红点 = "没交付"的意思，但它明明交付了** ⇒ 用户被误导，以为成功率很低 ✗

## 口径（本文件钉的）

```
显示"已完成（绿）" ⟺ **交付物齐全**（或成片在）
显示"未完成（红）" ⟺ 交付物不齐 **且** 进程也失败/中断了
```

⇒ 后端要在任务摘要里给出「交付物齐不齐」这个事实 ✓
   （前端不许自己猜、不许只看 status ✗）
"""
from __future__ import annotations

import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def test_backend_exposes_a_delivered_flag():
    """★ 后端必须给出「交付物齐不齐」这个事实 —— 前端不许自己猜 ✗"""
    import app.main as m
    # 找任务摘要的构造处（GET /api/v1/tasks 用的形状）
    src = (BACKEND / "app" / "main.py").read_text("utf-8", errors="replace")
    assert "delivered" in src, (
        "后端任务摘要里没有 `delivered` 字段 ⇒ 前端只能看 status ⇒ "
        "被我掐断但交付物齐全的任务会显示红点（用户就是这么被误导的）✗"
    )
    assert hasattr(m, "task_delivery_state") or "_task_delivered" in src, (
        "没有「算交付物齐不齐」的函数 ⇒ delivered 无处而来 ✗"
    )


def test_delivery_state_counts_real_files(tmp_path):
    """★ 核心判据：**看文件在不在**，不看进程怎么结束 ✓"""
    from app.main import task_delivery_state
    ws = tmp_path / "workspace"
    (ws / "out" / "douyin").mkdir(parents=True)
    (ws / "data").mkdir(parents=True)
    # 一个交付物都没有 ⇒ 没交付
    assert task_delivery_state(ws) == "none", task_delivery_state(ws)
    # 有成片 ⇒ 交付了
    (ws / "out" / "final.mp4").write_bytes(b"x" * 100)
    st = task_delivery_state(ws)
    assert st in ("delivered", "partial"), st
    # 补齐清单 ⇒ 齐全
    for rel in ("data/script.md", "data/bgm.wav", "data/beats.json", "data/vo_full.json",
                "out/douyin/hook.txt", "out/douyin/title.txt", "out/douyin/desc.txt",
                "out/douyin/cover.txt", "out/qa/qa-report.md"):
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    assert task_delivery_state(ws) == "delivered", task_delivery_state(ws)


def test_state_survives_a_killed_process(tmp_path):
    """★★ 本事故的核心：**进程被掐了，但交付物齐全 ⇒ 仍然算已交付** ✓

    （真机：332781e8 十项齐全 + 成片 4.7 MB，却因为我的重启显示红点 ✗）
    """
    from app.main import task_delivery_state
    ws = tmp_path / "workspace"
    for rel in ("out/final.mp4", "data/script.md", "data/bgm.wav", "data/beats.json",
                "data/vo_full.json", "out/douyin/hook.txt", "out/douyin/title.txt",
                "out/douyin/desc.txt", "out/douyin/cover.txt", "out/qa/qa-report.md"):
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    assert task_delivery_state(ws) == "delivered", (
        "交付物齐全却判成未交付 ⇒ 界面还是红点 ⇒ 用户以为失败了 ✗"
    )


def test_frontend_colours_by_delivery_not_only_status():
    """★ 前端上色要**先看交付**，再看进程状态 ✓"""
    fe = (BACKEND.parent / "frontend" / "src" / "components" / "Sidebar.tsx").read_text(
        "utf-8", errors="replace")
    assert "delivered" in fe, (
        "Sidebar 里没用到 `delivered` ⇒ 还是只看 status ⇒ 交付齐了也显示红点 ✗"
    )
    # 绿点必须能被 delivered 触发（不许只由 status==='done' 触发）
    i = fe.find("STATUS_DOT")
    assert i > 0
    tail = fe[i:]
    assert "delivered" in tail, (
        "上色函数没有把 `delivered` 算进去 ⇒ 修了后端也白修 ✗"
    )
