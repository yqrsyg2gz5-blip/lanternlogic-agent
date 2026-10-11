# -*- coding: utf-8 -*-
"""★ 第 8 项 · 按任务类型推荐角色 —— 2026-10-07。

## 为什么要它

24 个角色摆在那儿 ✓ 而新用户**不知道该派谁** ✓
（"我这句话该用哪个身份？"—— 选错了其实也没大事 ✓ 但他会**卡在这一步** ✓）

## 三条规矩（每条都在防"推荐变成骚扰"）

1. **只在命中关键词时才推** ✓ 一句都匹配不上 ⇒ 返回**空数组** ✓
   （乱推比不推更烦人 ✓ —— 用户会开始无视那块提示 ✓ 那就等于没有 ✓）
2. 推荐要**说得出为什么** ✓（"因为你提到了「测试」"✓ —— 用户才知道该不该听 ✓）
3. **不推"通用助理"** ✗ —— 它什么都能干 = 没有专长 ✓ 推它等于没推 ✓

★ 界面上也一样：**没把握就一个字都不显示** ✓ 而且只在用户**还没选身份**时才提示 ✓
  （已经选好身份的人不需要被推荐 ✓ 别去打扰他 ✓）
"""
from __future__ import annotations

import pathlib

from app import main as m
from app.roles import ROLE_LIBRARY, suggest_roles


# ═══ ① 推得准 ═══

def test_suggests_the_obvious_role():
    r = suggest_roles("帮我把这段代码跑一遍，看看有没有报错", 3)
    assert r, "明显是写代码的活却没推荐 ✗"
    assert r[0]["role"] in ("程序员", "测试工程师"), r
    assert "程序员" in [x["role"] for x in r], f"提到「代码」「报错」却没推程序员 ✗：{r}"


def test_every_suggestion_says_why():
    """★ **说得出为什么** ✓ —— 不然用户不知道该不该听 ✓（那就是瞎指挥 ✓）。"""
    for text, role in (("把这周的销售数据做成图表", "数据分析师"),
                       ("做个海报", "设计师"),
                       ("翻译成英文", "翻译")):
        r = suggest_roles(text, 3)
        assert r and r[0]["role"] == role, (text, r)
        why = str(r[0]["why"])
        assert "因为" in why and "「" in why, f"没给理由 ✗：{why}"


def test_vague_input_gets_no_suggestion():
    """★★ **没把握就不推** ✓ —— 回滚实验：让它"总能推一个最接近的" ⇒ 本组必红 ✓
    （乱推比不推更烦人 ✓ 用户会开始无视它 ✓）"""
    for vague in ("你好", "今天天气不错", "在吗", "嗯", ""):
        assert suggest_roles(vague) == [], f"「{vague}」这种也推了 ⇒ 会变成骚扰 ✗"


def test_it_never_suggests_the_generic_assistant():
    """★ 不推"通用助理" ✗ —— 它什么都能干 = 没有专长 ✓ 推它等于没推 ✓。"""
    for text in ("随便干点什么", "帮我处理一下这事", "写代码"):
        assert all(x["role"] != "通用助理" for x in suggest_roles(text, 5))


def test_suggestions_are_real_roles_with_summaries():
    """★ 推出来的必须是**角色库里真有的** ✓ 而且要带上那句简介 ✓（界面要显示 ✓）。"""
    r = suggest_roles("帮我看看服务器为什么连不上，日志排查一下", 3)
    assert r, "运维的活没推出来 ✗"
    for x in r:
        assert x["role"] in ROLE_LIBRARY, f"推了个不存在的角色 ✗：{x['role']}"
        assert x["summary"] and x["dept"], f"缺简介/部门 ⇒ 界面显示不出来 ✗：{x}"


def test_scoring_prefers_more_hits():
    """★ 命中越多越靠前 ✓（"代码 + 报错" 比只命中一个的更该排前面 ✓）。"""
    r = suggest_roles("这段代码报错了，帮我改一下", 3)
    assert r and r[0]["role"] == "程序员", r


def test_limit_is_respected():
    assert len(suggest_roles("代码 测试 部署 数据 海报", 2)) <= 2
    assert len(suggest_roles("代码", 99)) <= len(ROLE_LIBRARY)


# ═══ ② 接口与界面（"写了不等于接上了"✓）═══

def test_endpoint_is_wired():
    import pathlib
    src = pathlib.Path(m.__file__).read_text("utf-8")
    assert '@app.get("/api/v1/roles/suggest")' in src, "后端没有这个接口 ✗"
    assert "suggest_roles(input)" in src, "接口没真调推荐函数 ✗"


def test_frontend_shows_it_only_when_unsure_or_needed():
    """★ 界面三条：只在**没选身份**时提示 ✓ 没把握就**不显示** ✓ 带**一键切过去** ✓。"""
    src = (pathlib.Path(m.__file__).resolve().parents[2]
           / "frontend" / "src" / "components" / "TaskView.tsx").read_text("utf-8")
    assert "api.suggestRoles(" in src, "界面根本没问过后端 ✗"
    assert "if (identity || !lastUserText.trim() || !task)" in src, \
        "没判断'已经选了身份就不推'⇒ 会去打扰已经选好的人 ✗"
    assert "first ? { role: first.role, why: first.why } : null" in src, \
        "空数组时没设成 null ⇒ 会显示一个空提示 ✗"
    assert "用这个" in src, "没有一键切过去的按钮 ✗（只告诉他不给点 = 还得自己去下拉里找 ✓）"
