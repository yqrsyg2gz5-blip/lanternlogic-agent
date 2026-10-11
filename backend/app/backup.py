# -*- coding: utf-8 -*-
"""一键备份 / 恢复 —— 用户数据的安全网 ✓

## 为什么单开一个模块

仓库里已有**零散导出**（`/api/v1/memory/export` ✓ `/api/v1/kb/{name}/export` ✓）
但**没有"把整个 data 目录打包带走"** ✗ —— 而用户换电脑 / 重装 / 手滑清空时，
真正需要的是后者 ✓（今天用户自己就说过"一键备份/恢复"是他的待办 ✓）

## 三条硬规矩（都来自本仓已有的教训 ✓）

  ① ★ **恢复前先把现有数据挪走** ✗（不是删 ✓ 是改名到 `_pre_restore_<时间戳>/`）
     —— 照抄"一键清干净"的既有做法 ✓ 那次的教训是"直接删⇒用户找不回来"✗
  ② ★ **有任务在跑就拒绝恢复** ✗ —— 一边跑一边换数据目录 = 必然写坏 ✓
  ③ ★ **不假装成功** ✗：清单（MANIFEST）如实写清**装了什么、跳过了什么** ✓
     （跳过的东西必须能在清单里看到 ✓ —— 否则用户以为备份全了 ✗）

## 跳过什么（默认 ✓ 都可显式包含）

  · `snapshots/` —— 那是**备份的备份** ✗（每份最多 5 个滚动副本 ✓ 会让包体积翻几倍 ✓）
  · `_cleared_*` / `_pre_restore_*` / `_backups_` —— 历史上的清理备份与**上一次的备份** ✓ 同上 ✓
  · 每个任务的 `tts/` 音频 ✓ —— 可重新生成 ✓ 体积大 ✓（想要就 include_audio=True ✓）
  · ★ 2026-10-10 新增（备份卡死事故的产物 ✓）：
      `_edgeprof*` 浏览器实况缓存 ✗（每开一次浏览器留一个 profile ✓
        实测本机 20 多个、合计约 2 GB / 1.5 万个文件 ✓）
      `tts_qwen3/` `asr_models/` 模型文件 ✗（合计约 4 GB ✓ 换电脑重下即可 ✓）
    ⇒ 这几样默认**不打包**，但**清单里如实写明跳过了多少个、省了多少 MB** ✓
      （`include_heavy=True` 时全都打进去 ✓ —— 给"我要完整搬走"的人 ✓）

★ 一路不碰网络 ✓ 不依赖外部工具（用标准库 zipfile ✓）
★ 打包可能很慢（几个 GB / 几万个文件）⇒ **调用方必须放到线程里** ✗
  （`main.py` 的 `/api/v1/backup` 用 `asyncio.to_thread` ✓ ——
   曾经直接在异步接口里同步压 ⇒ 事件循环被占死 ⇒ **整个后端失去响应** ✗✗）
"""
from __future__ import annotations

import json
import pathlib
import shutil
import zipfile
from datetime import datetime, timezone

MANIFEST = "MANIFEST.json"

#: 目录名里出现这些片段就跳过（默认 ✓）
_SKIP_DIR_PARTS = ("snapshots", "_cleared_", "_pre_restore_", "_backups_")
#: 目录名以这些**开头**就跳过（默认 ✓）—— 浏览器实况工具的 profile ✓
_SKIP_DIR_PREFIXES = ("_edgeprof",)
#: 这几个顶层目录是**可重装的模型文件** ✗（几个 GB ✓）
_SKIP_DIR_NAMES = ("tts_qwen3", "asr_models")


def _skip_reason(rel: str, parts: list[str], *, include_snapshots: bool,
                 include_audio: bool, include_heavy: bool) -> str | None:
    """这个文件该不该跳过 ✓ 返回原因（人话 ✓）或 None ✓ —— 单独抽出来是为了能单测 ✓。"""
    if not include_snapshots and any(
            any(s in part for s in _SKIP_DIR_PARTS) for part in parts):
        return "历史备份/快照（备份的备份 ✗ 要就 include_snapshots=True）"
    if not include_heavy:
        if any(part.startswith(_SKIP_DIR_PREFIXES) for part in parts):
            return "浏览器实况缓存 _edgeprof*（每开一次留一个 ✗ 要就 include_heavy=True）"
        if any(part in _SKIP_DIR_NAMES for part in parts):
            return "模型文件（可重下 ✗ 要就 include_heavy=True）"
    if not include_audio and "/tts/" in f"/{rel}" and pathlib.PurePosixPath(rel).suffix.lower() in (
            ".mp3", ".wav", ".m4a", ".ogg"):
        return "朗读音频（可重生成 ✗ 要就 include_audio=True）"
    return None


def _rel(p: pathlib.Path, root: pathlib.Path) -> str:
    return p.relative_to(root).as_posix()


def make_backup(data_dir: str | pathlib.Path, out_zip: str | pathlib.Path, *,
                include_snapshots: bool = False, include_audio: bool = False,
                include_heavy: bool = False) -> dict:
    """把 data 目录打成一个 zip ✓ 返回 {ok, path, files, bytes, skipped, reason} ✓ **永不抛** ✗。

    ★ 清单里**如实写跳过了什么、省了多少 MB** ✓（用户有权知道自己的备份不全 ✗）
    ★ 调用方**必须放到线程里**跑 ✗（几个 GB / 几万个文件会占死事件循环 ✓ 见模块头注释 ✓）
    """
    src = pathlib.Path(data_dir)
    out = pathlib.Path(out_zip)
    if not src.is_dir():
        return {"ok": False, "path": "", "files": 0, "bytes": 0, "skipped": [],
                "reason": f"数据目录不存在：{src}"}

    skipped: list[str] = []
    packed: list[str] = []
    by_reason: dict[str, int] = {}
    total = 0
    skipped_bytes = 0
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            out_abs = out.resolve()
        except OSError:
            out_abs = None
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(src.rglob("*")):
                if not p.is_file():
                    continue
                # ★ 别把"正在写的这个 zip 自己"打进去 ✗（上一次的备份已经由 _backups_ 跳过 ✓）
                if out_abs is not None:
                    try:
                        if p.resolve() == out_abs:
                            continue
                    except OSError:
                        pass
                rel = _rel(p, src)
                parts = rel.split("/")
                why = _skip_reason(rel, parts, include_snapshots=include_snapshots,
                                   include_audio=include_audio, include_heavy=include_heavy)
                if why:
                    skipped.append(f"{rel}（{why}）")
                    by_reason[why] = by_reason.get(why, 0) + 1
                    try:
                        skipped_bytes += p.stat().st_size
                    except OSError:
                        pass
                    continue
                try:
                    z.write(p, rel)
                    packed.append(rel)
                    total += p.stat().st_size
                except OSError as e:
                    skipped.append(f"{rel}（读不了：{type(e).__name__}）")
            z.writestr(MANIFEST, json.dumps({
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "files": len(packed),
                "bytes": total,
                "skipped_count": len(skipped),
                "skipped_bytes": skipped_bytes,
                "skipped_by_reason": by_reason,
                "skipped_sample": skipped[:50],
                "note": ("跳过项默认含 snapshots（备份的备份）· _cleared_/_pre_restore_/_backups_"
                         "（历史清理备份与上一次的备份）· 朗读音频 · **浏览器实况缓存 _edgeprof***"
                         " · **模型文件 tts_qwen3/asr_models** ✓ —— 需要就加参数重新备份 ✓"
                         " 本清单如实记录每个原因跳过了多少个 ✓"),
            }, ensure_ascii=False, indent=2))
    except OSError as e:
        return {"ok": False, "path": "", "files": 0, "bytes": 0, "skipped": skipped,
                "reason": f"打包失败：{type(e).__name__}: {e}"}

    return {"ok": True, "path": str(out), "files": len(packed), "bytes": total,
            "skipped": skipped[:50], "skipped_count": len(skipped),
            "skipped_bytes": skipped_bytes, "skipped_by_reason": by_reason,
            "reason": f"已打包 {len(packed)} 个文件（{total / 1048576:.1f} MB）✓"
                      + (f"　跳过 {len(skipped)} 个（约 {skipped_bytes / 1048576:.1f} MB，"
                         "原因见清单 ✓）" if skipped else "")}


def restore_backup(backup_zip: str | pathlib.Path, data_dir: str | pathlib.Path, *,
                   stamp: str | None = None) -> dict:
    """从 zip 恢复 ✓ —— ★ **先把现有数据挪走** ✗ 再解包 ✓ 挪走的那份可人工找回 ✓。

    返回 {ok, restored, moved_aside, reason} ✓ **永不抛** ✗
    """
    z = pathlib.Path(backup_zip)
    dst = pathlib.Path(data_dir)
    if not z.is_file():
        return {"ok": False, "restored": 0, "moved_aside": "", "reason": f"备份文件不存在：{z}"}
    if not zipfile.is_zipfile(z):
        return {"ok": False, "restored": 0, "moved_aside": "", "reason": "这不是一个有效的 zip 备份 ✗"}
    try:
        with zipfile.ZipFile(z) as zf:
            names = zf.namelist()
            if MANIFEST not in names:
                return {"ok": False, "restored": 0, "moved_aside": "",
                        "reason": f"备份里没有 {MANIFEST} ✗ —— 这不像本应用的备份 ✓ 拒绝恢复 ✗"}
            # ★ zip slip 防护：任何越界路径一律拒绝整包 ✗（这是安全底线 ✓）
            for n in names:
                if n.startswith("/") or ".." in pathlib.PurePosixPath(n).parts:
                    return {"ok": False, "restored": 0, "moved_aside": "",
                            "reason": f"备份里有越界路径 ✗ 拒绝恢复：{n[:80]}"}
    except (OSError, zipfile.BadZipFile) as e:
        return {"ok": False, "restored": 0, "moved_aside": "", "reason": f"读不了备份：{e}"}

    moved = ""
    try:
        if dst.exists() and any(dst.iterdir()):
            moved_path = dst.parent / f"_pre_restore_{stamp or datetime.now().strftime('%Y%m%d-%H%M%S')}"
            n = 1
            while moved_path.exists():          # 一级碰撞也要让开 ✓（照抄快照那边的做法 ✓）
                moved_path = dst.parent / (moved_path.name + f"-{n}")
                n += 1
            shutil.move(str(dst), str(moved_path))   # ★ 挪走 ✓ 不是删 ✗
            moved = str(moved_path)
        dst.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(z) as zf:
            zf.extractall(dst)
    except OSError as e:
        # ★ 走到这儿说明可能已经挪走了数据却没解包成 ✗ ⇒ 必须把挪走那份**说清楚在哪** ✓
        return {"ok": False, "restored": 0, "moved_aside": moved,
                "reason": f"恢复失败：{type(e).__name__}: {e}"
                          + (f"　★ 你原来的数据没丢 ✓ 在：{moved} ✓ 可人工搬回 ✓" if moved else "")}
    return {"ok": True, "restored": 1, "moved_aside": moved,
            "reason": "恢复完成 ✓" + (f"　原数据已挪到：{moved} ✓（确认没问题后可自行删除 ✓）"
                                      if moved else "")}
