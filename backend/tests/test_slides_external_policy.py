# -*- coding: utf-8 -*-
"""对外材料的口径：**没验证的不许当功能写**（用户 2026-10-10 定的规矩）。

用户原话：
    "关键我希望没验证的咱就不说，没验证的可以笼统地说一下，简单地说一下，
      或者是直接就不说了。是不是验证过的说一下都无所谓。
      这个东西……你没验证，你打上去，他看人看了能得劲吗"

背景：Agent 做出来的一份对外 PPT 里，把"没验证过"的功能也列成了条目、还挂了「未验证」角标 ✗
      —— 那是**内部/交付**的分级口径 ✓ 不该出现在**对外**材料上 ✗
      （挂角标 = 当面告诉评委"这项我自己都没试过"✗）

判据（本文件钉「幻灯片制作」技能里那条口径）：
    1. 技能里必须写明"对外只写有证据的、没证据的不写或笼统带过" ✓
    2. 必须把**对外材料**与**内部分级**分开讲清楚 ✓
    3. 仍然保留"交付消息里要给分级"（那是产品特性，不能顺手删掉 ✗）
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SKILL = ROOT / "backend" / "skills" / "幻灯片制作" / "SKILL.md"


def test_slides_skill_has_external_material_policy():
    txt = SKILL.read_text("utf-8")
    assert "对外材料的口径" in txt, "技能里没有『对外材料的口径』这一节 ✗"
    assert "不许" in txt and "写成功能" in txt, \
        "技能里没写清『没验证的不许当功能列出来』✗（用户原话就是这个意思）"
    assert "笼统" in txt, "技能里没给出口（没证据的可以一句话笼统带过 ✓）"


def test_split_between_external_and_internal_grading():
    """★ 关键区分：对外不挂角标；**内部分级照旧要在交付消息里给**（那是产品特性，别删 ✗）。"""
    txt = SKILL.read_text("utf-8")
    assert "已实测" in txt and "自证" in txt and "未验证" in txt, \
        "分级口径被删了 ✗ —— 那是交付给用户/内部要用的 ✓（只是不上对外材料）"
    assert "不上对外材料" in txt or "不上对外" in txt, \
        "没写清『分级不上对外材料』✗（这正是这次踩的坑）"


def test_charts_and_sources_still_required():
    """别为了这条新规矩把老要求弄丢：图要手写、数据要有来源 ✓。"""
    txt = SKILL.read_text("utf-8")
    assert "来源" in txt, "数据要有来源标注 —— 这条不许丢 ✗"
    assert "slides.html" in txt, "产出文件仍是 slides.html ✓"
