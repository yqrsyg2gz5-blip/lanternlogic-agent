# -*- coding: utf-8 -*-
"""一键备份 / 恢复 —— 用**真目录真 zip**当考卷 ✓（不 mock ✗）

三条要害各有一条测试守着：
  ① ★ 恢复前**先把现有数据挪走** ✗（挪走的必须还在 ✓ 不然"恢复失败"= 数据没了 ✗）
  ② ★ 清单里**如实写跳过了什么** ✗（不能让用户以为备份是全的 ✓）
  ③ ★ zip slip（`../` 越界路径）**整包拒绝** ✗（这是安全底线 ✓）
"""
from __future__ import annotations

import json
import pathlib
import zipfile

from app.backup import MANIFEST, make_backup, restore_backup


def _seed_data(tmp_path) -> pathlib.Path:
    d = tmp_path / "data"
    (d / "tasks" / "task_1" / "tts").mkdir(parents=True)
    (d / "tasks" / "task_1" / "events.jsonl").write_text('{"a":1}\n', encoding="utf-8")
    (d / "tasks" / "task_1" / "tts" / "a.mp3").write_bytes(b"\x00" * 100)
    (d / "memory").mkdir(parents=True)
    (d / "memory" / "m.json").write_text("[]", encoding="utf-8")
    (d / "tasks" / "task_1" / "snapshots" / "20261009T000000").mkdir(parents=True)
    (d / "tasks" / "task_1" / "snapshots" / "20261009T000000" / "big.bin").write_bytes(b"\x00" * 500)
    (d / "_cleared_20261001").mkdir(parents=True)
    (d / "_cleared_20261001" / "old.json").write_text("{}", encoding="utf-8")
    return d


def test_backup_packs_the_real_data_and_writes_a_manifest(tmp_path):
    """真打包 ✓ 清单在 ✓ 条数对得上 ✓"""
    d = _seed_data(tmp_path)
    out = tmp_path / "backup.zip"
    got = make_backup(d, out)
    assert got["ok"] is True, got
    assert out.is_file() and out.stat().st_size > 0
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert MANIFEST in names
        man = json.loads(z.read(MANIFEST).decode("utf-8"))
        assert man["files"] == len([n for n in names if n != MANIFEST]), "清单里的条数与实际不符 ✗"
        assert "memory/m.json" in names and "tasks/task_1/events.jsonl" in names


def test_backup_skips_the_heavy_stuff_and_says_so(tmp_path):
    """★ 跳过的东西**必须在清单里如实写出来** ✗（不然用户以为备份是全的 ✓）"""
    d = _seed_data(tmp_path)
    out = tmp_path / "backup.zip"
    got = make_backup(d, out)
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert not any("snapshots" in n for n in names), "快照（备份的备份）不该进默认包 ✗"
        assert not any(n.endswith(".mp3") for n in names), "朗读音频默认不进包 ✗"
        man = json.loads(z.read(MANIFEST).decode("utf-8"))
    assert man["skipped_count"] > 0, "跳过了东西却没记 ✗"
    assert got["skipped_count"] == man["skipped_count"], "返回值与清单不一致 ✗"
    assert "跳过" in got["reason"], "话里要告诉用户'有跳过' ✓"


def test_backup_can_include_everything_when_asked(tmp_path):
    """显式要全量 ⇒ 快照与音频都进包 ✓（跳过是默认 ✗ 不是能力缺失 ✓）"""
    d = _seed_data(tmp_path)
    out = tmp_path / "full.zip"
    got = make_backup(d, out, include_snapshots=True, include_audio=True)
    assert got["ok"] is True
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    assert any("snapshots" in n for n in names)
    assert any(n.endswith(".mp3") for n in names)
    assert got["skipped_count"] == 0, got


def test_restore_moves_existing_data_aside_instead_of_deleting(tmp_path):
    """★★ 最重要的一条：恢复**先把现有数据挪走** ✗ —— 挪走那份必须还在 ✓"""
    d = _seed_data(tmp_path)
    out = tmp_path / "b.zip"
    make_backup(d, out)
    # 备份之后，用户又产生了**新数据**（恢复会把它换掉 ✓ 但必须留住 ✓）
    (d / "memory" / "new_after_backup.json").write_text('{"new":1}', encoding="utf-8")

    got = restore_backup(out, d, stamp="20261009-2359")
    assert got["ok"] is True, got
    assert got["moved_aside"], "没报告挪到哪去了 ✗"
    moved = pathlib.Path(got["moved_aside"])
    assert moved.is_dir(), "挪走的那份不在了 ⇒ 等于删了 ✗"
    assert (moved / "memory" / "new_after_backup.json").is_file(), \
        "备份之后产生的新数据没被留住 ✗（那就成了'恢复=丢数据'✗）"
    assert (d / "memory" / "m.json").is_file(), "恢复后的数据不全 ✗"


def test_restore_refuses_a_zip_that_is_not_our_backup(tmp_path):
    """不是本应用的备份 ⇒ 拒绝 ✓（宁可不恢复 ✓ 也不乱解包 ✗）"""
    d = tmp_path / "data"
    d.mkdir()
    (d / "mine.json").write_text("keep me", encoding="utf-8")
    other = tmp_path / "other.zip"
    with zipfile.ZipFile(other, "w") as z:
        z.writestr("hello.txt", "hi")
    got = restore_backup(other, d, stamp="x")
    assert got["ok"] is False, got
    assert MANIFEST in got["reason"], "要说清为什么拒绝 ✓"
    assert (d / "mine.json").read_text(encoding="utf-8") == "keep me", "拒绝时不许动数据 ✗"


def test_restore_refuses_zip_slip_paths(tmp_path):
    """★ 安全底线：`../` 越界路径 ⇒ **整包拒绝** ✗（一个文件都不许解出来 ✓）"""
    d = tmp_path / "data"
    d.mkdir()
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr(MANIFEST, "{}")
        z.writestr("../../pwned.txt", "boom")
    got = restore_backup(evil, d, stamp="x")
    assert got["ok"] is False, got
    assert "越界" in got["reason"], got
    assert not (tmp_path / "pwned.txt").exists()
    assert not (tmp_path.parent / "pwned.txt").exists(), "越界文件被写出来了 ✗"


def test_backup_of_a_missing_dir_is_an_honest_failure(tmp_path):
    """目录不存在 ⇒ 明确说不行 ✓ **不许抛** ✗ 也不许假装成功 ✗"""
    got = make_backup(tmp_path / "nope", tmp_path / "x.zip")
    assert got["ok"] is False and "不存在" in got["reason"]
