# -*- coding: utf-8 -*-
"""备份不再卡死 + 浏览器缓存可清（★ 2026-10-10 实测炸出来的那两件事）。

## 现场（原样）

```
点一次 GET /api/v1/backup  ⇒ 后端整个失去响应 ✗（连 GET /version 都超时 ✓）
生成的 zip 从 68 字节一路涨到 3.01 GB ✗（还在写 ⇒ 被我一重启打断 ⇒ BadZipFile 打不开 ✓）
根因：data = 7.9 GB / 30070 个文件（_edgeprof* 约 2 GB + 模型目录约 4 GB ✓）
      而打包是**在异步接口里同步跑的** ✗ ⇒ 事件循环被占死 ✓
```

## 本文件钉七件事

  ① 默认**跳过大件**：`_edgeprof*` 浏览器缓存 ✓ `tts_qwen3`/`asr_models` 模型 ✓
     以及 `_backups_`（上一次的备份 ✗ 别把备份打进备份 ✓）
  ② `include_heavy=True` 时**全都打进去** ✓（给"我要完整搬走"的人 ✓）
  ③ 清单里**如实报**：跳过了多少个、**省了多少 MB**、按原因分类 ✓
  ④ 打包**不在事件循环里跑** ✗（源码锚点：`asyncio.to_thread`）✓
  ⑤ 清理**只删 `_edgeprof*`** ✗ 普通目录一个字节都不碰 ✓
  ⑥ 清理**只在 data 目录里**动 ✗（外面的同名目录不碰 ✓ 防路径穿越 ✓）
  ⑦ 删不掉的（占用/权限）**如实报** ✗ 不假装清干净 ✓
"""
from __future__ import annotations

import pathlib
import zipfile

from app import backup, housekeeping

SRC = pathlib.Path(__file__).resolve().parents[1] / "app"


def _tree(tmp: pathlib.Path) -> pathlib.Path:
    """造一棵跟真实 data 同形的假树 ✓"""
    root = tmp / "data"
    (root / "tasks" / "task_x" / "workspace" / "_edgeprof" / "Default").mkdir(parents=True)
    (root / "tasks" / "task_x" / "workspace" / "_edgeprof" / "Default" / "Cache").write_bytes(b"x" * 5000)
    (root / "tasks" / "task_x" / "workspace" / "_edgeprof2").mkdir(parents=True)
    (root / "tasks" / "task_x" / "workspace" / "_edgeprof2" / "f").write_bytes(b"y" * 3000)
    (root / "tasks" / "task_x" / "workspace" / "结果.md").write_text("正常产物 ✓", "utf-8")
    (root / "tasks" / "task_x" / "snapshots" / "s1").mkdir(parents=True)
    (root / "tasks" / "task_x" / "snapshots" / "s1" / "旧.md").write_text("快照", "utf-8")
    (root / "tts_qwen3").mkdir(parents=True)
    (root / "tts_qwen3" / "model.bin").write_bytes(b"z" * 9000)
    (root / "_backups_").mkdir(parents=True)
    (root / "_backups_" / "backup-上回.zip").write_bytes(b"o" * 7000)
    return root


# ═══ ① 默认跳过大件 ═══

def test_skip_reason_covers_the_heavy_stuff():
    assert "edgeprof" in (backup._skip_reason(
        "tasks/t/workspace/_edgeprof/Cache/x", ["tasks", "t", "workspace", "_edgeprof", "Cache", "x"],
        include_snapshots=False, include_audio=False, include_heavy=False) or "")
    assert "模型" in (backup._skip_reason(
        "tts_qwen3/model.bin", ["tts_qwen3", "model.bin"],
        include_snapshots=False, include_audio=False, include_heavy=False) or "")
    assert "历史备份" in (backup._skip_reason(
        "_backups_/a.zip", ["_backups_", "a.zip"],
        include_snapshots=False, include_audio=False, include_heavy=False) or "")
    # 正常产物不跳 ✓
    assert backup._skip_reason("tasks/t/结果.md", ["tasks", "t", "结果.md"],
                               include_snapshots=False, include_audio=False,
                               include_heavy=False) is None


def test_backup_skips_heavy_by_default(tmp_path):
    root = _tree(tmp_path)
    out = tmp_path / "b.zip"
    got = backup.make_backup(root, out)
    assert got["ok"], got
    names = zipfile.ZipFile(out).namelist()
    assert "tasks/task_x/workspace/结果.md" in names, names
    assert not any("_edgeprof" in n for n in names), "浏览器缓存被打进去了 ✗"
    assert not any("tts_qwen3" in n for n in names), "模型文件被打进去了 ✗"
    assert not any("_backups_" in n for n in names), "上一次的备份被打进这次了 ✗"
    assert got["skipped_bytes"] > 0, "没如实统计省了多少 ✗"
    assert got["skipped_by_reason"], "没按原因分类 ✗"


def test_full_backup_includes_them(tmp_path):
    root = _tree(tmp_path)
    out = tmp_path / "full.zip"
    got = backup.make_backup(root, out, include_snapshots=True, include_audio=True, include_heavy=True)
    assert got["ok"], got
    names = zipfile.ZipFile(out).namelist()
    assert any("_edgeprof" in n for n in names), "include_heavy=True 却没打进去 ✗"
    assert any("tts_qwen3" in n for n in names), names


def test_manifest_tells_the_truth_about_skips(tmp_path):
    import json
    root = _tree(tmp_path)
    out = tmp_path / "b.zip"
    backup.make_backup(root, out)
    man = json.loads(zipfile.ZipFile(out).read(backup.MANIFEST).decode("utf-8"))
    assert man["skipped_bytes"] > 0 and man["skipped_by_reason"], man
    assert "edgeprof" in man["note"], "清单里没写明跳过了浏览器缓存 ✗"


def test_backup_never_packs_itself(tmp_path):
    root = _tree(tmp_path)
    out = root / "_backups_" / "self.zip"          # ★ 就写在 data 里（真实端点就是这么干的 ✓）
    got = backup.make_backup(root, out)
    assert got["ok"], got
    assert "MANIFEST.json" in zipfile.ZipFile(out).namelist(), "包坏了 ✗"


# ═══ ④ 打包不在事件循环里跑 ═══

def test_endpoint_runs_backup_in_a_thread():
    src = (SRC / "main.py").read_text("utf-8")
    i = src.index("async def download_backup")
    body = src[i:i + 1800]
    assert "asyncio.to_thread" in body, (
        "备份又在异步接口里同步跑了 ✗ —— 实测会占死事件循环 ⇒ 整个后端失去响应 ✓")
    assert "include_heavy=full" in body, "full=true 没把大件带上 ✗"


# ═══ ⑤⑥⑦ 浏览器缓存清理 ═══

def test_scan_counts_only_profiles(tmp_path):
    root = _tree(tmp_path)
    st = housekeeping.scan(root)
    assert st["dirs"] == 2, st          # _edgeprof + _edgeprof2 ✓（不含其它 ✓）
    assert st["files"] == 2 and st["bytes"] == 8000, st


def test_clean_removes_only_profiles(tmp_path):
    root = _tree(tmp_path)
    got = housekeeping.clean(root)
    assert got["ok"] and got["removed"] == 2, got
    assert got["freed_bytes"] == 8000 and got["failed_count"] == 0, got
    assert not list(root.rglob("_edgeprof*")), "还有没删掉的 ✗"
    assert (root / "tasks" / "task_x" / "workspace" / "结果.md").exists(), "普通产物被误删了 ✗✗"
    assert (root / "tts_qwen3" / "model.bin").exists(), "模型文件被误删了 ✗✗"
    assert (root / "_backups_" / "backup-上回.zip").exists(), "上一次的备份被误删了 ✗✗"


def test_clean_never_touches_outside_data(tmp_path):
    root = _tree(tmp_path)
    outside = tmp_path / "_edgeprof_outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("不许碰我", "utf-8")
    housekeeping.clean(root)
    assert outside.exists() and (outside / "keep.txt").exists(), "动了 data 目录外面的东西 ✗✗"


def test_clean_reports_what_it_could_not_delete(tmp_path, monkeypatch):
    import shutil as _sh
    root = _tree(tmp_path)
    real = _sh.rmtree

    def fake(p, *a, **k):
        if "_edgeprof2" in str(p):
            raise OSError("正被占用")
        return real(p, *a, **k)

    monkeypatch.setattr(housekeeping.shutil, "rmtree", fake)
    got = housekeeping.clean(root)
    assert got["removed"] == 1 and got["failed_count"] == 1, got
    assert "没删掉" in got["reason"], "删不掉却不说 ✗（诚实优先）"


def test_empty_scan_is_honest(tmp_path):
    (tmp_path / "data").mkdir()
    assert "没有" in housekeeping.scan(tmp_path / "data")["note"]
    got = housekeeping.clean(tmp_path / "data")
    assert got["removed"] == 0 and "没动任何东西" in got["reason"], got
