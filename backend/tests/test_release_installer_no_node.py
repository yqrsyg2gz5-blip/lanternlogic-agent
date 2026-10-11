# -*- coding: utf-8 -*-
"""发布包的安装脚本**不许把 Node.js 变成硬要求**（★ 2026-10-11 · 用户"门槛"问题的阶段 0）。

## 现场

  · 发布的 zip 里**已经带了构建好的 `frontend/dist`** ✓
  · 而 `install.bat` 的 [3/5] 与 [4b/5] 两步**无条件**跑 `npm ci` / `npm run build` ✗
    ⇒ 没装 Node.js 的人**卡在安装第 3 步** ✗（还配着一句 "Node.js 18+ required" ✓）
  · 可运行期**根本不需要 Node** ✓ —— `start.bat` 顶部注释自己写着：
    "No Node needed at runtime; the backend serves frontend\\dist" ✓
  ⇒ 这就是"上手有门槛"里**最冤**的一条 ✗：包里明明已经有界面 ✓

## 本文件钉三件事

  ① 有"dist 已在 ⇒ 跳过前端"的分支 ✓ 且两个落点（`:ui_ready` / `:ui_done`）都在 ✓
  ② 跳过是**有条件的** ✗ —— 不许把前端那两步直接删掉 ✗
     （源码树 checkout 出来**没有** dist ✓ 开发者的路必须照旧 ✓）
  ③ 那两步仍要能跑到 ✓（`npm ci` / `npm run build` 还在文件里 ✓）
"""
from __future__ import annotations

import pathlib

BAT = (pathlib.Path(__file__).resolve().parents[2] / "install.bat").read_text("utf-8", errors="replace")

SKIP_GUARD = 'if exist "%~dp0frontend\\dist\\index.html"'


# ═══ ① 跳过分支在 ✓ ═══

def test_installer_skips_frontend_when_the_ui_is_shipped():
    assert SKIP_GUARD in BAT, "install.bat 里没有『dist 已在就跳过前端』的判断 ✗（没装 Node 的人会卡住 ✓）"
    assert "goto :ui_ready" in BAT, "缺 [3/5] 那步的跳过 ✗"
    assert "goto :ui_done" in BAT, "缺 [4b/5] 那步的跳过 ✗"
    assert ":ui_ready" in BAT and ":ui_done" in BAT, "goto 的落点标签不在了 ✗（跳过去会掉进空处 ✓）"


def test_both_frontend_sections_are_covered():
    """[3/5] 装前端依赖 与 [4b/5] 构建界面 —— **两段都得跳过** ✓（只跳一段等于没跳 ✓）。"""
    assert "npm ci skipped" in BAT, "缺 [3/5] 那段的跳过提示 ✗（用户会以为装了前端 ✓）"
    assert "npm run build skipped" in BAT, "缺 [4b/5] 那段的跳过提示 ✗"


# ═══ ②③ 源码树的路不能被堵死 ✓ ═══

def test_source_checkout_still_builds_the_ui():
    assert "npm ci" in BAT, "把 npm ci 删了 ✗ —— 源码树没 dist，那样装不起来 ✓"
    assert "npm run build" in BAT, "把 npm run build 删了 ✗ —— 同上 ✓"


def test_skip_is_conditional_not_unconditional():
    """★ 跳过只能挂在"dist 存在"这个条件下 ✗ 不许无条件 goto ✓（否则源码树没法构建界面 ✓）。

    两种合法写法都收 ✓：
      · 同一行： `if exist "...dist\\index.html" goto :ui_ready`
      · 块里面： `if exist "...dist\\index.html" (` … `goto :ui_done` … `)`
    """
    lines = BAT.splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        if not s.startswith("goto :ui"):
            continue
        ok = "dist\\index.html" in s          # 写成同一行 ✓
        for j in range(i - 1, max(-1, i - 8), -1):   # 否则往上找守卫块的开始 ✓
            prev = lines[j].strip()
            if prev.startswith("if exist") and "dist\\index.html" in prev and prev.endswith("("):
                ok = True
                break
            if prev == ")" or (prev.startswith("if ") and prev.endswith("(")):
                break                      # 撞到别的块边界 ⇒ 说明它不归那个守卫管 ✓
        assert ok, f"出现了**无条件**跳过 ✗：{s}（源码树就没法构建界面了 ✓）"
