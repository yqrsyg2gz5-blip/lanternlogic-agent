"""Phase 3 ⑦（多轮"改一句重发"）：后端改写历史的锚点。

语义：把**指定的那条用户消息及其之后**的对话作废，用新文本从那里重跑。
  ① 事件 seq ↔ 历史条目的对位：数 seq ≤ edit_of_seq 的**用户消息事件**有几条（k），
     落盘历史里的第 k 条 user 消息就是目标 —— 对不上就 422，**绝不乱删**
  ② 保留它**之前**的全部历史（前面那几轮不能丢）
  ③ 任务正在跑时**拒绝**（正在跑的 loop 握着自己那份 history，改写会被它覆盖回去 ⇒
     "以为改了其实没改"，比报错更糟）
  ④ 事件流只追加（契约）⇒ 先补一条说明事件（从第 N 条重跑、作废几条），再走新一轮
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main as m
from app.schemas import EventEnvelope


def _ev(seq: int, type_: str, payload: dict) -> EventEnvelope:
    return EventEnvelope(id=f"evt_{seq:06d}", seq=seq, task_id="task_20261005_aaaa",
                         type=type_, version=1, ts="2026-10-05T00:00:00Z", payload=payload)


# 事件流：两条用户消息 + 助手回复（seq 是真实的连续序号）
EVENTS = [
    _ev(1, "message", {"role": "user", "text": "第一条问题"}),
    _ev(2, "status", {"state": "running", "detail": "任务开始"}),
    _ev(3, "message", {"role": "assistant", "text": "第一条回答"}),
    _ev(4, "message", {"role": "user", "text": "第二条问题"}),
    _ev(5, "message", {"role": "assistant", "text": "第二条回答"}),
]
HISTORY = [
    {"role": "user", "content": "第一条问题"},
    {"role": "assistant", "content": "第一条回答"},
    {"role": "user", "content": "第二条问题"},
    {"role": "assistant", "content": "第二条回答"},
]


@pytest.fixture()
def fake_store(monkeypatch):
    """假 store：只提供 read_events / load_history / save_history，并记录写入。"""
    saved: list[list[dict]] = []
    st = SimpleNamespace(
        read_events=lambda _tid: EVENTS,
        load_history=lambda _tid: list(HISTORY),
        save_history=lambda _tid, h: saved.append(list(h)),
    )
    monkeypatch.setattr(m, "store", st)
    return saved


def test_rewind_keeps_everything_before_the_edited_message(fake_store):
    info = m._rewind_history_to("task_20261005_aaaa", 4)      # 改第二条用户消息
    assert info["user_index"] == 2, info
    assert fake_store, "没写回历史"
    kept = fake_store[-1]
    assert [x["content"] for x in kept] == ["第一条问题", "第一条回答"], kept
    assert info["dropped"] == 2, info


def test_rewind_to_the_first_message_drops_everything(fake_store):
    info = m._rewind_history_to("task_20261005_aaaa", 1)
    assert fake_store[-1] == [], "改第一条时前面不该留任何历史"
    assert info["dropped"] == 4, info


@pytest.mark.parametrize("seq", [2, 3, 99, 0])
def test_non_user_message_seq_is_refused(fake_store, seq):
    with pytest.raises(Exception) as ei:
        m._rewind_history_to("task_20261005_aaaa", seq)
    assert "不是这个任务里的用户消息" in str(getattr(ei.value, "detail", ei.value))
    assert not fake_store, "拒绝了却还是写了历史"


def test_running_task_refuses_edit(monkeypatch, fake_store):
    """正在跑时改写会被运行中的 loop 覆盖回去 ⇒ 必须拒绝，而不是"以为改了"。"""
    class _Running:
        aio_task = SimpleNamespace(done=lambda: False)

        def inject_user(self, _t): pass

        def inject_system(self, _t): pass

    monkeypatch.setattr(m, "runs", {"task_20261005_aaaa": _Running()})
    monkeypatch.setattr(m, "_get_task", lambda _tid: SimpleNamespace(id="task_20261005_aaaa", title="t"))
    monkeypatch.setattr(m, "_start_run", lambda *a, **kw: pytest.fail("拒绝了却又启动了运行"))
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.post("/api/v1/tasks/task_20261005_aaaa/messages",
                   json={"text": "改一下", "edit_of_seq": 4})
    assert r.status_code == 409, r.text
    assert "正在运行" in r.json()["detail"]
    assert not fake_store, "拒绝了却还是写了历史"


def test_edit_rerun_started_from_the_rewound_history(monkeypatch, fake_store):
    """正常路径：作废 + 补一条说明事件 + 从改写点重跑。"""
    started: list[tuple] = []
    monkeypatch.setattr(m, "runs", {})
    monkeypatch.setattr(m, "_get_task", lambda _tid: SimpleNamespace(id="task_20261005_aaaa", title="t"))
    monkeypatch.setattr(m, "_start_run", lambda task, text, **kw: started.append((task.id, text, kw)))
    emitted: list[tuple] = []
    monkeypatch.setattr(m, "_emit_standalone",
                        lambda tid, type_, payload: emitted.append((type_, payload)) or _ev(6, type_, payload))
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.post("/api/v1/tasks/task_20261005_aaaa/messages",
                   json={"text": "第二条问题（改过）", "edit_of_seq": 4})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "edited_rerun" and body["user_index"] == 2, body
    assert [x["content"] for x in fake_store[-1]] == ["第一条问题", "第一条回答"]
    assert started and started[0][1] == "第二条问题（改过）", started
    # 事件流只追加：先补一条"从第 N 条重跑"的说明
    assert emitted and emitted[0][0] == "status", emitted
    assert "已在第 2 条用户消息处改写并重跑" in emitted[0][1]["detail"], emitted[0][1]
    assert "2 条对话已作废" in emitted[0][1]["detail"], emitted[0][1]


def test_plain_message_without_edit_still_works(monkeypatch, fake_store):
    """回归：不带 edit_of_seq 的老行为一个字节不变（普通续聊）。"""
    started: list[str] = []
    monkeypatch.setattr(m, "runs", {})
    monkeypatch.setattr(m, "_get_task", lambda _tid: SimpleNamespace(id="task_20261005_aaaa", title="t"))
    monkeypatch.setattr(m, "_start_run", lambda task, text, **kw: started.append(text))
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.post("/api/v1/tasks/task_20261005_aaaa/messages", json={"text": "再来一句"})
    assert r.status_code == 200 and r.json()["mode"] == "new_run", r.text
    assert started == ["再来一句"]
    assert not fake_store, "普通续聊不该动历史"

# ═══ 界面接线（脚本存在 ≠ 接上了）═══
import pathlib  # noqa: E402

_SRC = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"
_PANEL = (_SRC / "components" / "EventItem.tsx").read_text("utf-8")
_TV = (_SRC / "components" / "TaskView.tsx").read_text("utf-8")
_API = (_SRC / "api.ts").read_text("utf-8")
_VERIFY = (pathlib.Path(__file__).resolve().parents[2] / "frontend" / "scripts"
           / "verify_edit_resend.mjs").read_text("utf-8")


def test_edit_button_only_when_task_is_not_running():
    """运行中不给按钮 —— 后端会 409，界面先别让用户白点一次。"""
    assert "onEditResend={running ? undefined : editResend}" in _TV, \
        "没按运行状态开关「改一句重发」"
    assert "const editResend = useCallback(" in _TV, "回调没固定引用（会让 memo 的时间线整体重渲染）"


def test_edit_ui_warns_that_later_content_is_discarded():
    assert "改一句重发" in _PANEL and "edit-box" in _PANEL, "用户消息上没有编辑入口"
    assert "作废" in _PANEL, "编辑框里没提示「之后的内容会作废」（用户会以为只是改文字）"
    assert "disabled={!draft.trim() || draft.trim() === p.text.trim()}" in _PANEL, \
        "没改动时提交没禁用（误触就重跑一次）"


def test_api_passes_edit_of_seq():
    assert "editOfSeq?: number" in _API and "body.edit_of_seq = editOfSeq" in _API, \
        "前端没把 edit_of_seq 传给后端"


def test_verification_covers_both_directions_and_the_warning():
    assert "edit_of_seq" in _VERIFY, "没验证请求体里带 edit_of_seq"
    assert "运行中的任务不给这个按钮" in _VERIFY, "没验证「运行中不给按钮」"
    assert "作废" in _VERIFY, "没验证同屏提示"