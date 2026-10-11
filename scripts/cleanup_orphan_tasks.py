"""清理"界面上已删、硬盘还在"的任务目录。

背景（实测）：`DELETE /api/v1/tasks/{id}` 曾经只从索引移除、**一个文件都不删** ——
`data/tasks/` 里因此积了 213 个孤儿目录共 470.5 MB，其中还有一个明文出现过 API Key
的任务（用户以为删了，其实原封不动躺在盘上）。接口已修（7d），但**历史遗留需要清一次**。

★ 本脚本默认【只列不删】。要真删必须显式加 `--yes`。
★ 复用 store 的同一套护栏（路径穿越/符号链接/越界一律拒绝）；
   并且**索引为空或损坏时拒绝产出清单**（否则会把在用的任务全判成孤儿）。

用法：
    python scripts/cleanup_orphan_tasks.py                      # 只列清单 + 合计体积
    python scripts/cleanup_orphan_tasks.py --older-than-days 7  # 只列 7 天前的
    python scripts/cleanup_orphan_tasks.py --yes                # 真删（不可逆）
    python scripts/cleanup_orphan_tasks.py --yes --older-than-days 7
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.store import FsStore  # noqa: E402


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def main() -> int:
    ap = argparse.ArgumentParser(description="清理孤儿任务目录（默认只列不删）")
    ap.add_argument("--data-dir", default=None, help="data 目录（默认 backend/data）")
    ap.add_argument("--yes", action="store_true", help="★ 真删（不可逆）。不加就只列清单")
    ap.add_argument("--older-than-days", type=float, default=None,
                    help="只处理最后修改时间早于 N 天的目录")
    args = ap.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else (
        Path(__file__).resolve().parent.parent / "backend" / "data")
    store = FsStore(data_dir)

    orphans = store.orphan_task_dirs()
    if not orphans:
        print("没有孤儿目录（或索引不可用已被拒绝生成清单——见上面的告警）。")
        return 0

    cutoff = time.time() - args.older_than_days * 86400 if args.older_than_days else None
    picked = []
    for o in orphans:
        mtime = Path(o["path"]).stat().st_mtime
        o["mtime"] = mtime
        if cutoff is None or mtime < cutoff:
            picked.append(o)

    total = sum(o["bytes"] for o in orphans)
    ptotal = sum(o["bytes"] for o in picked)
    print(f"孤儿目录：{len(orphans)} 个，合计 {_human(total)}")
    if cutoff is not None:
        print(f"按 --older-than-days {args.older_than_days} 过滤后：{len(picked)} 个，{_human(ptotal)}")
    print()
    print(f"{'任务 id':<26}{'体积':>10}  最后修改")
    for o in sorted(picked, key=lambda x: x["mtime"])[:40]:
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(o["mtime"]))
        print(f"{o['id']:<26}{_human(o['bytes']):>10}  {ts}")
    if len(picked) > 40:
        print(f"…（其余 {len(picked) - 40} 个略）")

    if not args.yes:
        print()
        print("★ 以上只是清单，**没有删除任何东西**。")
        print(f"  确认要删这 {len(picked)} 个目录（释放 {_human(ptotal)}）就加 --yes 再跑一次。")
        return 0

    print()
    print(f"开始删除 {len(picked)} 个目录…")
    freed = 0
    ok = fail = 0
    for o in picked:
        try:
            r = store.delete_task_files(o["id"])
            if r["deleted"]:
                freed += r["bytes"]
                ok += 1
            else:
                fail += 1
                print(f"  未删除：{o['id']}（{r}）")
        except Exception as e:
            fail += 1
            print(f"  ★ 失败 {o['id']}：{type(e).__name__}: {e}")
    print(f"完成：删除 {ok} 个，失败 {fail} 个，释放 {_human(freed)}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
