"""扫 git **历史**（不只是当前文件）里有没有密钥/凭据 —— 开源前必做。

为什么必须扫历史：一旦推到 GitHub，**删掉当前文件是没用的** —— 历史里的 blob 还在，
别人可以 fork、缓存、用 `git log -p` 翻出来。所以"现在干净"不等于"推出去安全"。

★ 本脚本**只输出计数与所在提交/文件路径，绝不打印密钥明文**（命中处只显示前 4 位 + 长度）。

用法：
    python scripts/scan_git_history_secrets.py            # 全量历史
    python scripts/scan_git_history_secrets.py --limit 200
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# ★ 2026-10-06：Windows 中文控制台默认用 GBK 编码输出 ⇒ 脚本里的 `⚠️`/`✅` 一 print 就
#   UnicodeEncodeError 崩掉 ✗（本班实测：开源前的"密钥体检"这一步直接跑不起来 ✓）。
#   凡是会打印 emoji 的脚本都要先把 stdout 切到 UTF-8（不能编码的字符降级替换，不再崩 ✓）。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

# 常见密钥形态（与 app/redact.py 同族，但这里独立一份，免得互相影响）
PATTERNS = [
    ("sk- 类", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}")),
    ("AWS AKIA", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("Google API", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}")),
    ("Bearer 长串", re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{24,}")),
    ("私钥头", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("微信/手机号", re.compile(r"\b1[3-9]\d{9}\b")),
]

# 文件名像凭据的
NAME_RE = re.compile(r"(secret|credential|\.env$|id_rsa|\.pem$|\.pfx$|token)", re.I)

SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".tgz",
            ".gz", ".whl", ".exe", ".dll", ".so", ".pyc", ".woff", ".woff2", ".ttf",
            ".mp3", ".wav", ".mp4", ".safetensors", ".bin", ".onnx"}
MAX_BYTES = 2_000_000


def run(*args: str) -> str:
    p = subprocess.run(["git", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.stdout


def mask(s: str) -> str:
    s = s.strip()
    return f"{s[:4]}…（{len(s)} 位）" if len(s) > 6 else "…"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只扫最近 N 个提交")
    args = ap.parse_args()

    revs = run("rev-list", "--all").split()
    if args.limit:
        revs = revs[: args.limit]
    print(f"扫描范围：{len(revs)} 个提交（全部分支）")

    # 每个对象只扫一次（同一文件在多个提交里内容相同）
    seen_blobs: set[str] = set()
    hits: list[tuple[str, str, str, str]] = []   # (类别, 提交, 路径, 命中摘要)
    name_hits: list[tuple[str, str]] = []
    scanned = 0

    for rev in revs:
        out = run("ls-tree", "-r", "-z", rev)
        for entry in out.split("\0"):
            if not entry or "\t" not in entry:
                continue
            meta, path = entry.split("\t", 1)
            parts = meta.split()
            if len(parts) < 3 or parts[1] != "blob":
                continue
            sha = parts[2]
            if sha in seen_blobs:
                continue
            seen_blobs.add(sha)
            if NAME_RE.search(path):
                name_hits.append((rev[:8], path))
            if Path(path).suffix.lower() in SKIP_EXT:
                continue
            blob = subprocess.run(["git", "cat-file", "blob", sha], capture_output=True)
            raw = blob.stdout
            if not raw or len(raw) > MAX_BYTES:
                continue
            scanned += 1
            text = raw.decode("utf-8", errors="replace")
            for label, rx in PATTERNS:
                for m in rx.finditer(text):
                    hits.append((label, rev[:8], path, mask(m.group(0))))

    print(f"扫描 blob 数：{scanned}（去重后）")
    print()
    if name_hits:
        print(f"⚠️ 文件名像凭据的（{len(name_hits)} 处，去重后）：")
        for rev, path in sorted(set(name_hits))[:20]:
            print(f"   {rev}  {path}")
        print()
    if not hits:
        print("★ 内容扫描：**未在历史里发现密钥形态的字符串** ✓")
    else:
        print(f"★ 内容扫描：发现 {len(hits)} 处命中（只显示前 4 位，不打印明文）：")
        for label, rev, path, m in hits[:40]:
            print(f"   [{label}] {rev}  {path}  {m}")
        if len(hits) > 40:
            print(f"   …其余 {len(hits) - 40} 处略")
        print()
        print("⚠️ 处理建议：**别直接删文件了事** —— 历史里还在。要么重写历史"
              "（git filter-repo / BFG），要么**把命中的密钥全部轮换**（更省事更保险）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
