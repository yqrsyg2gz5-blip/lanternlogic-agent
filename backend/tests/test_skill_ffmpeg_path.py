# -*- coding: utf-8 -*-
"""技能自带的「找 ffmpeg」共用工具（`tools/ffmpeg_path.py`）的验收测试。

## 为什么有它（2026-10-13 真机事故）

应用执行命令用的是 **git bash**（实测 `bash 5.2.37`），而 bash 里：

    which ffmpeg  → /d/Software/GPT-SoVITS/GPT-SoVITS-v2pro-20250604/runtime/ffmpeg
    libx264 条数  → 0        ← **残缺版，编不了 H.264** ✗

完整的那个在 `D:\\MiniMax Design\\current\\resources\\ffmpeg`（libx264 = 2 ✓），
但它在 bash 的 PATH 里**排最后**，被前面那个盖住了。

而且**技能的文档/工具给的是 PowerShell 语法**（`$env:PATH = ...`）——
bash 直接报 `:PATH: command not found`，照做反而掉坑 ✗

⇒ 本模块做一件事：**给定可用候选，找出真正能用的那个 ffmpeg/ffprobe**，
   并给出 **bash 与 PowerShell 两种**可直接复制的设置命令 ✓

★ 关键约束：**探测必须真的跑一下 `-encoders` 确认有 libx264**，
  不能只看"文件存在"（PATH 上那个也存在，但它是残的 ✗）
"""
from __future__ import annotations

import importlib.util
import pathlib
import types

import pytest

SKILL = pathlib.Path(__file__).resolve().parents[2] / "backend" / "skills" / "卡点视频"
MOD = SKILL / "tools" / "ffmpeg_path.py"


def _load() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("skill_ffmpeg_path", MOD)
    assert spec and spec.loader, f"加载不了 {MOD}"
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_helper_ships_with_the_skill():
    assert MOD.exists(), f"技能里没有 ffmpeg 定位工具：{MOD} ✗"


# ─────────────────────────────────────────────────────────────────────────────
# 一、真的会去"验"有没有 libx264（不能只看文件在不在）
# ─────────────────────────────────────────────────────────────────────────────

def test_has_libx264_detects_real_ffmpeg():
    """★ 对**已知可用**的 ffmpeg 必须判 True；对**已知残缺**的必须判 False。

    本机两个都实测过：
      · D:\\MiniMax Design\\current\\resources\\ffmpeg\\ffmpeg.exe  → libx264 = 2  ✓
      · D:\\Software\\GPT-SoVITS\\...\\runtime\\ffmpeg.EXE          → libx264 = 0  ✗
    """
    m = _load()
    good = r"D:\MiniMax Design\current\resources\ffmpeg\ffmpeg.exe"
    bad = r"D:\Software\GPT-SoVITS\GPT-SoVITS-v2pro-20250604\runtime\ffmpeg.EXE"
    if pathlib.Path(good).exists():
        assert m.has_libx264(good) is True, "完整版被判成没有 libx264 ✗"
    if pathlib.Path(bad).exists():
        assert m.has_libx264(bad) is False, (
            "**残缺版被判成可用** ⇒ 这个探测等于没做（真机就是它让渲染崩的）✗"
        )


def test_has_libx264_handles_missing_file():
    """不存在的路径要安全返回 False（不许抛）。"""
    m = _load()
    assert m.has_libx264(r"Z:\nope\ffmpeg.exe") is False
    assert m.has_libx264("") is False


# ─────────────────────────────────────────────────────────────────────────────
# 二、能找到"能用"的那个（优先 env，其次候选表）
# ─────────────────────────────────────────────────────────────────────────────

def test_env_override_wins(tmp_path, monkeypatch):
    """`AGENT_FFMPEG` 环境变量优先级最高（用户/技能可以显式指定）。"""
    m = _load()
    good = r"D:\MiniMax Design\current\resources\ffmpeg\ffmpeg.exe"
    if not pathlib.Path(good).exists():
        pytest.skip("本机没有已知可用的 ffmpeg —— 该面无法验证 ✓")
    monkeypatch.setenv("AGENT_FFMPEG", good)
    assert m.find_ffmpeg() == good


def test_candidates_are_preferred_over_broken_path(tmp_path, monkeypatch):
    """★ 即使 PATH 上那个是残的，也必须找到候选表里完整的那一个。

    这是真机事故的核心：PATH 上是残版，不能就这么用它 ✗
    """
    m = _load()
    monkeypatch.delenv("AGENT_FFMPEG", raising=False)
    found = m.find_ffmpeg()
    if found is None:
        pytest.skip("本机找不到任何带 libx264 的 ffmpeg —— 该面无法验证 ✓")
    assert m.has_libx264(found) is True, f"find_ffmpeg 返回了一个不能用的：{found} ✗"


def test_find_ffprobe_pairs_with_ffmpeg():
    """ffprobe 要和 ffmpeg 同一个目录（版本配套，避免奇怪的不兼容）。"""
    m = _load()
    ff = m.find_ffmpeg()
    if not ff:
        pytest.skip("没有可用的 ffmpeg —— 该面无法验证 ✓")
    fp = m.find_ffprobe()
    assert fp, "找到了 ffmpeg 却没找到 ffprobe ✗"
    assert pathlib.Path(fp).parent == pathlib.Path(ff).parent, (
        f"ffprobe({fp}) 与 ffmpeg({ff}) 不在同一目录（版本可能不配套）✗"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 三、★ 给出 **bash 和 PowerShell 两种**设置命令（真机事故的根因之一）
# ─────────────────────────────────────────────────────────────────────────────

def test_env_snippets_cover_both_shells():
    """★★ 必须同时给 bash 与 PowerShell 两种写法。

    真机事故：应用跑的是 **git bash**，而技能只给 PowerShell 语法
    ⇒ `$env:PATH = ...` 在 bash 里报 `:PATH: command not found`，照做反而掉坑 ✗
    """
    m = _load()
    ff = m.find_ffmpeg()
    if not ff:
        pytest.skip("没有可用的 ffmpeg —— 该面无法验证 ✓")
    sn = m.env_snippets(ff)
    assert "bash" in sn and "powershell" in sn, f"没同时给两种 shell 的写法：{sn.keys()} ✗"
    b, p = sn["bash"], sn["powershell"]
    assert b.startswith("export PATH="), f"bash 写法不对：{b} ✗"
    # 必须把新目录**前置**到 PATH（`...:$PATH`），否则盖不住后面那个残版
    assert b.rstrip('"').endswith(":$PATH") or b.rstrip("'").endswith(":$PATH"), (
        f"bash 写法没把新目录**前置**到 PATH：{b} ✗"
    )
    assert "$env:PATH" in p, f"PowerShell 写法不对：{p} ✗"


def test_bash_snippet_quotes_paths_with_spaces():
    """★★ 路径含空格（本机就是 `MiniMax Design`）**必须加引号**。

    本班实测：不加引号的 `export PATH=/d/MiniMax Design/...:$PATH` 直接失败，
    加了引号立刻可用 —— 这个坑踩过一次 ✗
    """
    m = _load()
    sn = m.env_snippets(r"D:\Some Dir With Spaces\ffmpeg\ffmpeg.exe")
    b = sn["bash"]
    assert '"' in b or "'" in b, f"含空格的路径没加引号 ⇒ bash 里必失败：{b} ✗"
    # 引号必须包住整个目录段（不能只包一部分）
    import re
    assert re.search(r"""["'][^"']*Some Dir With Spaces[^"']*["']""", b), (
        f"引号没包住含空格的路径：{b} ✗"
    )


def test_posix_path_conversion():
    """Windows 盘符路径要能转成 bash 认的 `/d/...` 形式。"""
    m = _load()
    got = m.to_posix(r"D:\MiniMax Design\current\resources\ffmpeg")
    assert got == "/d/MiniMax Design/current/resources/ffmpeg", f"转换结果不对：{got} ✗"
    assert m.to_posix("/already/posix") == "/already/posix", "已经 posix 的路径被改坏了 ✗"


def test_which_shell_hint_defaults_to_bash():
    """默认要提示 bash —— 因为本机应用实测就是用 bash（别默认 PowerShell）。"""
    m = _load()
    hint = m.shell_hint()
    assert "bash" in hint.lower(), (
        f"默认提示不是 bash（真机应用就是 bash，默认 PowerShell 会把人带进坑）：{hint} ✗"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 四、★ 技能文档不许再退回 PowerShell 语法（真机事故的根因）
# ─────────────────────────────────────────────────────────────────────────────

#: PowerShell 专有、bash 里**一定跑不了**的记号
_PS_ONLY = (
    "$env:",              # $env:PATH = ...  → bash 报 :PATH: command not found
    "Select-String",      # PS 的 grep
    "Select-Object",      # PS 的 head
    "ForEach-Object",
    "Test-Path",          # PS 的 test
    "$LASTEXITCODE",
)


def test_skill_docs_do_not_tell_the_agent_to_use_powershell():
    """★★ 技能文档里**不许**再出现 PowerShell 专有语法当命令用。

    真机事故（task_20261013_6a006c3d）：模型照文档跑
        `$env:PATH = 'D:\\...' ; ffmpeg ...`
    **bash 报 `:PATH: command not found`** ⇒ 它拿不到带 libx264 的 ffmpeg ⇒
    反复 `ffmpeg -encoders | grep libx264` 8 次全空 ⇒ 护栏熔断 ⇒ **没做出视频** ✗

    ⇒ 本应用执行命令走 **git bash**（实测 `bash 5.2.37`），文档必须用 bash 语法 ✓

    ★ 允许的例外：**明说是"PowerShell 写法"的对照行**（给手工在 PS 里跑的人看）。
      判据：该行所在段落里带 "PowerShell" 字样就不算违规。
    """
    offenders = []
    for f in sorted(SKILL.rglob("*.md")):
        if "node_modules" in str(f):
            continue
        lines = f.read_text("utf-8", errors="replace").splitlines()
        for i, line in enumerate(lines, 1):
            if not any(k in line for k in _PS_ONLY):
                continue
            # 往前看 6 行里有没有"这是 PowerShell 写法"的说明
            ctx = "\n".join(lines[max(0, i - 6):i])
            if "PowerShell" in ctx or "powershell" in ctx:
                continue
            offenders.append(f"{f.relative_to(SKILL)}:{i}  {line.strip()[:90]}")
    assert not offenders, (
        "技能文档里还有 PowerShell 专有语法（bash 里跑不了）✗：\n  "
        + "\n  ".join(offenders[:8])
        + "\n  ⇒ 本应用用 git bash，命令必须写成 bash 形式"
    )


def test_skill_docs_mention_the_bash_path_fix():
    """★ 文档必须给出 **bash 形式**的 PATH 前置，且**强调引号不能省**。

    实测：路径含空格（本机 `MiniMax Design`），不加引号的
    `export PATH=/d/MiniMax Design/...:$PATH` 直接失败 —— 这个坑踩过一次 ✗
    """
    txt = "\n".join(f.read_text("utf-8", errors="replace")
                    for f in SKILL.rglob("*.md") if "node_modules" not in str(f))
    assert "export PATH=" in txt, "文档里没有 bash 形式的 PATH 前置 ✗"
    assert "/d/MiniMax Design" in txt, "文档里没给出可用的 ffmpeg 目录（posix 形式）✗"
    # 引号提醒
    assert "引号" in txt, "没提醒「路径含空格要加引号」（踩过的坑）✗"
