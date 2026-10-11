# -*- coding: utf-8 -*-
"""★ 第 10 项 ③ 员工头像 —— 钉住三件事（都是"看着小、做错了很烦"的那种）。

## 为什么用"首字头像"而不是上传图片

上传图片要加接口 ✓ 加存储 ✓ 加裁剪 ✓ 加失败处理 ✓ —— 而这一步要解决的是
**"一列人名分不出谁是谁"** ✓ 首字 + 稳定颜色就够 ✓ 而且**零后端改动** ✓

## 三条不能错的

1. **同一名字 ⇒ 同一颜色** ✓（按名字算 ✓ 不能随机 ✗ —— 随机的话每次刷新颜色都变 ✓ 更乱 ✗）
2. **"我"和"系统"不加头像** ✗（它们不是员工 ✓ 加了反而分不清 ✓）
3. **空名字不许崩** ✓（取首字前先兜底 ✓）
"""
from __future__ import annotations

import pathlib
import re

from app import main as m

TEAM = (pathlib.Path(m.__file__).resolve().parents[2]
        / "frontend" / "src" / "components" / "TeamView.tsx")


def test_avatars_exist_and_are_deterministic():
    """★ **同一名字必须同一颜色** ✓ —— 随机的话每次刷新都变 ✓ 比没有更乱 ✗。"""
    src = TEAM.read_text("utf-8")
    assert "const avatarOf" in src, "没有头像那个函数 ✗"
    i = src.find("const avatarOf")
    seg = src[i : i + 900]
    assert "charCodeAt" in seg, "颜色不是**按名字算**的 ⇒ 每次刷新都变 ✗"
    assert "Math.random" not in seg, "用随机数算颜色 ⇒ 刷新一次换一个色 ✗"
    assert "hsl(" in seg, "没给出颜色 ✗"
    # 首字：中文取第一个字 ✓ 英文首字母大写 ✓ 空名字兜底 ✓（别用 slice 切坏代理对 ✗）
    assert "charAt(0)" in seg and "toUpperCase()" in seg, "取首字的写法不对 ✗"
    assert "|| '?'" in seg or "|| \"?\"" in seg, "空名字没兜底 ⇒ 可能崩 ✗"


def test_avatars_are_used_in_both_places():
    """★ 光有函数不算 ✓ —— **员工卡**和**群里发言**都要用上 ✓（"写了不等于接上了"✓）。"""
    src = TEAM.read_text("utf-8")
    uses = re.findall(r"<Avatar\s", src)
    assert len(uses) >= 2, f"只用了 {len(uses)} 处 ⇒ 员工卡或群里漏了一处 ✗"


def test_me_and_system_get_no_avatar():
    """★ "我"和"系统"**不加头像** ✗ —— 它们不是员工 ✓ 加了反而分不清谁是谁 ✓。"""
    src = TEAM.read_text("utf-8")
    i = src.find("<Avatar name={who}")
    assert i > 0, "群里发言没加头像 ✗"
    seg = src[max(0, i - 200) : i + 80]
    assert "who !== '系统'" in seg, "系统消息也加头像了 ✗"
    assert "!mine" in seg, "自己说的话也加头像了 ✗（那是「我」✓ 不是员工 ✓）"
