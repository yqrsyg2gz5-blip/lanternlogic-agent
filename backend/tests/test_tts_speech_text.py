# -*- coding: utf-8 -*-
"""朗读前必须把 Markdown 记号剥掉（用户 2026-10-10 实测换来的）。

用户原话："他念一念，老是把一些符号什么星号、星号下划线这些都给念出来，这不能只念字吗"
原因：Agent 的回答是 Markdown，而朗读会把**原文**照着念 ⇒ `**重点**` 变成
      "星号星号重点星号星号"。

红线（本文件盯三条）：
    ① 记号必须剥干净（星号 / 下划线 / 井号 / 井号标题 / 反引号 / 表格竖线 / emoji）
    ② **正文一个字都不能删**（链接留文字、代码块留内容、标点与数字原样）
    ③ 结构保证：剥的这一步在**基类 generate()** 里 ⇒ 任何后端都绕不过去
"""
from __future__ import annotations

import asyncio

import pytest

from app.tts import _TTS_REGISTRY, TTSBackend, to_speech_text


@pytest.mark.parametrize("raw, want", [
    ("**重点**", "重点"),
    ("__重点__", "重点"),
    ("*斜体* 和 _下划线_", "斜体 和 下划线"),
    ("~~删掉~~", "删掉"),
    ("`code`", "code"),
    ("# 一级标题\n## 二级标题", "一级标题\n二级标题"),
    ("- 第一项\n- 第二项", "第一项\n第二项"),
    ("* 星号列表\n+ 加号列表", "星号列表\n加号列表"),
    ("> 引用一句话", "引用一句话"),
    ("---", ""),
    ("***", ""),
    ("1. 第一步\n2. 第二步", "1. 第一步\n2. 第二步"),
    ("看这里 [官网](https://example.com/a?b=1) 哦", "看这里 官网 哦"),
    ("![图](https://x/y.png)说明文字", "说明文字"),
    ("| 列A | 列B |\n|---|---|\n| 1 | 2 |", "列A 列B\n1 2"),
    ("裸网址 https://example.com/x 去掉", "裸网址 去掉"),
    ("★★ 小标题 ✓ ✗ 🛟", "小标题"),
    ("```python\nprint(1)\n```", "print(1)"),
    ("你好，这是一句普通话。", "你好，这是一句普通话。"),
    ("", ""),
])
def test_markdown_markers_are_stripped(raw, want):
    assert to_speech_text(raw) == want


def test_body_text_is_never_dropped():
    """★ 反面红线：只剥记号 —— 正文、标点、数字一个都不能少。"""
    body = "今天做了 3 件事：写代码、跑测试、写文档。"
    assert body in to_speech_text(f"## 汇报\n\n**{body}**\n\n- 完")


def test_no_marker_survives_in_a_real_answer():
    """拿一段"像 Agent 真的会写的"回答过一遍：记号字符一个都不许剩。"""
    answer = ("# 结论\n\n**已完成** ✓ 代码见 `app/tts.py`：\n\n"
              "- 第一项 _重点_\n- 第二项 ~~旧的~~\n\n"
              "[仓库](https://gitee.com/yangbo0801/lanternlogic-agent) | 见附表\n")
    out = to_speech_text(answer)
    for ch in "*_#`|~[]()":
        assert ch not in out, f"记号 {ch!r} 还在：{out!r}"
    assert "已完成" in out and "第一项" in out and "仓库" in out


def test_base_generate_applies_cleaning(tmp_path):
    """★ 结构保证：清理在基类 ⇒ 子类只实现 _synthesize，绕不过去。"""
    seen: dict[str, str] = {}

    class Dummy(TTSBackend):
        async def _synthesize(self, text: str, output_path):  # noqa: ANN001
            seen["text"] = text
            output_path.write_bytes(b"x")
            return output_path

    p = tmp_path / "a.wav"
    asyncio.run(Dummy().generate("**念这个** `别念记号`", p))
    assert seen["text"] == "念这个 别念记号"


def test_every_registered_backend_inherits_the_cleaning():
    """每个真后端都必须是"基类 generate + 自己的 _synthesize"，不许自己重写 generate。"""
    assert _TTS_REGISTRY, "后端表空了？"
    for name, cls in _TTS_REGISTRY.items():
        assert "generate" not in cls.__dict__, f"{name} 自己重写了 generate() ⇒ 会绕过朗读清理"
        assert "_synthesize" in cls.__dict__, f"{name} 没实现 _synthesize()"
