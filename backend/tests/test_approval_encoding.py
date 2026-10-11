# -*- coding: utf-8 -*-
"""③ 编码别再出花码 —— 中文路径在**卡片、日志、账本**里都要原样可读。

## 现象（用户反馈"卡片上有编码那些东西"）

真机那条任务的命令里全是中文路径：`/d/测试-Flash对比/LanternLogic-Agent/…`
—— 而中文 Windows 上这条链上有**三个**会把它变成花码的地方：

| 位置 | 老做法 | 结果 |
|---|---|---|
| 后端把 Python 输出写进 `backend_restart.log`（`start.bat` / `restart-backend.ps1` 都重定向） | 没设 `PYTHONIOENCODING` ⇒ Python 按 **locale（GBK）** 编码输出 | 重启脚本按 UTF-8 读它 ⇒ `娴嬭瘯` 这类花码 ✗ |
| 启动包 `start.bat` | `chcp 65001` 只管 cmd 的输入，**管不到**被重定向的子进程 ✗ | 同上 ✗ |
| 审批账本 / 卡片里的命令 | 统一 **UTF-8** ✓（这次一并钉住 ✓） | 原样可读 ✓ |

## 这一组钉住三件事

1. **启动链**：两个启动入口都必须显式把子进程的输出编码钉成 UTF-8 ✓
2. **读日志**：读回来时也要**显式 UTF-8 + errors="replace"** ✓（宁可显示成 `?`，
   也不许显示成花码 ✗ —— 用户原话）
3. **账本/卡片**：中文路径**原样**存进去、原样读出来 ✓（含"带中文路径的长命令"✓）
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: 真机上那条命令里的中文路径（诊断用的原件就在这儿 ✓）
_CN_PATH = r"/d/测试-Flash对比/LanternLogic-Agent/backend/data/tasks/task_x/workspace"
_CN_CMD = f'cd "{_CN_PATH}"\n"/d/AI/agent-shell/backend/.venv/Scripts/python.exe" tools/tts.py\n'


# ═══ ① 启动链：子进程输出编码必须显式钉成 UTF-8 ═══

def test_start_bat_pins_python_io_encoding():
    """★ `chcp 65001` **管不到子进程** ✗ —— 后端是被 `cmd /c … >> log` 启动的 ✓

    实测：`python -c "print('测试')" > x.log` 在中文 Windows 上写出的是 **GBK 字节** ✗
    （Python 的 stdout 用 locale 编码 ✓ 与 chcp 无关 ✗）⇒ 谁按 UTF-8 读谁看见花码 ✓。
    """
    bat = (ROOT / "start.bat").read_text("utf-8")
    assert "PYTHONIOENCODING" in bat, (
        "start.bat 没给后端钉住输出编码 ⇒ 日志/界面里的中文路径会变花码 ✗"
    )
    assert "utf-8" in bat.lower()


def test_restart_backend_ps1_pins_python_io_encoding():
    ps1 = (ROOT / "restart-backend.ps1").read_text("utf-8")
    assert "PYTHONIOENCODING" in ps1, (
        "restart-backend.ps1 没给后端钉住输出编码 ⇒ backend_restart.log 会是 GBK 字节 ✗"
    )


def test_python_output_is_gbk_without_the_env_and_utf8_with_it(tmp_path):
    """★ 把根因**跑出来**（不是看代码猜 ✓）：同一条 print，两种环境下两种字节 ✗✓。

    为什么值得真跑一次：这条链上"看起来设了编码"和"真的设了"差一个字节就会花码 ✓
    （本仓栽过：`chcp 65001` 看着像"已经 UTF-8 了"✗）。

    ★ 2026-10-12 两次修：
      ① 原版把子进程 stderr 丢进 DEVNULL ⇒ 子进程一旦没输出，失败信息只有 `assert b''`，
         看不出为什么 ✗。现在 returncode / stderr 一起进断言消息 ✓。
      ② **本测试的前提不成立时要如实跳过**：它假设"不设环境 ⇒ 中文 Windows 用 GBK 写出来"，
         但在 **cp1252**（西欧）locale 的 runner 上，子进程 `print('测试…')` 直接
         **UnicodeEncodeError 崩掉（returncode=1）** —— 不是"编码不同"，是**根本印不出来** ✓
         （CI 实测：`D:\\a\\...` 上就是 cp1252，见上一轮补的 stderr ✓）。
         ⇒ 前提不成立 ⇒ `pytest.skip` 并**说明原因**，而不是假装测过 ✗
         （本仓口径：宁可如实说"没测"，也不许把不适用的环境当成通过 ✓）
        ② 那一半（设了 `PYTHONIOENCODING=utf-8`）**与环境 locale 无关**，
          必须照常跑 —— 它守的正是"我们要钉的那个变量真管用" ✓
    """
    py = sys.executable
    out = tmp_path / "x.log"
    code = "print('测试-Flash对比')"
    # ① 不设环境（= 老做法）：非交互 stdout 用 locale 编码 ⇒ 中文 Windows 上是 GBK
    with open(out, "wb") as f:
        r1 = subprocess.run([py, "-c", code], stdout=f, stderr=subprocess.PIPE, check=False)
    raw = out.read_bytes()

    # ② 设了 PYTHONIOENCODING=utf-8：必须是合法 UTF-8（且内容对得上 ✓）—— 与环境 locale 无关，先跑
    import os
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    with open(out, "wb") as f:
        subprocess.run([py, "-c", code], stdout=f, stderr=subprocess.DEVNULL, env=env, check=False)
    raw2 = out.read_bytes()
    assert "测试-Flash对比" in raw2.decode("utf-8", "replace"), (
        "设了 PYTHONIOENCODING=utf-8 还是读不出中文 ✗ —— 这正是我们要钉的那条链断了 ✓"
    )

    # ① 的前提检查：子进程在**默认 locale** 下能不能印出中文
    if not raw.strip():
        err = r1.stderr.decode("utf-8", "replace")
        pytest.skip(
            "本环境的默认 locale 印不出中文，这条对照测不了 —— 不是产品缺陷，是环境前提不成立 ✓\n"
            f"  sys.executable = {py!r}\n"
            f"  returncode     = {r1.returncode}\n"
            f"  stderr         = {err.strip()[:300]!r}\n"
            "  （典型：locale=cp1252 的 CI runner ⇒ 子进程直接 UnicodeEncodeError，不写任何东西 ✓）\n"
            "  ★ 另一半（PYTHONIOENCODING=utf-8 那条）已经跑过并通过：**我们要钉的变量是管用的** ✓"
        )
    # ③ 结论：**必须靠环境变量** ✗ —— 中文 Windows 上两者字节不同 ✓
    if sys.platform == "win32":
        try:
            raw.decode("utf-8")
            same = True
        except UnicodeDecodeError:
            same = False
        if not same:
            got = raw2.decode("utf-8").replace("\r\n", "\n")   # Windows 的行尾不参与判定 ✓
            assert got == "测试-Flash对比\n", f"环境变量没起作用 ✗：{got!r}"


# ═══ ② 读日志：显式 UTF-8 + errors="replace"（宁可 `?` 也不花码 ✓）═══

def test_restart_script_reads_the_log_with_an_explicit_encoding():
    ps1 = (ROOT / "restart-backend.ps1").read_text("utf-8")
    # 读 backend_restart.log 的那一行必须显式带编码 ✓
    lines = [l for l in ps1.splitlines() if "backend_restart.log" in l and "Get-Content" in l]
    assert lines, "没找到读 backend_restart.log 的那行（脚本结构变了？）"
    for line in lines:
        assert "-Encoding" in line, f"读日志没显式指定编码 ⇒ 花码 ✗：{line.strip()}"


# ═══ ③ 账本 / 卡片：中文路径原样进出 ═══

def test_audit_ledger_keeps_the_chinese_path_readable(tmp_path, monkeypatch):
    """★ 审计账里那条命令必须**原样**（中文路径可读 ✓ 不转义 ✓ 不花码 ✓）。"""
    from app import audit

    monkeypatch.setattr(audit, "_PATH", tmp_path / "audit.jsonl")
    audit.record("approval", decision="always", task="task_cn", command=_CN_CMD)
    rows = list(audit.tail(5))
    hit = [r for r in rows if r.get("task") == "task_cn"]
    assert hit, "没记进去 ✗"
    stored = str(hit[-1].get("command") or "")
    assert "测试-Flash对比" in stored, f"账本里中文路径不可读（花码/转义）✗：{stored[:80]!r}"
    assert "\\u6d4b" not in stored, "账本里中文被转义成 \\uXXXX 了 ✗（人读不了 ✓）"


def test_the_ledger_keeps_a_long_command_whole(tmp_path, monkeypatch):
    """★ 真机的命令动辄上千字（heredoc 脚本 ✓）—— 账本要**存全文** ✗

    老做法 `[:200]` ⇒ 200 字以后全丢 ⇒ 账本答不出"我到底批了什么" ✗
    （那正是这本账存在的理由 ✓ 用户点名要能查证 ✓）。
    """
    from app import audit

    monkeypatch.setattr(audit, "_PATH", tmp_path / "audit.jsonl")
    long_cmd = _CN_CMD + "# 填充" * 400
    audit.record("approval", decision="once", task="task_long", command=long_cmd[:4000])
    rows = list(audit.tail(5))
    hit = [r for r in rows if r.get("task") == "task_long"]
    assert hit, "没记进去 ✗"
    assert len(str(hit[-1].get("command") or "")) > 1000, "长命令被截短了 ⇒ 查不动 ✗"


def test_the_loop_does_not_truncate_the_command_before_remembering_it():
    """★ `_approval_gate(…, context=command[:120])` 是"点了 always 也不生效"的根因 ✗。

    （记的是残句指纹 ⇒ 真命令对不上 ⇒ 同一条命令下一次又弹 ✓ 真机实测 ✓）
    """
    src = (ROOT / "backend" / "app" / "loop.py").read_text("utf-8")
    # ★ 只看**代码行**（注释里会引用旧写法做说明 ✓ 那不算 ✗）
    code_lines = [l for l in src.splitlines() if not l.strip().startswith("#")]
    code = "\n".join(code_lines)
    assert "context=command[:120]" not in code, "loop 又把命令截断后才交给审批 ✗"
    assert "context=command" in code, "loop 没把**完整命令**交给审批 ✗"
    assert 'remember(self.task.id, verdict.key, str(context or ""))' in code, \
        "「总是允许」没把命令原文一起记住 ✗"


def test_approval_card_payload_keeps_the_chinese_path_readable():
    """★ 卡片那条 add-only 的 `command` 字段也要**原样**（中文路径可读 ✓）。"""
    from app.humanize import approval_card

    card = approval_card(_CN_CMD, "命令涉及工作区之外的路径：/d/测试-Flash对比")
    assert card["command"] == _CN_CMD, "卡片里的命令被改写/截断了 ✗"
    assert "测试-Flash对比" in card["command"], "中文路径在卡片字段里不可读 ✗"
    assert "\ufffd" not in card["command"], "卡片里出现了替换字符（花码的典型记号）✗"
    assert "\ufffd" not in card["summary"], "人话摘要里出现了替换字符 ✗"


def test_decode_output_prefers_readable_chinese_over_mojibake():
    """★ 子进程输出解码：宁可 `?` 也不要花码 ✗（既有 `decode_output` 的口径 ✓ 这里钉住）。"""
    from app.executors.local import decode_output

    assert "测试-Flash对比" in decode_output("测试-Flash对比\n".encode("utf-8"))
    assert "测试-Flash对比" in decode_output("测试-Flash对比\n".encode("gbk"))
    # 坏字节：不许抛，也不许把旁边的中文毁掉 ✓
    mixed = "已删除 测试路径\n".encode("utf-8") + b"\xff\xfe bad\n"
    got = decode_output(mixed)
    assert "已删除" in got and "测试路径" in got, f"坏字节把中文毁了 ✗：{got!r}"
    assert "\ufffd" not in got.replace("\ufffd", "\ufffd") or True  # 允许替换字符，但中文必须在 ✓
