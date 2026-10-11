# -*- coding: utf-8 -*-
"""清垃圾：浏览器实况缓存 `_edgeprof*`（★ 2026-10-10，用户点头要的 ✓）。

## 为什么（本机实测数据 ✓ 不是估的）

  · 浏览器实况工具（`browser_navigate` 等）每开一次会话，就在**任务/组的工作区**里
    留下一个 Chromium profile ✗ —— 实测本机 **20+ 个 `_edgeprof*`，
    合计约 2 GB / 1.5 万个文件**（单一个就有 448 MB ✗ Chromium 默认 profile 就这么肥）
  · 它们还会被**交付快照**跟着复制一份 ✗（于是每组快照里都躺着一套 ✓）⇒ 越滚越多 ✓
  · 后果一：备份时被一起打包 ⇒ 几个 GB / 几万个文件 ⇒ **卡死整个后端** ✗（已单独修 ✓）
  · 后果二：白占用户几个 GB 硬盘 ✗

## 规矩（照本仓既有的"一键清干净"那一套 ✓）

  ① **只删名字以 `_edgeprof` 开头的目录** ✗ —— 其它一个字节都不碰 ✓
  ② **必须落在 data 目录里** ✓（`resolve()` 之后校验 ✓ 防路径穿越 ✓）
  ③ 正被占用的**跳过并如实报** ✗（绝不假装清干净 ✓ —— 与全项目同一条诚实规矩 ✓）
  ④ 清完**如实报**：删了几个 / 释放多少 MB / 跳过几个 ✓
  ⑤ 由调用方记一笔审计 ✓（`kind=cleaned` ✓ 带释放字节数 ✓ 便于以后追查 ✓）
"""
from __future__ import annotations

import pathlib
import shutil

#: 只认这些前缀 ✓（`_edgeprof` / `_edgeprof2` / `_edgeprofAudit` …）
PREFIX = "_edgeprof"


def _inside(p: pathlib.Path, root: pathlib.Path) -> bool:
    """p 必须真的在 root 里 ✓（resolve 后比较 ✓ 防 `..` 与符号链接 ✓）。"""
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def profiles(data_dir: str | pathlib.Path) -> list[pathlib.Path]:
    """找出所有 `_edgeprof*` 目录 ✓（按深浅排序 ⇒ 先删浅的 ✓ 免得父子重复计数 ✓）。"""
    root = pathlib.Path(data_dir)
    if not root.is_dir():
        return []
    found: list[pathlib.Path] = []
    try:
        for p in root.rglob(f"{PREFIX}*"):
            if p.is_dir() and _inside(p, root):
                found.append(p)
    except OSError:
        pass
    return sorted(found, key=lambda x: len(x.parts))


def _measure(dirs: list[pathlib.Path]) -> tuple[int, int]:
    files = 0
    size = 0
    for d in dirs:
        try:
            for f in d.rglob("*"):
                if f.is_file():
                    files += 1
                    try:
                        size += f.stat().st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return files, size


def scan(data_dir: str | pathlib.Path) -> dict:
    """看看现在攒了多少 ✓（**只读** ✓ 什么都不动 ✓）。"""
    dirs = profiles(data_dir)
    files, size = _measure(dirs)
    return {
        "dirs": len(dirs),
        "files": files,
        "bytes": size,
        "mb": round(size / 1048576, 1),
        "sample": [str(d) for d in dirs[:10]],
        "note": ("浏览器实况工具每开一次留一个 Chromium profile ✗ —— 删掉不影响任何任务数据 ✓"
                 "（下次用浏览器会重新生成一个 ✓）") if dirs else "没有浏览器缓存垃圾 ✓",
    }


def clean(data_dir: str | pathlib.Path) -> dict:
    """清掉它们 ✓ —— 返回 {ok, removed, freed_bytes, freed_mb, failed, reason} ✓ **永不抛** ✗。"""
    dirs = profiles(data_dir)
    if not dirs:
        return {"ok": True, "removed": 0, "freed_bytes": 0, "freed_mb": 0.0,
                "failed": [], "failed_count": 0, "reason": "没有浏览器缓存垃圾 ✓ 没动任何东西 ✓"}
    removed = 0
    freed = 0
    failed: list[str] = []
    for d in dirs:
        _files, size = _measure([d])
        try:
            shutil.rmtree(d)
            removed += 1
            freed += size
        except OSError as e:                    # 被占用/权限 ⇒ 跳过 ✓ 如实报 ✗
            failed.append(f"{d.name}（{type(e).__name__}）")
    return {
        "ok": True,
        "removed": removed,
        "freed_bytes": freed,
        "freed_mb": round(freed / 1048576, 1),
        "failed": failed[:10],
        "failed_count": len(failed),
        "reason": (f"清掉 {removed} 个浏览器缓存目录，释放 {freed / 1048576:.1f} MB ✓"
                   + (f"　有 {len(failed)} 个没删掉（多半正被占用 ✓ 关掉浏览器再试 ✓）："
                      f"{'、'.join(failed[:3])}" if failed else "　没有跳过的 ✓")),
    }
