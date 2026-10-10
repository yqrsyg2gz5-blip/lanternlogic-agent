"""`.ps1` 编码守卫：仓库里的 PowerShell 脚本必须存成 **UTF-8 with BOM**。

为什么要有这条（2026-10-04 A3 批血的教训）：
  本班用编辑器重写 `scripts/check_all.ps1` 时 BOM 被丢掉（文件内容本身没问题，
  只是从 `EF BB BF` 开头变成 `3C 23 0A`）⇒ **Windows PowerShell 5.1 会按 GBK
  解码无 BOM 的 UTF-8** ⇒ 中文注释变乱码 ⇒ 直接语法错：
      Missing '=' operator after key in hash literal.
      At D:\\AI\\agent-shell\\scripts\\check_all.ps1:25 char:44
  整条守门脚本**跑不起来**（不是断言红、不是测试红，是根本解析不了）。
  操作备忘里早写着"`.ps1` 必须存成 UTF-8 with BOM"，但**没有任何机制保证它**——
  与 A4（零 CI/零 pre-commit 之前的处境）同型：靠人记得 = 不成立。

本文件把这条规则变成可执行断言：任何一次改动把 BOM 弄丢，pre-commit 的 pytest
就会红。判据刻意做成"**至少扫到一个 .ps1**"（防空扫假绿）+ 逐个文件断言 BOM。

回滚实验：scripts/redgreen_check.py 的「守门 .ps1 BOM 守卫回退」组
（用 _experiment_real_repo 在真仓库上摘掉 check_all.ps1 的 BOM → 本文件必红）。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 跳过：依赖目录 / 归档 / 构建产物（它们不参与守门，也不该由本仓库负责）
_SKIP_DIRS = {"node_modules", ".venv", ".git", "__pycache__",
              "_attic", "build_test", "dist_test", ".pytest_cache"}

#: ★★ 2026-10-09：**运行时的用户数据不扫** ✗（按相对路径前缀判 ✓ 比按目录名精确 ✓）
#:   为什么（今天实测的教训 ✓ 排查花了好几轮 ✗）：
#:     Agent 干活时会在**自己的任务工作区**里生成 `.ps1`（那天的 `diag_cpu.ps1` ✓ 没带 BOM ✗）
#:     ⇒ 那个文件在 `backend/data/tasks/.../workspace/` 下 ✓ 属于**用户数据** ✗
#:     ⇒ 结果：**用户随便跑个任务就能把仓库的门弄红** ✗
#:       而门红了的排查成本极高（今天先怀疑自己改坏了 ✓ 又怀疑快照 ✓ 最后才发现是它 ✗）
#:   ★ 但**源码**里一个都不许例外 ✓ —— scripts/ · 根目录 · 将来新增的目录 ✓
#:   ★ 反空扫与"源码仍被扫到"由下面两条测试各自守住 ✓（放宽不能变成失守 ✗）
_SKIP_PREFIXES = ("backend/data", "frontend/dist", ".dsh")

#: UTF-8 BOM 的三字节（★ 别删 ✗ —— 我 2026-10-09 编辑时手滑删过一次 ✓ 测试当场 NameError ✓）
BOM = b"\xef\xbb\xbf"


def _ps1_files() -> list[Path]:
    out: list[Path] = []
    for p in ROOT.rglob("*.ps1"):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        rel = p.relative_to(ROOT).as_posix()
        if any(rel.startswith(pre) for pre in _SKIP_PREFIXES):
            continue
        out.append(p)
    return sorted(out)


def test_repo_has_ps1_files_to_guard():
    """反空扫：扫不到文件就说明本守卫在假绿（路径规则写错了/仓库结构变了）。"""
    files = _ps1_files()
    assert len(files) >= 2, f"只扫到 {len(files)} 个 .ps1：{files}——守卫在空扫"


def test_source_ps1_are_still_scanned():
    """★ 放宽之后**源码必须仍被扫到** ✗ —— 否则上面那条排除就成了"把守门关掉" ✓

    （今天是踩了坑才加的这条：放宽排除范围最容易顺手把守卫也放没了 ✗）
    """
    rels = {p.relative_to(ROOT).as_posix() for p in _ps1_files()}
    for must in ("scripts/check_all.ps1", "restart-backend.ps1"):
        assert must in rels, f"源码脚本 {must} 没被扫到 ✗ —— 排除范围写宽了 ✓ 守门失效 ✗"


def test_every_ps1_has_utf8_bom():
    """★ 核心：每个 .ps1 都必须以 UTF-8 BOM 开头（PowerShell 5.1 的判据）。"""
    bad = []
    for p in _ps1_files():
        head = p.read_bytes()[:3]
        if head != BOM:
            bad.append(f"{p.relative_to(ROOT)}（开头是 {head.hex(' ')}，应为 ef bb bf）")
    assert not bad, (
        "这些 .ps1 丢了 UTF-8 BOM —— Windows PowerShell 5.1 会按 GBK 解码 ⇒ 中文乱码 ⇒ "
        "脚本直接语法错、守门跑不起来。修复：\n"
        "  $t=[IO.File]::ReadAllText($p,[Text.UTF8Encoding]::new($false));"
        "[IO.File]::WriteAllText($p,$t,[Text.UTF8Encoding]::new($true))\n"
        + "\n".join(bad)
    )


def test_ps1_content_decodes_as_utf8():
    """BOM 之外再钉一层：文件体必须是合法 UTF-8（防"用 GBK 另存"这条反向错法）。"""
    bad = []
    for p in _ps1_files():
        try:
            p.read_bytes().decode("utf-8")
        except UnicodeDecodeError as e:
            bad.append(f"{p.relative_to(ROOT)}：{e}")
    assert not bad, "这些 .ps1 不是合法 UTF-8（可能是被 GBK 另存过）：\n" + "\n".join(bad)
