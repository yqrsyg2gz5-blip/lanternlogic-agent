"""history 规范化回归测试 —— 第 2 班（P0-7）。

背景：`task_done` 分支在写 tool 回执前就 `return`，导致落盘 history 以悬空
`assistant[tool_calls]` 结尾；续聊时该序列直接接新的 user 消息，OpenAI 兼容
接口按协议必须 400。
实测：44 份 `history.json` 中 **23 份（52%）** 落此状态。

本文件的用例就是这条缺陷的护栏——mock 冒烟测不出来（MockProvider 不读 history）。
"""
from __future__ import annotations

from app.loop import normalize_history


def _asst(ids: list[str]) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": i, "type": "function", "function": {"name": "n", "arguments": "{}"}}
            for i in ids
        ],
    }


def test_trailing_dangling_call_gets_receipt():
    """最主流形态：任务以 task_done 收尾 → 末尾悬空。"""
    h = [{"role": "user", "content": "hi"}, _asst(["call_001"])]
    out = normalize_history(h)
    assert [m["role"] for m in out] == ["user", "assistant", "tool"]
    assert out[-1]["tool_call_id"] == "call_001"
    assert out[-1]["content"]


def test_dangling_before_user_message_gets_receipt():
    """续聊场景：悬空之后已经跟了一条 user 消息（必须把回执插在它前面）。"""
    h = [
        {"role": "user", "content": "a"},
        _asst(["call_001"]),
        {"role": "user", "content": "继续"},
    ]
    out = normalize_history(h)
    assert [m["role"] for m in out] == ["user", "assistant", "tool", "user"]
    assert out[2]["tool_call_id"] == "call_001"
    assert out[3]["content"] == "继续"


def test_paired_history_is_untouched():
    """已经配对完整的历史必须原样通过。"""
    h = [
        {"role": "user", "content": "a"},
        _asst(["call_001"]),
        {"role": "tool", "tool_call_id": "call_001", "content": "ok"},
    ]
    assert normalize_history(h) == h


def test_idempotent():
    """幂等：二次规范化不改变结果（落盘前会反复调用）。"""
    h = [{"role": "user", "content": "a"}, _asst(["call_001"])]
    once = normalize_history(h)
    assert normalize_history(once) == once


def test_multiple_pending_ids_all_get_receipts():
    h = [_asst(["call_001", "call_002"])]
    out = normalize_history(h)
    assert [m["tool_call_id"] for m in out[1:]] == ["call_001", "call_002"]


def test_original_list_not_mutated():
    """返回新列表：调用方（_save_history）可能依赖入参不被就地改写。"""
    h = [{"role": "user", "content": "a"}, _asst(["call_001"])]
    normalize_history(h)
    assert len(h) == 2


def test_middle_dangling_does_not_swallow_later_messages():
    """中间悬空 + 后续还有完整轮次：只补该补的，不动其它。"""
    h = [
        {"role": "user", "content": "a"},
        _asst(["call_001"]),
        {"role": "user", "content": "b"},
        _asst(["call_002"]),
        {"role": "tool", "tool_call_id": "call_002", "content": "ok"},
    ]
    out = normalize_history(h)
    assert [m["role"] for m in out] == ["user", "assistant", "tool", "user", "assistant", "tool"]
    assert out[2]["tool_call_id"] == "call_001"
    assert out[-1]["tool_call_id"] == "call_002"


def test_real_world_shape_from_actual_data():
    """复刻真实落盘形态（取自 backend/data/tasks/*/history.json 的悬空样本）。"""
    h = [
        {"role": "system", "content": "[可用技能]\n- 写作助手：…"},
        {"role": "user", "content": "做个介绍 LanternLogic Agent 的单页网站"},
        _asst(["call_004"]),
        {"role": "tool", "tool_call_id": "call_004", "content": "已写入 index.html"},
        _asst(["call_005"]),  # ← task_done 交付：悬空（真实数据就是这样结尾的）
    ]
    out = normalize_history(h)
    assert out[-1]["role"] == "tool"
    assert out[-1]["tool_call_id"] == "call_005"
    # 前面已经配对的 call_004 不受影响
    assert [m.get("tool_call_id") for m in out if m["role"] == "tool"] == ["call_004", "call_005"]
