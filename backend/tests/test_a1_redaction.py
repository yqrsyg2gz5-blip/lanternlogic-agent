"""二十六轮第 7 批 A1：用户消息 / assistant 文本落盘必须打码。

真实现状（改前，真实事故）：
    `data/tasks/task_20261001_be1d/` 明文躺着 **6 处密钥形状串、0 处打码**。
    用户把 Key 粘进聊天框、或 agent 跑 `env` 把 Key 打出来，都会原样进
    `history.json` 与 `events.jsonl` —— 因为此前只有【工具观察面】与
    【action params】打了码，而 loop.py 里
      history user / emit user / history assistant / emit assistant
    这四个面**从没过打码管线**。

修法（分层，关键）：
    · 内存里的 history **保持真值**（agent 干活要用——把它的记忆打码会让它认不出
      自己的路径与取值，这个坑审计方踩过并专门回退过一次）；
    · **只有写盘的那份副本**过打码，实现在 `store.py`（落盘的唯一入口）；
    · 且只打【对话正文 user / assistant】两个面：其它事件各有自己的打码面
      （工具观察面刻意保留"版本名 / 编号"两类），在落盘口再套强规则会推翻它们的决定
      ——本班实测：全量套强规则时 `test_observation_filename_shape_preserved_e2e`
      当场变红。

本文件把两半都钉住，缺一不可：
    ① 磁盘上 0 明文；② 内存上下文里仍是真值。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.approval import ApprovalManager
from app.bus import EventBus
from app.executors.local import LocalExecutor
from app.loop import TaskRun
from app.providers.base import AssistantTurn, ModelProvider
from app.schemas import EventEnvelope, TaskSummary
from app.store import FsStore

# 三种不同形态的密钥（覆盖 sk- / AWS / GitHub 三个正则分支）
SK_KEY = "sk-live-9f3a2b7c8d1e4f5a"
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
GH_KEY = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

USER_INPUT = f"帮我看下这两个 Key 还有效吗：{SK_KEY} 和 {AWS_KEY}"
ASSISTANT_REPLY = f"我看到的值是 {GH_KEY} ，建议轮换。"


class _KeyEchoProvider(ModelProvider):
    """回复里【故意】带一个密钥形状串 —— 复刻"模型把看到的值复述出来"的真实场景。"""

    name = "key-echo"

    async def next_turn(self, task_input, history, tools, on_delta=None):  # type: ignore[override]
        return AssistantTurn(text=ASSISTANT_REPLY)


def _run(tmp_path, user_input: str = USER_INPUT) -> TaskRun:
    """走真实入口 `_run()`（它才有 finally 里的 `_save_history()`）。"""
    store = FsStore(tmp_path / "data")
    task = TaskSummary(
        id="task_20261004_a1aa", title="A1 落盘打码",
        created_at="2026-10-04T00:00:00Z", updated_at="2026-10-04T00:00:00Z",
    )
    ex_cfg = SimpleNamespace(
        workspace_root=tmp_path / "ws", timeout_seconds=5.0, shell="",
        search_url="", browser_channel="", comfyui_url="", image_checkpoint="",
        allowed_dirs=[],
    )
    run = TaskRun(
        task, user_input, store=store, bus=EventBus(), provider=_KeyEchoProvider(),
        executor=LocalExecutor(ex_cfg), approval=ApprovalManager(), tools=[],
        max_iterations=2, timeout_seconds=5.0, approval_required=[],
        on_finish=lambda r: None,
    )
    asyncio.run(run._run())
    return run


# ══════════════════ ① 磁盘上 0 明文 ══════════════════


def test_user_message_not_plaintext_on_disk(tmp_path):
    """★ 核心锚点（真实事故那一面）：用户粘进聊天框的 Key 不得明文落盘。"""
    run = _run(tmp_path)
    hist_raw = run.store.history_file(run.task.id).read_text(encoding="utf-8")
    ev_raw = run.store.events_file(run.task.id).read_text(encoding="utf-8")

    for name, raw in (("history.json", hist_raw), ("events.jsonl", ev_raw)):
        hits = [s for s in (SK_KEY, AWS_KEY) if s in raw]
        assert not hits, f"{name} 里仍是明文：{hits}"
    # 而且确实打了码（不是"这条消息根本没落盘"式的假绿）
    assert "已隐藏" in hist_raw, f"history.json 里没看到打码标记——消息可能压根没落盘：{hist_raw[:200]}"
    assert "已隐藏" in ev_raw, f"events.jsonl 里没看到打码标记：{ev_raw[:200]}"


def test_assistant_text_not_plaintext_on_disk(tmp_path):
    """assistant 复述出来的 Key 同样不许明文落盘。"""
    run = _run(tmp_path)
    hist_raw = run.store.history_file(run.task.id).read_text(encoding="utf-8")
    ev_raw = run.store.events_file(run.task.id).read_text(encoding="utf-8")
    assert GH_KEY not in hist_raw, "history.json 里 assistant 的 Key 是明文"
    assert GH_KEY not in ev_raw, "events.jsonl 里 assistant 的 Key 是明文"


# ══════════════════ ② 内存上下文仍是真值（另一半，别漏） ══════════════════


def test_memory_context_keeps_real_value(tmp_path):
    """★ 反向锚点：agent 的内存上下文**必须**保留真值。

    直接把 agent 记忆也打码，会让它认不出自己的路径/取值 —— 那个坑我们踩过
    （title 打码回退）。所以这条和上面那条是【一对】，缺一个都不算修好。
    """
    run = _run(tmp_path)
    blob = json.dumps(run.history, ensure_ascii=False)
    assert SK_KEY in blob, "内存里的用户消息被打码了 —— agent 会拿不到真值"
    assert GH_KEY in blob, "内存里的 assistant 文本被打码了"
    assert "已隐藏" not in blob, f"内存上下文里出现了打码标记，说明写盘时改到了原对象：{blob[:200]}"


def test_store_does_not_mutate_input_args(tmp_path):
    """单元级把"不改入参"钉死（函数契约，比 e2e 更好定位）。"""
    store = FsStore(tmp_path / "data")
    history = [{"role": "user", "content": f"key={SK_KEY}"},
               {"role": "tool", "content": "SKU: SK-2026-001"}]
    snapshot = json.dumps(history, ensure_ascii=False)
    store.save_history("task_20261004_a1aa", history)
    assert json.dumps(history, ensure_ascii=False) == snapshot, "save_history 改了入参（内存真值被破坏）"
    assert SK_KEY not in store.history_file("task_20261004_a1aa").read_text(encoding="utf-8")

    ev = EventEnvelope(id="evt_000001", seq=1, task_id="task_20261004_a1aa", type="message",
                       ts="2026-10-04T00:00:00Z",
                       payload={"role": "user", "text": f"key={SK_KEY}"})
    store.append_event(ev)
    assert ev.payload["text"] == f"key={SK_KEY}", "append_event 改了传入的 event 对象"
    assert SK_KEY not in store.events_file("task_20261004_a1aa").read_text(encoding="utf-8")


# ══════════════════ 反过度打码（保护既有决定） ══════════════════


def test_observation_face_keeps_its_own_weaker_rule(tmp_path):
    """★ 反过度打码：工具观察面走的是【弱一档】口径，刻意保留两类形态。

    `SK-2026-001`（编号）与 `sk-model-v2`（版本名）是【结构上确定不是密钥】的形态，
    agent 要靠它们认东西。落盘口**不许**再套强规则把它们打掉 ——
    本班第一版就是在 store 里无脑套了 redact_deep，当场把
    `test_observation_filename_shape_preserved_e2e` 打红。

    ★ 架构说明（别误会）：观察面的打码发生在【上游】`loop._redact`
      （走 `redact_text_tool`），store 刻意【不】对它二次打码。
      本条的职责是"防止有人往落盘口加全量强规则"，不是"证明观察面已打码"。
      观察面为什么必须保留弱一档、以及它已接线，由
      `test_observation_filename_shape_preserved_e2e` 等既有锚点负责。
    """
    store = FsStore(tmp_path / "data")
    ev = EventEnvelope(id="evt_000001", seq=1, task_id="task_20261004_a1aa", type="observation",
                       ts="2026-10-04T00:00:00Z",
                       payload={"result": "SKU: SK-2026-001 / model: sk-model-v2"})
    store.append_event(ev)
    raw = store.events_file("task_20261004_a1aa").read_text(encoding="utf-8")
    assert "SK-2026-001" in raw, "把编号形态打掉了（过度打码）"
    assert "sk-model-v2" in raw, "把版本名形态打掉了（过度打码）"
