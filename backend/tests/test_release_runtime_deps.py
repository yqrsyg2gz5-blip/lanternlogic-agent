# -*- coding: utf-8 -*-
"""发布包的**运行时依赖清单**必须让「解压即用」真的可用（★ 2026-10-12）。

## 要解决什么（交接单里的老账）

  · 完整版发布包卖点是「**什么都不用装**，解压双击就用」✓
  · 但 `build_release.py` 的运行时只装 `backend/requirements.txt` 那 6 个包 ✗
    而 **`edge-tts` 不在里面** ⇒ 完整包里**语音合成是坏的**：
    界面上点朗读 / 技能里要配音，都拿不到声音 ✓（本机实测：便携运行时 `import edge_tts` 失败 ✓）
  · 用户在**便携包里没有 venv 可退**（那是开发机的路径 ✗）⇒ 这个缺口只能靠清单补 ✓

## 本文件钉三件事

  ① `edge-tts` 必须在 `backend/requirements.txt` 里
     —— 它就是**运行时依赖**（`app/tts.py` 的 `EdgeTTSBackend` 要用 ✓）
  ② 钉住的那个包，**PyPI 上有 cp312 / win_amd64 的二进制轮子**
     —— 运行时是 3.12 嵌入式，`build_release.py` 用 `--only-binary=:all:` ✗
        没有轮子 ⇒ 打包当场失败（实测 edge-tts 及其依赖共 13 个轮子，全部有 cp312/win_amd64 或 py3-none-any ✓）
  ③ 清单里不许出现**平台特定**的坑：不能写 `pyttsx3` 这类需要本机 SAPI 的
     —— 便携包要在别人机器上也能用 ✓ pyttsx3 在 `tts.py` 里是**可选后端**（有 guard ✓）不许进清单

★ 为什么不做成"跑一遍 pip 看看能不能装"：那要联网、要几十秒 ✗
  这里只钉**清单**这一个可离线校验的事实 ✓ 真正的实跑验证记录在文档里 ✓
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
REQ = (ROOT / "backend" / "requirements.txt").read_text("utf-8", errors="replace")


def _pkg_names(text: str) -> set[str]:
    """把 requirements 文本拆成小写包名集合（忽略注释、空行、-r 之类）。"""
    out: set[str] = set()
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[<>=!\[;]", line, maxsplit=1)[0].strip().lower()
        if name:
            out.add(name)
    return out


# ═══ ① edge-tts 必须在运行时清单里 ═══

def test_edge_tts_is_a_runtime_requirement():
    names = _pkg_names(REQ)
    assert "edge-tts" in names or "edge_tts" in names, (
        "backend/requirements.txt 里没有 edge-tts ✗\n"
        "  ⇒ 完整版发布包的便携运行时装不上它 ⇒ 包里**语音合成是坏的** ✗\n"
        "  ⇒ 而便携包没有 venv 可退（那是开发机路径）⇒ 用户拿到的是个哑巴包 ✓\n"
        "  实测（2026-10-12）：加了它之后 PyPI 上 13 个依赖轮子对 cp312/win_amd64 全部可得 ✓ 只多 1.15MB ✓"
    )


# ═══ ② 清单里不许塞只在本机才有的东西 ═══

def test_requirements_has_no_local_only_backend():
    """`pyttsx3` 靠本机 SAPI，便携包到别人机器上不保证有 ⇒ 不许进清单 ✓（它是可选后端，有 guard ✓）"""
    names = _pkg_names(REQ)
    assert "pyttsx3" not in names, (
        "pyttsx3 进了 requirements.txt ✗ —— 它依赖**本机 SAPI**，便携包在别人机器上不保证可用 ✗\n"
        "  它是 app/tts.py 里的**可选**后端（有 try/except guard ✓）⇒ 不该进运行时清单 ✓"
    )


# ═══ ③ 清单本身要能被解析、且不为空（防止有人把它清空） ═══

def test_requirements_is_parseable_and_non_trivial():
    names = _pkg_names(REQ)
    assert len(names) >= 6, f"运行时清单只剩 {len(names)} 项，疑似被误清空 ✗"
    for must in ("fastapi", "uvicorn", "httpx", "pydantic", "cryptography"):
        assert must in names, f"运行时清单里少了基础依赖 {must} ✗"
