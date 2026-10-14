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

    ★★ 2026-10-14 修（用户跑干净 checkout 时抓到，本文件原写法**换台机器必红** ✗）：
       原来拿 `sys.executable` 当"受保护路径" —— 那**只在"跑测试的 Python 恰好在
       本仓 backend\\.venv 里"时才成立** ✓（开发机上正是这样，所以一直绿）。
       别人机器上 Python 可能装在 `C:\\Python313\\` ⇒ 那个路径**根本不在受保护清单里**
       ⇒ 硬保护不该触发 ⇒ 返回 `allow_all`（因为测试开了"全部允许"）⇒ 断言红 ✗
       **实测复现**：干净 clone 里跑单条即失败（用户就是这么撞上的）。

       ⇒ 现在**显式取一个真受保护路径**（`_protected_dirs()` 里的），
         不再依赖解释器装在哪 ✓ —— 这才是这条测试真正想验的东西。
    """
    import sys  # noqa: F401  （保留：说明本测试曾依赖它）
    am = ApprovalManager()
    prot = [str(d) for d in am._protected_dirs()]
    assert prot, "受保护目录清单是空的 ⇒ 硬保护形同虚设 ✗"

    # ① 解释器**能被调用**（用真解释器，不要求它在受保护清单里）
    py = str(sys.executable)
    for cmd in (f'"{py}" todo.py add 买牛奶', f'cd /w && "{py}" -m unittest test_todo -v'):
        v = _check(cmd, allow_all=True)
        assert v is None or "应用自身的目录" not in (v.reason or ""), (cmd, v)

    # ② "删/移/写"到**真受保护路径** ⇒ 必须被这道墙拦下（开了全部允许也不行）
    target = prot[0]
    for bad in (f'rm -f "{target}/x.py"', f'del "{target}\\x.py"',
                f'move "{target}/x.py" /tmp/x', f'cp evil.exe "{target}/x.py"'):
        v = _check(bad, allow_all=True)
        assert v is not None and v.action == "ask", (
            f"「全部允许」绕过了硬保护 ⇒ 能删掉应用自己的目录 ✗\n  {bad}\n  {v}"
        )
        assert "应用自身的目录" in (v.reason or ""), (
            f"拦是拦了，但不是硬保护拦的（原因不对）✗\n  {v.reason!r}"
        )


def test_hard_wall_is_checked_before_allow_all_in_the_source():
    """★★ 顺序锚点：硬保护必须**排在「本任务全部允许」之前** ✓

    为什么单独立一条（而不是只靠上面那条行为测试）：
      · 行为测试需要"命令里出现一个真受保护路径"才成立 —— 构造得对才有效 ✓
      · 顺序一旦被谁改反（把 `if task_id in self._allow_all` 挪到 `_protected_hit` 前面），
        行为测试**可能仍然绿**（取决于构造），但**洞已经开了**：
        "全部允许" → 删掉应用自己的 .venv ✗
      ⇒ 这条直接盯源码顺序，改反了就红 ✓

    真机背景：2026-10-14 有人的干净 checkout 上这条行为测试红了，
    但根因是**测试依赖解释器位置**，不是顺序错 —— 顺序本身是对的 ✓
    给它加个锚点，免得以后真被改反了没人发现。
    """
    src = (pathlib.Path(__file__).resolve().parents[1] / "app" / "approval.py").read_text(
        "utf-8", errors="replace")
    i_prot = src.find("protected = self._protected_hit(command, is_inside)")
    i_allow = src.find("if task_id in self._allow_all:")
    assert i_prot > 0, "找不到硬保护调用 ⇒ 顺序无从谈起 ✗"
    assert i_allow > 0, "找不到「本任务全部允许」判断 ⇒ 顺序无从谈起 ✗"
    assert i_prot < i_allow, (
        "「本任务全部允许」排到了硬保护**前面** ⇒ 开了全部允许就能删掉应用自己的目录 ✗\n"
        "  （硬保护必须先在前面判，连 allow_all 也不豁免）"
    )


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


# ─────────────────────────────────────────────────────────────────────────────
# ★ 2026-10-14：**发布包形态**的运行时也要在硬墙之内（第三方测评抓出的 P1）
#
# 现场：第三方只读测评对 `D:\测试-Flash对比\LanternLogic-Agent`（下载版）实测
#
#     rm -f "<repo>/runtime/python.exe"     （**即使开了「本任务全部允许」**）
#     → 返回 allow_all（放行）✗
#
# 根因：`_protected_dirs()` 只保护了 `backend/.venv`（开发机形态），
#       **没有 `<repo>/runtime`**（发布包形态）。
#       而发布包的整套 Python 运行时（解释器 + 全部依赖）就在那儿 ✗
#
# 这正是 2026-10-06「venv 里 python.exe 被删」事故的**发布包翻版**，
# 而且删掉的是更完整的东西 —— 用户再也起不来应用 ✓
#
# ⚠️ 为什么原来没抓到：开发机上 `sys.executable` 指向 `backend/.venv/...`（在名单里），
#    所以本文件里那条用 `sys.executable` 的测试**在开发机永远绿**；
#    只有拿**发布包的运行时**去跑才红 ⇒ 这就是它当时红的原因，红得对 ✓
# ─────────────────────────────────────────────────────────────────────────────

def test_protected_dirs_include_the_portable_runtime():
    """★ 发布包的 `runtime/` 必须在硬墙之内（它是整套 Python 运行时）。

    判据用**仓库相对路径**算，这样换机器、换目录都成立 ✓
    """
    repo = pathlib.Path(__file__).resolve().parents[2]        # <repo>
    want = str(repo / "runtime").replace("\\", "/").lower()
    dirs = ApprovalManager._protected_dirs()
    assert want in dirs, (
        f"发布包的运行时目录不在硬墙保护内 ⇒ Agent 一条 rm 就能删掉整套 Python ✗\n"
        f"  期望包含: {want}\n  实际名单: {dirs}"
    )


def test_portable_runtime_cannot_be_deleted_even_with_allow_all():
    """★ 端到端：删发布包运行时的解释器 —— **开了「全部允许」也必须拦下**。

    这条模拟的就是测评里那次实测（`rm -f <repo>/runtime/python.exe` → allow_all ✗）。
    """
    repo = pathlib.Path(__file__).resolve().parents[2]
    rt_py = repo / "runtime" / "python.exe"
    rt_lib = repo / "runtime" / "Lib"
    for bad in (f'rm -f "{rt_py}"', f'del "{rt_py}"', f'rm -rf "{rt_lib}"'):
        v = _check(bad, allow_all=True)
        assert v is not None and v.action == "ask", (bad, v)
        assert "应用自身的目录" in (v.reason or ""), (bad, v.reason)


def test_portable_runtime_is_still_usable_as_an_interpreter():
    """★ 别误伤：**调用**发布包运行时的解释器跑脚本，**不该**触发硬墙。

    （"光是调用解释器不算"是 2026-10-06 立的规矩 —— 那是环境交底教的用法 ✓）
    """
    repo = pathlib.Path(__file__).resolve().parents[2]
    rt_py = repo / "runtime" / "python.exe"
    for ok in (f'"{rt_py}" todo.py add 买牛奶', f'"{rt_py}" -m unittest test_todo -v'):
        v = _check(ok, allow_all=True)
        assert v is None or "应用自身的目录" not in (v.reason or ""), (ok, v)
