"""★ 应用自身目录的**硬保护**（2026-10-06 夜实测教训）。

## 现场

夜里发现后端 venv 里的 **`python.exe` 不见了**（只剩 `pythonw.exe`）✗ ——
而任务日志显示：它们跑的是**宿主上的真命令**（沙箱关着），并且**真的在跑 `rm -rf`**：

```
cd "data\\groups\\grp_59515f\\workspace" && PY="D:\\AI\\agent-shell\\backend\\.venv\\Scripts\\python.exe" && rm -rf data todo.json
```

它们本意是清理自己工作区里的测试数据 ✓，路径也对 ✓ ——
但**只要哪一次路径写歪**（相对路径、`cd` 失败、变量为空…），`rm -rf` 就会落到
**应用自己的安装目录**上 ✗。`python.exe` 消失这件事，最可能就是这一类手滑 ✓。

## 规矩

**应用关键目录是禁区**：`<app>`、`<backend>/.venv`、`<backend>/data/tasks`、
`<repo>/scripts`、`<repo>/frontend`、配置文件。
命令里**提到**这些目录、且**不在本任务工作区里** ⇒ **一律人工确认** ✓，而且：

· 这道判断放在**「本任务全部允许」之前** ⇒ 那条授权**不能**绕过它 ✓
· 群里的卡片照旧出现（用户点一下就行）✓
· 工作区内的路径**不受影响**（工作区本来就在 data 目录下）✓

**宁可多问一句，也不能让一次手滑把装好的环境删掉。**
"""
from __future__ import annotations

import pathlib

import os
from pathlib import Path

from app.approval import ApprovalManager

WS = str(Path(os.environ.get("TEMP", "/tmp")) / "ws_probe")


def _inside_ws(p: str) -> bool:
    """假装工作区就是 WS（`check()` 的 is_inside 回调语义）。"""
    return str(p).replace("\\", "/").lower().startswith(WS.replace("\\", "/").lower())


def _check(cmd: str, allow_all: bool = False):
    am = ApprovalManager()
    if allow_all:
        am.allow_all("t1")
    return am.check("t1", cmd, ["shell_exec"], _inside_ws)


def test_running_the_protected_interpreter_does_not_trigger_the_hard_wall():
    """★ 2026-10-06 实测的误伤：任务**正常调用** venv 的解释器跑脚本 ✓（那是环境交底教的用法）。
    硬保护只该在"**破坏性动词 + 受保护路径**同时出现"时拦 —— 光是调用解释器不算。

    （注意：调用工作区之外的路径**另有**一条更早就存在的规则会问一次 ✓ 那是原行为，不是这道墙。）
    """
    import sys
    py = str(sys.executable)
    for cmd in (f'"{py}" todo.py add 买牛奶', f'cd /w && "{py}" -m unittest test_todo -v'):
        v = _check(cmd, allow_all=True)
        assert v is None or "应用自身的目录" not in (v.reason or ""), (cmd, v)
    # 但"删/移/写"到受保护路径 ⇒ 必须被这道墙拦下（开了全部允许也不行）
    for bad in (f'rm -f "{py}"', f'del "{py}"', f'move "{py}" /tmp/x', f'cp evil.exe "{py}"'):
        v = _check(bad, allow_all=True)
        assert v is not None and v.action == "ask", (bad, v)


def test_interpreter_can_be_run_but_not_deleted():
    """★★ 2026-10-06：**解释器不算"越界路径"** —— 任务要用后端自己的 python 跑工作区里的脚本
    （那是环境交底教它们的用法 ✓），可它在工作区之外 ⇒ 每跑一次问一次 ✗
    （实测：终验那一步为此被问了 3 次，用户睡觉时全靠自动放行器顶着）。

    放宽的边界很窄：**只是"执行"这个解释器**、且命令里没有破坏性动作 ✓。
    """
    am = ApprovalManager()
    import sys
    py = str(sys.executable)
    # 这三形态算"执行" ⇒ 放行
    for cmd in (f'"{py}" todo.py add 买牛奶',
                f'cd /w && "{py}" -m unittest test_todo -v',
                f'echo hi | "{py}" -c "print(1)"'):
        assert am._iterpreter_only(py, cmd) is True, cmd
    # 破坏性动作、或根本不是解释器 ⇒ 不放行（删/移仍被应用目录硬保护拦 ✓）
    for cmd in (f'rm -f "{py}"', f'del "{py}"', f'move "{py}" /tmp/x', 'notepad.exe x.txt'):
        assert am._iterpreter_only(py, cmd) is False, cmd


def test_app_dirs_are_always_asked_even_with_allow_all():
    """★ 关键：**「本任务全部允许」不能绕过这道保护**（硬保护在它之前判）。"""
    # ★ 2026-10-09（CI 实跑抓出来的 ✗）：这里原来**写死开发机路径**
    #   `D:\AI\agent-shell\backend\.venv\Scripts\python.exe` ✗
    #   CI 上仓库在 D:\a\<repo>\<repo>\ ✓ 路径不同 ⇒ 应用目录保护匹配不上 ⇒ 裁决变 allow_all ✗
    #   ⇒ 改成**按当前仓库真实位置算** ✓ 换机器换目录都对 ✓
    _venv_py = (pathlib.Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "python.exe")
    cmd = f'del /f /q {_venv_py}'
    v = _check(cmd, allow_all=True)
    assert v is not None and v.action == "ask", v
    assert "应用自身的目录" in v.reason, v.reason


def test_protected_dirs_cover_the_install_and_the_app_code():
    dirs = ApprovalManager._protected_dirs()
    joined = " | ".join(dirs)
    assert any(d.endswith("/app") for d in dirs), joined          # 应用代码
    assert any(".venv" in d for d in dirs), joined                # 安装环境
    assert any(d.endswith("/scripts") for d in dirs), joined      # 守门脚本
    assert any(d.endswith("/config.json") for d in dirs), joined  # 配置


def test_commands_inside_the_workspace_are_not_blocked():
    """工作区内的路径不受影响（那儿本来就该随便写）—— 开了全部允许就走"免问但留审计"。"""
    cmd = f'cd "{WS}" && rm -rf data todo.json && python todo.py list'
    v = _check(cmd, allow_all=True)
    assert v is not None and v.action == "allow_all", v      # 免问，但留审计
    assert "应用自身的目录" not in (v.reason or ""), v.reason
    assert _check(cmd) is None, "没开全部允许时，工作区内的普通命令也不该触发保护墙"


def test_normal_commands_are_unaffected():
    for cmd in ("ls -la", "python todo.py add 买牛奶", "grep -n x todo.py"):
        assert _check(cmd) is None, cmd
