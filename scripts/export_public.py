# -*- coding: utf-8 -*-
"""把仓库导出成一份**可直接上传的干净副本** ✓（AGPL 第 13 条要源码 ✓ 但不要你的内部记录 ✗）

## 为什么需要它（不是洁癖 ✗）

体检发现三类东西**跟着历史一起在仓库里** ✓ 就算从最新提交里删掉 ✓
**git 历史里还在** ✗（别人 clone 下来照样翻得到 ✓）：

  1. `docs/collab/**`  —— 班次记录：里面有**开发机的绝对路径**、内部审查过程、
      AI 协作话术、以及"哪些坑踩过"的复盘 ✓ 那是**你的工作笔记** ✗ 不是给用户看的 ✓
  2. `HANDOVER.md` —— 交接书：写明"当前进度 / 下一步 / 谁欠谁" ✓ 同上 ✗
  3. `AUDIT_REPORT.md` —— 内部审计报告 ✓ 同上 ✗

★ 光在最新提交里删**没用** ✗ —— 历史里还有 ✓
  ⇒ 正确做法：**导出一份干净副本**（只含该公开的文件 ✓ 全新 git 历史 ✓ 零泄漏 ✓）
  ⇒ 然后在那份副本里 `git init` + push ✓

## 用法

    python scripts\\export_public.py --out D:\\LanternLogicAgent-public

它会：按 `git ls-files` 取**被跟踪**的文件 ✓ 减去排除清单 ✓ 拷到目标目录 ✓
      并写一份 `EXPORT-README.txt` 说明这份副本是怎么来的 ✓
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _out(text: str) -> None:
    """按控制台自己的编码打印 ✓（zh-CN 的 GBK 控制台也不会因为 ✓ / ✗ / ★ 崩掉 ✗）。

    ★ 2026-10-10 实测踩到：末尾那句 `print("✓ 导出完成…")` 在 GBK 控制台抛
      UnicodeEncodeError ⇒ 脚本以**非 0 退出**、末句打不出来 ✓
      （文件其实早拷完了 —— 报错吓人、结果没事 ✗ 更糟：看的人以为导出失败了 ✓）。
    口径与 `scripts/preflight.py` 的 `_out` 一致：编不出的字符降级成 `?` ✓ 绝不抛 ✓。
    """
    buf = getattr(sys.stdout, "buffer", None)
    if buf is None:                       # 被测试/宿主换过 stdout ⇒ 退回普通 print
        print(text)
        return
    enc = sys.stdout.encoding or "utf-8"
    buf.write((text + chr(10)).encode(enc, errors="replace"))
    buf.flush()

#: ★ 不公开的（内部工作记录 ✓ 见文件头那段"为什么"）
EXCLUDE_PREFIX = ("docs/collab/",)
EXCLUDE_EXACT = {
    "HANDOVER.md",
    "AUDIT_REPORT.md",
    # （那 3 张真机截图已于 2026-10-09 从仓库撤下 ✓ 重拍后再放回 ✓
    #   到时候若还没拍，这里要重新加回排除 ✓）
}


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True).stdout
    return [f for f in out.decode("utf-8", "replace").split("\0") if f.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description="导出可上传的干净副本")
    ap.add_argument("--out", required=True, help="目标目录（不存在则创建 ✓ 已存在且非空则拒绝 ✗）")
    ap.add_argument("--include-internal", action="store_true", help="连内部文档一起导（默认不导 ✓）")
    a = ap.parse_args()
    out = pathlib.Path(a.out).resolve()
    if out.exists() and any(out.iterdir()):
        _out(f"✗ 目标目录非空，拒绝覆盖：{out}")
        return 2

    files = tracked()
    kept, skipped = [], []
    for f in files:
        if not a.include_internal and (f.startswith(EXCLUDE_PREFIX) or f in EXCLUDE_EXACT):
            skipped.append(f)
            continue
        src = ROOT / f
        if not src.is_file():
            skipped.append(f)
            continue
        dst = out / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        kept.append(f)

    (out / "EXPORT-README.txt").write_text(
        "这份副本由 scripts/export_public.py 导出 ✓\n"
        f"源仓库：{ROOT}\n"
        f"文件数：{len(kept)}（已排除 {len(skipped)} 个内部文件 ✓）\n\n"
        "下一步：\n"
        "  cd <这个目录> && git init && git add -A && git commit -m 'first public release'\n"
        "  git remote add origin <你的仓库地址> && git push -u origin main\n\n"
        "★ 别忘了把仓库地址填进 README.md / backend/app/version.py(SOURCE_URL) / NOTICE ✓\n"
        "  （AGPL 第 13 条要求用网络访问的人能拿到源码 ✓）\n",
        encoding="utf-8")

    _out(f"✓ 导出完成：{out}")
    _out(f"   带上 {len(kept)} 个文件 ✓ 排除 {len(skipped)} 个 ✗")
    for f in skipped[:20]:
        _out(f"     - {f}")
    if not a.include_internal:
        _out("")
        _out("★ 这份副本**不含 git 历史** ✓ 所以内部记录不会被翻出来 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
