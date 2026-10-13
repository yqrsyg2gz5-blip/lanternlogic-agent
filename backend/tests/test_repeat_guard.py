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
    _repeat_notes,
    _repeat_thresholds,
    _repeat_trip_message,
    _repeat_verdict,
    _repeat_verdict_for,
    _repeat_warn_message,
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


# ─────────────────────────────────────────────────────────────────────────────
# ★ 2026-10-13 真机事故：护栏"拦得住但说不清"（用户 task_20261013_0ea1f8de）
#
# 模型在工作区建了 `data/inspect.py`，**把 Python 标准库的 inspect 顶掉了**，
# 于是脚本连续 8 次报同一个错：
#     AttributeError: module 'inspect' has no attribute 'signature'
# 护栏判断是对的（该停），但提醒只说「请换一种做法（改参数、换工具、或先查清为什么没成功）」
# —— **没告诉它"你八次都在撞同一个错"**，模型继续重试到熔断收工 ✗
#
# 用户的原话：「同一个动作已经同步 10 次，提前收工……只会把部事扫在同一件事上」
# ⇒ 修法：把**上一次的报错原样附到提醒里**，让它一眼看出该改根因（这次是改文件名）。
# ─────────────────────────────────────────────────────────────────────────────

_INSPECT_ERR = ("Traceback (most recent call last):\n"
                '  File "<string>", line 7, in <module>\n'
                "AttributeError: module 'inspect' has no attribute 'signature'")


def test_repeat_notes_are_empty_without_an_error():
    """没有报错就别硬凑提示（成功后的重跑不是"卡住"，不该被说成卡住）。"""
    assert _repeat_notes(None) == ""
    assert _repeat_notes("") == ""


def test_repeat_notes_quote_the_actual_error():
    """★ 核心：把上一次的**报错原文**带出来 —— 这是模型唯一缺的信息。"""
    notes = _repeat_notes(_INSPECT_ERR)
    assert "inspect" in notes and "signature" in notes, f"没带出报错原文 ✗\n{notes}"
    assert "AttributeError" in notes, "报错类型丢了 ✗"


def test_repeat_notes_tell_it_to_fix_the_root_cause():
    """光贴报错不够，要明确说"别再跑同一条命令，改根因"。"""
    notes = _repeat_notes(_INSPECT_ERR)
    assert "根因" in notes, "没告诉它改根因 ✗"
    assert "再跑一次同一条命令" in notes, "没劝阻重试 ✗"


def test_repeat_notes_truncate_long_errors():
    """报错可能很长 —— 要截断（别把提醒本身撑成一条巨观察）。"""
    notes = _repeat_notes("X" * 5000, limit=300)
    assert len(notes) < 900, f"没截断，长度 {len(notes)} ✗"
    assert "…" in notes, "截断了但没有省略号（看不出被截）✗"


def test_warn_message_carries_the_error():
    """提醒正文里要能看到报错（模型读的就是这条 observation）。"""
    txt = _repeat_warn_message("shell_exec", 5, _INSPECT_ERR)
    assert "shell_exec" in txt
    assert "inspect" in txt, "提醒里没带报错 ⇒ 模型仍不知道改什么 ✗"


def test_trip_message_carries_the_error():
    """熔断收工的说明里也要带报错（这是它最后一次看到的信息）。"""
    txt = _repeat_trip_message("shell_exec", 12, _INSPECT_ERR)
    assert "12" in txt, "次数没写清 ✗"
    assert "inspect" in txt, "熔断说明里没带报错 ✗"
    assert "拆小" in txt or "换个办法" in txt, "没给出路 ✗"


def test_warn_and_trip_still_work_without_an_error():
    """没记录到报错时，提醒/熔断照常工作（不许因为新字段缺失就崩）。"""
    w = _repeat_warn_message("shell_exec", 3, None)
    t = _repeat_trip_message("shell_exec", 5, None)
    assert "shell_exec" in w and "shell_exec" in t
    assert "系统提醒" in w, "提醒的标志字样丢了（别处也认它）✗"
    assert "inspect" not in w, "没报错却编了一条报错出来 ✗"


def test_loop_records_and_clears_the_last_error():
    """★ 接线验证：loop 里要**记下报错**、并在成功时**清掉**。

    用源码级断言（跟仓里其它"接线不许被拆掉"的护栏同一手法）——
    真正要防的是"后来人把记录那几行删了、功能静默失效"。
    """
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "app" / "loop.py"
    code = src.read_text("utf-8")
    assert "_action_errors: dict[str, str] = {}" in code, "没有初始化 _action_errors ✗"
    assert "self._action_errors[sig] = output" in code, "失败时没记下报错 ✗"
    assert "self._action_errors.pop(sig, None)" in code, "成功时没清掉旧报错 ✗"
    # ⚠ sig 必须在执行链**外**算好：只在熔断分支里算的话，warning 分支与"记录报错"都拿不到它
    i_sig = code.find("sig = _action_signature(name, args)")
    i_chain = code.find("if err := self._validate(name, args):")
    assert 0 < i_sig < i_chain, "sig 没提到执行链之前（warning/记录会拿不到它）✗"


# ─────────────────────────────────────────────────────────────────────────────
# ★★ 2026-10-13 第二次真机事故：第一版的修法**自己套娃了**（用户当场抓到）
#
# 用户贴回来的实际输出：
#     ★ 而且你上一次的报错是这一条： （系统提醒）你已经做过 8 次完全一样的…
#     ★ 而且你上一次的报错是这一条： （系统提醒）你已经做过 7 次完全一样的…
#     …一层套一层，**真正的报错（AttributeError: inspect）反而被挤掉了** ✗
#
# 根因：我（画蛇添足）在 warn 分支把**提醒自己**写回了 `_action_errors`，
#       于是下一条提醒引用的"上一次报错"变成了上一条提醒。
# 正确语义：`_action_errors` **只存工具的报错**；提醒不是报错，不写回、也不在提醒时清掉
#           （`pop` 同样会丢掉真报错 —— 必须 `get`）。
# ─────────────────────────────────────────────────────────────────────────────

_GUARD_MSG = ("（系统提醒）你已经做过 8 次完全一样的「shell_exec」调用，参数也相同。"
              "请换一种做法（改参数、换工具、或先查清为什么没成功）；"
              "如果确实需要重试，请在正文里说明这次与上次有什么不同。")


def test_repeat_notes_refuse_to_nest_guard_messages():
    """★★ 套娃闸：护栏提醒**不是报错** ⇒ 不许被当成"上一次的报错"引出来。

    这条钉的就是用户当场抓到的那个 bug：提醒引用提醒，真报错被挤掉 ✗
    """
    notes = _repeat_notes(_GUARD_MSG)
    assert notes == "", f"把护栏提醒当成报错引出来了（套娃）✗\n{notes}"
    # 带前导空白的变体也要挡住（消息可能被拼过）
    assert _repeat_notes("  \n" + _GUARD_MSG) == "", "带空白前缀的提醒没挡住 ✗"
    # 而真报错照常带出
    assert "inspect" in _repeat_notes(_INSPECT_ERR), "真报错被误伤 ✗"


def test_warn_message_does_not_nest_even_if_fed_its_own_output():
    """端到端防套娃：拿 warn 的输出再喂回去，也不许长出新的一层。"""
    first = _repeat_warn_message("shell_exec", 5, _INSPECT_ERR)
    assert "inspect" in first, "第一次提醒就该带真报错 ✗"
    # 把第一条提醒当"上一次报错"喂进去（模拟当初的错误写法）
    second = _repeat_warn_message("shell_exec", 6, first)
    assert "而且你上一次的报错" not in second, f"第二次提醒又套了一层 ✗\n{second}"
    # 反复喂也不许增长
    third = _repeat_warn_message("shell_exec", 7, second)
    assert len(third) - len(second) < 60, "提醒在逐次变长 ⇒ 还在套娃 ✗"


def test_loop_does_not_write_guard_messages_back_into_the_error_map():
    """★ 接线不许回退：warn 分支**不得**把提醒写回 `_action_errors`。"""
    import pathlib
    code = (pathlib.Path(__file__).resolve().parents[1] / "app" / "loop.py").read_text("utf-8")
    assert "self._action_errors[sig] = result.output" not in code, (
        "warn 分支又把提醒写回报错表了 ⇒ 会再套娃 ✗（当初就是这么错的）"
    )
    # 提醒期间取出真报错必须用 get（pop 会把它丢掉）
    i_warn = code.find('if verdict_kind == "warn":')
    i_get = code.find("prev_error = self._action_errors.get(sig)")
    assert 0 < i_get < i_warn, "取报错没用 get（pop 会把真报错丢掉）✗"
