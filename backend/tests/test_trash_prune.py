# -*- coding: utf-8 -*-
"""★ 回收站（`data\\_deleted_`）的**清理策略** —— 2026-10-07。

## 为什么必须有（这是我自己新加的功能带出来的新债 ✗）

侧栏那个叉现在**不真删**、挪到 `data\\_deleted_<时间戳>\\` ✓ —— 这是对的 ✓
但**不设上限**的话，删久了它自己就变成一个新垃圾堆 ✗
—— 跟当年那 **213 个"界面上已删、硬盘还在"的目录共 470.5 MB** ✗ **同一个病** ✓
**本项目栽过一次的事，不该因为"这次是挪不是删"就再栽第二次** ✓

## 三条纪律

1. 留**最近 N 次** ✓（按目录名 = 时间戳排序 ✓ 清最旧的 ✓）
2. **清理也记账** ✓（`trash_pruned` ✓ —— 真删掉东西必须留痕 ✓ 与"清空数据/删任务"同类 ✓）
3. 全程**不抛** ✗（清理失败绝不影响用户正在做的删除 ✓）
"""
from __future__ import annotations

import pathlib

import pytest

from app import audit, main as m


@pytest.fixture(autouse=True)
def _ledger(tmp_path):
    audit.bind(tmp_path / "audit")
    yield
    audit.bind(pathlib.Path(m._DATA_DIR))


def _make_trash(tmp_path, n: int) -> pathlib.Path:
    """造 n 个时间戳目录 ✓ 每个里放点文件 ✓（模拟删过 n 次任务 ✓）。"""
    root = tmp_path / "data" / "_deleted_"
    for i in range(n):
        d = root / f"_deleted_2026100{i}-1200" / f"task_{i}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "events.jsonl").write_text("x" * (100 * (i + 1)), "utf-8")
    return root


def test_it_keeps_only_the_newest(tmp_path, monkeypatch):
    """★★ **只留最近 N 次** ✓ —— 回滚实验：把 `[keep:]` 去掉 ⇒ 本组必红 ✓"""
    monkeypatch.setattr(m, "_DATA_DIR", tmp_path / "data")
    root = _make_trash(tmp_path, 6)
    n = m._prune_trash(keep=3)
    assert n == 3, f"应该清掉 3 个（6 个留 3 个），实际 {n} ✗"
    left = sorted(d.name for d in root.iterdir() if d.is_dir())
    assert left == ["_deleted_20261003-1200", "_deleted_20261004-1200", "_deleted_20261005-1200"], \
        f"清错了一边 ⇒ 把**新的**清掉、留着旧的 ✗：{left}"


def test_pruning_is_recorded(tmp_path, monkeypatch):
    """★ **清理也要留痕** ✓ —— 真删掉东西必须记 ✓（与"清空数据/删任务"同类 ✓）。"""
    monkeypatch.setattr(m, "_DATA_DIR", tmp_path / "data")
    _make_trash(tmp_path, 5)
    m._prune_trash(keep=2)
    rows = [r for r in audit.tail(20) if r.get("kind") == "trash_pruned"]
    assert len(rows) == 3, f"清了 3 个却记了 {len(rows)} 条 ✗"
    assert rows[0].get("bytes"), "没记清掉了多少字节 ⇒ 用户不知道回收站占多大 ✗"
    assert "_deleted_" in str(rows[0].get("where")), "没记清掉的是哪个 ✗"


def test_under_the_limit_does_nothing(tmp_path, monkeypatch):
    """★ 没超上限 ⇒ **一个都不许动** ✗（别把用户还能找回的东西提前清了 ✓）。"""
    monkeypatch.setattr(m, "_DATA_DIR", tmp_path / "data")
    root = _make_trash(tmp_path, 3)
    assert m._prune_trash(keep=20) == 0
    assert len([d for d in root.iterdir() if d.is_dir()]) == 3


def test_missing_trash_is_fine(tmp_path, monkeypatch):
    """★ 回收站还不存在 ⇒ 返回 0 ✓（第一次删任务之前就是这样 ✓ 不许报错 ✗）。"""
    monkeypatch.setattr(m, "_DATA_DIR", tmp_path / "还没有")
    assert m._prune_trash(keep=20) == 0


def test_prune_never_raises(tmp_path, monkeypatch):
    """★★ **清理失败绝不影响用户正在做的删除** ✓ —— 盘满了/文件被占用也得照常删 ✓。"""
    monkeypatch.setattr(m, "_DATA_DIR", tmp_path / "data")
    _make_trash(tmp_path, 4)
    import shutil as _sh
    monkeypatch.setattr(_sh, "rmtree", lambda *a, **k: (_ for _ in ()).throw(OSError("被占用了")))
    assert m._prune_trash(keep=1) == 0        # ← 不许抛 ✗


def test_the_delete_endpoint_prunes():
    """★ 端点得**真接上** ✓（"写了不等于接上了"✓ 本仓老教训 ✓）。"""
    src = pathlib.Path(m.__file__).read_text("utf-8")
    i = src.find("async def delete_task")
    seg = src[i : i + 2600]
    assert "_prune_trash(" in seg, "删任务后没顺手收拾回收站 ⇒ 它自己会变成新垃圾堆 ✗"
    assert "trash_pruned" in seg, "清掉几个没回报给用户 ✗（他该知道回收站自动在瘦身 ✓）"
