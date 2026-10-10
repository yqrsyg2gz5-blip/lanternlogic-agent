# -*- coding: utf-8 -*-
"""★ 2026-10-06 经验注入（"这类活上次怎么栽的" ✓）。

## 为什么加它（今天真跑反复见到的 ✗）

同一个坑**反复出现** ✓：交付里不贴真实输出 ⇒ 被打回 ✓ ⇒ **下一个任务又犯** ✓。
每一次都重烧十几万 tok ✗ —— 而"上次为什么栽"这件事，**系统本来就知道** ✓
（验收意见白纸黑字 ✓），只是**从来没人把它带给下一个干活的人** ✗。

⇒ 记下来 ✓ 下次派**同类**活时自动带上 ✓。

## 边界（刻意保守 ✓）

· 只记"被打回/失败"的原因 ✓（成功经验信息量低 ✗ 还占上下文 ✗）
· **相关性门槛**：关键词重叠 < 3 就不注入 ✓（不像的活硬塞经验 = 噪音 ✗）
· **硬上限**：一次最多 3 条 / 500 字 ✓（注入的也是上下文 ✓ 不能撑大每次派活 ✗）
· **落盘** ✓ 重启仍在 ✓；坏了**当空**处理 ✓（绝不能让经验库拖垮派活 ✗）
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.lessons import MAX_CHARS, MAX_INJECT, LessonStore  # noqa: E402
from app.team import TeamStore  # noqa: E402


def _store(tmp_path) -> LessonStore:
    return LessonStore(tmp_path)


def test_records_and_dedupes(tmp_path):
    """同一句教训只留一条 ✓ 再遇到就**加计数** ✓（"栽过 3 次"比"栽过一次"更该被看见 ✓）。"""
    L = _store(tmp_path)
    assert L.record("写个 wordcount.py 并真跑", "要改什么：补上自测命令的真实运行输出") is True
    assert L.record("写个 wordcount.py 并真跑", "要改什么：补上自测命令的真实运行输出") is False
    items = L.all()
    assert len(items) == 1 and items[0]["hits"] == 2, items


def test_squeezes_a_long_review_into_one_line(tmp_path):
    """验收意见几百字 ⇒ 只存**一句话** ✓（注入的是"教训" ✓ 不是"案卷" ✓）。"""
    L = _store(tmp_path)
    long_reason = ("这一项整体不通过。先说背景：文件确实都在工作区里，位置也没问题。"
                   "但交付说明里没有任何可核验的证据，所以必须打回。"
                   "要改什么：在交付里补上自测命令的真实运行输出。"
                   "另外顺便说一句，格式其实可以更好看一点。")
    L.record("写个 wordcount.py", long_reason)
    what = L.all()[0]["what"]
    assert "补上自测命令的真实运行输出" in what, what
    assert len(what) <= 120, f"没压成一句话：{what}"


def test_only_similar_tasks_get_the_lesson(tmp_path):
    """**相关性门槛** ✓：不像的活不硬塞经验 ✗（否则每次派活都多一段噪音 ✓）。"""
    L = _store(tmp_path)
    L.record("写个 wordcount.py 并真跑自测", "要改什么：补上自测命令的真实运行输出")
    assert "自测命令" in L.render("写一个 count_words.py 并真跑测试"), "同类活该拿到 ✓"
    assert L.render("帮我写一封辞职邮件") == "", "不像的活不该塞经验 ✗"


def test_injection_is_bounded(tmp_path):
    """**硬上限** ✓：条数与字数都封顶 ✓（注入的也是上下文 ✓ 不能撑大每一次派活 ✗）。"""
    L = _store(tmp_path)
    for i in range(10):
        L.record("写个 python 脚本并真跑自测测试", f"要改什么：第 {i} 条很长的教训" + "细节" * 40)
    tip = L.render("写个 python 脚本并真跑自测测试")
    assert tip.count("·") <= MAX_INJECT, tip
    assert len(tip) <= MAX_CHARS + 60, f"注入太长了：{len(tip)}"


def test_broken_file_never_breaks_dispatch(tmp_path):
    """经验库文件坏了 ⇒ 当空 ✓ —— **绝不能让派活挂掉** ✗（它是锦上添花 ✓ 不是关键路径 ✓）。"""
    p = tmp_path / "lessons.json"
    p.write_text("{ 这不是 JSON", encoding="utf-8")
    L = _store(tmp_path)
    assert L.all() == [] and L.render("随便什么活") == ""
    assert L.record("随便什么活", "要改什么：补上输出") is True     # 坏了也能继续用 ✓


def test_handoff_carries_the_lesson(tmp_path):
    """端到端：栽过的坑，**下次派同类活时工作单里就有它** ✓。"""
    st = TeamStore(tmp_path)
    st.record_lesson("写个 wordcount.py 并真跑自测",
                     "要改什么：在交付里补上自测命令的真实运行输出")
    ids = [st.add_employee({"name": "程序员", "dept": "技术部", "role": "程序员",
                            "persona": "干活", "mode": "expert"})["id"]]
    g = st.create_group("开发群", ids, mode="leader")
    st.leader_begin(g["id"], "写一个 count_words.py 并真跑测试", [
        {"name": "程序员", "task": "写 count_words.py 并真跑自测", "output": "count_words.py",
         "depends_on": []},
    ])
    gg = st.get_group(g["id"])
    text = st.leader_handoff_text(gg, gg["leader_plan"][0])
    assert "过去这类活栽过的地方" in text, "同类活的工作单里没带上教训 ✗"
    assert "真实运行输出" in text, text[-300:]
