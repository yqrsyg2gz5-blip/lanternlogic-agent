# -*- coding: utf-8 -*-
"""交付体检必须在**别的线程**里跑（★ 2026-10-10 功能扫测真跑抓到的 ✗）。

## 现场（原样）

```
【🖱️ 交付体检（没做成）】未做真点击体检：体检过程出错
  （Error: It looks like you are using Playwright Sync API inside the asyncio loop.）
【📋 交付声明分级：未验证】   ← 好交付也被这么标 ✗
```

查实：`loop.py` 的交付收尾路径在**异步任务循环**里**直接**调同步版 `audit_html()` ✗
  ⇒ 同步版 Playwright 在事件循环里必炸 ✓ ⇒ **「交付体检」从来没成功过** ✗
  ⇒ 已害 5 个任务（PPT / 网站建设 / 数据分析 / 综合报告 / 本地模型做的那个网页 ✓）
  ⇒ 还连带把它们的「交付声明分级」降成 **未验证** ✗（其实产物是好的 ✓）

## 本文件钉三件事

  ① 那个调用点必须是 `asyncio.to_thread` ✓（源码锚点 —— 本仓对"同步重活不许占事件循环"
     已有先例：备份卡死那次 ✓）
  ② 行为复刻：**在异步循环里直接调**会炸 ✓（哪天不炸了 = Playwright 行为变了 ⇒ 回来重判 ✓）
  ③ 行为验证：**放进线程里调**不炸 ✓（连没装 Edge 的机器上也成立 ✓ 那是"如实跳过"✓）
"""
from __future__ import annotations

import asyncio
import pathlib
import re

import pytest

from app.click_audit import audit_html

SRC = pathlib.Path(__file__).resolve().parents[1] / "app"
HTML = "<!DOCTYPE html><html><body><h1>t</h1><button>点我</button></body></html>"


def _page(tmp_path: pathlib.Path) -> pathlib.Path:
    p = tmp_path / "x.html"
    p.write_text(HTML, "utf-8")
    return p


# ═══ ① 调用点（源码锚点）═══

def test_loop_runs_the_audit_in_a_thread():
    src = (SRC / "loop.py").read_text("utf-8")
    # ★ 精确一点：注释里也会出现 `audit_html` 这个词 ✗ 所以不能靠 index() 取窗口
    #   （第一版就是这么写错的 ✓：搜到的是注释，真正的调用点在几百字之后 ✓）
    assert re.search(r"asyncio\.to_thread\(\s*audit_html", src), (
        "交付体检没有放进线程跑 ✗ —— 实测必炸（Sync API inside the asyncio loop）✓")
    assert not re.search(r"=\s*audit_html\(", src), (
        "又出现了直接调用（会占死事件循环）✗：应当走 asyncio.to_thread ✓")


# ═══ ②③ 行为（要本机装了 playwright 才有意义 ✓ 没装就如实跳过 ✓）═══

def test_direct_call_inside_a_loop_hits_the_known_failure(tmp_path):
    pytest.importorskip("playwright", reason="没装 playwright —— 点击体检本来就「如实跳过」✓ 这条在本机无从谈起")
    html = _page(tmp_path)

    async def go():
        return audit_html(html)          # ★ 直接调 = 复刻当时那个 bug ✓

    got = asyncio.run(go())
    assert got["ok"] is False, got
    assert "asyncio" in got["detail"].lower(), (
        f"现场没复刻出来（Playwright 行为变了？）—— 那这条测试该重判 ✓：{got['detail']}")


def test_in_a_thread_it_no_longer_hits_that_error(tmp_path):
    pytest.importorskip("playwright", reason="没装 playwright —— 同上 ✓")
    html = _page(tmp_path)

    async def go():
        return await asyncio.to_thread(audit_html, html)

    got = asyncio.run(go())
    assert "asyncio" not in got["detail"].lower(), (
        f"挪进线程还是撞了同一个错 ✗：{got['detail']}")
    # 本机装了 Edge ⇒ 应当**真跑成功** ✓；没装 ⇒ 如实跳过 ✓ 两种都算对 ✓
    assert got["ok"] or "Edge" in got["detail"] or "playwright" in got["detail"], got
