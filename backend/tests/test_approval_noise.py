# -*- coding: utf-8 -*-
"""★ 2026-10-11「审批降噪」后续：**只读白名单要认得真实命令的形态**。

## 现场（真机那条 18 秒卡点视频，DeepSeek flash）

审计账 + 那次任务的 `events.jsonl` 里一共 **84 条 shell 命令**，用真判定重放：
**78 条判 ask**（= 真机会弹 78 张卡），只有 4 条走"越界只读"、2 条放行。用户
一晚被弹了上百次 ✗。

翻这 78 条的归因（`_diag_classify.py` 的口径）：

| 记忆键 | 次数 | 真相 |
|---|---|---|
| `outside:d:/ai/agent-shell/backend` | 20 | 全是在调**本仓自带的 venv python** 跑工作区脚本 |
| `outside:d:/minimax` | 7 | 全是 `"$FF" -version` / `-filters` / `ffprobe` 这类**探测** |
| `outside:c://program` | 4 | 全是 `export CHROME_PATH="C:\\Program Files …\\msedge.exe"` |
| `outside:d:/_ghsrc/…` | 2 | `cd X && sed -n '120,330p' render.mjs` —— **只读** ✗ |
| `struct:heredoc` / `struct:subst` / `net:inline:*` | 42 | 内联代码/heredoc/`$()` —— **本来就该问** ✓ 一条不动 ✓ |
| `verb:rm` / `cp` / `struct:var` | 5 | 真的在删/拷/改名 —— **本来就该问** ✓ |

## 两个根因（都不是"策略太严"）

① **只读快路压根没跑到** —— `check()` 里它是
   `if not self._outside_paths(...) and self._readonly_ok(...)`，
   而真机命令**每一条都带一个工作区之外的绝对路径**（`cd /d/测试-Flash对比/…`、
   `"/d/AI/agent-shell/backend/.venv/Scripts/python.exe"`、`/d/MiniMax Design/…`）
   ⇒ `_outside_paths` 非空 ⇒ **`_readonly_ok` 一次都没被求值** ✗
   （上一班的"只读批量放行"因此在这条任务上等于没接上 ✓ 诊断里 `readonly_ok` 列全是 False ✓）

② **白名单不认真实写法** —— 即使跑到，也过不去：
   · `FF="/d/MiniMax…/ffmpeg.exe"` —— `_part_readonly` 见 `=` 即判"脚本片段" ✗
   · `export PYTHONIOENCODING=utf-8` —— 同上 ✗（它只是个静态赋值 ✓ 不看文件 ✓）
   · `sed -n '120,330p' x.mjs` / `test -f x` —— 这两个**根本不在白名单里** ✗
   · `ffmpeg -version` / `node -v` / `python -V` —— 整类探测命令不在表里 ✗

## 这一组钉住的红线（一条都不许松 ✗）

删除 / 覆写已有文件 / 内联代码 / heredoc / `$()` / 联网外传 / 越界写 /
包装器藏破坏性动作 —— **全部照旧问** ✓（`test_approval_friction.py` 里那批
红线用例原样保留 ✓ 本文件只加"该不问的别问"）。
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.approval import ApprovalManager  # noqa: E402

REQUIRED = ["rm", "del", "rmdir", "rd", "erase", "format", "reg", "remove-item"]

#: 真机那条任务里出现的**真命令**（逐字从 events.jsonl 抄的 ✓ 只删了正文）
_WS = "/d/测试-Flash对比/LanternLogic-Agent/backend/data/tasks/task_x/workspace"
_VENV = "/d/AI/agent-shell/backend/.venv/Scripts/python.exe"
_FF = "/d/MiniMax Design/current/resources/ffmpeg/ffmpeg.exe"


def _mgr() -> ApprovalManager:
    m = ApprovalManager()
    m._forever.clear()
    return m


def _inside_only_ws(p: str) -> bool:
    """生产里 `_path_is_inside` 的形状：只认工作区。"""
    s = str(p).replace("\\", "/").lower()
    return s.startswith(_WS.lower())


# ═══ ① 只读快路必须**真的跑到**（根因一）═══

def test_readonly_fastpath_runs_even_when_command_touches_outside_paths():
    """★★ 核心：命令里有**工作区之外的绝对路径**，但它**纯粹是只读** ⇒ 不弹审批 ✓。

    真机原话（seq 85）：
        `cd /d/_ghsrc/efi/every-frame-is-code-main/render && sed -n '120,330p' render.mjs`
    旧判定：`_outside_paths` 非空 ⇒ 只读快路**整条跳过** ⇒ 判 `ask` ✗
    （于是"越界只读 → 记审计"那一档也一起失效了 ✗ —— 它本该走 allow_readonly ✓）
    """
    m = _mgr()
    cmd = "cd /d/_ghsrc/efi/every-frame-is-code-main/render\nsed -n '120,330p' render.mjs"
    v = m.check("task_ro", cmd, REQUIRED, _inside_only_ws)
    assert v is None or v.action != "ask", f"只读的 sed 仍在弹审批 ✗：{v}"


def test_pure_readonly_probe_commands_no_longer_ask():
    """真机那一屏**探测命令**：版本/过滤器/字体/浏览器在不在 —— 看一眼而已 ✓。

    旧判定：两条路都堵死 ——
      · `ffmpeg`/`ffprobe`/`node`/`python` 不在只读白名单 ✗
      · 命令里带 `/d/MiniMax Design/…`（越界）⇒ 只读快路又跳过 ✗
    """
    m = _mgr()
    cmds = [
        f'"{_FF}" -version 2>&1 | head -1',
        f'"{_FF}" -hide_banner -filters 2>/dev/null | grep -E " xfade | drawtext "',
        '"/d/MiniMax Design/current/resources/ffmpeg/ffprobe.exe" -v error -show_entries format=duration -of default=nw=1 x.wav',
        "node -v 2>&1",
        f'"{_VENV}" -V',
        "test -f '/c/Windows/Fonts/msyhbd.ttc' && echo OK || echo MISSING",
        "command -v ffmpeg; which ffprobe",
    ]
    bad = []
    for c in cmds:
        v = m.check("task_ro", c, REQUIRED, _inside_only_ws)
        if v is not None and v.action == "ask":
            bad.append((c, v.reason))
    assert not bad, "这些纯探测命令仍在弹审批 ✗：\n" + "\n".join(f"  {c}  ← {r}" for c, r in bad)


def test_static_shell_assignments_do_not_defeat_the_readonly_whitelist():
    """根因二之一：`FF="…"` / `export PYTHONIOENCODING=utf-8` 是**静态赋值** ✓ 不看文件 ✓。

    旧判定：`_part_readonly` 一见 `=` 就判"脚本片段，判定不了" ⇒ 整条命令判"非只读" ✗
    —— 真机 84 条里有 **53 处** `export …`、**32 处** `NAME=…` ✓ 它们是命令噪声的大头 ✓。

    ★ 只放**静态**值 ✗：值里带 `$(…)`/反引号/`$VAR` 的一律照旧问 ✓（下一条测试钉着 ✓）。
    """
    m = _mgr()
    cmds = [
        f'FF="{_FF}"\necho "1) ffmpeg:"; "$FF" -version 2>&1 | head -1',
        'export CHROME_PATH="C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"\ntest -f "$CHROME_PATH" && echo OK',
        "export PYTHONIOENCODING=utf-8\nls -la out/",
    ]
    bad = []
    for c in cmds:
        v = m.check("task_ro", c, REQUIRED, _inside_only_ws)
        if v is not None and v.action == "ask":
            bad.append((c, v.reason))
    assert not bad, "静态赋值把只读命令带成了审批 ✗：\n" + "\n".join(f"  {c}  ← {r}" for c, r in bad)


def test_readonly_reads_outside_workspace_are_audited_not_asked():
    """越界**只读** ⇒ 走既有的 `allow_readonly`（留一条审计 ✓ 不打断人 ✓）。

    ★ 语义与既有契约一致（`test_approval_friction.py::test_readonly_does_not_relax_outside_path_semantics` ✓）：
      只读越界 = allow_readonly ✓；越界**写** = ask ✓（下一条测试钉着 ✓）。
    """
    m = _mgr()
    v = m.check("task_ro", "cat /d/MiniMax Design/current/README.md", REQUIRED, _inside_only_ws)
    assert v is not None and v.action == "allow_readonly", f"越界只读没走审计档 ✗：{v}"


# ═══ ② 红线：上面这些放宽，一条都不许带出"该问的" ═══

def test_outside_writes_still_ask():
    """越界**写**照旧问 ✓（只读快路不碰写的那一档 ✓）。"""
    m = _mgr()
    for cmd in (
        "cp renders/out.mp4 /d/别的目录/out.mp4",
        "rm -f /d/MiniMax Design/current/tmp.txt",
        "mkdir -p /d/别的目录/newdir",
        'cd /d/_ghsrc/efi/x && echo hi > notes.txt',
        'cd /d/_ghsrc/efi/x && sed -i "s/a/b/" render.mjs',
    ):
        v = m.check("task_danger", cmd, REQUIRED, _inside_only_ws)
        assert v is not None and v.action == "ask", f"越界写被放行了 ✗：{cmd} → {v}"


def test_dynamic_values_still_ask():
    """值里有 `$()`/`$VAR`/反引号的赋值 ⇒ 判定不了 ⇒ 照旧问 ✓。"""
    m = _mgr()
    for cmd in (
        'FF="$(which ffmpeg)"; "$FF" -version',
        'export PATH="/d/MiniMax Design/current/resources/ffmpeg:$PATH"\nffmpeg -version',
        'X=`whoami`; echo "$X"',
    ):
        v = m.check("task_danger", cmd, REQUIRED, _inside_only_ws)
        assert v is not None and v.action == "ask", f"动态赋值被放行了 ✗：{cmd} → {v}"


def test_inline_code_and_heredoc_still_ask_after_the_widening():
    """★ 内联代码 / heredoc / `$()` 照旧问 ✓（本次**故意**不动它们 ✓）。"""
    m = _mgr()
    for cmd in (
        f'"{_VENV}" -c "import json; print(json.load(open(\'x.json\')))"',
        "python - <<'PY'\nprint(1)\nPY",
        "python3 - <<'PY' 2>/dev/null || echo no-python3\nPY",
        "node -e \"console.log('node',process.version)\"",
        "perl -0pi -e 's/a/b/' tools/build_timeline.py",
        "echo $(rm -rf /tmp/x)",
    ):
        v = m.check("task_danger", cmd, REQUIRED, _inside_only_ws)
        assert v is not None and v.action == "ask", f"内联/替换被放行了 ✗：{cmd!r} → {v}"


def test_destructive_heads_stay_out_of_the_readonly_whitelist():
    """`sed -i`（就地改）与 `ffmpeg -i in -c out`（出片）**不是**只读 ✓ 照旧问 ✓。"""
    m = _mgr()
    for cmd in (
        "sed -i 's/a/b/' render.mjs",
        f'"{_FF}" -hide_banner -y -i in.mp4 out.mp4',
        f'"{_FF}" -hide_banner -i in.wav -filter_complex_script out/graph.txt -map "[musd]" out/mix.wav',
    ):
        v = m.check("task_danger", cmd, REQUIRED, _inside_only_ws)
        assert v is not None and v.action == "ask", f"写形态的媒体命令被放行了 ✗：{cmd} → {v}"


def test_readonly_widening_does_not_touch_the_protected_dirs_guard():
    """应用目录硬保护仍在最前面 ✓（放宽只读判定不许把它盖掉 ✓）。"""
    m = _mgr()
    venv = pathlib.Path(__file__).resolve().parents[1] / ".venv"
    cmd = f'cd /w && rm -rf "{venv}"'
    v = m.check("task_p", cmd, REQUIRED, _inside_only_ws)
    assert v is not None and v.action == "ask", f"硬保护失效 ✗：{v}"


# ═══ ③ 「总是允许」要连着**完整命令**一起记住（真机 176 次的另一半）═══

def test_always_remember_uses_the_full_command_not_a_truncated_one():
    """★★ 真机根因：`_approval_gate(..., context=command[:120])` 把命令**截断**了 ✗
    ⇒ `remember()` 存的是**前 120 字**的指纹 ⇒ 真命令（几百字）的指纹永远对不上 ✗
    ⇒ 用户点了「总是允许」，同一条命令**下一次又弹** ✓（审计账里 3 次 `always` 全白点 ✓）。

    这里钉住的契约：**记住的是用户看到的那条完整命令** ✓。
    """
    m = _mgr()
    full = ('cd "/d/测试-Flash对比/LanternLogic-Agent/backend/data/tasks/task_x/workspace"\n'
            + 'export PYTHONIOENCODING=utf-8\n'
            + '"/d/AI/agent-shell/backend/.venv/Scripts/python.exe" tools/build_timeline.py; echo "EXIT=$?"\n'
            + "# " + "填充" * 80)                      # 保证远超 120 字
    v = m.check("task_keep", full, REQUIRED, _inside_only_ws)
    assert v is not None and v.action == "ask", f"前提不成立（这条本来该问）：{v}"
    m.remember("task_keep", v.key, full)               # 用户点了「总是允许」
    again = m.check("task_keep", full, REQUIRED, _inside_only_ws)
    assert again is None or again.action != "ask", f"同一条命令又问了一遍 ✗：{again}"
    # 截断版（旧的错误做法）**不该**被记住 —— 保证"记住的是完整原文"这条语义没跑偏
    assert not m._command_remembered("task_keep", full[:120] + "（截断）")


def test_always_remember_works_even_for_structural_commands():
    """★ 用户点「总是允许」时，**同一条**结构命令（内联代码/heredoc）不再问 ✓。

    边界（一条都不松 ✓）：
      · 只认**一模一样**的这一条 ✗（换一个字就是新命令 ⇒ 照问 ✓）
      · 只认**这个任务** ✗（跨任务照旧靠 `forever` 名单 ✓）
      · `struct:*` **整类**的记忆键仍然不写 ✗（一次豁免 = 整类任意代码 ✗ 审计 P1 ✓）
    """
    m = _mgr()
    one = "python - <<'PY'\nprint('第一条')\nPY"
    two = "python - <<'PY'\nprint('第二条')\nPY"
    v = m.check("task_k", one, REQUIRED, _inside_only_ws)
    assert v is not None and v.action == "ask", f"前提不成立：{v}"
    # 用户点了「总是允许」—— 记忆里**只**多了一条"命令指纹"，没有 struct 整类键 ✗
    m.remember("task_k", v.key, one)
    assert not any(k.startswith("struct:") for k in m._always.get("task_k", set())), \
        "结构性记忆键被写进去了 ✗（那是「整类任意代码免审批」✗ 审计 P1）"
    assert m.check("task_k", one, REQUIRED, _inside_only_ws) is None, "同一条又被问了 ✗"
    other = m.check("task_k", two, REQUIRED, _inside_only_ws)
    assert other is not None and other.action == "ask", f"换了一条也不问了 ✗：{other}"
    cross = m.check("task_other", one, REQUIRED, _inside_only_ws)
    assert cross is not None and cross.action == "ask", f"记忆跨任务泄漏 ✗：{cross}"


def test_command_memory_does_not_bypass_still_asking_heads():
    """★ 记忆**不豁免**硬保护 ✓（它在记忆之前判 ✓）—— 与既有用例同口径 ✓。"""
    m = _mgr()
    venv = pathlib.Path(__file__).resolve().parents[1] / ".venv"
    cmd = f'cd /w && rm -rf "{venv}"'
    v = m.check("task_p", cmd, REQUIRED, _inside_only_ws)
    assert v is not None and v.action == "ask"
    m.remember("task_p", v.key, cmd)
    again = m.check("task_p", cmd, REQUIRED, _inside_only_ws)
    assert again is not None and again.action == "ask", f"记忆绕过了硬保护 ✗：{again}"


# ═══ ④ 真机回归：那条 18 秒视频的 **84 条命令**要按"该放/该问"分类 ═══

#: 真机 events.jsonl 里抄下来的**逐字命令**（只删了 heredoc 的正文 —— 太长了 ✓）
#: 左边期望：True = 不该问（只读/纯探测）✓ / False = 该问（写·内联·herdoc·删）✗
_REAL_SHAPES: list[tuple[bool, str]] = [
    # ── 该放：纯探测（原判全问 ✗）──
    (True, '"/d/MiniMax Design/current/resources/ffmpeg/ffmpeg.exe" -version 2>&1 | head -1'),
    (True, '"/d/MiniMax Design/current/resources/ffmpeg/ffmpeg.exe" -hide_banner -filters 2>/dev/null | grep -E " xfade | drawtext "'),
    (True, '"/d/MiniMax Design/current/resources/ffmpeg/ffprobe.exe" -v error -show_entries format=duration -of default=nw=1 x.wav'),
    (True, "node -v 2>&1"),
    (True, '"/d/AI/agent-shell/backend/.venv/Scripts/python.exe" -V'),
    (True, "test -f '/c/Windows/Fonts/msyhbd.ttc' && echo OK || echo MISSING"),
    (True, "command -v ffmpeg; which ffprobe"),
    # ── 该放：静态赋值 + 只看一眼（原判全问 ✗）──
    (True, 'FF="/d/MiniMax Design/current/resources/ffmpeg/ffmpeg.exe"\n'
           'echo "1) ffmpeg:"; "$FF" -version 2>&1 | head -1'),
    (True, 'export CHROME_PATH="C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"\n'
           'test -f "$CHROME_PATH" && echo OK'),
    (True, "export PYTHONIOENCODING=utf-8\nls -la out/"),
    # ── 该放：越界**只读**（走 allow_readonly ✓ 不是 ask ✓）──
    (True, "cd /d/_ghsrc/efi/every-frame-is-code-main/render\nsed -n '120,330p' render.mjs"),
    (True, "cat /d/MiniMax Design/current/README.md"),
    # ── 该问：写已有文件 / 删 / 拷 / 就地改（原判也问 ✓ 一条都不许松 ✗）──
    #   ★ `mkdir -p 新目录` **不算** ✗：建新目录不覆盖、不删除任何东西 ✓
    #     （与既有契约一致：`test_approval_friction` 只钉"覆写已有文件"要问 ✓）
    (True, 'cd /w\nmkdir -p data assets/audio/vo renders out/qa'),
    (False, 'cp /d/_ghsrc/efi/render/{a.mjs,b.mjs} tools/render/'),
    (False, 'cd /d/_ghsrc/efi/x && echo hi > notes.txt'),
    (False, 'cd /d/_ghsrc/efi/x && sed -i "s/a/b/" render.mjs'),
    (False, "rm -f out/stills/*.png"),
    # ── 该问：内联代码 / heredoc / 命令替换（安全底线 ✓）──
    (False, '"/d/AI/agent-shell/backend/.venv/Scripts/python.exe" -c "import json;print(1)"'),
    (False, 'node -e "console.log(\'node\',process.version)"'),
    (False, "perl -0pi -e 's/a/b/' tools/build_timeline.py"),
    (False, "python - <<'PY'\nimport io\nprint(1)\nPY"),
    (False, 'echo "x: $($FF -version)"'),
    (False, 'export PATH="/d/MiniMax Design/current/resources/ffmpeg:$PATH"\nnode beatmap.mjs x.wav'),
]


def test_real_task_shapes_are_classified_the_same_way_after_the_fix():
    """★★ 真机回归：那 84 条命令的**典型形态**逐条钉住"该放/该问" ✗✓。

    为什么值得单独一条：这次改动**放宽的都是"看一眼"**（探测/静态赋值/越界只读 ✓），
    **一条安全底线都没动**（写 / 内联代码 / heredoc / `$()` / 删 ✓）。
    以后谁再动 `_readonly_ok` 或白名单，这条会先红 —— 而它左边那列就是真机事实 ✓。
    """
    m = _mgr()
    bad = []
    for should_pass, cmd in _REAL_SHAPES:
        v = m.check("task_real", cmd, REQUIRED, _inside_only_ws)
        asked = v is not None and v.action == "ask"
        if should_pass and asked:
            bad.append(f"该放却被问 ✗：{cmd[:70]}  ← {v.reason}")
        if not should_pass and not asked:
            bad.append(f"该问却被放 ✗：{cmd[:70]}  ← {v}")
    assert not bad, "真机形态分类跑偏了：\n" + "\n".join("  " + b for b in bad)
    assert sum(1 for ok, _ in _REAL_SHAPES if ok) >= 12, "该放的那一档样本太少，这条测试会失去判别力 ✗"
    assert sum(1 for ok, _ in _REAL_SHAPES if not ok) >= 8, "该问的那一档样本太少 ✗"
