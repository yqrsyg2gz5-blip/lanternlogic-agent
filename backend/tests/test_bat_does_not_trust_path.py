# -*- coding: utf-8 -*-
"""别指望用户的 PATH 里有 System32（★ 2026-10-11 · 用户真机实测炸出来的 ✗✗）。

## 现场（用户双击 start.bat 的原始输出 ✓）

```
'chcp' 不是内部或外部命令，也不是可运行的程序或批处理文件。
'curl' 不是内部或外部命令，也不是可运行的程序或批处理文件。
'timeout' 不是内部或外部命令，也不是可运行的程序或批处理文件。
 [X] Backend did not answer within 30 seconds.
```

查实：他那台机器的**机器级 PATH 第一条是 `%Path%`（自我引用）** ✗
  六条里**没有一条指向 `C:\\Windows\\System32`** ✗
  ⇒ 新开的 cmd 里 `chcp` / `curl` / `timeout` 全找不到 ✓
  ⇒ `start.bat` 于是卡在"探活"那一步，30 秒后报"后端没响应" ✗
    （其实后端可能压根没被拉起来 ✓ —— 因为 `curl`/`timeout` 都没了 ✓）

★ 为什么我自己的"干净机器"测试没抓到 ✗：我构造的 PATH 里**恰好留着 System32** ✓
  ⇒ 教训：模拟"干净"要连**系统目录**都考虑进去 ✓ 不能只想着"没装 Python/Node" ✓

## 本文件钉三件事

  ① `start.bat` 与 `install.bat` 都必须**把系统目录顶到 PATH 最前面** ✓
  ② 而且必须**排在任何系统命令之前** ✗（`chcp` 之前 ✓ —— 不然顶了也没用 ✓）
  ③ 用到的系统命令（chcp/curl/timeout）仍然在 ✓ —— 只是不再依赖 PATH 里本来就有 ✓
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
START = (ROOT / "start.bat").read_text("utf-8", errors="replace")
INSTALL = (ROOT / "install.bat").read_text("utf-8", errors="replace")

PREPEND = 'set "PATH=%SystemRoot%\\System32;%SystemRoot%;%SystemRoot%\\System32\\Wbem;%PATH%"'


def test_start_bat_prepends_system_dirs():
    assert PREPEND in START, "start.bat 没有把系统目录顶到 PATH 最前面 ✗（用户机器上 chcp/curl 会找不到 ✓）"
    i_prepend = START.index(PREPEND)
    for cmd in ("chcp", "curl", "timeout"):
        i = START.index(f"\n{cmd} ")            # 第一次真正调用它
        assert i_prepend < i, f"{cmd} 出现在'顶 PATH'之前 ✗ ⇒ 顶了也白顶 ✓"


def test_install_bat_prepends_system_dirs():
    assert PREPEND in INSTALL, "install.bat 没有顶系统目录 ✗（它后面会调 preflight ✓）"


def test_system_commands_are_still_used():
    """★ 别为了'不依赖 PATH'就把系统命令删掉 ✗ —— 顶完 PATH 之后它们本来就该能用 ✓。"""
    assert "chcp 65001" in START, "start.bat 里的 chcp 没了 ✗"
    assert "curl -s -m 2" in START, "start.bat 里的 curl 探活没了 ✗"
    assert "timeout /t 2" in START, "start.bat 里的 timeout 没了 ✗"
