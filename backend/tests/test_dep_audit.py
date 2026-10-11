# -*- coding: utf-8 -*-
"""★ 第 6 项 · 依赖查漏洞（2026-10-07）—— 本文件钉住"**只查不改**"这条底线。

## 查出来的东西（都写进报告了 ✓）

**产品自己声明的依赖（`backend/requirements.txt` 那 6 条）：0 个已知漏洞** ✓
**可选功能装进来的**（用户点「一键装」装的本地语音识别那套 ✓）：
`transformers` 11 条 / `accelerate` 2 条 / `nltk` 1 条 ✗
**前端开发依赖**：`esbuild`（中）+ `vite`（高）✗ —— 修它要跨大版本（vite 5 → **8.3.3**）✗
  ★ 而 `source-map-js`（高）**不用跨版本就能修** ⇒ 已经修了 ✓（1.2.1 → 1.2.2 ✓ 构建照过 ✓）

## 本文件守的是什么

这个脚本是**给用户反复跑的工具** ✓ —— 工具最容易犯的错就是"顺手帮你升级" ✗：
  自动升依赖 = 悄悄把用户环境换掉 ✓ 而"升级"和"修漏洞"**不是一回事** ✓
  升完可能**跑不起来** ✗ —— 那是拿"能用"换"看起来安全" ✓
"""
from __future__ import annotations

import pathlib
import re

import pytest

from app import main as m

ROOT = pathlib.Path(m.__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "audit_deps.py"
REVIEW = pathlib.Path(r"D:\丹东云杉网络工作室\agent-shell-评审")
_needs_review = pytest.mark.skipif(not REVIEW.exists(), reason="没有评审工作区 ⇒ 跳过")


def test_audit_script_exists():
    assert SCRIPT.exists(), "没有这个一键查依赖的脚本 ✗（那用户每次都得手敲两条命令 ✓）"


def test_audit_script_never_upgrades_anything():
    """★★ **只查不改** ✓ —— 回滚实验：把命令换成 `npm audit fix --force` ⇒ 本组必红 ✓

    （`--force` 会跨大版本把 vite 从 5 换到 8 ✗ 那可能直接把构建弄坏 ✓
      ——升级是**用户的决定**，不是脚本顺手做的事 ✗）

    ★ 检查的是**它真正跑的那条命令** ✓ 不是全文找关键字 ✗ ——
      第一版就栽在这儿：脚本**打印的说明里**提到过 `--force` ✓（那是好事：告诉用户有这么条路 ✓）
      而全文 grep 把它当成"脚本自己会跑 --force"✗ 误判 ✓（注释/文案会替代码蒙混过关，本仓老教训 ✓）
    """
    src = SCRIPT.read_text("utf-8")
    # 找出**所有**它跑的命令 ✓ 只挑那条 npm 的 ✓
    # （第一版抓了第一条 = `pip freeze` ✗ 于是断言全落空 ✓ —— 又是"测的东西不对"✓）
    argvs = re.findall(r"subprocess\.run\(\[(.*?)\]", src, flags=re.S)
    npm_argv = next((a for a in argvs if "npm" in a), "")
    assert npm_argv, f"没找到它跑 npm 的那条命令 ⇒ 这条测试没在测东西 ✗（找到的：{argvs}）"
    assert "audit" in npm_argv, f"跑的不是 audit？{npm_argv[:120]}"
    for bad in ("fix", "force", "update", "install"):
        assert bad not in npm_argv, f"它跑的这条命令里有「{bad}」⇒ 会顺手改用户环境 ✗：{npm_argv[:120]}"
    assert "registry" in npm_argv, "没指定官方源 ✗"
    # Python 侧：只允许 pip freeze（读）✓ 不许有装/升（pip install）✗
    assert not any(re.search(r'"pip",\s*"install"', a) for a in argvs), "脚本会去装/升 Python 包 ✗"


def test_audit_uses_the_official_registry_for_npm():
    """★ 必须指定官方源 ✓ —— 国内镜像（npmmirror）**没实现**审计接口 ✗
    （本机实测：直接跑 `npm audit` 只会得到一句 `404 NOT_IMPLEMENTED` ✓ 等于白跑 ✓）。"""
    src = SCRIPT.read_text("utf-8")
    assert "registry.npmjs.org" in src, "没指定官方源 ⇒ 在国内镜像下查不出任何东西 ✗"
    assert "--registry=" in src, "没把源传给 npm ✗"


def test_audit_separates_declared_from_optional_deps():
    """★ 必须把"产品自己的依赖"和"可选功能装进来的"**分开报** ✓
    （不分开 ⇒ 用户看到 transformers 11 条会以为这软件到处是洞 ✗ 其实那不是产品依赖 ✓）。"""
    src = SCRIPT.read_text("utf-8")
    assert "requirements.txt" in src and "declared_names" in src, "没区分声明依赖 ✗"
    assert "可选功能" in src, "没有「可选功能装进来的」这一档 ✗"
    assert "OSV" in src, "Python 侧没走 OSV（还是靠装 pip-audit？那又得多装东西 ✗）"


def test_audit_says_so_when_it_cannot_check():
    """★ 查不到就说查不到 ✓ —— 断网时**绝不能**显示成"没有漏洞" ✗（那是假安心 ✓）。"""
    src = SCRIPT.read_text("utf-8")
    assert "查不到" in src and "没有结论" in src, "查询失败时没说清「这次没结论」✗"


def test_the_safe_fix_was_actually_applied():
    """★ `source-map-js` 那条**不用跨版本就能修** ⇒ 已经修了 ✓
    （1.2.1 → 1.2.2 ✓ 修完**构建照过** ✓ —— 这才是"能修的就修、不能修的报告" ✓）。"""
    lock = ROOT / "frontend" / "package-lock.json"
    txt = lock.read_text("utf-8")
    assert '"source-map-js"' in txt, "lockfile 里找不到它？确认一下 ✓"
    assert "source-map-js-1.2.1.tgz" not in txt, "还是旧的 1.2.1（有已知漏洞）✗"
    assert "source-map-js-1.2.2" in txt, "没升到修好的 1.2.2 ✗"


@_needs_review
def test_the_report_is_written_down():
    """★ 结论要落成**能打开看的东西** ✓（散在对话里的结论会丢 ✓）。"""
    rep = REVIEW / "第6项-依赖查漏洞-2026-10-07.md"
    assert rep.exists(), "第 6 项的报告没写 ✗"
    txt = rep.read_text("utf-8")
    assert "transformers" in txt, "没写可选功能那几条 ✗"
    assert "vite" in txt, "没写前端那两条 ✗"
    assert "0" in txt, "没写「产品自己的依赖是干净的」这句 ✓（那是用户最该先知道的一句 ✓）"
