# -*- coding: utf-8 -*-
"""★ 2026-10-06 用户实测提问：「建一个群聊就得新建一个员工卡呀？」✗

**产品不是那样设计的** ✓ —— 建群是"勾选已有员工" ✓（`create_group` 收的是**成员 id** ✓）。
但用户会这么以为，说明**界面上没讲清** ✗ + **报错没说怎么办** ✗。

这三条改动：
1. **同名报错说人话** ✓（原来只有一句「已有同名员工：小甲」✗ 用户不知道该干嘛 ✓）
2. **建群表单写清"勾已有的就行，不用新建"** ✓
3. **员工卡上印"这张卡在为哪些群干活"** ✓（一眼看出卡是可复用的 ✓）
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.team import TeamStore  # noqa: E402

_FE = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "components" / "TeamView.tsx"


def _store(tmp_path) -> TeamStore:
    return TeamStore(tmp_path)


def test_duplicate_name_error_tells_you_what_to_do(tmp_path):
    """**报错要给出路** ✓ —— 用户看到"已有同名员工"时的正确回答通常是
    "**你不用新建 ✓ 建群时直接勾选他**" ✓（而不是让他去改名字 ✓）。"""
    st = _store(tmp_path)
    st.add_employee({"name": "小甲", "dept": "技术部", "role": "程序员",
                     "persona": "干活", "mode": "expert"})
    with pytest.raises(ValueError) as ei:
        st.add_employee({"name": "小甲", "dept": "技术部", "role": "程序员",
                         "persona": "干活", "mode": "expert"})
    msg = str(ei.value)
    assert "已有同名员工：小甲" in msg, msg
    assert "不用新建" in msg, f"没告诉他'不用新建' ✗：{msg}"
    assert "勾选" in msg, f"没告诉他怎么办 ✗：{msg}"


def test_rename_to_a_taken_name_explains_why(tmp_path):
    """改名撞名 ⇒ 解释**为什么**不能同名（@点名 靠名字找人 ✓）。"""
    st = _store(tmp_path)
    a = st.add_employee({"name": "小甲", "dept": "技术部", "role": "程序员",
                         "persona": "干活", "mode": "expert"})
    st.add_employee({"name": "小乙", "dept": "技术部", "role": "程序员",
                     "persona": "干活", "mode": "expert"})
    with pytest.raises(ValueError) as ei:
        st.update_employee(a["id"], {"name": "小乙"})
    assert "同名" in str(ei.value) and "@" in str(ei.value), str(ei.value)


def test_one_card_can_serve_several_groups(tmp_path):
    """★ 这张测试是**产品语义**的守卫 ✓：**同一张员工卡可以同时进好几个群** ✓
    —— 所以"建群"从来不等于"建人" ✓（用户那个疑问的根源就在这儿 ✓）。"""
    st = _store(tmp_path)
    e = st.add_employee({"name": "小甲", "dept": "技术部", "role": "程序员",
                         "persona": "干活", "mode": "expert"})
    g1 = st.create_group("群一", [e["id"]], mode="manual")
    g2 = st.create_group("群二", [e["id"]], mode="leader", leader=e["id"])
    groups = st.groups()
    in_groups = [g["name"] for g in groups if e["id"] in (g.get("members") or [])]
    assert sorted(in_groups) == ["群一", "群二"], in_groups
    assert g1["id"] != g2["id"] and len(st.employees()) == 1, "建了两个群却多出了员工卡 ✗"


def test_ui_says_you_do_not_need_a_new_card():
    """界面文案（零风险但要钉住 ✓）：建群表单必须写明"勾已有的就行、不用新建" ✓。"""
    src = _FE.read_text("utf-8")
    assert "不用新建" in src, "建群表单没写'不用新建' ✗"
    assert "同一个人可以同时待在好几个群里" in src, "没讲清'卡可复用' ✗"
    assert "groupsOf" in src, "员工卡上没显示'在哪些群里' ✗"
