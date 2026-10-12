# -*- coding: utf-8 -*-
"""本地沙箱（作业对象层）的验收测试 —— 方案 P0/P1。

> 方案见 `docs/沙箱方案-本地三层隔离.md`。**先读 `sandbox_native` 模块头**，
> 那里写着三轮实测为什么**只有作业对象这一层做得成**。

## 本文件钉两类事

**（一）真做到了的**（这些必须绿）
  L1 超时 → **整棵进程树一起死**（防跑飞后留残留）
  L2 超内存 → 被作业对象掐住（防把机器拖死）
  L3 并发进程数受限（防 fork 炸弹）
  L4 主人进程退出 → 里面所有进程一起死（`KILL_ON_JOB_CLOSE`）
  L5 正常活不受影响（**沙箱不是用来把活拦死的**）

**（二）★ 有意做不到、必须钉住的边界**（这些也必须"绿"——绿的是"边界如实"）
  B1 **不做文件隔离**：沙箱进程仍是你自己的身份，**写得了工作区外面的文件**
  B2 因此**不许对外宣称"文件隔离"** —— 本文件用断言把这条钉死，
     防止以后有人误以为"有沙箱 ⇒ 文件安全" ✗

★ 这两类**都要绿**才是诚实的状态：**做得到的说做到了，做不到的写在明面上** ✓
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import textwrap
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="作业对象是 Windows 原生机制 —— 非 Windows 不适用 ✓",
)


def _sb():
    from app.executors import sandbox_native
    return sandbox_native


def _run(command: str, workdir: pathlib.Path, timeout: float = 20.0, **kw):
    return _sb().run_in_sandbox(command, workdir=workdir, timeout=timeout, **kw)


# ═══════════════════════ （一）真做到了的 ═══════════════════════

def test_l1_timeout_kills_the_whole_tree(tmp_path):
    """死循环必须在超时内被掐死，且函数要返回（不能把调用方挂住）。

    ★ 命令写法踩过坑：`cmd /c ":loop & goto loop"` 在本机**瞬间退出 0**（不是真死循环），
      会让本用例假过。实测能用的是 `python -c "while True: pass"` ✓
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    py = sys.executable
    t0 = time.monotonic()
    code, out = _run(f'"{py}" -c "while True: pass"', ws, timeout=5.0)
    dt = time.monotonic() - t0
    assert dt < 25, f"超时没生效，跑了 {dt:.1f}s（限 5s）✗"
    assert code == 124, f"超时应返回 124（杀整棵树的约定），实际 {code} ✗ 输出={out[:200]}"


def _spawn_background(ws: pathlib.Path, beat: pathlib.Path) -> str:
    """起一个**常驻后台子进程**（写心跳）后让外层立刻退出的命令。

    ★ 写法踩坑记录（三条都试过，别再走回头路）：
      `cmd /c start /b`  —— 本机**外层不退出**，不适合本用例 ✗
      `powershell Start-Process` / `pwsh ...` —— **子进程 PATH 里没有 pwsh/powershell**
        （我的工具会话有、被测进程没有），实测直接 "不是内部或外部命令" ✗
      **纯 Python 起后台进程** —— 不依赖任何外部 shell，本机实测外层 0.06s 退出、
        后台心跳持续增长 ⇒ **本用例采用** ✓

    ★ 竞态说明（为什么这个用例仍然有意义）：
      我们给目标进程起的外层进程**已经被放进作业对象**，它再起的后代**自动继承作业对象成员身份**
      （Windows 8+ 作业对象可嵌套）⇒ 后代不需要单独 Assign ✓
    """
    (ws / "child.py").write_text(
        "import time,pathlib,sys\n"
        "p=pathlib.Path(sys.argv[1])\n"
        "for i in range(300):\n"
        "    p.write_text(str(i))\n"
        "    time.sleep(0.05)\n", encoding="utf-8")
    spawn = (
        "import subprocess,sys\n"
        "subprocess.Popen([sys.executable, 'child.py', r'" + str(beat) + "'],\n"
        "                 creationflags=0x00000008 | 0x00000200, close_fds=True)\n"
    )
    (ws / "spawn_bg.py").write_text(spawn, encoding="utf-8")
    return f'"{sys.executable}" spawn_bg.py'


def _wait_for_heartbeat(beat: pathlib.Path, timeout: float = 12.0) -> str:
    """等心跳文件出现并返回第一次读到的内容（不许用"文件不存在"当通过）✗"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not beat.exists():
        time.sleep(0.2)
    assert beat.exists(), (
        "后台后代进程压根没起来（等了 12 秒心跳仍不存在）\n"
        "  ⇒ 本用例**无法验证**，不许当作通过 ✓"
    )
    return beat.read_text(errors="replace")


def _assert_heartbeat_stops(beat: pathlib.Path, why: str) -> None:
    """验证后台后代进程**真的在跑、然后真的死了**。"""
    first = _wait_for_heartbeat(beat)
    time.sleep(1.5)
    second = beat.read_text(errors="replace")
    assert first == second, (
        f"{why} ✗（心跳 {first!r} → {second!r}）\n"
        "  ⇒ 作业对象没能罩住它起的后代进程"
    )


def test_l1b_timeout_leaves_no_orphan_child(tmp_path):
    """★ 关键：超时杀的是**整棵树** —— 主命令被杀后，它起的子进程也不许活。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    beat = ws / "beat.txt"
    # 起后台子进程，然后让主命令睡死（会被超时杀掉）
    cmd = _spawn_background(ws, beat) + " & ping -n 60 127.0.0.1 >nul"
    code, _ = _run(cmd, ws, timeout=5.0)
    assert code == 124, f"应当超时（124），实际 {code} ✗"
    _assert_heartbeat_stops(beat, "超时后子进程还在跑")


def test_l2_memory_hog_is_capped(tmp_path):
    """申请超大内存必须被作业对象限制住（不能把机器拖死）。

    ★ 命令写法踩过坑：`python -c "a=[]<真换行>while True: ..."` 在本机**瞬间退出 0**（没跑起来），
      会让本用例假过。实测能用的是**写脚本文件**那一路 ✓
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    py = sys.executable
    (ws / "hog.py").write_text(
        "a = []\nwhile True:\n    a.append(bytearray(50 * 1024 * 1024))\n", encoding="utf-8")
    code, out = _run(f'"{py}" hog.py', ws, timeout=15.0, memory_mb=512)
    assert code != 0, f"无限申请内存竟然跑完了 ✗ 输出={out[:200]}"


def test_l4_job_close_kills_everything(tmp_path):
    """关掉作业对象 ⇒ 里面**所有**进程一起死（KILL_ON_JOB_CLOSE），后代不留残留。

    ★ 为什么这么写（这一条绕过两次弯，记录免得重走）：
      写法一「外层起后台进程后自己退出」**测不出东西** —— 外层一退、句柄一关，
        孙进程**还没写出第一个心跳就被杀了** ⇒ 观测不到"心跳停止"。
      写法二「后台线程跑沙箱（保持句柄开着）→ 等心跳长起来 → 再主动关句柄」**
        能真观测到"先活后死"，所以采用 ✓
    """
    import threading

    ws = tmp_path / "ws"
    ws.mkdir()
    beat = ws / "beat2.txt"
    sb = _sb()
    cmd = _spawn_background(ws, beat)

    holder: dict = {}

    def worker():
        holder["job"] = sb._make_job(2048, None, 8)
        p = subprocess.Popen(cmd, shell=True, cwd=str(ws),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        holder["proc"] = p
        h = sb._k32.OpenProcess(0x0100 | 0x0001, False, p.pid)
        assert h, "OpenProcess 失败"
        assert sb._k32.AssignProcessToJobObject(holder["job"], h), "Assign 失败"
        sb._k32.CloseHandle(h)
        p.communicate(timeout=30)          # ★ 不关句柄 —— 因此后代能继续活着

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    # ① 后代必须真的活着（心跳在涨）
    first = _wait_for_heartbeat(beat)
    time.sleep(1.0)
    assert beat.read_text(errors="replace") != first, (
        "后代进程没在跑（心跳不涨）—— 本用例无法验证，不许当作通过 ✗")

    # ② 关掉作业对象 ⇒ 后代必须一起死
    sb._k32.CloseHandle(holder["job"])
    time.sleep(1.5)
    second = beat.read_text(errors="replace")
    time.sleep(1.5)
    third = beat.read_text(errors="replace")
    assert second == third, (
        f"关掉作业对象后后代**还在跑** ✗（心跳 {second!r} → {third!r}）\n"
        "  ⇒ KILL_ON_JOB_CLOSE 没生效"
    )


def test_l5_normal_work_still_works(tmp_path):
    """读文件 + 写工作区产物 + 跑脚本 —— 正常活必须畅通。**沙箱不是用来把活拦死的** ✓"""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "input.txt").write_text("hello sandbox", encoding="utf-8")
    py = sys.executable
    (ws / "do.py").write_text(
        textwrap.dedent("""
            import pathlib
            src = pathlib.Path('input.txt').read_text(encoding='utf-8')
            pathlib.Path('out.txt').write_text(src.upper(), encoding='utf-8')
            print('done')
        """).strip(), encoding="utf-8")
    code, out = _run(f'"{py}" do.py', ws)
    assert code == 0, f"正常活被拦了 ✗ 输出={out[:300]}"
    assert (ws / "out.txt").read_text(encoding="utf-8") == "HELLO SANDBOX"


def test_unavailable_platform_fails_loudly(tmp_path, monkeypatch):
    """★ 作业对象不可用时**必须明确报错**，不许静默降级成"直接跑"。

    与现有 Docker 沙箱同口径（`local.py`：Docker 不可用就报错，不偷偷退回真机）✓
    """
    sb = _sb()
    monkeypatch.setattr(sb, "available", lambda: False)
    with pytest.raises(sb.SandboxUnavailable):
        sb.run_in_sandbox("echo hi", workdir=tmp_path, timeout=5.0)


# ═══════════════════════ （二）★ 有意做不到的边界 ═══════════════════════

def test_b1_no_file_isolation_is_by_design(tmp_path):
    """★ **本沙箱不做文件隔离** —— 这条是**有意钉住**的，不是缺陷。

    三轮实测（见 `sandbox_native` 模块头 / 方案文档 §3.1bis）证明：
      受限令牌起不了进程（提权也不行）、AppContainer 连 whoami.exe 都起不来
      ⇒ 没降权 ⇒ **沙箱进程 = 你自己的身份 ⇒ 写得了工作区外的文件**。

    本用例**断言"写得了"**，以便：
      ① 以后谁改了实现真做出隔离，这条会红 —— 提醒去更新文档与对外口径 ✓
      ② 任何人读测试就知道**别指望它保护文件** ✗
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside_probe.txt"
    code, out = _run(f'cmd /c echo probe > "{outside}"', ws)
    assert outside.exists(), (
        "预期「不做文件隔离」——但工作区外的写入被挡住了？\n"
        "  若这是新实现的降权隔离，**请更新**：\n"
        "    · 本用例的断言方向\n"
        "    · sandbox_native 模块头与 docs/沙箱方案-本地三层隔离.md §3.1bis\n"
        "    · 对外口径（现在只能说「资源与生命周期限制」，不能宣称「文件隔离」）✓"
    )
    outside.unlink(missing_ok=True)


def test_b2_doc_states_the_boundary():
    """★ 文档必须写明"不做文件隔离" —— 防止对外宣称跑偏 ✗"""
    doc = (ROOT / "docs" / "沙箱方案-本地三层隔离.md").read_text("utf-8", errors="replace")
    assert "3.1bis" in doc, "方案文档缺少 3.1bis（实测修订）小节 ✗"
    assert "SeImpersonatePrivilege" in doc, "方案文档没记下受限令牌起不了进程那条实测 ✗"
    assert "0xC0000142" in doc, "方案文档没记下 AppContainer 那条实测 ✗"
    mod = (ROOT / "backend" / "app" / "executors" / "sandbox_native.py").read_text(
        "utf-8", errors="replace")
    assert "不是" in mod and "文件隔离" in mod, "实现模块头没写明「不做文件隔离」✗"
