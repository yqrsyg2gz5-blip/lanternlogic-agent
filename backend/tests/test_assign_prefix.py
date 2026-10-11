"""二十六轮第 7 批第 9 处：shell 赋值前缀（`FOO=bar cmd`）必须穿透到真 head。

现状（独立验证员在 0d4bee4 上实测 + 本班复现，原始输出）：
    git -c core.pager='docker system prune -a' log        → ASK
    FOO=bar git -c core.pager='docker system prune -a' log → PASS  ★绕过
    git push origin main                                  → ASK
    FOO=bar git push origin main                          → PASS  ★绕过（net 面）
    dd if=/dev/zero of=$HOME/x                            → ASK
    FOO=bar dd if=/dev/zero of=$HOME/x                    → PASS  ★绕过（写入面）
根因：代码里【只有容器包装器那一条面】会剥赋值前缀（旧局部闭包），而
git/pip/net/写入/动词等**所有按 head 判定的面**都是 `part.split()[0]` 直接取
head ⇒ 看到 "FOO=bar" 就不认识本体，整族静默放行。
修法：在咽喉点 `_deep_parts()` 出口统一剥（`_strip_assign_prefix_str`），
一处修好所有面；其余部分逐字节不动（不 split/join，保引号结构）。

本文件用【不变量】而不是逐条枚举来钉住它——见
`test_assign_prefix_does_not_change_verdict`：加前缀前后判定必须一字不变。
这样以后新增任何命令形态都自动被覆盖，不会重演"只修一个形态"。
"""
from __future__ import annotations

import pytest

from tests.test_approval_capability_matrix import _is_ask, _make_workspace
from tests.test_round9_fixes import _LIFE_MODES, _run_cmd  # noqa: F401


@pytest.fixture()
def ws():
    """本文件自带（`ws` 是 test_round9_fixes 的模块级 fixture，跨文件不可见）。"""
    w, _out = _make_workspace()
    return w


# 七种赋值前缀写法（同类变体：裸值 / 单引号含空格 / 多前缀串联 / 反斜杠转义空格 /
# ★ 含空格的长引号值（token 级启发式会截断在引号中间——第一版实现的残余洞）/
# 双引号长值 / 双引号内转义）
_PREFIX_FORMS = [
    "FOO=bar ",
    "FOO='a b' ",
    "A=1 B=2 ",
    "FOO=a\\ b ",
    "FOO='!echo PWNED' ",
    'FOO="long value here" ',
    'FOO="a\\"b c" ',
]

# 覆盖面：git 间接执行 / net / 写入 / 工具间接执行 / 动词 / 解释器 / 无害对照
_BASES = [
    "git log",
    "git status",
    "git push origin main",
    "git -c core.pager='docker system prune -a' log",
    "git --config-env=core.pager=EVIL log",
    "git --exec-path=/tmp/evil log",
    "git --attr-source=HEAD check-attr filter f.txt",
    "curl http://example.com",
    "wget http://example.com/x",
    "dd if=/dev/zero of=$HOME/x",
    "echo x > /etc/hosts",
    "pip install --constraint /tmp/x pkg",
    "tar --to-command='sh -c id' -xf a.tar",
    "rsync -e 'sh -c id' a b",
    "rm -rf /tmp/x",
    "python -c 'import os'",
    "sh -c 'rm -rf /'",
    "echo hi",
    "ls",
    "cat /w/x.txt",
    "tee /etc/hosts",
]


@pytest.mark.parametrize("base", _BASES)
def test_assign_prefix_does_not_change_verdict(ws, base):
    """★ 核心不变量：给命令加 shell 赋值前缀，审批判定必须一字不变。

    修复前：`git push origin main`(ASK) 加前缀后变 PASS（本班实测 21 基例里
    4 条不一致：git push / git -c / git --config-env / dd of=$HOME）。
    修复后：21 基例 × 4 前缀 = 84 组全部一致。
    """
    want = _is_ask(_run_cmd(ws, base))
    for p in _PREFIX_FORMS:
        got = _is_ask(_run_cmd(ws, f"{p}{base}"))
        assert got == want, (
            f"赋值前缀改变了判定：{p + base!r} → {'ASK' if got else 'PASS'}"
            f"（无前缀本体是 {'ASK' if want else 'PASS'}）——赋值前缀必须穿透到真 head"
        )


# 独立验证员实测的 4 条真绕过（修复前全部 PASS）——按名单独钉住，
# 防"不变量被弱化/基例被删"式的静默回归。
_BYPASSES = [
    "FOO=bar git -c core.pager='docker system prune -a' log",
    "FOO=bar git --config-env=core.pager=EVIL log",
    "FOO=bar git push origin main",
    "FOO=bar dd if=/dev/zero of=$HOME/x",
]


@pytest.mark.parametrize("cmd", _BYPASSES)
def test_assign_prefix_bypass_matches_bare(ws, cmd):
    """★ 这 4 条是验证员实测"加前缀就绕过"的形态；判据**按模式对齐**。

    ⚠️ 不能用"三模式都必须 ASK"：`沙箱+断网` 模式下联网命令**本体**就是 PASS
    （既有设计——网络被禁用时联网不构成风险；本班实测
    `git push origin main` 在 [sb=True,nd=True] 下裸命令也是 PASS）。
    所以正确判据是：**加前缀的判定 == 同模式下裸命令的判定**。
    """
    bare = cmd.split(" ", 1)[1]  # 去掉 "FOO=bar "
    for in_sandbox, network_disabled in _LIFE_MODES:
        vb = _is_ask(_run_cmd(ws, bare, in_sandbox=in_sandbox,
                              network_disabled=network_disabled))
        vp = _is_ask(_run_cmd(ws, cmd, in_sandbox=in_sandbox,
                              network_disabled=network_disabled))
        assert vp == vb, (
            f"赋值前缀改变了判定：{cmd!r} → {'ASK' if vp else 'PASS'}，"
            f"而同模式裸命令 {bare!r} → {'ASK' if vb else 'PASS'}"
            f"（[in_sandbox={in_sandbox}, network_disabled={network_disabled}]）"
        )
    # 宿主模式（验证员复现绕过时用的模式）这一条必须真的拦住
    assert _is_ask(_run_cmd(ws, cmd)), (
        f"宿主模式下赋值前缀绕过未被拦：{cmd} → {_run_cmd(ws, cmd)}"
    )


# 前缀出现在分号/&& 之后（分段）也要穿透
_SEGMENTED = [
    "echo hi && FOO=bar git push origin main",
    "FOO=bar ; git push origin main",
]


@pytest.mark.parametrize("cmd", _SEGMENTED)
def test_assign_prefix_after_separator_asks(ws, cmd):
    v = _run_cmd(ws, cmd)
    assert _is_ask(v), f"分段后的赋值前缀绕过必须拦：{cmd} → {v}"


# ★ 第一版实现的残余洞（本班自查发现并修复）：
#   token 级启发式对【含空格的长引号值】会截断在引号中间——原串
#   `FOO='!echo PWNED' git push` 被 .split() 切成 `FOO='!echo` + `PWNED'` + …，
#   碎片规则只吞 ≤4 字的引号残片 ⇒ 残余成 `PWNED' git push origin main`
#   ⇒ 真 head 依然露不出来 ⇒ 该形态**仍然绕过**（实测 PASS）。
#   改用【引号感知扫描器】(_skip_shell_word) 后修复；下面按名成对钉住。
_QUOTED_PREFIX_PAIRS = [
    ("FOO='!echo PWNED' git push origin main", "git push origin main"),
    ('FOO="long value here" git push origin main', "git push origin main"),
    ("FOO='aaaa bbbb cccc' git -c core.pager='docker system prune -a' log",
     "git -c core.pager='docker system prune -a' log"),
    ('FOO="a\\"b c" git push origin main', "git push origin main"),
]


@pytest.mark.parametrize("prefixed,bare", _QUOTED_PREFIX_PAIRS)
def test_quoted_value_prefix_matches_bare(ws, prefixed, bare):
    """引号内含空格的赋值前缀，判定必须与裸命令一致（残余洞回归锚点）。"""
    vb = _is_ask(_run_cmd(ws, bare))
    vp = _is_ask(_run_cmd(ws, prefixed))
    assert vb, f"本锚点的前提失效：{bare!r} 本体应为 ASK"
    assert vp == vb, (
        f"含空格的长引号值前缀改变了判定：{prefixed!r} → {'ASK' if vp else 'PASS'}，"
        f"裸命令 {bare!r} → {'ASK' if vb else 'PASS'}"
    )


def test_quoted_value_prefix_yields_real_head():
    """剥完必须露出真 head（残余洞的直接判据：不能截断在引号中间）。"""
    from app.approval import _strip_assign_prefix_str as f

    assert f("FOO='!echo PWNED' git push origin main") == "git push origin main"
    assert f('FOO="long value here" git push') == "git push"
    assert f("GIT_CONFIG_VALUE_0='!echo PWNED' git log") == "git log"
    assert f("FOO='a b'  git   push") == "git   push"


def test_strip_assign_prefix_str_is_byte_exact():
    """剥前缀必须【其余部分逐字节不动】——不 split/join，保引号与空格结构。

    ★ 这条是"为什么不能简单 split()[1:] join"的证据：下游有大量按引号/正则
    分析的判定面（重定向、find -exec、脚本扫描），重建字符串会破坏结构。
    """
    from app.approval import _strip_assign_prefix_str as f

    cases = [
        ("FOO=bar git log", "git log"),
        ("A=1 B=2 git push origin main", "git push origin main"),
        ("FOO='a b'  git   push", "git   push"),          # 内部多空格保留
        ("FOO=a\\ b git push", "git push"),               # 反斜杠转义空格的续体
        ("FOO=\"a b\" git push", "git push"),
        ("  FOO=bar   git   log  ", "git   log  "),
        ("git log", "git log"),                           # 无前缀 → 原样返回
        ("FOO=bar", ""),                                  # 纯赋值片段 → 空
        # ★ 不能误伤：引号里的 = 不是赋值前缀，整串必须原样
        ('git -c core.pager="x y" log', 'git -c core.pager="x y" log'),
        ("git config --global user.name=a=b", "git config --global user.name=a=b"),
    ]
    for src, want in cases:
        got = f(src)
        assert got == want, f"剥前缀结果不符：{src!r} → {got!r}，期望 {want!r}"


def test_strip_assign_prefix_does_not_touch_option_values():
    """反回归：只在【开头】剥，命令中段/选项值里的 NAME= 一律不动。"""
    from app.approval import _strip_assign_prefix_str as f

    for src in [
        "git -c core.pager=x log",
        "docker run -e FOO=bar img",
        "env FOO=bar git log",          # env 是包装器，前缀在 env 之后，此处不动
        "make VAR=1 build",
    ]:
        assert f(src) == src, f"不该动却动了：{src!r} → {f(src)!r}"
