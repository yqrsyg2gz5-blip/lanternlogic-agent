# -*- coding: utf-8 -*-
"""★ 门禁：前端**产物**必须比源码新（2026-10-05 的教训）。

## 为什么要有这一条

用户的应用是后端托管 `frontend/dist`（8642），而我所有前端验证都跑在 dev server（5173）。
于是一整天我改的界面（错误边界、搜索崩溃修复、审批卡片、接力/开会两个模式）
**用户一个都没看到**：他的 dist 停在 10/04 14:51，而源码已经是 10/05 06:20。
他甚至因此踩到了我早就修好的崩溃（旧包里那个"搜索改 DOM"的 bug + 没有错误边界 ⇒ 整页全黑）。

单靠"记得重新构建"是靠不住的 —— 加一条机器检查：

    python scripts/check_dist_freshness.py        # 过期则退出码 1

判据：`frontend/dist/index.html` 的 mtime 必须 **不早于** `frontend/src` 下最新的源文件；
另外顺手检查几个"必须出现在产物里"的关键串（防止构建产物是坏的/半截的）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "frontend" / "dist"
SRC = ROOT / "frontend" / "src"

# 产物里必须能找到这些串（中文会被打包进 bundle；找不到说明构建不全或前端接线丢了）
NEEDLES = ("approval-card", "需要你批准", "这一块渲染出错了")


def main() -> int:
    # 控制台可能是 GBK：强制 UTF-8 输出，否则中文/符号会 UnicodeEncodeError
    # （本班实测：一个 ✓ 就让脚本崩掉、退出码变成 1，看着像"产物过期"）
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    index = DIST / "index.html"
    if not index.exists():
        print("!! 没有前端产物：frontend/dist/index.html 不存在（先跑 npm run build）")
        return 1
    if not SRC.exists():
        print("!! 找不到 frontend/src（路径不对？）")
        return 1

    newest = max((p for p in SRC.rglob("*") if p.is_file()), key=lambda p: p.stat().st_mtime)
    dist_m = index.stat().st_mtime
    src_m = newest.stat().st_mtime
    behind = src_m - dist_m

    print(f"  产物 frontend/dist/index.html ：{_fmt(dist_m)}")
    print(f"  最新源码 {newest.relative_to(ROOT)} ：{_fmt(src_m)}")
    if behind > 1:
        print(f"!! 产物落后源码 {behind / 60:.1f} 分钟 —— 用户看到的会是旧界面"
              f"（跑：cd frontend && npm run build）")
        return 1
    print("  产物是最新的 [ok]")

    assets = list((DIST / "assets").glob("*.js"))
    if not assets:
        print("!! dist/assets 下没有 js 产物")
        return 1
    blob = "\n".join(a.read_text("utf-8", errors="ignore") for a in assets)
    missing = [n for n in NEEDLES if n not in blob]
    if missing:
        print(f"!! 产物里缺少关键界面（{missing}）—— 前端接线可能丢了")
        return 1
    print(f"  关键界面都在产物里 [ok]（{len(NEEDLES)} 项）")
    return 0


def _fmt(ts: float) -> str:
    from datetime import datetime
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M:%S")


if __name__ == "__main__":
    sys.exit(main())
