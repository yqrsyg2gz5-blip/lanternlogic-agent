"""★ P0-3 重复动作熔断：治"步骤重复"——实证里最高频的多智能体失败。

## 依据

· **MAST**（200+ 轨迹，NeurIPS 2025）：FC1 里 **"步骤重复"占 17.1%**，是所有失败模式里最高的；
  论文把它归因于"**死板的轮次配置**"——模型会一遍遍重试同一个动作，把预算烧光。
· 我们自己的现场：3 个群任务（测试工程师/程序员/设计师）都是**把 25 步烧在同几件事上**，
  最后以"达到最大迭代次数"失败（用户问过这个）。

## 语义

1. **指纹**：工具名 + 归一化参数（键排序、去空白、忽略 `call_id` 这类每次都变的键）。
   保守归一化 —— 宁可漏判，也不要把"参数略不同的合理尝试"当成重复
2. 同一指纹累计到 **3 次** ⇒ 提醒：**不执行这次调用**，直接回一条观察让它换办法（省时间也省副作用）
3. 累计到 **5 次** ⇒ **熔断收工**：如实报 `step_repetition`，状态置 `partial`
   （已产出的东西照常交付、验收照常跑——这才是诚实的"做了一部分"，而不是"全完了"）
4. 群里要告诉用户**怎么办**（拆小 / 明确换个做法），而不是只报一句失败
"""
from __future__ import annotations

import pytest

from app import main as m
from app.loop import (
    _action_signature,
    _repeat_thresholds,
    _repeat_verdict,
    _repeat_verdict_for,
)


def test_signature_normalizes_conservatively():
    s = _action_signature
    assert s("shell_exec", {"command": "npm test"}) == s("shell_exec", {"command": "npm  test "})
    assert s("shell_exec", {"a": 1, "b": 2}) == s("shell_exec", {"b": 2, "a": 1})
    assert s("shell_exec", {"x": 1, "call_id": "a"}) == s("shell_exec", {"x": 1, "call_id": "b"})
    assert s("shell_exec", {"x": 1, "y": ""}) == s("shell_exec", {"x": 1})


def test_signature_distinguishes_real_differences():
    s = _action_signature
    assert s("shell_exec", {"command": "npm test"}) != s("shell_exec", {"command": "npm run build"})
    assert s("shell_exec", {"x": 1}) != s("file_read", {"x": 1})


@pytest.mark.parametrize("count,expect", [
    (1, "ok"), (2, "ok"),
    (3, "warn"), (4, "warn"),
    (5, "trip"), (9, "trip"),
])
def test_thresholds(count, expect):
    """★ 第 3 次提醒（不执行、喂一条观察），第 5 次熔断收工（默认 25 步预算）。"""
    assert _repeat_verdict(count) == expect


@pytest.mark.parametrize("budget,warn,trip", [
    (25, 3, 5),      # 默认：提醒 3、熔断 5
    (60, 7, 12),     # 预算调大 ⇒ 阈值跟着放大（别在还早的时候就掐）
    (8, 3, 5),       # 预算很小 ⇒ 仍不早于 3/5
    (4, 3, 4),       # 极小预算：熔断不会小于等于提醒（否则第一次就熔断）
])
def test_thresholds_scale_with_the_step_budget(budget, warn, trip):
    """★ Jev 判定建议：阈值要跟**步数预算**联动（固定 3/5 只适合 25 步那种预算）。"""
    assert _repeat_thresholds(budget) == (warn, trip)


def test_scaled_verdict_never_trips_before_warning():
    for budget in (4, 8, 25, 60, 200):
        warn, trip = _repeat_thresholds(budget)
        assert trip > warn, (budget, warn, trip)
        assert _repeat_verdict_for(warn, warn, trip) == "warn"
        assert _repeat_verdict_for(trip, warn, trip) == "trip"
        assert _repeat_verdict_for(warn - 1, warn, trip) == "ok"


def test_loop_uses_the_guard_and_reports_honestly():
    """接线锚点：循环里真的调了它，且熔断时**如实报 partial**（不是装作全做完、也不是全盘失败）。"""
    src = (__import__("pathlib").Path(__file__).resolve().parents[1] / "app" / "loop.py").read_text("utf-8")
    assert "_repeat_verdict_for(hit, _warn_at, _trip_at)" in src, "循环没接上重复判定"
    assert 'code": "step_repetition"' in src, "熔断没报出可识别的 code"
    assert 'self.set_status("partial")' in src, "熔断后应当如实报 partial（已有产物照常交付）"
    # 提醒分支必须**跳过这次调用**（否则提醒了还是照跑，等于没治）
    assert "不执行**这次调用" in src or "不执行" in src


def test_group_tells_the_user_what_to_do_about_repetition(monkeypatch):
    """群里不能只报一句失败：要给出"拆小 / 明确换个做法"两条出路。"""
    from types import SimpleNamespace

    monkeypatch.setattr(m.store, "read_events", lambda tid: [
        SimpleNamespace(type="error", payload={"code": "step_repetition",
                                              "message": "同一动作重复 5 次，提前收工"}),
    ])
    why = m._task_failure_reason("t1")
    assert "打转" in why or "反复重试" in why, why
    assert "拆小" in why and "换" in why, why


def test_other_failure_codes_still_work(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(m.store, "read_events", lambda tid: [
        SimpleNamespace(type="error", payload={"code": "max_iterations", "message": "达到最大迭代次数（25）"}),
    ])
    assert "步数用完" in m._task_failure_reason("t1")
