# -*- coding: utf-8 -*-
"""交付收尾时**必须**发出"打开方式"事件 —— 用今天的真目录当考卷 ✓

为什么单独测这件事：
  · 提示算得对（entry_hint）✓ 已经测过了 ✓ 但**发不出来等于没做** ✗
  · 所以这里测的是**接线**：任务收尾时那条 knowledge 事件得真的出现 ✓

同时测两条"不许"：
  · 目录里啥都没有 ⇒ **不许抛** ✗（收尾路径抛异常 = 把整个任务带崩 ✓）
  · 提示是锦上添花 ⇒ 出问题**不许影响交付** ✓
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.loop import TaskRun


class _FakeStore:
    def __init__(self, ws):
        self._ws = ws

    def workspace_dir(self, task_id):
        return self._ws

    def snapshots_dir(self, task_id):
        import tempfile
        import pathlib
        return pathlib.Path(tempfile.mkdtemp(prefix="snaps-"))


def _mk_loop(ws):
    """造一个最小 loop：只挂上 _snapshot_workspace 需要的东西 ✓"""
    loop = TaskRun.__new__(TaskRun)          # 不走 __init__ ✓（它副作用太多）
    loop.task = SimpleNamespace(id="t1", status="done")
    loop.store = _FakeStore(ws)
    loop.takeover = False
    events: list[tuple[str, dict]] = []
    loop.emit = lambda kind, payload=None, **kw: events.append((kind, payload or {}))
    return loop, events


def test_entry_hint_event_is_emitted(tmp_path):
    """★ 核心：目录里有 index.html + 一堆测试文件 ⇒ 收尾必须发出"打开方式"✓"""
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    for n in ("_test.html", "_clicktest_dom.html", "_probetest.html"):
        (tmp_path / n).write_text("<html></html>", encoding="utf-8")
    loop, events = _mk_loop(tmp_path)

    # ★ 2026-10-10：它改成 async 了 ✓（里面的"交付体检"要 await asyncio.to_thread ✓）
    #   —— 同步调它只会拿到一个**从没跑过**的协程 ✗（事件当然一条都没有 ✓）
    asyncio.run(loop._emit_entry_hint())

    hints = [p for k, p in events if k == "knowledge" and "打开方式" in str(p.get("title", ""))]
    assert hints, f"没发出'打开方式'事件 ✗（收到的事件：{[p.get('title') for _, p in events]}）"
    body = hints[0]["content"]
    assert "index.html" in body, body
    assert "沙箱" in body, "必须说明'预览点不动是正常的'✓ —— 否则用户又会以为东西坏了 ✗"


def test_no_html_no_hint(tmp_path):
    """目录里没有网页 ⇒ 不发这条 ✓（别制造噪音 ✗）"""
    (tmp_path / "notes.txt").write_text("hi", encoding="utf-8")
    loop, events = _mk_loop(tmp_path)
    asyncio.run(loop._emit_entry_hint())        # ★ 同上：必须 await 才真跑到 ✓
    assert not [p for k, p in events if "打开方式" in str(p.get("title", ""))]


def test_missing_workspace_does_not_raise(tmp_path):
    """★ 工作区不存在 ⇒ **不许抛** ✗（收尾路径一抛就把交付带崩 ✓）"""
    loop, events = _mk_loop(tmp_path / "nope")
    asyncio.run(loop._emit_entry_hint())        # 不抛即通过 ✓（★ 同上：必须真跑到 ✓）
