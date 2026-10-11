# -*- coding: utf-8 -*-
"""★ 2026-10-06 文件夹变动触发（`kind="watch"`）。

## 为什么加它

Manus 2.0 的 Automations 主打"**事件触发**"✓ 而我们此前只有定时 ✓。
而用户真正每天会用的那件事（"**下载目录来新文件就整理**"✓）本质是**文件变动** ✓ ——
既不需要邮箱配置 ✗ 也不需要外部服务 ✗，本地看一眼就够了 ✓。

## 行为约定（这几条都是"别让用户莫名其妙" ✓）

· **建的时候必须路径存在** ✓ —— 存一个不存在的目录 = 一个永远不会触发的自动化 ✓
  而用户会以为它在盯 ✗（比报错更糟 ✓）
· **第一次扫描不算变化** ✓ —— 否则一建好就立刻跑一次 ✗
· **防抖** ✓：两次触发至少隔 `watch_seconds`（默认 60 秒 ✓）——
  否则复制 100 个文件会开出 100 个任务 ✗
· **只看一层** ✓（不递归 ✗）：递归扫大目录会拖慢每一轮 ✓ 而"下载目录"这类用法一层就够 ✓
· 快照最多记 300 个文件 ✓（防内存无限长 ✓）
"""
from __future__ import annotations

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import main as m  # noqa: E402


def _auto(tmp_path, **kw) -> dict:
    a = {"id": "auto_x", "name": "看下载目录", "kind": "watch",
         "watch_path": str(tmp_path), "watch_seconds": 60, "watch_seen": None, "enabled": True}
    a.update(kw)
    return a


def test_first_scan_is_not_a_change(tmp_path):
    """**建好那一刻不算变化** ✓ —— 否则自动化一建就立刻跑一次 ✗（用户会莫名其妙 ✓）。"""
    (tmp_path / "a.txt").write_text("已有的文件", encoding="utf-8")
    a = _auto(tmp_path)
    assert m._watch_changed(a) == [], "第一次扫描不该算变化 ✗"
    assert "a.txt" in a["watch_seen"], "该把已有的文件记进快照 ✓"


def test_new_file_is_a_change(tmp_path):
    """新来个文件 ⇒ 算变化 ✓（这就是最常用的那条：下载完就整理 ✓）。

    ★ 而且**盯一个空目录**也要能触发 ✓ —— 本班第一版拿"快照非空"当"扫过了" ✗
      ⇒ 空目录里放进第一个文件时被判成"第一次扫描"⇒ **不触发** ✓
      （正好把最常用的用法废掉 ✗ 这条测试就是钉它的 ✓）。
    """
    a = _auto(tmp_path)                                   # 目录是**空的** ✓
    m._watch_changed(a)                                   # 建基线快照
    (tmp_path / "new.txt").write_text("刚下载的", encoding="utf-8")
    got = m._watch_changed(a)
    assert got == ["new.txt"], got


def test_modified_file_is_a_change(tmp_path):
    """同一个文件**内容变了**（mtime/size 变）也算 ✓。"""
    f = tmp_path / "a.txt"
    f.write_text("v1", encoding="utf-8")
    a = _auto(tmp_path)
    m._watch_changed(a)
    time.sleep(0.01)
    f.write_text("v2-更长了", encoding="utf-8")
    assert m._watch_changed(a) == ["a.txt"]


def test_unchanged_files_do_not_retrigger(tmp_path):
    """**没变就不该再触发** ✓ —— 否则每 20 秒开一个任务 ✗（这是最容易写错的地方 ✓）。"""
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    a = _auto(tmp_path)
    m._watch_changed(a)
    assert m._watch_changed(a) == []
    assert m._watch_changed(a) == []


def test_missing_dir_is_silent_not_crash(tmp_path):
    """目录被删/移走 ⇒ **安静地什么都不做** ✓（循环不能因为一个坏自动化挂掉 ✗）。"""
    a = _auto(tmp_path / "不存在")
    assert m._watch_changed(a) == []


def test_subdirectories_are_ignored(tmp_path):
    """只看一层 ✓（子目录里的文件不算 ✓ —— 递归扫大目录会拖慢每一轮 ✓）。"""
    a = _auto(tmp_path)
    m._watch_changed(a)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.txt").write_text("深处", encoding="utf-8")
    assert m._watch_changed(a) == []


def test_create_requires_an_existing_dir():
    """建自动化时**路径必须真的存在** ✓ —— 存个不存在的目录，用户会以为它在盯 ✗。"""
    import pytest
    from fastapi import HTTPException
    from app.schemas import AutomationReq

    import asyncio

    req = AutomationReq(name="看下载目录", kind="watch", task_input="整理一下",
                        watch_path="D:/绝对不存在的目录_xyz123")
    with pytest.raises(HTTPException):
        asyncio.run(m.create_automation(req))
    # 缺路径也要拦 ✓
    with pytest.raises(HTTPException):
        asyncio.run(m.create_automation(
            AutomationReq(name="看下载目录", kind="watch", task_input="整理一下")))
