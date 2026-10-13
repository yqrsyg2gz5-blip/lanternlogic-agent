# -*- coding: utf-8 -*-
"""防护测试：技能不许教模型去"扫全盘"（真机事故：用户风扇转了一小时）。

## 事故（2026-10-13）

用户报「风扇一直转」。查进程发现两个孤儿 `find`：

    PID 25200  find / -maxdepth 6 -name tools -type d        烧 63 分钟 CPU
    PID 23188  find / -maxdepth 6 -type d -name 卡点视频      烧 35 分钟 CPU

**从 `/` 开始扫全盘**（C 盘 + D 盘几百万文件）⇒
**各吃满一个核 + 持续疯狂读磁盘 + 永远跑不完**（父进程都退出了它们还在扫）
⇒ 用户机器 CPU 181% / 内存 79% / **风扇不停** ✗

关掉后：CPU 181% → 4%，可用内存 6.8 GB → 18.4 GB，**风扇停了** ✓

## 本文件钉什么

1. 技能文档里**不许出现**"从根目录/盘符开始找"的示例命令
2. 必须**明确写出禁令**（模型想搜的时候能看到"别这么干"）
3. 必须给出**替代做法**（东西在哪里直接去拿 + 只能在**自己工作区**里搜）
"""
from __future__ import annotations

import pathlib
import re

SKILL = pathlib.Path(__file__).resolve().parents[2] / "backend" / "skills" / "卡点视频"

#: 会引发"扫全盘"的危险写法（从根/盘符起扫、递归整盘）
DANGEROUS = (
    re.compile(r"(?<![\w./-])find\s+/(?![\w])"),          # find / …
    re.compile(r"(?<![\w./-])find\s+[A-Za-z]:[\\/]"),       # find D:\ …
    re.compile(r"(?<![\w./-])grep\s+-r\s+/(?![\w])"),       # grep -r / …
    re.compile(r"(?<![\w./-])ls\s+-R\s+/(?![\w])"),         # ls -R / …
    re.compile(r"\bdir\s+/s\b", re.I),                      # cmd 的 dir /s
)


def _doc_files():
    return [f for f in sorted(SKILL.rglob("*.md")) if "node_modules" not in str(f)]


def test_no_whole_disk_search_commands_in_docs():
    """★ 技能文档里**不许**出现"从根目录/盘符起扫"的命令（会烧死用户机器）。

    ★ 例外：**明确标为禁止**的行（同一行或紧邻上文带 ⛔/✗/禁止/别用）不算违规 ——
      因为我们要靠这些"反面例子"告诉模型别这么干。
    """
    offenders = []
    for f in _doc_files():
        lines = f.read_text("utf-8", errors="replace").splitlines()
        for i, line in enumerate(lines, 1):
            if not any(p.search(line) for p in DANGEROUS):
                continue
            ctx = "\n".join(lines[max(0, i - 4):i + 1])
            if any(k in ctx for k in ("禁止", "⛔", "别用", "不要", "✗", "反面", "事故")):
                continue          # 这是"反面例子"，允许
            offenders.append(f"{f.relative_to(SKILL)}:{i}  {line.strip()[:90]}")
    assert not offenders, (
        "技能文档里出现了「扫全盘」命令（会烧死用户机器、风扇狂转）✗：\n  "
        + "\n  ".join(offenders[:6])
    )


def test_docs_explicitly_forbid_whole_disk_scans():
    """★ 必须**明确写出禁令** —— 模型想搜的时候要能看到"别这么干"。"""
    txt = "\n".join(f.read_text("utf-8", errors="replace") for f in _doc_files())
    assert "扫全盘" in txt or "全盘" in txt, "文档里没提「全盘扫描」这件事 ✗"
    assert "find /" in txt, "没把 `find /` 这个具体危险写法点出来做反面例子 ✗"
    # 要说清代价（模型才有理由遵守）
    assert re.search(r"(风扇|CPU|磁盘)", txt), "没说清「扫全盘」的代价（风扇/CPU/磁盘）✗"


def test_docs_give_the_alternative():
    """光禁止不够 —— 必须告诉它**东西在哪、该怎么搜**（否则它还会去乱找）。"""
    txt = "\n".join(f.read_text("utf-8", errors="replace") for f in _doc_files())
    # 替代做法 1：直接去已知位置拿
    assert "tools" in txt, "没说脚本就在本技能包的 tools/ 里 ✗"
    # 替代做法 2：只能在自己工作区里搜（`.` 开头）
    assert re.search(r"find\s+\.\s", txt) or "工作区里找" in txt or "自己的工作目录" in txt, (
        "没给出正确的搜法（限定在自己的工作区，`find . -maxdepth N`）✗"
    )
