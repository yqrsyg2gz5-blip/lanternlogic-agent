# -*- coding: utf-8 -*-
"""可选依赖的导入纪律（2026-10-10 **CI 当场抓到的** —— 正是"本地能跑 ≠ 别人能跑"那条）。

事故经过：
    `test_melotts_nltk_quiet.py` 在**模块顶层**裸写 `import nltk` ✓
    本机有 nltk（随 MeloTTS 一起装 ✓）而 CI 的干净环境**没有**（CI 只装
    `requirements-dev.txt` ⇒ 里面没有 nltk ✗）
    ⇒ CI 在**收集阶段**就 ImportError：
          ModuleNotFoundError: No module named 'nltk'
      整轮 pytest 变成"2068 收集 + 1 error"、退出码 1 ✗

判据（本文件盯一条）：
    `backend/tests/**` 里，**可选依赖不许在模块顶层裸导入** ——
    要么 `pytest.importorskip(...)`（缺了如实跳过 ✓ 与"缺件就说缺件"同口径），
    要么放进函数体里再 try/except ✓

★ 为什么只盯这几个包：它们**不在** `backend/requirements.txt` 里，
  是随本地引擎/模型一起装的（MeloTTS / 千问3-TTS / 本地 ASR / 本地出图 …）。
"""
from __future__ import annotations

import ast
import pathlib

#: 可选依赖（不在 requirements.txt 里 ⇒ CI 上不存在 ⇒ 顶层裸导入必炸）
OPTIONAL = frozenset({
    "nltk", "torch", "torchaudio", "edge_tts", "pyttsx3", "melo", "qwen_tts",
    "faster_whisper", "modelscope", "soundfile", "imageio_ffmpeg", "fastembed",
})

TESTS_DIR = pathlib.Path(__file__).resolve().parent


def _top_level_imports(tree: ast.Module) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for node in tree.body:                      # ★ 只看模块顶层（函数体内的不算 ✓）
        if isinstance(node, ast.Import):
            out += [(node.lineno, a.name.split(".")[0]) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append((node.lineno, node.module.split(".")[0]))
    return out


def test_optional_deps_are_not_imported_at_module_level():
    bad = []
    for f in sorted(TESTS_DIR.glob("test_*.py")):
        tree = ast.parse(f.read_text("utf-8"))
        for lineno, name in _top_level_imports(tree):
            if name in OPTIONAL:
                bad.append(f"{f.name}:{lineno} 顶层 import {name}")
    assert not bad, (
        "可选依赖不许在模块顶层裸导入（CI 上没装 ⇒ 收集阶段就炸、整轮 pytest 退出码 1 ✗）：\n  "
        + "\n  ".join(bad)
        + "\n  改用 pytest.importorskip(\"xxx\") ✓ 或放进函数体里 ✓"
    )
