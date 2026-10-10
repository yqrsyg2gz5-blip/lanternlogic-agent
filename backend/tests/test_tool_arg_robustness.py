"""工具参数畸形不许弄死任务（2026-10-05 用户实测崩溃）。

## 现场（task_20261005_adb002a7）

运维工程师那一步 `#12 [action] update_plan`，模型把 `steps` 传成了**JSON 字符串**：
    "[{\"status\": \"done\", \"text\": \"梳理工作区现状…\"}, …]"
旧代码 `for s in raw_steps: s.get("text")` 于是**遍历字符串里的单个字符** ⇒
`AttributeError: 'str' object has no attribute 'get'` ⇒ 异常冒到 loop 之外 ⇒
**整个任务 failed**（用户看到的就是"跑到一半没了，不知道为什么"）。

## 立的规矩

1. `update_plan` 的 steps 先"尽力解析成列表"（JSON 字符串 / 单对象 / 纯文本 / 列表里混 JSON 串都要接住）
2. **任何**工具执行异常都兜成一条 `ok=false` 的观察（模型看到提示还能改），不许让任务死
3. 群里要说清失败原因 + 下一步怎么办（尤其 `max_iterations`：把活拆小或调高上限）
"""
from __future__ import annotations

import pytest

from app.loop import _coerce_steps


def test_json_string_steps_are_parsed():
    """★ 崩的就是这个形状：steps 是一段 JSON 字符串。"""
    raw = '[{"status": "done", "text": "梳理现状"}, {"status": "pending", "text": "写文档"}]'
    out = _coerce_steps(raw)
    assert [x["text"] for x in out] == ["梳理现状", "写文档"], out


def test_other_shapes_are_tolerated():
    assert _coerce_steps([{"text": "一步"}]) == [{"text": "一步"}]
    assert _coerce_steps({"text": "忘套数组"}) == [{"text": "忘套数组"}]
    assert _coerce_steps("先看代码再动手") == ["先看代码再动手"]
    assert _coerce_steps(['[{"text":"a"}]', {"text": "b"}]) == [{"text": "a"}, {"text": "b"}]


@pytest.mark.parametrize("raw", [None, "", "   ", 42, 3.14, True, [], {}])
def test_garbage_never_raises(raw):
    """垃圾输入只能得到空/合理结果，**绝不许抛异常**（抛了就是整任务失败）。"""
    out = _coerce_steps(raw)
    assert isinstance(out, list)


def test_plan_payload_survives_a_json_string():
    """★ 崩的就是这个形状：steps 是一段 JSON 字符串 ⇒ 必须解析成两步，而不是逐字符崩掉。"""
    from app.loop import _build_plan_payload

    payload = _build_plan_payload({
        "steps": '[{"status": "done", "text": "梳理现状"}, {"status": "pending", "text": "写文档"}]',
        "reflection": "从零搭交付目录",
    })
    assert [s["text"] for s in payload["steps"]] == ["梳理现状", "写文档"], payload
    assert payload["steps"][0]["status"] == "done"
    assert payload["steps"][0]["no"] == 1 and payload["steps"][1]["no"] == 2
    assert payload["reflection"] == "从零搭交付目录"


def test_plan_payload_tolerates_junk_items():
    from app.loop import _build_plan_payload

    payload = _build_plan_payload({"steps": ["第一步", {"text": ""}, {"status": "weird", "text": "第二步"}]})
    texts = [s["text"] for s in payload["steps"]]
    assert texts == ["第一步", "第二步"], payload       # 空文本跳过；非法状态归一为 pending
    assert all(s["status"] in ("pending", "in_progress", "done") for s in payload["steps"])


def test_group_says_why_a_task_failed(tmp_path, monkeypatch):
    """群里失败消息要带"原因 + 怎么办"（用户实测：只有一句空话，不知道发生了什么）。"""
    from types import SimpleNamespace

    from app import main as m
    from app.team import TeamStore

    st = TeamStore(tmp_path)
    emp = st.add_employee({"name": "甲", "dept": "技术部", "role": "工程师", "persona": "干活", "mode": "expert"})
    st.create_group("测试群", [emp["id"]], mode="manual")
    monkeypatch.setattr(m, "_team_store", st)
    monkeypatch.setattr(m.store, "read_events", lambda tid: [
        SimpleNamespace(type="error", payload={"code": "max_iterations",
                                               "message": "达到最大迭代次数（25），任务中止"}),
    ])
    why = m._task_failure_reason("t1")
    assert "步数用完" in why and "拆小" in why and "调高" in why, why


def test_group_says_nothing_when_there_is_no_error(tmp_path, monkeypatch):
    from app import main as m

    monkeypatch.setattr(m.store, "read_events", lambda tid: [])
    assert m._task_failure_reason("t1") == ""
