# -*- coding: utf-8 -*-
"""技能自带的「环境体检」脚本 —— 开工前一条命令扫平三堵墙（2026-12-13 实测得出）。

## 为什么要它（三轮真机失败换来的）

用户真机跑 30 秒卡点视频，**两轮共 720 步、1336 万 token、0 个产物**。
事后逐项实测，发现三堵墙卡在"渲染"前面（agent 前两轮根本没走到那步，所以连撞都没撞上）：

  ① `tools/render/node_modules` 没装（渲染要 playwright-core）⇒ 渲染器起不来
  ② `CHROME_PATH` 没设 ⇒ `render.mjs` 去找 Google Chrome（本机没装 Chrome）⇒ 直接失败
  ③ **PATH 上的 ffmpeg 是残缺版**（`GPT-SoVITS\\runtime\\ffmpeg.EXE`，**无 libx264**）
     ⇒ 渲染跑到编码那步报 `Unknown encoder 'libx264'` 崩掉
     ★ 这条最阴：agent 完全猜不到"同一个命令名有两个版本，PATH 上那个是残的"

**实测**：把这三样修好后，`render.mjs` 立刻出片 ——
1080×1920 竖屏、h264、60 帧、2.000 秒、38.5 fps ✓

## 这个脚本做什么 / 不做什么（★ 分工是刻意的）

**做**（机械、确定、易错的活）：
  · 检查并（可选）安装 `render\\node_modules`
  · 找浏览器并给出 `CHROME_PATH` 该设成什么
  · 找**带 libx264 的** ffmpeg（PATH 上那个可能是残的）
  · 把结果打印成一眼能看懂的清单 + 可复制的环境设置命令

**不做**（全是 AI 的活，脚本绝不越界）：
  · ❌ 不生成 HTML / 不碰任何视觉与文案决策
  · ❌ 不决定视频多长、几个场景、什么节奏
  · ❌ 不改任何已有文件（除了自己 `--install` 时装依赖）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

#: 常见的浏览器位置（本机实测：Edge 在 Program Files (x86) 下）
_BROWSER_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
)

#: 已知的"可能残缺"的 ffmpeg 位置（PATH 上排在前面、但没 libx264）
_KNOWN_BAD_FFMPEG = (
    r"D:\Software\GPT-SoVITS\GPT-SoVITS-v2pro-20250604\runtime\ffmpeg.EXE",
)

#: 已知的"完整"的 ffmpeg 位置（实测带 libx264）
_KNOWN_GOOD_FFMPEG = (
    r"D:\MiniMax Design\current\resources\ffmpeg\ffmpeg.exe",
)


def _run(cmd: list[str], timeout: float = 30.0) -> tuple[int, str]:
    """跑一条命令，返回 (退出码, 输出)。跑不起来返回 (-1, 原因)。"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return -1, "找不到该命令"
    except subprocess.TimeoutExpired:
        return -2, f"超时（>{timeout:.0f}s）"
    except OSError as e:
        return -3, f"{type(e).__name__}: {e}"


def ffmpeg_has_libx264(ffmpeg: str) -> bool:
    """这个 ffmpeg 带不带 libx264（不带就编不了 H.264 ⇒ 渲染必崩）。"""
    rc, out = _run([ffmpeg, "-hide_banner", "-encoders"])
    if rc != 0:
        return False
    for line in out.splitlines():
        if "libx264" in line and line.lstrip().startswith("V"):
            return True
    return False


def find_good_ffmpeg() -> str | None:
    """找一个**带 libx264** 的 ffmpeg。先看 PATH，再看已知位置。"""
    on_path = shutil.which("ffmpeg")
    if on_path and ffmpeg_has_libx264(on_path):
        return on_path
    for cand in _KNOWN_GOOD_FFMPEG:
        if Path(cand).exists() and ffmpeg_has_libx264(cand):
            return cand
    return None


def find_browser() -> str | None:
    """找一个能用的浏览器（渲染要它）。"""
    env = os.environ.get("CHROME_PATH")
    if env and Path(env).exists():
        return env
    for cand in _BROWSER_CANDIDATES:
        if cand and Path(cand).exists():
            return cand
    return None


def node_modules_ok(render_dir: Path) -> bool:
    """渲染依赖装了没（playwright-core）。"""
    return (render_dir / "node_modules" / "playwright-core").exists()


def npm_available() -> bool:
    return shutil.which("npm") is not None


def install_node_modules(render_dir: Path) -> tuple[bool, str]:
    """装 `render\\node_modules`（**只装 playwright-core，不下载浏览器**）。"""
    if not npm_available():
        return False, "找不到 npm（请先装 Node.js）"
    pkg = render_dir / "package.json"
    if not pkg.exists():
        return False, f"缺少 {pkg}（技能包不完整？）"
    try:
        p = subprocess.run(["npm", "install", "--no-audit", "--no-fund"],
                           cwd=str(render_dir), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"{type(e).__name__}: {e}"
    if p.returncode != 0:
        tail = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()
        return False, tail[-1][:200] if tail else f"npm install 退出码 {p.returncode}"
    return node_modules_ok(render_dir), "npm install 完成"


def render_dir_of(skill_dir: Path) -> Path:
    return skill_dir / "tools" / "render"


def build_report(skill_dir: Path) -> dict:
    """体检报告（**只读，不动任何东西**）。"""
    rd = render_dir_of(skill_dir)
    ff = shutil.which("ffmpeg")
    good_ff = find_good_ffmpeg()
    browser = find_browser()
    return {
        "skill_dir": str(skill_dir),
        "render_dir": str(rd),
        "render_dir_exists": rd.is_dir(),
        "playwright_installed": node_modules_ok(rd),
        "npm_available": npm_available(),
        "browser": browser,
        "chrome_path_set": bool(os.environ.get("CHROME_PATH")),
        "ffmpeg_on_path": ff,
        "ffmpeg_on_path_ok": bool(ff) and ffmpeg_has_libx264(ff),
        "ffmpeg_good": good_ff,
        "ready": False,
    }


def is_ready(rep: dict) -> bool:
    """★ 能不能**直接**开工 —— 四个条件全满足才算。

    ★ 第一版把 `ready` 写成 `playwright ∧ browser ∧ ffmpeg_good`，**是错的**：
      机器上"存在一个好 ffmpeg/浏览器"**不等于**"当前 shell 已经能用它们"。
      实测踩到：脚本一边报「✗ CHROME_PATH 已设 / ✗ PATH 上的 ffmpeg 无 libx264」，
      一边又打印「✅ 环境就绪，可以直接开工」—— **自相矛盾**，
      而且比不报更坏：agent 看到"就绪"就直接开工，然后又撞墙 ✗

    ⇒ 必须 `已设 CHROME_PATH` **且** `PATH 上那个 ffmpeg 就带 libx264` 才算就绪 ✓
    """
    return bool(
        rep.get("playwright_installed")
        and rep.get("chrome_path_set")
        and rep.get("ffmpeg_on_path_ok")
    )


def _to_posix(path: str) -> str:
    """Windows 盘符路径 → bash 认的 `/d/...`（已经 posix 的原样返回）。

    ★ 为什么需要：本应用执行命令走 **git bash**（实测 `bash 5.2.37`），
      而技能原来只给 PowerShell 语法（`$env:PATH = ...`）⇒ bash 报
      `:PATH: command not found`，**照做反而掉坑** ✗（真机 task_20261013_6a006c3d）
    """
    s = str(path or "").strip().replace("\\", "/")
    if len(s) >= 2 and s[1] == ":" and s[0].isalpha():
        return "/" + s[0].lower() + s[2:]
    return s


def _mark(ok: bool) -> str:
    return "OK  " if ok else "✗   "


def format_report(rep: dict) -> str:
    """人（和 agent）一眼能看懂的清单 + 可直接复制的环境设置命令 ✓"""
    lines: list[str] = []
    a = lines.append
    a("=" * 68)
    a("卡点视频技能 · 环境体检")
    a("=" * 68)
    a(f"  技能目录      {rep['skill_dir']}")
    a(f"  渲染器目录    {rep['render_dir']}")
    a("")
    a("【1/3】渲染依赖（playwright-core）")
    a(f"  {_mark(rep['playwright_installed'])}node_modules"
      + ("" if rep["playwright_installed"] else "   ← 没装：渲染器起不来"))
    a(f"  {_mark(rep['npm_available'])}npm 可用")
    a("")
    a("【2/3】浏览器（渲染要它；本机没 Chrome，用 Edge）")
    a(f"  {_mark(bool(rep['browser']))}浏览器：{rep['browser'] or '没找到'}")
    if rep["chrome_path_set"]:
        a(f"  {_mark(True)}CHROME_PATH 已设 ✓")
    elif rep["browser"]:
        a(f"  {_mark(False)}CHROME_PATH **没设**   ← render.mjs 会去找 Chrome 然后失败；"
          f"下面有可复制的命令")
    else:
        a(f"  {_mark(False)}CHROME_PATH 没设，且本机没找到 Edge/Chrome ✗")
    a("")
    a("【3/3】ffmpeg（必须有 libx264，否则编码崩溃）")
    a(f"  {_mark(rep['ffmpeg_on_path_ok'])}PATH 上的 ffmpeg：{rep['ffmpeg_on_path'] or '没有'}")
    if rep["ffmpeg_on_path"] and not rep["ffmpeg_on_path_ok"]:
        a("        ★ 这个**没有 libx264**（残缺版）—— 渲染到编码会报 Unknown encoder 'libx264' ✗")
    a(f"  {_mark(bool(rep['ffmpeg_good']))}可用的完整 ffmpeg：{rep['ffmpeg_good'] or '没找到'}")
    a("")
    ok = is_ready(rep)
    a("=" * 68)
    a("结论：" + ("✅ 环境就绪，可以直接开工 ✓" if ok else
                  "✗ **还不能开工** —— 先按下面修（没设好就往下走，后面必崩）"))
    a("=" * 68)
    if not ok:
        a("")
        a("★ **默认给 bash 语法**（本应用执行命令用 git bash —— 实测 `bash 5.2.37`）。")
        a("  把下面这些行和你真正要跑的命令写在**同一条命令里**")
        a("  （环境变量只影响当前 shell，下一条命令是新进程、拿不到）：")
        a("")
        if not rep.get("ffmpeg_on_path_ok"):
            if rep["ffmpeg_good"]:
                posix = _to_posix(str(Path(rep["ffmpeg_good"]).parent))
                a(f'  export PATH="{posix}:$PATH"')
                a("  ★ 路径含空格 ⇒ **引号不能省**（本机目录名就是 `MiniMax Design`）")
            else:
                a("  # 本机找不到带 libx264 的 ffmpeg —— 需要先装一个完整版")
        if not rep.get("chrome_path_set"):
            if rep["browser"]:
                posix_b = _to_posix(rep["browser"])
                a(f'  export CHROME_PATH="{posix_b}"')
            else:
                a("  # 本机没找到 Edge/Chrome —— 需要先装一个浏览器")
        if not rep["playwright_installed"]:
            a(f"  cd '{rep['render_dir']}' && npm install")
        a("")
        a("  【PowerShell —— 只有你手工在 PS 里跑时才用】")
        if rep["ffmpeg_good"]:
            a(f"    $env:PATH = '{str(Path(rep['ffmpeg_good']).parent)};' + $env:PATH")
        if rep["browser"]:
            a(f"    $env:CHROME_PATH = '{rep['browser']}'")
        a("")
        a("（本脚本加 `--install` 可自动装渲染依赖；PATH/CHROME_PATH **只打印不代设** ——")
        a("  环境变量只影响当前 shell，代设了下一个进程也拿不到，会造成假象 ✓）")
        a("")
        a("★ 这一页只解决环境。**页面/风格/片长/文案全部由你（AI）决定** ——")
        a("  脚本不碰任何创作决策 ✓ 契约见 `tools/render/example.html`。")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(add_help=True, description="卡点视频技能 · 环境体检")
    ap.add_argument("--skill-dir", default="", help="技能目录（默认：本脚本的上上级）")
    ap.add_argument("--install", action="store_true", help="缺渲染依赖时自动 npm install")
    ap.add_argument("--json", action="store_true", help="输出 JSON（给程序读）")
    args = ap.parse_args(argv)

    if args.skill_dir:
        skill_dir = Path(args.skill_dir).resolve()
    else:
        # 本脚本在 <skill>\tools\preflight.py ⇒ 上上级就是技能目录
        skill_dir = Path(__file__).resolve().parent.parent

    rep = build_report(skill_dir)

    if args.install and not rep["playwright_installed"]:
        ok, msg = install_node_modules(Path(rep["render_dir"]))
        rep["install_result"] = msg
        rep["playwright_installed"] = ok
        if not args.json:
            print(f"[install] {msg}\n")

    rep["ready"] = is_ready(rep)

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(format_report(rep))
    return 0 if rep["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
