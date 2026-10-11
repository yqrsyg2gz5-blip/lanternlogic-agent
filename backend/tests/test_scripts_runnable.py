# -*- coding: utf-8 -*-
"""★ 2026-10-06：仓库脚本在**中文 Windows 控制台**上必须能跑完（不能因为 emoji 崩 ✗）。

现场：开源前的"密钥体检"这一步，`scripts/scan_git_history_secrets.py` 一 print `⚠️` 就
`UnicodeEncodeError: 'gbk' codec can't encode character '\\u26a0'` 直接退出 ✗
—— 也就是说**这一步在新机器上根本跑不起来** ✓（而它恰恰是开源前必做的一步 ✓）。

修法：凡是会打印 emoji/中文的脚本，先把 stdout/stderr 切到 UTF-8（不可编码字符降级 ✓）。
这条测试用**子进程**真跑一遍，退出码必须是 0 ✓（不是"文件里有没有那句话"的弱锚点 ✗）。
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_secret_scanner_runs_without_encoding_crash():
    script = ROOT / "scripts" / "scan_git_history_secrets.py"
    assert script.exists(), "密钥扫描脚本不见了"
    out = subprocess.run([sys.executable, str(script), "--limit", "3"],
                         capture_output=True, text=True, timeout=300,
                         encoding="utf-8", errors="replace", cwd=str(ROOT))
    assert out.returncode == 0, f"脚本崩了（多半又是控制台编码 ✗）：\n{out.stderr[-800:]}"
    assert "扫描范围" in out.stdout, out.stdout[:300]
