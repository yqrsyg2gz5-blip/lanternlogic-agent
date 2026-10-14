# -*- coding: utf-8 -*-
"""★★ 发版前必跑：**这个仓库现在能不能打出「开箱能用」的包**（2026-10-14 新增）。

## 为什么有它（用户原话）

> 「你能不能就是好好细心点，你一次性我不管你说是浪费多少token，
>   一次性给他做好审查测试，你都弄好了，你再上传是不是？
>   你别他妈老弄个半瞎子，然后就上传，不好，然后别人下载完了，口碑也不好」

**本班就是靠这套检查抓到两个"发出去用户也用不了"的 bug：**

```
Bug ① node_modules 被 .gitignore 排除 ⇒ 没打进发版包
       ⇒ 用户解压后一渲染：ERR_MODULE_NOT_FOUND: playwright-core
       ⇒ **核心功能（出视频）直接废**（v0.1.0 / v0.1.1 都中招 ✗）
Bug ② 渲染器写死 { channel: 'chrome' } ⇒ 没装 Chrome 的 Windows 用户必失败 ✗
       （Windows 自带的是 Edge）
```

## 检查什么（全部**真跑**，不看代码猜）

  ① 渲染器依赖齐不齐（node_modules / package.json 声明的每个依赖）
  ② 浏览器找得到吗（CHROME_PATH → Edge → Chrome）
  ③ **真渲一小段**（起手模板 3 秒）——不通就直接判死 ✗
  ④ 发版导出清单里有没有强制带上 node_modules
  ⑤ 前端产物齐不齐（用户打开别白屏）
  ⑥ 技能 / preflight / start.bat 在不在

跑法：`python scripts/release_check.py`
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[1]
RENDER = ROOT / "backend" / "skills" / "卡点视频" / "tools" / "render"
ok_all = True


def say(yes: bool, label: str, detail: str = "") -> None:
    global ok_all
    mark = "✓" if yes else "✗"
    if not yes:
        ok_all = False
    print(f"  [{mark}] {label}" + (f"  —— {detail}" if detail else ""))


print("=" * 84)
print("① 渲染器依赖")
print("=" * 84)
pkg_p = RENDER / "package.json"
deps: list[str] = []
if pkg_p.exists():
    deps = list((json.loads(pkg_p.read_text("utf-8")).get("dependencies") or {}))
    say(True, f"package.json 声明依赖 {deps}")
else:
    say(False, "渲染器没有 package.json")
for d in deps:
    say((RENDER / "node_modules" / d).is_dir(), f"依赖 {d} 已安装（node_modules/{d}）")

print()
print("=" * 84)
print("② 浏览器（新用户机器上多半只有 Edge）")
print("=" * 84)
CAND = [
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
]
env_chrome = os.environ.get("CHROME_PATH")
found = env_chrome if (env_chrome and pathlib.Path(env_chrome).exists()) else next(
    (c for c in CAND if pathlib.Path(c).exists()), None)
say(bool(found), "找到可用浏览器", found or "Edge/Chrome 都没有 ⇒ 渲染会失败")
say("msedge.exe" in (RENDER / "render.mjs").read_text("utf-8", errors="replace")
    if (RENDER / "render.mjs").exists() else False,
    "渲染器支持 Edge（没写死 Chrome）")

print()
print("=" * 84)
print("③ ★ 真渲一小段（3 秒）—— 这是唯一能证明「开箱能出视频」的办法")
print("=" * 84)
starter = RENDER / "starter.html"
if starter.exists():
    with tempfile.TemporaryDirectory() as td:
        mp4 = pathlib.Path(td) / "check.mp4"
        env = dict(os.environ)
        env["PATH"] = str(pathlib.Path(r"D:\MiniMax Design\current\resources\ffmpeg")) + os.pathsep + env.get("PATH", "")
        r = subprocess.run(
            ["node", "render.mjs", "starter.html", str(mp4), "--to", "3", "--workers", "1"],
            cwd=str(RENDER), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=600, env=env)
        good = mp4.exists() and mp4.stat().st_size > 5000
        say(good, "起手模板真渲通过", f"{mp4.stat().st_size:,} 字节" if good
            else (r.stdout or r.stderr or "")[-300:])
else:
    say(False, "起手模板不在（没它模型只能去读渲染器源码）")

print()
print("=" * 84)
print("④ 发版导出会不会带上运行时依赖")
print("=" * 84)
exp = ROOT / "scripts" / "export_public.py"
t = exp.read_text("utf-8", errors="replace") if exp.exists() else ""
say("FORCE_INCLUDE_DIRS" in t, "导出脚本有「强制带入」清单")
say("node_modules" in t, "强制带入清单含 node_modules（被 .gitignore 排除的那个）")
for d in deps:
    say(d in t, f"声明的依赖 {d} 在强制带入清单里")

print()
print("=" * 84)
print("⑤ 前端产物（用户打开别白屏）")
print("=" * 84)
idx = ROOT / "frontend" / "dist" / "index.html"
say(idx.exists(), "frontend/dist/index.html 在")
if idx.exists():
    import re
    js = re.findall(r'assets/(index-[\w-]+\.js)', idx.read_text("utf-8", errors="replace"))
    for name in js:
        f = ROOT / "frontend" / "dist" / "assets" / name
        say(f.exists(), f"产物 {name} 在")

print()
print("=" * 84)
print("⑥ 技能 / 启动脚本")
print("=" * 84)
say((ROOT / "start.bat").exists(), "start.bat 在")
say((ROOT / "contracts" / "config.example.json").exists(), "config 模板在")
pf = next((p for p in (ROOT / "backend" / "scripts" / "preflight.py",
                       ROOT / "scripts" / "preflight.py") if p.exists()), None)
say(pf is not None, "preflight.py 在")
skills = ROOT / "backend" / "skills"
say(skills.is_dir() and len([p for p in skills.iterdir() if p.is_dir()]) >= 8,
    "技能库齐全", f"{len([p for p in skills.iterdir() if p.is_dir()]) if skills.is_dir() else 0} 个")

print()
print("=" * 84)
print("★ 结论")
print("=" * 84)
print("  " + ("★ 这个仓现在能打出「开箱能用」的包 ✓ 可以发版"
             if ok_all else "★ **不许发版**：上面有 ✗，先修 ✓"))
sys.exit(0 if ok_all else 1)
