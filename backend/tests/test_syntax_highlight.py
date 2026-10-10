# -*- coding: utf-8 -*-
"""★ 2026-10-06：代码块语法高亮的**行为测试**。

为什么这么写：高亮器是前端的纯 JS（`frontend/src/lib/tinyHighlight.js`，无依赖 ✓），
代码库里没有前端测试运行器 ✗ —— 但这个函数是纯的、可直接用 **node** 跑 ✓，
所以这里起一个 node 子进程做**真实行为断言**（不是"文件里有没有这个词"的弱锚点 ✗）。

不引 100KB 的 highlight.js，是用户对体积敏感的决定 ✓；那就要自己证明它**真的能用** ✓
（不能只写一句"我做了高亮"就算了 ✗）。
"""
import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
HL = ROOT / "frontend" / "src" / "lib" / "tinyHighlight.js"
NODE = shutil.which("node")


def _run(code: str, lang: str) -> list[dict[str, str]]:
    if NODE is None:
        pytest.skip("没有 node，跑不了行为验证")
    script = (
        f"import('{HL.as_uri()}').then(m => {{"
        f"  console.log(JSON.stringify(m.tokenize({json.dumps(code)}, {json.dumps(lang)})));"
        f"}});"
    )
    out = subprocess.run([NODE, "--input-type=module", "-e", script],
                         capture_output=True, text=True, timeout=60,
                         # ★ 必须显式指定 UTF-8 ✗：Windows 上默认按 GBK 解码 ⇒
                         #   node 吐回的中文直接解码失败（本班踩过）✓
                         encoding="utf-8", errors="replace")
    assert out.returncode == 0, out.stderr[-500:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_python_highlight_marks_the_right_kinds():
    src = 'def add(a, b):\n    """doc"""\n    return a + b  # plus\n'
    toks = _run(src, "python")
    kinds = {t["k"] for t in toks}
    assert "kw" in kinds and "com" in kinds, toks
    kws = [t["t"] for t in toks if t["k"] == "kw"]
    assert "def" in kws and "return" in kws, kws
    # 函数名（后面跟括号）要被认出来
    assert any(t["k"] == "fn" and t["t"] == "add" for t in toks), toks
    # 注释整段是一块
    com = "".join(t["t"] for t in toks if t["k"] == "com")
    assert com.strip() == "# plus", com
    # 高亮**不能吃掉或改动字符** —— 拼回去必须和原文一模一样 ✓（错了比不高亮更难看 ✗）
    assert "".join(t["t"] for t in toks) == src


def test_roundtrip_is_lossless_for_non_ascii_too():
    """中文/emoji 也必须原样拼回 ✓（交付里到处是中文，掉了字就毁了 ✗）。"""
    src = 'x = "中文"  # 注释里有中文和 emoji ✅\n'
    toks = _run(src, "python")
    assert "".join(t["t"] for t in toks) == src


def test_js_highlight_and_lossless_roundtrip():
    src = 'const n = 42;\n// hi\nfunction go() { return "ok"; }\n'
    toks = _run(src, "js")
    assert "".join(t["t"] for t in toks) == src, "拼回去必须和原文一致 ✓"
    assert any(t["t"] == "const" and t["k"] == "kw" for t in toks), toks
    assert any(t["t"] == "42" and t["k"] == "num" for t in toks), toks
    assert any(t["k"] == "str" and "ok" in t["t"] for t in toks), toks


def test_unknown_language_is_left_alone():
    """不认识的语言 ⇒ **原样返回** ✓（宁可不高亮，也不要错着高亮 ✗）。"""
    src = "some ~~~ weird ### text"
    toks = _run(src, "brainfuck")
    assert len(toks) == 1 and toks[0]["k"] == "" and toks[0]["t"] == src, toks


def test_component_wires_the_highlighter_and_css_has_token_colors():
    md = (ROOT / "frontend" / "src" / "components" / "Markdown.tsx").read_text("utf-8")
    css = (ROOT / "frontend" / "src" / "styles.css").read_text("utf-8")
    assert "tokenize(" in md and "tok-" in md, "代码块没接高亮"
    for k in ("com", "str", "num", "kw", "fn"):
        assert f".tok-{k}" in css, f"缺 .tok-{k} 样式"
