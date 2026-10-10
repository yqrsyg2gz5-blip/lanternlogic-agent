# -*- coding: utf-8 -*-
"""`scripts/export_public.py` 的输出编码红线（2026-10-10 实测踩到）。

事故：zh-CN 的 GBK 控制台下，末尾那句 `print("✓ 导出完成…")` 抛
      UnicodeEncodeError ⇒ 脚本以**非 0 退出**、末句打不出来
      —— 而文件其实早拷完了：报错吓人、结果没事，看的人只会以为导出失败 ✗。

判据（走"目标目录非空 → 拒绝覆盖"这条早退路 ✓ 不碰 git ✓ 红绿副本里也跑得起来）：
      GBK 控制台下必须 **退出码 2** + **没有 Traceback** + **把原因说出来**。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "export_public.py"


def test_refusal_survives_gbk_console(tmp_path):
    target = tmp_path / "not-empty"
    target.mkdir()
    (target / "x.txt").write_text("x", encoding="utf-8")

    env = {**os.environ, "PYTHONIOENCODING": "gbk"}
    r = subprocess.run([sys.executable, str(SCRIPT), "--out", str(target)],
                       capture_output=True, text=True, encoding="gbk", errors="replace",
                       env=env, timeout=120)

    assert r.returncode == 2, (
        f"应当以 2 拒绝覆盖，实际 {r.returncode}（1 = 被 UnicodeEncodeError 崩掉了）\n"
        f"STDOUT:{r.stdout}\nSTDERR:{r.stderr}")
    assert "UnicodeEncodeError" not in (r.stdout + r.stderr), "输出又崩在编码上了"
    assert "拒绝覆盖" in r.stdout, "拒绝了却没说清原因（用户看不到为什么）"
