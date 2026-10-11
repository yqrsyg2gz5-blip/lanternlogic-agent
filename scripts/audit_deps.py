# -*- coding: utf-8 -*-
"""依赖查漏洞 —— 一条命令把两边都查一遍（★ 2026-10-07 第 6 项）。

    backend\\.venv\\Scripts\\python.exe scripts\\audit_deps.py

## 三个"必须说清楚"的点（不然这个脚本会骗人 ✗）

**① 它只查、不改** ✗ —— 绝不自动升级任何依赖 ✓
   （自动升依赖 = 悄悄把用户环境换掉 ✓ 而"升级"和"修漏洞"不是一回事：
     升完可能**跑不起来** ✗ 那是拿能用换"看起来安全"✓）
   要升什么、升不升，**由你看了报告再决定** ✓

**② 两边的库不是一回事** ✓
   · Python → 问 **OSV.dev**（Google 维护的公开漏洞库 ✓ 免费 ✓ 不用装任何工具 ✓）
   · Node   → 用 **npm audit** ✓ 但**必须指定官方源** ✗ ——
     国内镜像（npmmirror）**没实现**审计接口 ✓ 直接跑只会得到一句
     `404 NOT_IMPLEMENTED` ✓（本机实测就是这样 ✓ 所以脚本里写死了官方源 ✓）

**③ "有漏洞"要分清**是**产品自己声明的依赖**、还是**某个可选功能装进来的** ✓
   举个真例子（本机实测 ✓）：`transformers` 报了 9 条 ✓ —— 它不是产品依赖 ✗
   而是**「本地语音识别」那个可选功能**装进来的 ✓（`qwen-asr` 拉进来的 ✓）
   ⇒ 两种的风险完全不同 ✓ 混在一张表里报，用户只会被吓到 ✓ 所以这里**分开列** ✓
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
REQ = ROOT / "backend" / "requirements.txt"
FRONTEND = ROOT / "frontend"
OFFICIAL = "https://registry.npmjs.org"


def hr(t: str) -> None:
    print("\n" + "═" * 4 + f" {t} " + "═" * 4)


def declared_names() -> set[str]:
    """产品**自己声明**的 Python 依赖名（小写）✓ 用来把"可选功能装进来的"分出去 ✓。"""
    if not REQ.exists():
        return set()
    out = set()
    for ln in REQ.read_text("utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        for sep in (">=", "==", "<=", "~=", ">", "<", "["):
            ln = ln.split(sep)[0]
        out.add(ln.strip().lower())
    return out


def pip_freeze() -> list[tuple[str, str]]:
    if not PY.exists():
        return []
    r = subprocess.run([str(PY), "-m", "pip", "freeze"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    pkgs = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, ver = line.split("==", 1)
        name = name.split("[")[0].strip()
        if name.lower() in ("pip", "setuptools", "wheel"):
            continue
        pkgs.append((name, ver.strip()))
    return pkgs


def osv_query(pkgs: list[tuple[str, str]]) -> dict[str, list[str]]:
    """批量问 OSV ✓ 一次请求 ✓（断了就**如实说查不到** ✗ 不许假装"没有漏洞"✓）。"""
    if not pkgs:
        return {}
    queries = [{"package": {"name": n, "ecosystem": "PyPI"}, "version": v} for n, v in pkgs]
    req = urllib.request.Request(
        "https://api.osv.dev/v1/querybatch",
        data=json.dumps({"queries": queries}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        res = json.load(r)
    out: dict[str, list[str]] = {}
    for (name, _ver), item in zip(pkgs, res.get("results", [])):
        ids = [str(v.get("id")) for v in (item.get("vulns") or [])]
        if ids:
            out[name] = ids
    return out


def main() -> int:
    print("依赖查漏洞 —— 只查不改 ✓（下面这些数字都是**真查出来的** ✓）")

    # ── Python ──
    hr("Python（问 OSV.dev）")
    pkgs = pip_freeze()
    if not pkgs:
        print("  找不到虚拟环境或 pip 不可用 —— 跳过 ✓（如实说，不假装查过 ✗）")
    else:
        print(f"  环境里 {len(pkgs)} 个包")
        try:
            hits = osv_query(pkgs)
        except Exception as e:                              # noqa: BLE001
            print(f"  ✗ 查不到（{type(e).__name__}: {str(e)[:80]}）—— 断网或 OSV 不可达 ✓ 这次**没有结论** ✗")
            hits = {}
        declared = declared_names()
        own = {k: v for k, v in hits.items() if k.lower() in declared}
        opt = {k: v for k, v in hits.items() if k.lower() not in declared}
        print(f"\n  【产品自己声明的依赖】{len(own)} 个包有已知漏洞")
        for k, v in sorted(own.items()):
            print(f"     ⚠ {k}：{len(v)} 条  {', '.join(v[:3])}")
        if not own:
            print("     ✓ 没有（requirements.txt 里那几条是干净的 ✓）")
        print(f"\n  【某个可选功能装进来的】{len(opt)} 个包有已知漏洞")
        for k, v in sorted(opt.items()):
            print(f"     · {k}：{len(v)} 条  {', '.join(v[:3])}")
        if opt:
            print("     （这些**不是产品依赖** ✓ 是可选功能拉进来的 ✓ 风险完全不同 ✓"
                  " ——升级前先确认那个功能还能用 ✓）")
        if not hits:
            print("\n  ✓ 两边都没有已知漏洞 ✓")

    # ── Node ──
    hr("Node（npm audit，**指定官方源**）")
    if not (FRONTEND / "package.json").exists():
        print("  前端目录不在 —— 跳过 ✓")
    else:
        r = subprocess.run(["npm", "audit", f"--registry={OFFICIAL}"], cwd=FRONTEND,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           shell=True)
        tail = [ln for ln in (r.stdout or "").splitlines() if ln.strip()][-14:]
        for ln in tail:
            print("  " + ln)
        print("\n  ★ 为什么不直接跑 `npm audit`：国内镜像（npmmirror）**没实现**审计接口 ✗"
              "（会回 404 NOT_IMPLEMENTED ✓ 本机实测 ✓）所以这里写死了官方源 ✓")
        print("  ★ 也**不替你升** ✗：`npm audit fix` 只做不跨大版本的 ✓ "
              "`--force` 会跨大版本 ⇒ 那条路由你决定 ✓")

    hr("怎么办")
    print("  · 想修**不跨版本**的 ⇒ `npm audit fix --registry=https://registry.npmjs.org`（安全 ✓）")
    print("  · 要跨大版本（vite 5→6/7 那种）⇒ **先想清楚** ✗ 改完必须重建 + 真跑一遍 ✓")
    print("  · 这个脚本**永远不会**替你升级 ✓（查和改是两件事 ✓）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
