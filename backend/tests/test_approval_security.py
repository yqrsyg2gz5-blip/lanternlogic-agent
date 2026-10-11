"""审批护栏的安全回归 —— P1-5（可绕过）与 P1-6（并发冲突）。

修复前的两处真实问题：
  P1-5 只比较命令的**第一个词**：`cmd /c del x`、`bash -c "rm -rf …"`、
       `powershell -Command "Remove-Item …"` 全部直通审批。
  P1-6 `_pending` 只以 `call_id` 为键，而 `call_id` 每轮从 `call_001` 重计 ——
       两个任务同时等审批时，后者的 Future **覆盖**前者：一个永久挂起、另一个被误放行。
"""
from __future__ import annotations

import pathlib

import asyncio

from app.approval import ApprovalManager, _to_windows_path

REQUIRED = ["rm", "del", "rmdir", "format", "reg", "remove-item"]


def _check(cmd: str, inside=None, required=None, mgr: ApprovalManager | None = None, task="task_x"):
    m = mgr or ApprovalManager()
    return m.check(task, cmd, list(required or REQUIRED), inside or (lambda p: False))


# ---------------- P1-5：不再只看第一个词 ----------------


def test_plain_dangerous_command_still_caught():
    v = _check("rm -f tmp.txt")
    assert v is not None
    assert v[1] == "verb:rm"


def test_cmd_wrapper_is_unwrapped():
    """`cmd /c del x` —— 旧实现看首词是 cmd，直接放行。"""
    v = _check("cmd /c del important.txt")
    assert v is not None and v[1] == "verb:del"


def test_bash_wrapper_is_unwrapped():
    v = _check('bash -c "rm -rf /tmp/x"')
    assert v is not None and v[1] == "verb:rm"


def test_powershell_wrapper_and_cmdlet():
    v = _check('powershell -Command "Remove-Item -Recurse C:\\tmp\\x"')
    assert v is not None, "PowerShell 包装 + Remove-Item 必须被拦下"
    assert v[1] == "verb:remove-item"


def test_chained_commands_are_scanned():
    """危险动作出现在 `;` `&&` `|` 之后也要命中。"""
    assert _check("cd build && rm -rf dist") is not None
    assert _check("ls -la ; rmdir empty_dir") is not None
    assert _check("cat a.txt | format") is not None


def test_quoted_word_in_echo_does_not_trigger():
    """`echo "rm"` 不该弹审批（避免天天误报）。"""
    assert _check('echo "rm"') is None


def test_benign_command_passes():
    assert _check("ls -la") is None
    assert _check("python build.py") is None
    assert _check("git status") is None


def test_empty_command_passes():
    assert _check("   ") is None


# ---------------- P1-5：越界路径必审批 ----------------


def test_outside_path_requires_approval_even_for_safe_verb():
    """`cp` 不危险，但写到桌面就危险 —— 旧实现完全不管。"""
    v = _check("cp a.txt /c/Users/y/Desktop/x.txt", inside=lambda p: False)
    assert v is not None
    assert "工作区之外" in v[0]
    assert v[1].startswith("outside:")


def test_inside_path_does_not_trigger():
    # is_inside 收到的是**归一化**路径（小写、正斜杠、~ 已展开），所以判断要不区分大小写
    v = _check("cp a.txt /c/Users/y/Desktop/x.txt", inside=lambda p: "desktop" in p.lower())
    assert v is None


def test_gitbash_drive_path_is_normalized_before_check():
    """`/c/Users/...` 要归一成 `c:/users/...` 再问 is_inside，否则 allowed_dirs 会误报越界。"""
    seen: list[str] = []

    def inside(p: str) -> bool:
        seen.append(p)
        return True

    _check("ls /c/Users/y/Desktop", inside=inside)
    assert seen, "is_inside 必须被调用"
    assert seen[0].startswith("c:/"), f"未归一：{seen}"
    assert "\\" not in seen[0], f"归一后应统一用正斜杠：{seen}"


def test_https_url_is_not_mistaken_for_a_path():
    """URL 里的 /api/v1 不能被当成越界路径（否则天天弹审批）。"""
    assert _check("curl -s http://127.0.0.1:8642/api/v1/tasks") is None


def test_to_windows_path_helper():
    assert _to_windows_path("/c/Users/y") == "C:\\Users\\y"
    assert _to_windows_path("/d/AI/x") == "D:\\AI\\x"
    assert _to_windows_path("~/Desktop") == "~/Desktop"  # 由调用方 expanduser


# ---------------- 记忆（always）----------------


def test_always_remembers_by_verb():
    m = ApprovalManager()
    first = m.check("t", "rm -f a", REQUIRED, lambda p: False)
    assert first is not None
    m.remember("t", first[1])
    assert m.check("t", "rm -rf b", REQUIRED, lambda p: False) is None, "记住 rm 后同类不该再问"
    assert m.check("t", "del x", REQUIRED, lambda p: False) is not None, "别的动作仍要问"


def test_always_memory_is_per_task():
    m = ApprovalManager()
    v = m.check("t1", "rm -f a", REQUIRED, lambda p: False)
    m.remember("t1", v[1])
    assert m.check("t2", "rm -f a", REQUIRED, lambda p: False) is not None, "记忆不能跨任务"


def test_outside_path_memory_keyed_by_directory():
    m = ApprovalManager()
    v = m.check("t", "cp a /c/Users/y/Desktop/a", REQUIRED, lambda p: False)
    assert v is not None
    m.remember("t", v[1])
    # 同目录的另一个文件：不再问
    assert m.check("t", "cp b /c/Users/y/Desktop/b", REQUIRED, lambda p: False) is None


def test_always_memory_unifies_equivalent_path_spellings():
    """★ 真实任务暴露的摩擦：同一目录的三种写法必须算同一个记忆键。

    2026-09-30 扫描 Downloads 时，Agent 分别用了
    `C:\\Users\\y\\Downloads`、`~/Downloads`、`/c/Users/y/Downloads`，
    旧实现产生 3 个不同的键 —— 用户点了「总是允许」仍被追问。
    """
    m = ApprovalManager()
    first = m.check("t", f"ls {pathlib.Path.home() / 'Downloads'}", REQUIRED, lambda p: False)
    assert first is not None
    m.remember("t", first[1])

    assert m.check("t", "ls ~/Downloads", REQUIRED, lambda p: False) is None, "波浪号写法应命中同一记忆"
    # ★ 2026-10-09（CI 第二次抓 ✗）：这里原来写死 `/c/Users/y/Downloads` ✗
    #   （Git Bash 风格的绝对路径 ✓ 里面那个 `y` 是**开发机用户名** ✗ CI 上是 runneradmin ✗）
    #   ⇒ 按**当前用户的主目录**算出这个写法 ✓（跟用户名无关 ✓ 那条测试要验的是"三种写法归一" ✓）
    _home = pathlib.Path.home()
    _bash_home = "/" + _home.drive.rstrip(":").lower() + _home.as_posix().split(":", 1)[1]
    assert m.check("t", f"find {_bash_home}/Downloads -type f", REQUIRED,
                   lambda p: False) is None, "Git Bash 写法应命中同一记忆"
    # 另一个目录仍要问
    assert m.check("t", "ls /c/Users/y/Documents", REQUIRED, lambda p: False) is not None


# ---------------- P1-6：并发隔离 ----------------


def test_same_call_id_in_two_tasks_do_not_clobber():
    """★ 核心回归：两个任务都用 call_001 等审批，必须各自独立。"""
    m = ApprovalManager()

    async def scenario() -> None:
        t1 = asyncio.create_task(m.wait_decision("task_a", "call_001"))
        await asyncio.sleep(0.01)
        t2 = asyncio.create_task(m.wait_decision("task_b", "call_001"))
        await asyncio.sleep(0.01)

        assert m.pending_count() == 2, "旧实现这里是 1（后者覆盖了前者）"

        assert m.resolve("task_a", "call_001", "deny") is True
        await asyncio.sleep(0.01)  # 让被唤醒的协程跑一拍
        assert t1.result() == "deny"
        assert not t2.done(), "B 任务不能被 A 的决议放行"

        assert m.resolve("task_b", "call_001", "once") is True
        assert await t2 == "once"
        await asyncio.gather(t1, t2)

    asyncio.run(scenario())


def test_resolve_requires_matching_task():
    m = ApprovalManager()

    async def scenario() -> None:
        t = asyncio.create_task(m.wait_decision("task_a", "call_002"))
        await asyncio.sleep(0.01)
        assert m.resolve("task_b", "call_002", "once") is False, "不能用别的任务批准"
        assert not t.done()
        assert m.resolve("task_a", "call_002", "once") is True
        assert await t == "once"

    asyncio.run(scenario())


def test_resolve_unknown_call_id_returns_false():
    assert ApprovalManager().resolve("task_a", "call_999", "once") is False


# ---------------- A 方案：越界「纯只读」免审批 ----------------

OUTSIDE = "C:\\Users\\y\\Downloads"


def test_wrapper_is_actually_unwrapped():
    """★ 回归：拆壳必须真的发生（不能只靠"全文匹配"蒙对）。

    旧正则只认"开关紧跟解释器"，`powershell -NoProfile -Command "…"` 会**静默不拆**，
    首词变成 powershell，只读白名单随之失效 —— 真实任务里用的正是这种写法。
    """
    assert ApprovalManager.unwrap('bash -c "rm -rf x"') == "rm -rf x"
    assert ApprovalManager.unwrap("cmd /c del x") == "del x"
    assert ApprovalManager.unwrap("cmd.exe /C del x") == "del x"
    assert ApprovalManager.unwrap('powershell -Command "Get-ChildItem x"') == "Get-ChildItem x"
    assert (
        ApprovalManager.unwrap('powershell -NoProfile -Command "Get-ChildItem x"')
        == "Get-ChildItem x"
    ), "带额外开关的 PowerShell 写法也必须拆开"
    assert (
        ApprovalManager.unwrap('pwsh -NoLogo -NoProfile -Command "ls"') == "ls"
    ), "多个开关也要能拆"
    # 不是包装形式的命令保持原样
    assert ApprovalManager.unwrap("ls -la") == "ls -la"


def test_simple_read_commands_are_auto_allowed():
    """真实任务里 6 次审批全是这类纯查询命令 —— 它们不该打断用户。"""
    cases = [
        ("ls /c/Users/y/Downloads", "ls"),
        ("find /c/Users/y/Downloads -type f | wc -l", "find|wc"),
        ("du -sh /c/Users/y/Downloads", "du"),
        ('powershell -NoProfile -Command "Get-ChildItem -LiteralPath \'C:\\Users\\y\\Downloads\' -Recurse -File"', "powershell 拆壳"),
        ("uname -a; ls -d ~/Downloads; pwd", "多段只读"),
        ("cat /c/Users/y/Downloads/a.txt", "cat"),
        ("grep -c foo /c/Users/y/Downloads/a.csv", "grep"),
        # ↓ 这三条是实机验证时被旧白名单误拦的真实写法
        ('ls -d "/c/Users/y/Downloads" 2>/dev/null; echo "---"; uname -a', "含 2>/dev/null"),
        ("cd 'C:/Users/y/Downloads' || exit 1\nfind . -type f -printf '%s\\n' | awk '{n++; s+=$1} END {print n, s}'", "cd + find|awk 管道"),
        ("cd 'C:/Users/y/Downloads' || exit 1\nfind . -type f -printf '%f|%s\\n' | awk -F'|' '{c[$1]++} END {for (e in c) print e, c[e]}' | sort | head -5", "find|awk|sort|head"),
    ]
    for cmd, label in cases:
        v = _check(cmd)
        assert v is not None, f"{label} 应当命中越界判定"
        assert v.action == "allow_readonly", f"{label} 应当免审批放行，实际 {v.action}"


def test_write_like_commands_still_ask():
    """只要有一点写/执行/外传的味道，就必须问。"""
    cases = [
        ("cat /c/Users/y/Downloads/a.txt > /c/Users/y/out.txt", "输出重定向"),
        ("find /c/Users/y/Downloads -delete", "find -delete"),
        ("find /c/Users/y/Downloads -exec rm {} ;", "find -exec"),
        ("ls /c/Users/y/Downloads | tee out.txt", "tee"),
        ("rm -f /c/Users/y/Downloads/a", "rm"),
        ('python -c "import shutil; shutil.rmtree(\'C:/Users/y/Downloads\')"', "python 脚本"),
        ("curl http://x -o /c/Users/y/Downloads/a", "curl 下载"),
        ("ls /c/Users/y/Downloads && rm -f x", "只读+危险混合"),
        ("Get-ChildItem C:\\Users\\y\\Downloads | Set-Content out.txt", "Set-Content"),
        ("ls /c/Users/y/Downloads | while read f; do echo $f; done", "shell 循环"),
        ("$d='C:\\\\Users\\\\y\\\\Downloads'; Get-ChildItem $d", "变量赋值脚本"),
        ("awk 'BEGIN{system(\"rm -rf /c/Users/y/Downloads\")}'", "awk system()"),
        ("sed -i 's/a/b/' /c/Users/y/Downloads/a.txt", "sed"),
        ("powershell -NoProfile -Command \"Set-Content -Path C:\\\\x -Value hi\"", "PS 写 cmdlet"),
    ]
    for cmd, label in cases:
        v = _check(cmd)
        assert v is not None, f"{label} 应当命中"
        assert v.action == "ask", f"{label} 必须弹审批，实际 {v.action}"


def test_dangerous_verb_inside_workspace_still_asks(prod_is_inside):
    """工作区内也照样问 —— 只读放行只针对"越界"这一维度。"""
    v = ApprovalManager().check("t", "rm -f workspace/a.txt", REQUIRED, prod_is_inside)
    assert v is not None and v.action == "ask"


# ---------------- 危险动作判定只看「子命令首词」（2026-09-30 误报修复）----------------


def test_verb_in_argument_literal_is_not_a_hit():
    """★ 真机误报回归：Agent 自查脚本时执行 grep，正则字面量里含 `rm` 就被误判成危险动作。"""
    assert ApprovalManager._verb_hits(r"grep -nE '\b(rm|mv|shred|del)\b' script.sh", REQUIRED) == []
    assert ApprovalManager._verb_hits('echo "rm -rf /"', REQUIRED) == []
    assert ApprovalManager._verb_hits("grep -c rm notes.txt", REQUIRED) == []
    assert ApprovalManager._verb_hits("ls -la", REQUIRED) == []


def test_wrappers_do_not_hide_the_real_verb():
    """跳过前缀包装后仍必须命中 —— 覆盖不能因为"只看首词"而降低。"""
    cases = {
        "bash -c \"rm -rf x\"": "rm",
        "cmd /c del x": "del",
        'powershell -NoProfile -Command "Remove-Item x"': "remove-item",
        "cd build && rm -rf dist": "rm",
        "cat a.txt | format": "format",
        "xargs rm -f": "rm",
        "sudo rm -rf /tmp/x": "rm",
        "timeout 5 rm -rf x": "rm",
        "nice -n 10 rm -rf x": "rm",
        "env FOO=1 rm -rf x": "rm",
        "find . -name '*.tmp' -exec rm {} ;": "rm",
        "find . -type d -execdir rmdir {} ;": "rmdir",
    }
    for cmd, expected in cases.items():
        hits = ApprovalManager._verb_hits(cmd, REQUIRED)
        assert expected in hits, f"{cmd!r} 应命中 {expected}，实际 {hits}"


def test_readonly_pass_also_remembers_directory():
    """只读放行同样按归一化目录记账，换写法不会重复触发。"""
    m = ApprovalManager()
    a = m.check("t", "ls C:\\Users\\y\\Downloads", REQUIRED, lambda p: False)
    b = m.check("t", "find /c/Users/y/Downloads -type f", REQUIRED, lambda p: False)
    assert a is not None and b is not None
    assert a.key == b.key
