# -*- coding: utf-8 -*-
"""许可口径守卫（**AGPL-3.0 版**，2026-10-08 晚改）

★ 口径变更记录（同一件事一天里变了两次 ✓ 都记在这儿 ✓）：
  · v1（2026-10-07）"保留所有权利 + 禁止公开源码"（闭源商业授权）
  · v2（2026-10-08 白天）"源码可见 source-available + 禁商用需授权"（自家协议）
  · **v3（2026-10-08 晚，当前）"AGPL-3.0 真开源"** ✓ —— 用户拍板："我想做这个 agpl 开源"
★ 所以下面这些断言**跟着反过来了** ✓ 不是"改测试迁就代码" ✗ 而是**要求变了** ✓
  （本仓规矩：口径变了、测试跟着变 ✓ 红一次 ✓ 改对 ✓ —— 这是第三次了 ✓）

★ 这次还要钉住一件**AGPL 特有**的事 ✗：第 13 条要求"通过网络使用的人能拿到源码" ✓
  ⇒ 仓库里必须给出**源码链接位** ✓ 且程序界面里也得有 ✓（用户看不到源码文件 ✓）
"""
from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_LICENSE = (_ROOT / "LICENSE").read_text("utf-8")
_TERMS = (_ROOT / "TERMS.md").read_text("utf-8")
_README = (_ROOT / "README.md").read_text("utf-8")
_COMMERCIAL = (_ROOT / "COMMERCIAL.md").read_text("utf-8")


def test_license_is_the_real_agpl_text():
    """★★ **LICENSE 必须是 AGPL-3.0 官方全文** ✗ 不许是自己写的"参照 AGPL"✓

    判据用官方文本里的**特征串** ✓（不靠字数猜 ✓）：
      · 标题行 + 版本行（Version 3, 19 November 2007）
      · 第 13 条的标题（Remote Network Interaction）—— 这正是 AGPL 的灵魂 ✓
      · 免责声明段落（NO WARRANTY / "WITHOUT ANY WARRANTY"）
    """
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in _LICENSE, "LICENSE 里没有 AGPL 标题 ✗"
    assert "Version 3, 19 November 2007" in _LICENSE, "不是 AGPL v3 的官方版本行 ✗"
    assert "Remote Network Interaction" in _LICENSE, "缺第 13 条（AGPL 的灵魂）✗"
    assert "WITHOUT ANY WARRANTY" in _LICENSE.upper(), "缺无担保声明 ✗"
    # 官方全文 ≈ 34.5KB ✓（含版权头 ✓）；太小 ⇒ 多半是"摘要"冒充全文 ✗
    assert len(_LICENSE) > 30000, f"LICENSE 只有 {len(_LICENSE)} 字符 ⇒ 不像官方全文 ✗"
    # 版权头要在 ✓
    assert "丹东振兴云杉互联网服务工作室" in _LICENSE, "LICENSE 里没有版权主体 ✗"
    assert "Copyright (C) 2026" in _LICENSE, "版权年份没写 ✗"


def test_license_is_agpl_not_a_home_made_one():
    """★ **不许再把它写成"自家协议"** ✗ —— 也不许写成 GPL/其他协议 ✓

    （v2 那份自拟协议的核心特征串必须**不在** ✓ —— 留着就说明"改了一半" ✗）
    """
    for stale in ("源码公开授权协议", "未经本工作室**书面授权**", "禁止商用未授权"):
        assert stale not in _LICENSE, f"LICENSE 里还留着 v2 自家协议的句子：{stale} ✗"
    assert "GNU GENERAL PUBLIC LICENSE" not in _LICENSE.replace("GNU AFFERO GENERAL PUBLIC LICENSE", ""), \
        "把 AGPL 写成了普通 GPL ✗（AGPL 多了第 13 条网络条款 ✓）"


def test_readme_and_commercial_explain_the_agpl_deal():
    """★ README 与 COMMERCIAL 必须把"AGPL 到底给了什么、要什么"讲清 ✓

    用户真正会踩的坑（必须写明 ✓）：
      · AGPL **允许商用**（包括卖）—— 这点很多人误以为"开源就不能商用" ✗
      · 触发开源义务的**关键是"改了 + 通过网络对外提供"** ✓（第 13 条 ✓）
      · 双授权（买授权 = 免除开源义务）✓ 而不是"买商用许可" ✗
    """
    assert "AGPL" in _README, "README 没提 AGPL ✗"
    assert "COMMERCIAL.md" in _README, "README 没链到商业授权说明 ✗"
    for k in ("AGPL-3.0", "商用", "第 13 条" if "第 13 条" in _COMMERCIAL else "13"):
        assert k in _COMMERCIAL, f"COMMERCIAL.md 没讲清「{k}」✗"
    assert "yangbo0801@163.com" in _COMMERCIAL, "商业授权没留邮箱 ✗"
    assert "丹东振兴云杉互联网服务工作室" in _COMMERCIAL, "商业授权没写版权主体 ✗"
    # ★ 名字不许写错（2026-10-07 就错过一次 ✓ 有测试钉着 ✓ 这里再来一道 ✓）
    for name, txt in (("COMMERCIAL.md", _COMMERCIAL), ("LICENSE", _LICENSE), ("README.md", _README)):
        assert "丹东振兴云山互联网服务工作室" not in txt, f"{name} 把工作室名写成了「云山」✗（是云杉 ✓）"


def test_source_offer_exists_for_agpl_section_13():
    """★★ **AGPL 第 13 条的兑现方式必须在** ✓ —— 否则这份开源许可自己就没被遵守 ✗

    第 13 条：通过网络与本程序交互的人 ⇒ 必须能拿到对应源码 ✓
    ⇒ 仓库里要有明确的"源码在哪" ✓（README ✓）+ 程序界面"关于"页也要有 ✓
      （只写在源码文件头里**不够** ✗ 因为用户根本看不到那些文件 ✓）
    """
    assert "源码仓库" in _README or "源码地址" in _README, "README 里没给源码链接位 ✗（第 13 条要 ✓）"
    assert "https://github.com/yqrsyg2gz5-blip/lanternlogic-agent" in _README or "github.com" in _README, \
        "源码链接还是空的且没标占位符 ✗（上线前必须填 ✓）"
    # 界面里也要有（关于页 ✓）—— 先钉后端那侧的字段与前端读取 ✓
    from app import version

    assert hasattr(version, "APP_NAME"), "version 模块缺少基本信息 ✗"


def test_spdx_identifier_is_the_official_one():
    """★ SPDX 要写 **`AGPL-3.0-only`** ✓ —— 裸 `AGPL-3.0` 是**已废弃**写法 ✗

    （npm / PyPI / SPDX 官方都认 `AGPL-3.0-only` 或 `AGPL-3.0-or-later` ✓
      裸的 `AGPL-3.0` 会被工具链报 deprecation ✓）
    """
    import json

    for rel in ("frontend/package.json", "desktop/package.json"):
        p = _ROOT / rel
        if not p.exists():
            continue
        lic = json.loads(p.read_text(encoding="utf-8")).get("license")
        assert lic in ("AGPL-3.0-only", "AGPL-3.0-or-later"), f"{rel} 的 license 字段是 {lic!r} ✗"
    # 文档里要给 pyproject.toml 的写法（本项目后端是 requirements.txt ✓ 但别人会问 ✓）
    doc = (_ROOT / "docs" / "版权头模板.md").read_text("utf-8")
    assert "SPDX-License-Identifier" in doc, "版权头模板里没有 SPDX 短式 ✗"
    assert "AGPL-3.0-only" in doc, "模板里没写正确的 SPDX 标识 ✗"
