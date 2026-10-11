"""二十六轮第 7 批第 7 处：`GET /api/v1/usage` 的【端点级】锚点。

现状（二十六轮第 6 批验收，决定性原始输出）：
    把 main.py 的 `label = _usage_label(full_title)` 回退成 `full_title[:42]` → rc=0（不红）
    撤掉 `t_model = _usage_model(t_model)`                                → rc=0（不红）
    全仓 grep 'api/v1/usage|get_usage' 在 tests 里 → 【0 命中】
  ⇒ 端点的**接线**完全没有锚点；既有锚点（test_loop_history_recording.py）只测
    `_usage_label` / `_usage_model` / `_compose_usage_label` 三个纯函数
    ——"函数改对了"不等于"端点接上了"。

本文件打【真端点】：TestClient 真调 `GET /api/v1/usage`，再**递归遍历整个响应
JSON（含 dict 的键名）**断言三条密钥明文 0 出现。三面各埋一条不同形态的密钥：
  · TITLE_SECRET → 走 `_usage_label`（label 面）
  · MSG_SECRET   → 走 `_compose_usage_label` 的"首条 user 消息"分支（full_label 面）
  · MODEL_SECRET → 走 `_usage_model`，它同时出现在 `by_model` 的【键名】与
    `by_task[].model` 的【值】上。
    ★ 口径更正（二十六轮第 7 批独立验证员用 values-only 对照遍历器实测）：
      键名遍历是【额外检出通道】，**不是**本锚点变红的必要条件——同一个
      t_model 既喂 by_model 键（main.py:1490）也喂 by_task[].model 值（:1524），
      所以只查 value 的遍历器同样会红。保留键名遍历是为了覆盖"只由键承载"
      的形态，并让泄漏定位能直接指到键上。（原稿写"只查 value 抓不到它，
      这正是必须连键名一起遍历的原因"——**该必要性声称已被证伪，此处更正**。）
并配一个**反假绿对照**：先证明响应里确实有这条任务的用量（否则"0 明文"是因为
响应是空的，锚点会假绿）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main as m
from app.schemas import TaskSummary

TID = "task_20261004_aa11"
TITLE_SECRET = "sk-title-9f3a2b7c8d1e4f5a"          # \bsk-[A-Za-z0-9_-]{6,}
MSG_SECRET = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"  # \bghp_[A-Za-z0-9]{20,}
MODEL_SECRET = "AKIAIOSFODNN7EXAMPLE"                # \b(?:AKIA|ASIA)[A-Z0-9]{16}
SECRETS = (TITLE_SECRET, MSG_SECRET, MODEL_SECRET)

# 标题必须 >=30 字才会走"首条 user 消息"分支（main.py:_compose_usage_label 的规则）
TITLE = f"处理密钥迁移与轮换的完整任务标题 {TITLE_SECRET}"
assert len(TITLE) >= 30, len(TITLE)


def _walk(node, path="$"):
    """递归产出 (路径, 字符串)。★ dict 的【键名】也要下钻。"""
    if isinstance(node, dict):
        for k, v in node.items():
            yield f"{path}.{k}（键名）", str(k)
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, str(node)


def _usage_event() -> SimpleNamespace:
    """一条结构化用量事件（title 含"用量"才被端点认作用量事件）。"""
    return SimpleNamespace(
        type="knowledge",
        payload={
            "title": f"用量（{MODEL_SECRET}）",
            "content": "LLM 调用 3 次 | 输入 100 tok + 输出 50 tok",
            "usage": {
                "model": MODEL_SECRET, "calls": 3,
                "input_tokens": 100, "output_tokens": 50, "cached_tokens": 7,
            },
        },
    )


def _fake_store() -> SimpleNamespace:
    return SimpleNamespace(
        read_events=lambda tid: [_usage_event()],
        load_history=lambda tid: [{"role": "user", "content": f"处理 {MSG_SECRET} 迁移"}],
    )


def _task() -> TaskSummary:
    return TaskSummary(
        id=TID, title=TITLE,
        created_at="2026-10-04T00:00:00Z", updated_at="2026-10-04T00:00:00Z",
    )


@pytest.fixture()
def usage_json(monkeypatch):
    """真端点响应体（lifespan 真跑；Host 用回环名过 guard_local_origin）。"""
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        monkeypatch.setattr(m, "tasks", {TID: _task()})
        monkeypatch.setattr(m, "store", _fake_store())
        r = c.get("/api/v1/usage")
        assert r.status_code == 200, r.text
        return r.json()


def test_usage_endpoint_has_no_plaintext_secret_anywhere(usage_json):
    """★ 核心锚点：整个响应 JSON（含键名）不得出现任何一条明文密钥。"""
    leaks = [
        (p, s) for p, s in _walk(usage_json) if any(sec in s for sec in SECRETS)
    ]
    assert not leaks, (
        "GET /api/v1/usage 响应里出现明文密钥（接线漏打码）："
        + "; ".join(f"{p} 含 {s[:12]}…" for p, s in leaks)
    )
    # 三条面都要被真的走到——防止把某面改没了导致本条"假绿"
    assert usage_json["tasks_with_usage"] == 1
    assert usage_json["by_task"], "by_task 为空：响应没走到 label/full_label 面"
    assert usage_json["by_model"], "by_model 为空：响应没走到 model 键面"


def test_usage_endpoint_positive_control(usage_json):
    """反假绿对照：响应必须真的带上这条任务的用量数据。

    若某次改动让端点返回空结构，上一条会因"没有明文"而假绿——本条钉住它。
    """
    assert usage_json["calls"] == 3, usage_json
    assert usage_json["input_tokens"] == 100
    assert usage_json["output_tokens"] == 50
    assert usage_json["cached_tokens"] == 7
    assert usage_json["by_task"][0]["task_id"] == TID
    assert usage_json["by_task"][0]["total_tokens"] == 150
    # 打码后的形态仍非空（不是"整条被删"式的通过）
    assert usage_json["by_task"][0]["label"], "label 被删成空——不是打码是丢数据"
    assert usage_json["by_task"][0]["model"] not in SECRETS


def test_usage_label_keeps_42_char_budget(usage_json):
    """label 仍是 [:42] 的显示口径（脱敏后截断），别把重命名任务的标签改短。"""
    label = usage_json["by_task"][0]["label"]
    assert len(label) <= 42, f"label 长度 {len(label)} 超 42"


def test_walk_helper_scans_dict_keys():
    """元锚点：确认遍历器真的会下钻到 dict 键名。

    ★ 口径更正（独立验证员用 values-only 对照遍历器实测）：键名遍历是
    【额外检出通道】，不是主锚点变红的**必要条件**——同一个 t_model 也喂
    `by_task[].model` 的【值】，只查 value 同样会红。本元锚点的作用是保证
    "by_model 键"这条独立通道**不退化**（否则键侧泄漏会漏检），而不是
    "没有它主锚点就瞎"。原文写"否则上面那条锚点会瞎"——夸大，已更正。
    """
    seen = dict(_walk({"AKIAIOSFODNN7EXAMPLE": 1}))
    assert any(MODEL_SECRET in s for _, s in seen.items()), \
        "遍历器没扫键名——by_model 键这条独立检出通道退化了"


# ---------- full_label 的"无条件脱敏"（第5批第4处 / 第6批钉住） ----------
SHORT_MSG_SECRET = "sk-short-1a2b3c4d"   # 15 字 < 30 —— 走"短串"分支


def test_usage_full_label_short_first_message_still_redacted(monkeypatch):
    """★ 首条 user 消息 <30 字时也必须打码（覆盖"无条件化"这条口径）。

    第 4 批版本是 `(_redact_text(base)[:200] if len(base) >= 30 else base)`
    ——短消息（含密钥）**原样进外发面**（验证员实测指出的路径不对称）。
    把这条"无条件"改回条件式即红；这也是 §6 要恢复的 full_label 回滚组的红例来源。
    """
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        monkeypatch.setattr(m, "tasks", {TID: _task()})
        monkeypatch.setattr(m, "store", SimpleNamespace(
            read_events=lambda tid: [_usage_event()],
            load_history=lambda tid: [{"role": "user", "content": SHORT_MSG_SECRET}],
        ))
        r = c.get("/api/v1/usage")
        assert r.status_code == 200, r.text
        j = r.json()
    assert len(TITLE) >= 30, "前提：标题够长才会走首条消息分支"
    leaks = [(p, s) for p, s in _walk(j) if SHORT_MSG_SECRET in s]
    assert not leaks, f"短首条消息（<30 字）未打码，明文进外发面：{leaks}"
    assert j["by_task"][0]["full_label"], "full_label 被删空——不是打码是丢数据"

