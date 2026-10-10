# -*- coding: utf-8 -*-
"""跨任务审计流水（★ 2026-10-07 第 9 项）。

## 为什么要有（现状）

现在每个任务自己的事件流里都记得挺全 ✓（`data/tasks/<id>/events.jsonl` ✓）——
但那是**按任务分的** ✗。于是用户想问的那几个问题，**一个都答不上来** ✓：
  · "这一个月里，Agent 一共让我批过哪些命令？我批了些什么？"
  · "那几条「以后都别问」我是**什么时候**、**为什么**放开的？"
  · "昨天它为什么突然停下来了？"
  ⇒ 只能一个个任务点进去翻 ✗（202 个任务 ✓ 谁能翻得动 ✓）

## 记什么（**只记"需要有人负责"的那些** ✓ 不是什么都记 ✗）

★ **以代码里真有的写入点为准** ✓（下表的 kind 就是 `audit.record()` 实际会写的那几种 ✓
  —— 曾经这里还列过 `forever` / `task` 两种 ✗ 而**根本没有代码去写它们** ✓
  界面上照着列出来 ⇒ 选了永远是空的 ✓ 那是"界面骗人"✓ 已改掉 ✓
  ★ 2026-10-07 A-4 起，`task` **真有人写了** ✓（起：`main._launch_task` ✓
    止：`loop.TaskRun._run` 的 `finally` ✓ 一条一个 `phase` ✓）⇒ 已如实列回下表 ✓）：

| 类别 | 说明 | 谁写 |
|---|---|---|
| `approval` | 审批决议（once / always / forever / deny ✓）+ 那条命令 ✓ —— **最该留痕的** ✓ | `_do_approve` ✓ |
| `blocked` | 被闸门挡下（单次花费上限 / 每个 Key 上限 ✓）—— "为什么停了" ✓ | `_budget_block_reason` ✓ |
| `task` | **任务起止** ✓（`phase=start` / `phase=done` ✓ 止那条带**当场那个真状态** ✓） | `_launch_task` / `TaskRun._run` ✓ |
| `cleared` | **清空数据** ✓（带**备份路径** ✓ 所以它同时是"找回东西的线索"✓） | 清空端点 ✓ |
| `deleted_task` | **删任务**（挪进回收站 ✓ 带 `where` ⇒ 找得回来 ✓） | 删任务端点 ✓ |
| `trash_pruned` | 回收站**自动瘦身** ✓（真删掉了旧备份 ⇒ 同样得留痕 ✓） | `_prune_trash` ✓ |

★「这类以后都别问」的**放权**确实记了 ✓ —— 它记在 `approval` 里、带 `decision=forever` ✓
  （不另立一种 kind ✗：一次审批就是一次审批 ✓ 分开记反而要两处对齐 ✓）

**不记**：对话内容 ✗、工具输出 ✗、文件内容 ✗ ——
那是任务事件流的活 ✓ 总账只回答"谁在什么时候放了什么权、挡了什么"✓

## 三条设计

1. **JSONL 追加写** ✓（一行一条 ✓ 追加不重写 ✓ 崩了也只丢最后一行 ✓）
2. **有上限** ✓（默认留最近 2000 条 ✓ 超了从头截 ✓ —— 不设上限就是个磁盘炸弹 ✗）
3. **打码** ✓（命令里可能有密钥 ✓ 过 `redact_text` ✓ 与全项目同一套 ✓）
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .redact import redact_text

#: 最多留多少条（超过就从**头部**截掉最老的 ✓）
MAX_ENTRIES = 2000
#: 截断时保留的比例（避免"每加一条就重写整个文件"✗）
_KEEP_RATIO = 0.8

_LOCK = threading.Lock()
_PATH: Path | None = None


def bind(data_dir: Path) -> None:
    """告诉这本账该写哪儿 ✓（由 main 在启动时调一次 ✓ 与其它存储同源 ✓）。"""
    global _PATH
    _PATH = Path(data_dir) / "audit.jsonl"


def _path() -> Path | None:
    return _PATH


def record(kind: str, **fields: Any) -> None:
    """记一条 ✓ —— **永不抛** ✗（记账失败绝不能影响用户正在干的事 ✓）。"""
    p = _path()
    if p is None:
        return
    try:
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": str(kind)[:24]}
        for k, v in fields.items():
            if v is None:
                continue
            # 命令/文本类一律**打码** ✓（可能带密钥 ✓ 与全项目同一套实现 ✓）
            row[str(k)[:24]] = redact_text(str(v))[:400] if isinstance(v, str) else v
        line = json.dumps(row, ensure_ascii=False)
        with _LOCK:
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            _trim_if_needed(p)
    except Exception:                                       # noqa: BLE001
        pass


def _trim_if_needed(p: Path) -> None:
    """超上限就从头截 ✓（在锁里调 ✓）。"""
    try:
        if not p.exists():
            return
        lines = p.read_text("utf-8", errors="replace").splitlines()
        if len(lines) <= MAX_ENTRIES:
            return
        keep = lines[int(len(lines) * (1 - _KEEP_RATIO)) :]
        p.write_text("\n".join(keep) + "\n", "utf-8")
    except Exception:                                       # noqa: BLE001
        pass


def tail(limit: int = 200, kind: str = "") -> list[dict[str, Any]]:
    """读最近 N 条 ✓（可按类别过滤 ✓）。**倒序**（最新在前 ✓ 用户想看的就是最近的 ✓）。"""
    p = _path()
    if p is None or not p.exists():
        return []
    try:
        lines = p.read_text("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for ln in reversed(lines):
        ln = ln.strip()
        if not ln:
            continue
        try:
            row = json.loads(ln)
        except json.JSONDecodeError:
            continue                                            # 坏行跳过 ✓ 不因为一行坏了就整本读不出 ✓
        if kind and str(row.get("kind")) != kind:
            continue
        out.append(row)
        if len(out) >= max(1, min(1000, limit)):
            break
    return out


def stats() -> dict[str, Any]:
    """这本账现在什么样（给界面显示 ✓）。"""
    p = _path()
    rows = tail(1000)
    by: dict[str, int] = {}
    for r in rows:
        by[str(r.get("kind"))] = by.get(str(r.get("kind")), 0) + 1
    return {
        "path": str(p) if p else "",
        "count": len(rows),
        "by_kind": by,
        "max_entries": MAX_ENTRIES,
    }
