# -*- coding: utf-8 -*-
"""交付体检（真点击）—— 用**故意做坏的页面**当考卷 ✓

考卷设计（关键 ✓）：
  造一个页面上有**两种按钮**：
    · 「好按钮」点了会改页面 ✓ ⇒ **不许**被判成死的 ✗
    · 「死按钮」点了啥也不干 ✓ ⇒ ★ **必须**被抓出来 ✓
  ⇒ 这样测的不是"函数能跑"✗ 而是"**它真能分辨死活**"✓
  （2026-10-09 用户那件事就是缺这一条：Agent 的自测**分辨不出**死按钮 ✓）

★ 需要真浏览器（系统 Edge）✓ 没有就 **skip** ✓ ——
  绝不假装验过 ✗（本仓 CI 上没 Edge ⇒ 那几条自然跳过 ✓ 这是设计 ✓ 不是漏洞 ✓）
"""
from __future__ import annotations

import pytest

from app.click_audit import _skip, audit_html

PAGE_OK = """<html><head><title>试卷</title></head><body>
<button id="good" onclick="document.getElementById('out').textContent='变了'">好按钮</button>
<button id="dead">死按钮</button>
<div id="out">原样</div>
</body></html>"""

PAGE_ALL_DEAD = """<html><head><title>全死</title></head><body>
<button>死一</button><button>死二</button>
</body></html>"""


def _edge_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return False
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch(channel="msedge", headless=True)
            b.close()
        return True
    except Exception:
        return False


_need_edge = pytest.mark.skipif(not _edge_available(), reason="本机没有系统 Edge（跳过真点击体检）")


# ── 不需要浏览器的部分（纯读 ✓ 任何机器都能跑 ✓）──

def test_missing_file_is_skipped_not_crashed(tmp_path):
    """★ 文件不存在 ⇒ 如实跳过 ✓ **不许抛** ✗（它在交付收尾路径上 ✓）"""
    r = audit_html(tmp_path / "nope.html")
    assert r["ok"] is False
    assert "不存在" in r["skipped_reason"]
    assert r["dead"] == []


def test_non_html_is_skipped(tmp_path):
    """不是网页 ⇒ 跳过（别去点一个 txt ✗）"""
    f = tmp_path / "a.txt"
    f.write_text("hi", encoding="utf-8")
    r = audit_html(f)
    assert r["ok"] is False and "不是网页" in r["skipped_reason"]


def test_skip_shape_is_honest():
    """★ 跳过时**不许**装作验过 ✗ —— detail 必须写明"未做真点击体检"✓"""
    r = _skip("本机没装 playwright")
    assert r["ok"] is False
    assert "未做真点击体检" in r["detail"], "跳过的措辞必须诚实 ✗（不许含糊成'通过'✓）"


# ── 需要真浏览器的部分（本机有 Edge ⇒ 真跑 ✓；CI 没 Edge ⇒ 自动跳过 ✓）──

@_need_edge
def test_catches_the_dead_button_and_spares_the_good_one(tmp_path):
    """★★ 核心考卷：一个死按钮必须被抓 ✓ 一个好按钮不许被冤枉 ✗"""
    f = tmp_path / "page.html"
    f.write_text(PAGE_OK, encoding="utf-8")
    r = audit_html(f)
    assert r["ok"] is True, r["detail"]
    assert r["buttons"] >= 2, r
    assert "死按钮" in r["dead"], f"死按钮没抓到 ✗：{r}"
    assert "好按钮" not in r["dead"], f"好按钮被冤枉了 ✗：{r}"
    assert "缺陷" in r["detail"], "结论里必须点明这是缺陷 ✓（别含糊 ✓）"


@_need_edge
def test_all_dead_is_reported_as_such(tmp_path):
    """全死 ⇒ 全部列出来 ✓（不因为"全军覆没"就改口径 ✓）"""
    f = tmp_path / "all_dead.html"
    f.write_text(PAGE_ALL_DEAD, encoding="utf-8")
    r = audit_html(f)
    assert r["ok"] is True, r["detail"]
    assert set(r["dead"]) == {"死一", "死二"}, r
    assert r["detail"].startswith("⚠️"), r["detail"]


@_need_edge
def test_no_buttons_is_neither_pass_nor_fail(tmp_path):
    """没有按钮 ⇒ **不说通过也不说失败** ✓（诚实 ✓ 别自己编结论 ✗）"""
    f = tmp_path / "plain.html"
    f.write_text("<html><body><p>纯文字</p></body></html>", encoding="utf-8")
    r = audit_html(f)
    assert r["ok"] is True
    assert r["buttons"] == 0 and r["dead"] == []
    assert "不算通过也不算失败" in r["detail"], r["detail"]
