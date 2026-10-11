"""二十六轮第 7 批第 10 处：git 的【配置注入环境变量】面（施工单风险点②）。

现状证据（独立验证员在 0d4bee4 上实测 + 本班复现）：
    git -c core.pager='…' log                                    → ASK
    GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.pager \\
      GIT_CONFIG_VALUE_0='!echo PWNED' git log                   → PASS ★绕过
    env GIT_CONFIG_COUNT=1 … git log                             → ASK（包装器面拦到）
  ⇒ 同一能力面，两种写法一个拦一个不拦；真机实测 `… GIT_CONFIG_KEY_0=alias.pwn
    GIT_CONFIG_VALUE_0='!echo PWNED' git pwn` **真的执行了 shell**。

为什么只能在这个位置拦：payload（键与值）**根本不在命令行参数里**——它们是通过
环境变量传给 git 的。所以参数面（`_GIT_EXEC_FLAGS`，管 `-c/--config-env/--exec-path`
那套）永远看不见；唯一线索是命令开头那串 `NAME=value` 赋值前缀。
（而前缀又会被 `_deep_parts` 统一剥掉，所以该面必须看剥之前的原串。）

边界（宁严 vs 误伤的取舍，写在这里供复核）：
  · head 不是 git 时不拦（`GIT_CONFIG_COUNT=1 ./myscript.sh` 是把变量喂给别的程序）；
  · `GIT_CONFIG_COUNT=1 git status` 这种"只有 COUNT 没有 KEY/VALUE"也拦——
    这三条是 git 专用的注入开关，正常脚本不写，宁可多问一次。
"""
from __future__ import annotations

import pytest

from tests.test_approval_capability_matrix import _is_ask, _make_workspace
from tests.test_round9_fixes import _LIFE_MODES, _run_cmd  # noqa: F401


@pytest.fixture()
def ws():
    w, _out = _make_workspace()
    return w


_INJ = "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.pwn GIT_CONFIG_VALUE_0='!echo PWNED' "

# 注入形态：三模式都必须拦（这条能力与"是否断网"无关——它改写的是本机 git 配置）
_ASKS = [
    _INJ + "git pwn",
    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.pager GIT_CONFIG_VALUE_0='!echo PWNED' git log",
    "GIT_CONFIG_KEY_0=core.pager GIT_CONFIG_VALUE_0='!id' git log",       # 没有 COUNT 也要拦
    "FOO=bar GIT_CONFIG_KEY_0=alias.x GIT_CONFIG_VALUE_0='!id' git st",   # 前面还有别的赋值
    "GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=alias.a GIT_CONFIG_VALUE_0='!id' "
    "GIT_CONFIG_KEY_1=alias.b GIT_CONFIG_VALUE_1='!id' git a",            # 多组键值
    _INJ + "/usr/bin/git pwn",                                            # 带路径的 head
    "echo hi && " + _INJ + "git pwn",                                     # 分段之后
]

# 不能误伤
_PASSES = [
    "echo GIT_CONFIG_COUNT=1",                    # 只是把字符串打印出来，不是前缀
    'echo "GIT_CONFIG_KEY_0=core.pager"',
    "GIT_CONFIG_COUNT=1 ls",                      # head 不是 git ⇒ 不是 git 注入
    "GIT_CONFIG_COUNT=1 ./myscript.sh",
    "FOO=bar git log",
    "git log",
    "DOCKER_HOST=tcp://x docker ps",
]


@pytest.mark.parametrize("cmd", _ASKS)
def test_git_config_env_injection_asks(ws, cmd):
    """★ git 配置注入环境变量必须拦：它与 `git -c` 是同一能力面。"""
    for in_sandbox, network_disabled in _LIFE_MODES:
        v = _run_cmd(ws, cmd, in_sandbox=in_sandbox, network_disabled=network_disabled)
        assert _is_ask(v), f"GIT_CONFIG 注入必须拦：{cmd} → {v}"


@pytest.mark.parametrize("cmd", _PASSES)
def test_git_config_env_face_no_false_positive(ws, cmd):
    """反误伤：不是「git 的注入前缀」就不许拦。"""
    v = _run_cmd(ws, cmd)
    assert not _is_ask(v), f"不该拦却拦了（误伤）：{cmd} → {v}"


def test_injection_payload_is_not_in_argv():
    """机制说明锚点：payload 不在参数里 ⇒ 参数面结构上就拦不到，只能看赋值前缀。

    这条用纯字符串事实钉住"为什么必须单独做这个面"：如果把 payload 挪进参数，
    那它就是 `git -c` 形态（已被别的面覆盖），本面就不需要了。
    """
    cmd = _INJ + "git pwn"
    argv = cmd.split()
    assert "alias.pwn" in " ".join(argv), "前提：payload 确实出现在命令串里（在赋值段）"
    # git 之后的参数只有子命令本身——没有任何 -c/--config-env
    git_at = next(i for i, t in enumerate(argv) if t.endswith("git"))
    assert argv[git_at + 1:] == ["pwn"], argv[git_at:]
    assert not any(t.startswith("-c") or t.startswith("--config") for t in argv[git_at + 1:])
