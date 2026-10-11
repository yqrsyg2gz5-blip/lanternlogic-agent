# -*- coding: utf-8 -*-
"""交付入口提示 —— 治"10 个同名 HTML，用户不知道点哪个" ✓

## 为什么要它（2026-10-09 用户实测 ✓）

Agent 交付了一个项目 ✓ 工作区里躺着 **10 个名字几乎一样、标题完全一样的 HTML** ✗：
    index.html                         ← ★ 真成品
    _test.html / _test_dom.html        ← 它自己的测试
    _clicktest.html / _clicktest_dom.html / _clicktest_desktop.html / _clicktest_mobile.html
    _probetest.html / _probetest_dom.html / _errprobe.html / _index_dom.html …
用户点进去一看"点啥都不动"✗ —— 因为**他点的是测试文件** ✓
⇒ 不是用户笨 ✗ 是**交付没说清入口** ✓

## 这个模块做什么

给一个目录 ✓ 算出：
  · **entry**：该双击哪个 ✓（优先 `index.html` ✓ 否则最像"应用"的那个 ✓）
  · **test_like**：疑似测试/临时产物 ✓（一律进这个名单 ✓ 用户不用管 ✓）
  · **note**：给用户看的一句话 ✓（直接可贴进交付卡 ✓）

★ 判据保守 ✓：**只做"建议"不做"移动"** ✗ ——
  擅自搬动 Agent/用户的文件风险大 ✓（可能搬坏相对路径引用 ✓）
  ⇒ 本模块**纯读** ✓ 不改任何东西 ✓

★ 判据**可解释** ✓（每条都有理由 ✓ 便于将来调）：
  1. 下划线开头 = 测试/临时（本仓既有约定 ✓ 今天那 10 个里 9 个如此 ✓）
  2. 名字里含 test/probe/diag/check/dom/snapshot 等词 = 测试/诊断
  3. `*.min.html` / `*.bak.html` 之类 = 副本
  4. 其余按"优先级 + 名字短"排序 ✓（`index.html` 永远第一 ✓）
"""
from __future__ import annotations

import pathlib

#: 命中这些词就当成测试/诊断产物（小写比对 ✓）
_TEST_WORDS = ("test", "probe", "diag", "check", "clicktest", "dom", "snapshot", "demo_", "sample")

#: 入口优先级（越靠前越优先 ✓）
_PREFERRED = ("index.html", "main.html", "app.html", "home.html")


def _is_test_like(name: str) -> bool:
    low = name.lower()
    if low.startswith("_"):
        return True
    if low.endswith((".bak.html", ".min.html", ".old.html")):
        return True
    return any(w in low for w in _TEST_WORDS)


def suggest_entry(workspace: str | pathlib.Path) -> dict:
    """算出"该双击哪个文件"。**纯读** ✓ 目录不存在/无候选时如实返回空 ✓ 不抛 ✓。"""
    ws = pathlib.Path(workspace)
    try:
        if not ws.is_dir():
            return {"entry": "", "test_like": [], "others": [], "note": ""}
        htmls = [p.name for p in ws.iterdir() if p.is_file() and p.suffix.lower() in (".html", ".htm")]
    except OSError:
        return {"entry": "", "test_like": [], "others": [], "note": ""}

    # ★ 入口判定：**先把"首选名"直接挑出来** ✓（`index.html` 命中就它 ✓）
    #   为什么不用排序：我第一版就是排序 ✓ 结果 `app.html` 因为名字短而压过 `index.html` ✗
    #   （测试当场变红 ✓）—— 排序键这种东西**容易想当然**✗ ⇒ 改成**显式判断** ✓ 一眼看得懂 ✓
    by_lower = {n.lower(): n for n in htmls}
    entry = ""
    for pref in _PREFERRED:
        if pref in by_lower:
            entry = by_lower[pref]
            break
    if not entry:
        for n in sorted(htmls, key=lambda s: (len(s), s)):
            if not _is_test_like(n):
                entry = n
                break
    if not entry and htmls:                     # 全是测试样名字 ⇒ 至少给一个 ✓（诚实标注 ✓）
        entry = sorted(htmls, key=lambda s: (len(s), s))[0]

    rest = [n for n in htmls if n != entry]
    test_like = [n for n in rest if _is_test_like(n)]
    others = [n for n in rest if n not in test_like]

    if not entry:
        note = ""
    elif test_like or others:
        skip = len(test_like) + len(others)
        note = (f"打开方式：双击 **{entry}**（本目录另有 {skip} 个测试/临时文件，不用管它们 ✓）"
                f"　★ 应用里那个预览是**安全沙箱**（脚本不执行 ✓ 点不动是正常的 ✓）"
                f"要真试请用「在系统浏览器里打开」✓")
    else:
        note = (f"打开方式：双击 **{entry}**　★ 应用里的预览是安全沙箱 ✓ "
                f"要真试请用「在系统浏览器里打开」✓")
    return {"entry": entry, "test_like": test_like, "others": others, "note": note}
