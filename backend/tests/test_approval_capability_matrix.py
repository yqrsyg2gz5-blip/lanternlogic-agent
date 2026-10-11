# -*- coding: utf-8 -*-
"""审批能力面矩阵测试（第六轮复验：替代"按清单打勾"，去常量桩）。

病根（审查第 6 轮第 5 条）：此前 `target_exists=(lambda tok: exists)` 是**常量桩，
忽略命令**——"解析失败 / /w 映射 / junction / 别名"类漏洞在结构上不可能被发现；
矩阵 A 的目标状态维度从未与动词交叉。

现在 target_exists 接**真实解析实现**（与本机 LocalExecutor.resolve_in_workspace
同构 + 沙箱 /w 映射 + symlink 逃逸检测），矩阵 A 做真交叉：
  破坏性写动词 × 目标状态 {存在, 不存在, 不可核实, /w 形式, symlink-escape}。
新洞只要落在任一能力面组合里就会被矩阵覆盖。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

REQ = ["rm", "del", "rmdir", "rd", "erase", "format", "reg", "remove-item"]


# 十九轮 🔴5：临时目录登记制——session 结束统一清理（此前 asw-*/outside-*
# 每跑一次全量 pytest 泄漏 170 个，累积到 6488/16559）
_LEAKED: list[Path] = []


def _register_tmp(p: Path) -> None:
    _LEAKED.append(p)


import atexit as _atexit
import shutil as _sh_cleanup


@_atexit.register
def _cleanup_leaked_tmp() -> None:
    """会话结束清理全部登记的临时目录（静默——清理失败不阻塞退出）。"""
    for _d in _LEAKED:
        try:
            _sh_cleanup.rmtree(_d, ignore_errors=True)
        except Exception:
            pass


def _make_workspace() -> tuple[Path, Path]:
    """建临时工作区：交付物存在；escape 为指向外部的链接（symlink 需特权时退 junction）。"""
    ws = Path(tempfile.mkdtemp(prefix="asw-"))
    _register_tmp(ws)
    (ws / "deliverable.txt").write_text("PRECIOUS", encoding="utf-8")
    (ws / "exist.txt").write_text("X", encoding="utf-8")
    outside = Path(tempfile.mkdtemp(prefix="outside-"))
    _register_tmp(outside)
    (outside / "target.txt").write_text("VICTIM", encoding="utf-8")
    escape_ok = False
    try:
        os.symlink(outside, ws / "escape", target_is_directory=True)
        escape_ok = True
    except OSError:
        # Windows 无 symlink 特权：junction（mklink /J）不提权即可建——攻击者真实可用形态
        import subprocess
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(ws / "escape"), str(outside)],
                           capture_output=True)
        escape_ok = r.returncode == 0
    return ws, outside if escape_ok else None  # type: ignore[return-value]


def _is_junction(p: Path) -> bool:
    """Windows junction 判定（os.path.isjunction 自 3.12 起有；Path.is_symlink 对
    junction 返回 False——这正是 D1 那条锚点此前瞎掉的一半原因）。"""
    try:
        return bool(os.path.isjunction(p))  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False


def _relink(src: Path, dst: Path) -> None:
    """在真实工作区里**重建链接**（symlink/junction），而不是复制它指向的内容。"""
    target = os.readlink(src)
    try:
        os.symlink(target, dst, target_is_directory=True)
        return
    except OSError:
        pass
    import subprocess
    subprocess.run(["cmd", "/c", "mklink", "/J", str(dst), str(target)], capture_output=True)


def _real_run(ws: Path):
    """构造**生产级** TaskRun（真实 LocalExecutor + 真实 store/工作区映射）。

    第六轮复验③：此前矩阵自己写了一份 /w 映射逻辑——打补丁成 fail-open 仍全绿
    （验证的是副本）。现在直接用生产实例类型；_target_exists/_path_is_inside
    被改坏时，引用本函数的测试必红。
    第九轮复验③：暴露整个 run（而非只 _target_exists），供锚点测试同时取
    _path_is_inside（is_inside fail-open 的回退锚点）。
    ★ D1（2026-10-04）：同步夹具时要**保留链接形态**——此前目录链接走 copytree
      （跟随链接、把外靶内容复制成工作区内的普通目录）⇒ 真实工作区里根本没有
      逃逸形态，`escape/target.txt` 变成"存在的工作区内文件"，②"覆写已有文件"
      顺手问了一次 ⇒ `test_matrix_a_symlink_escape_asks` **绿得纯属巧合**
      （审计方因此判"K3 报的失败复现不出"，而 K3 的根因描述其实是对的）。
    """
    from app.loop import TaskRun
    from app.schemas import TaskSummary
    from app.store import FsStore
    from app.approval import ApprovalManager
    from app.bus import EventBus
    from app.executors.local import LocalExecutor
    from types import SimpleNamespace
    import shutil as _sh
    import secrets as _secrets

    store = FsStore(ws.parent / (ws.name + "-store-" + _secrets.token_hex(3)))  # 每个用例独立目录（防跨用例文件残留）
    _register_tmp(store.tasks_dir.parent)  # 十九轮 🔴5：store 目录也登记（此前漏→每次 _real_run 泄漏 1 个）
    real_ws = store.workspace_dir("task_20261003_mx01")
    # 把夹具文件同步进真实工作区（矩阵断言里用同样的相对名）
    for item in ws.iterdir():
        dst = real_ws / item.name
        try:
            if item.is_symlink() or _is_junction(item):
                _relink(item, dst)                      # ★ 保留链接（D1）
            elif item.is_dir():
                _sh.copytree(item, dst, dirs_exist_ok=True)
            elif item.is_file():
                _sh.copy2(item, dst)
        except OSError:
            pass
    executor = LocalExecutor(SimpleNamespace(
        type="local", workspace_root=str(real_ws), allowed_dirs=[str(real_ws)],
        timeout_seconds=30, sandbox="off", shell=None, cfg_shell="", search_url="",
        searxng_url="", browser_channel="msedge", comfyui_url="http://127.0.0.1:9",
        image_checkpoint="x", sandbox_image="python:3.12-slim",
        sandbox_network="none", sandbox_memory="512m",
    ))
    return TaskRun(
        TaskSummary(id="task_20261003_mx01", title="mx", created_at="2026-10-03T00:00:00Z",
                    updated_at="2026-10-03T00:00:00Z"),
        "矩阵", store=store, bus=EventBus(), provider=None, executor=executor,
        approval=ApprovalManager(), tools=[], max_iterations=1, timeout_seconds=1.0,
        approval_required=[], on_finish=lambda r: None,
    )


def _real_target_exists(ws: Path):
    """生产 `_target_exists` 的绑定（复用 _real_run）。"""
    return _real_run(ws)._target_exists


@pytest.fixture()
def ws():
    _ws, _out = _make_workspace()
    return _ws


def _check_cmd(cmd, ws, *, scripts=None, in_sandbox=False):
    """十二轮 🔴5②：三个回调全部走生产实现（_path_is_inside / _target_exists /
    _read_script_for_scan）——脚本写入真实工作区，reader 不再用字典桩。"""
    run = _real_run(ws)
    for name, content in (scripts or {}).items():
        target = Path(run.workdir) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return run.approval.check(
        "task_20261002_mx01", cmd, REQ, run._path_is_inside,
        in_sandbox=in_sandbox,
        target_exists=run._target_exists,
        script_reader=run._read_script_for_scan,
    )


def _is_ask(v):
    return v is not None and v.action == "ask"


# ═══ 矩阵 A（真交叉）：破坏性写动词 × 目标状态 ═══
# 每行 = (命令模板, 目标状态标签, 期望 ask)
TARGET_STATES = [
    # (描述, token 写法, 期望)
    ("存在·相对", "deliverable.txt", True),
    ("存在·/w 形式", "/w/deliverable.txt", True),
    ("不存在·新文件", "brand_new.txt", False),
]

OVERWRITE_VERBS = [
    ("truncate -s 0 {t}", "truncate"),
    ("dd if=/dev/zero of={t} bs=1k count=1", "dd"),
    ("cp /dev/null {t}", "cp"),
    ("install -m 644 /dev/null {t}", "install"),
    ("tee {t} < /dev/null", "tee"),
    # 注：tee 到"不存在的新文件"是放行的（第六轮复验⑥/⑥修正：tee 写新文件不误伤，
    # 见 test_matrix_a_verb_x_target_x_sandbox 的"不存在·新文件"行）
    ("echo x > {t}", "重定向 >"),
    ("echo x >> {t}", "重定向 >>"),
    ("echo x >| {t}", "重定向 >|"),
]


@pytest.mark.parametrize("tmpl,label", OVERWRITE_VERBS, ids=[v[1] for v in OVERWRITE_VERBS])
@pytest.mark.parametrize("tstate,token,want_ask", TARGET_STATES, ids=[t[0] for t in TARGET_STATES])
@pytest.mark.parametrize("in_sandbox", [False, True], ids=["host", "sandbox"])
def test_matrix_a_verb_x_target_x_sandbox(ws, tmpl, label, tstate, token, want_ask, in_sandbox):
    """★ 真交叉：每种写的写法 × 每种目标状态 × 宿主/沙箱。

    例外语义（tee）：`tee 新文件` 放行（tee 常用于写结果日志/新产物，
    第六轮复验⑥）；`tee 已有文件` 仍拦（覆写）。下面的 want_ask 修正处理它。
    """
    cmd = tmpl.format(t=token)
    expect = want_ask
    if label == "tee" and tstate == "不存在·新文件":
        expect = False  # tee 写新文件 = 输出重定向到新文件，不误伤
    v = _check_cmd(cmd, ws, in_sandbox=in_sandbox)
    if _is_ask(v) != expect:
        te = _real_target_exists(ws)
        dbg_state = te("new.txt")
        dbg_ws = sorted(p.name for p in ws.iterdir())
        print("DEBUG:", repr(cmd), "| state(new.txt)=", dbg_state, "| ws=", dbg_ws, "| sandbox=", in_sandbox)
    assert _is_ask(v) == expect, (
        f"【{label} × {tstate} × {'sandbox' if in_sandbox else 'host'}】"
        f"期望 {'ask' if expect else 'pass'}，实际 {v}"
    )


def test_matrix_a_symlink_escape_asks(ws):
    """symlink/junction 指向工作区外 → 不可核实 → fail-closed ask（宿主模式）。

    ★ D1（2026-10-04，实录）：这条锚点此前**是瞎的**。
      `_real_run` 同步夹具时对目录链接走 `copytree`（跟随链接、把外靶内容复制成
      工作区内的普通目录）⇒ 真实工作区里没有逃逸形态，`escape/target.txt` 是
      "存在的工作区内文件"，②"覆写已有文件"顺手问了一次 ⇒ 测试**绿得纯属巧合**。
      审计方因此判"K3 报的失败复现不出"；而 K3 的根因描述是对的：
      宿主模式下 `st is None`（不可核实）那一支因 `and in_sandbox` 不生效，
      ④越界面又只扫绝对路径（相对路径的链接逃逸进不去）⇒ **静默放行**。
      修法：fail-closed 不分模式（approval.py ②）；本测试**再多断言一步**
      "真实工作区里确实是链接"，让"没建成功"退化成 skip，而不是静默通过。
    """
    if not (ws / "escape").exists():
        pytest.skip("本机无法创建 symlink/junction——逃逸形态不可达")
    run = _real_run(ws)
    esc = Path(run.workdir) / "escape"
    if not (esc.is_symlink() or _is_junction(esc)):
        pytest.skip("真实工作区里没能重建链接形态——本条无法验证（不许静默通过）")
    v = run.approval.check(
        "task_20261002_mx01", "cp /dev/null escape/target.txt", REQ,
        run._path_is_inside, in_sandbox=False,
        target_exists=run._target_exists, script_reader=run._read_script_for_scan,
    )
    assert _is_ask(v), f"symlink 逃逸必须 fail-closed（宿主模式）：{v}"
    assert "无法核实" in v.reason, f"理由应为不可核实：{v.reason}"


def test_matrix_a_symlink_escape_asks_in_sandbox(ws):
    """同一形态在沙箱模式也必须问（原判据 `and in_sandbox` 只管这一半）。"""
    if not (ws / "escape").exists():
        pytest.skip("本机无法创建 symlink/junction——逃逸形态不可达")
    run = _real_run(ws)
    esc = Path(run.workdir) / "escape"
    if not (esc.is_symlink() or _is_junction(esc)):
        pytest.skip("真实工作区里没能重建链接形态——本条无法验证（不许静默通过）")
    v = run.approval.check(
        "task_20261002_mx01", "truncate -s 0 escape/target.txt", REQ,
        run._path_is_inside, in_sandbox=True,
        target_exists=run._target_exists, script_reader=run._read_script_for_scan,
    )
    assert _is_ask(v), f"symlink 逃逸在沙箱模式同样必须 fail-closed：{v}"


def test_matrix_a_mv_semantics(ws):
    """mv 语义（第六轮复验⑥修正）：改名放行（日常整理）；覆写已有目标才拦。"""
    # 改名到新名字 → 放行（此前按"源存在"拦 = 误伤文件整理）
    v_rename = _check_cmd("mv exist.txt renamed.txt", ws)
    assert v_rename is None, f"改名到新名字不应拦：{v_rename}"
    # 目标已存在（rename 会覆写它）→ ask
    v_overwrite = _check_cmd("mv new.txt deliverable.txt", ws)
    assert _is_ask(v_overwrite), f"覆写已有目标必须 ask：{v_overwrite}"


# ═══ 矩阵 B：脚本入口形态 × 内容 ═══

DANGER_PY = "import os" + chr(10) + "os.system('rm -rf /c/Users/y/Documents')" + chr(10)
CLEAN_PY = "print('hello')" + chr(10)
DANGER_JS = 'const fs=require("fs");fs.rmSync("dist",{recursive:true,force:true});' + chr(10)
CLEAN_JS = '// cleanup step: rm -rf dist (documented only, not executed)' + chr(10) + 'console.log("ok");' + chr(10)

ENTRY_FORMS = [
    ("python evil.py", {"evil.py": DANGER_PY}, True),
    ("python < evil.py", {}, True),
    ("python -", {}, True),
    ("echo x | xargs python", {}, True),
    ("python -m evilmod", {"evilmod.py": DANGER_PY}, True),
    ("./evil.sh", {"evil.sh": "rm -rf /c/Users/y/Documents" + chr(10)}, True),
    ("bash evil.sh", {"evil.sh": "rm -rf /c/Users/y/Documents" + chr(10)}, True),
    ("make -f evil.mk", {"evil.mk": "all:" + chr(10) + chr(9) + "rm -rf /documents"}, True),
    ("make -C sub", {"sub/Makefile": "all:" + chr(10) + chr(9) + "rm -rf /documents"}, True),  # 十二轮：make -C 找的是 sub/Makefile，夹具路径随之（生产 reader 按真实路径读）
    ("node build_clean.js", {"build_clean.js": DANGER_JS}, True),
]


@pytest.mark.parametrize("cmd,scripts,expect_ask", ENTRY_FORMS)
@pytest.mark.parametrize("in_sandbox", [False, True])
def test_matrix_b_danger_entry_asks(ws, cmd, scripts, expect_ask, in_sandbox):
    v = _check_cmd(cmd, ws, scripts=scripts, in_sandbox=in_sandbox)
    assert _is_ask(v), f"危险脚本入口必须 ask（沙箱={in_sandbox}）：{cmd} → {v}"


CLEAN_FORMS = [
    ("python build.py", {"build.py": CLEAN_PY}),
    ("python strings_only.py", {"strings_only.py": 'HELP = "use rm -rf dist to clean"' + chr(10) + "print(HELP)"}),
    ("python gen_docstring.py", {"gen_docstring.py": "def f():" + chr(10) + '    """os.system(\'x\') documented"""' + chr(10) + "    return 1"}),
    ("node postbuild.js", {"postbuild.js": CLEAN_JS}),
    ("python --version", {}),
    ("npm --version", {}),
]


@pytest.mark.parametrize("cmd,scripts", CLEAN_FORMS)
def test_matrix_b_clean_scripts_pass(ws, cmd, scripts):
    v = _check_cmd(cmd, ws, scripts=scripts)
    assert v is None, f"干净脚本不应误伤：{cmd} → {v}"


def test_matrix_b_unreadable_fail_closed(ws):
    """reader 不可用 → fail-closed ask（is_inside 也接生产实现）。"""
    run = _real_run(ws)
    v = run.approval.check(
        "t", "python myscript.py", REQ, run._path_is_inside,
        target_exists=run._target_exists, script_reader=lambda tok: None,
    )
    assert _is_ask(v), f"reader 不可用必须 fail-closed：{v}"


# ═══ 矩阵 C：容器执行换入口（含带值选项） ═══

CONTAINER_FORMS = [
    "docker run --rm alpine sh -c 'rm -rf /w/*'",
    "docker run --rm img truncate -s 0 deliverable.txt",
    "docker run -v /x:/w --name c1 --rm img rm -rf deliverable.txt",
    "docker exec c1 rm -rf /w/deliverable.txt",
    "docker compose run --rm app rm -rf /w/deliverable.txt",
    "docker compose run --rm -e A=1 app rm -rf /w/deliverable.txt",
    "docker compose run --rm -u root -v /h:/w app rm -rf deliverable.txt",
    "docker compose exec -T app rm -rf /w/deliverable.txt",
]


@pytest.mark.parametrize("cmd", CONTAINER_FORMS)
def test_matrix_c_container_exec_asks(ws, cmd):
    v = _check_cmd(cmd, ws)
    assert _is_ask(v), f"容器内危险命令必须 ask：{cmd} → {v}"

