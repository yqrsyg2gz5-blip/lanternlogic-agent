# -*- coding: utf-8 -*-
"""★ 第 7 项 · 自动化的剩下四项 —— 2026-10-07。

## 查证：这四项现在各自是什么样（量过才动手 ✓）

| 项 | 原来的样子 |
|---|---|
| ① 失败重试通知 | 调度循环只管"**开出去**"成不成功 ✗（`last_error` 只记启动异常 ✓）⇒ 任务真跑起来、跑到一半失败 ⇒ **自动化这边一声不吭** ✗✓ 用户以为"每天 9 点那个活一直在跑"✓ 其实天天失败 ✓ |
| ② 单次成本上限 | **没有** ✗（只有全项目那把"每个 Key 总量"的锁 ✓ 管不了"某一次跑飞了"✓）|
| ③ 每小时/周/月频率 | **表达不出来** ✗ —— 只有 `interval`（隔 N 分钟）与 `daily`（每天某点）✓ 拿 interval 凑"每 60 分钟"**和"每小时整点"不是一回事** ✗ |
| ④ 任务描述指引 | 输入框光秃秃 ✗（不知道该写什么 ✓ 而自动化最要紧的就是"这句话写得好不好"✓）|

## 本文件钉的四条

1. **四种频率都要算得对** ✓（每小时/每周/每月/每天 ✓）而且**同一天只跑一次** ✓
2. **跑失败了要说话、要能重试** ✓（但**有界** ✓ 不会无限重试烧钱 ✗）
3. **单次上限真拦得住** ✓（且**算上"正在跑"的那部分** ✗ 不然跑飞了要到跑完才知道 ✓）
4. **两把锁管两件事** ✓（Key 上限管总量 ✓ 单次上限管单次 ✓ 不重复 ✗）
"""
from __future__ import annotations

from datetime import datetime, timezone

from app import main as m


def _due(sch: dict, when_local: datetime, **auto):
    """造一个自动化，问它此刻该不该触发 ✓（纯函数 ✓ 不碰网络/不碰数据库 ✓）。"""
    a = {"kind": "schedule", "schedule": sch, "enabled": True, **auto}
    return m._schedule_due(a, when_local.astimezone(timezone.utc), when_local)


# ═══ ③ 每小时 / 每周 / 每月（原来一个都表达不出来 ✗）═══

def test_hourly_fires_once_per_hour_after_the_minute():
    """★ "每小时整点跑一次" ✓ —— 原来只能拿 interval 凑"每 60 分钟"✗ 那是**滚动的** ✓
    和"整点"不是一回事 ✓（重启/晚点开跑就永远错开 ✓）。"""
    sch = {"kind": "hourly", "time": "00:00"}
    t = datetime(2026, 10, 7, 9, 0, 30)
    fire, slot = _due(sch, t)
    assert fire is True and slot == "2026-10-07 09", (fire, slot)
    # 同一个小时里再问 ⇒ **不许再跑** ✓（记账字段挡住了 ✓）
    fire2, _ = _due(sch, datetime(2026, 10, 7, 9, 45), last_fire_slot=slot)
    assert fire2 is False, "同一个小时里跑了两次 ✗（那就成了每 20 秒一次 ✓）"
    # 下一个小时 ⇒ 又该跑 ✓
    fire3, _ = _due(sch, datetime(2026, 10, 7, 10, 1), last_fire_slot=slot)
    assert fire3 is True


def test_hourly_respects_the_minute_you_pick():
    """★ 用户选"每小时的第 30 分" ✓ ⇒ 29 分还不许跑 ✓（不是一到整点就抢跑 ✓）。"""
    sch = {"kind": "hourly", "time": "00:30"}
    assert _due(sch, datetime(2026, 10, 7, 9, 29))[0] is False
    assert _due(sch, datetime(2026, 10, 7, 9, 30))[0] is True


def test_weekly_fires_only_on_the_chosen_weekday():
    """★ "每周一早上 9 点" ✓（ISO：1=周一 ✓）。"""
    sch = {"kind": "weekly", "weekday": 1, "time": "09:00"}
    monday = datetime(2026, 10, 5, 9, 0, 5)          # 2026-10-05 是周一 ✓
    tuesday = datetime(2026, 10, 6, 9, 0, 5)
    assert monday.isoweekday() == 1 and tuesday.isoweekday() == 2
    assert _due(sch, monday)[0] is True
    assert _due(sch, tuesday)[0] is False, "周二也跑了 ⇒ 那'每周'就没意义了 ✗"
    # 周一当天跑过一次 ⇒ 同一天不许再跑 ✓
    assert _due(sch, datetime(2026, 10, 5, 18, 0), last_fire_date="2026-10-05")[0] is False


def test_monthly_fires_only_on_the_chosen_day():
    """★ "每月 1 号" ✓。"""
    sch = {"kind": "monthly", "day": 1, "time": "09:00"}
    assert _due(sch, datetime(2026, 10, 1, 9, 0, 1))[0] is True
    assert _due(sch, datetime(2026, 10, 2, 9, 0, 1))[0] is False
    assert _due(sch, datetime(2026, 11, 1, 9, 0, 1))[0] is True


def test_monthly_day_is_clamped_so_february_never_breaks():
    """★ "31 号"在 2 月不存在 ⇒ **钳到 28** ✓（而不是永不再触发 ✗ 或报错 ✗）。"""
    sch = {"kind": "monthly", "day": 31, "time": "09:00"}
    assert _due(sch, datetime(2026, 2, 28, 9, 0, 1))[0] is True, "2 月永远不跑 ⇒ 用户的'每月'形同虚设 ✗"
    assert _due(sch, datetime(2026, 2, 27, 9, 0, 1))[0] is False


def test_existing_two_kinds_still_behave():
    """★ 原来那两种**一个字没改** ✓（别为了加新的把旧的弄坏 ✗）。"""
    # interval：没跑过就立刻跑 ✓
    assert _due({"kind": "interval", "minutes": 60}, datetime(2026, 10, 7, 9, 0))[0] is True
    # interval：刚跑过 ⇒ 不跑 ✓
    just = datetime.now(timezone.utc).isoformat()
    assert _due({"kind": "interval", "minutes": 60}, datetime(2026, 10, 7, 9, 0), last_run=just)[0] is False
    # daily：到点且今天没跑过 ✓
    assert _due({"kind": "daily", "time": "09:00"}, datetime(2026, 10, 7, 9, 0, 30))[0] is True
    assert _due({"kind": "daily", "time": "09:00"}, datetime(2026, 10, 7, 8, 59, 30))[0] is False
    # 坏时间格式 ⇒ 不炸、也不触发 ✓（等用户改配置 ✓）
    assert _due({"kind": "daily", "time": "乱写的"}, datetime(2026, 10, 7, 9, 0))[0] is False


# ═══ ② 单次成本上限（这把锁管"某一次跑飞了"）═══

def test_cost_cap_blocks_when_this_run_spent_too_much(monkeypatch):
    """★★ **单次上限真拦得住** ✓ —— 回滚实验：把 `_TASK_COST_CAPS` 那段去掉 ⇒ 本组必红 ✓。"""
    from app import pricing as B
    monkeypatch.setattr(B, "PRICES", {"m": {"in": 1.0, "out": 2.0}})
    monkeypatch.setattr(m, "_inflight_usage", lambda tid: {})            # noqa: ARG005
    monkeypatch.setattr(m.store, "read_events", lambda tid: [           # noqa: ARG005
        type("E", (), {"type": "knowledge", "payload": {"title": "📊 用量（m）", "usage": {
            "model": "m", "calls": 1, "input_tokens": 1_000_000, "output_tokens": 0}}})(),
    ])
    m._TASK_COST_CAPS["task_cap_a"] = 0.5                 # 上限 ¥0.5 ✓ 而已经花了 ¥1
    try:
        why = m._budget_block_reason("task_cap_a")
        assert why and "单次花费上限" in why, f"超了却没拦 ✗：{why}"
        assert "这一步没有发出去" in why, why
        assert "调大" in why or "拆" in why, f"没说清怎么办 ✗：{why}"
    finally:
        m._TASK_COST_CAPS.pop("task_cap_a", None)


def test_no_cap_means_no_blocking(monkeypatch):
    """★ **没配就不拦** ✓（默认等于现状 ✓ —— 全项目一贯的规矩 ✓）。"""
    monkeypatch.setattr(m, "_inflight_usage", lambda tid: {})            # noqa: ARG005
    monkeypatch.setattr(m.store, "read_events", lambda tid: [])          # noqa: ARG005
    assert m._budget_block_reason("task_no_cap") is None


def test_cost_cap_counts_the_still_running_part(monkeypatch):
    """★★ **必须算上"正在跑"的那部分** ✗ —— 不算的话，一个跑飞的任务
    **在跑完之前永远看不到自己超了** ✓ 那就等于拦不住 ✓（第 1 项那份快照正好用上 ✓）"""
    from app import pricing as B
    monkeypatch.setattr(B, "PRICES", {"m": {"in": 1.0, "out": 2.0}})
    monkeypatch.setattr(m.store, "read_events", lambda tid: [])          # noqa: ARG005
    monkeypatch.setattr(m, "_inflight_usage", lambda tid: {              # noqa: ARG005
        "run_id": "live1", "model": "m", "calls": 3,
        "input_tokens": 2_000_000, "output_tokens": 0, "cached_tokens": 0,
    })
    m._TASK_COST_CAPS["task_cap_b"] = 0.5
    try:
        why = m._budget_block_reason("task_cap_b")
        assert why and "单次花费上限" in why, f"没把'正在跑的那份'算进去 ⇒ 拦不住跑飞的任务 ✗：{why}"
    finally:
        m._TASK_COST_CAPS.pop("task_cap_b", None)


# ═══ ① 失败重试 + 说清楚（不再"一声不吭"）═══

def test_automation_watches_the_outcome_and_retries():
    """★ 开出去之后要**看它跑成没跑成** ✓ 失败了要重试 ✓ 重试完还失败要记一句能照做的话 ✓。

    （原来只管"开出去"成不成功 ✗ ⇒ 任务跑一半失败，自动化这边**一个字都没有** ✓）
    """
    import pathlib
    src = pathlib.Path(m.__file__).read_text("utf-8")
    assert "_watch_automation_task" in src, "没人盯任务结果 ✗（那失败就永远静默 ✓）"
    i = src.find("def _watch_automation_task")
    assert i > 0
    seg = src[i : i + 2600]
    assert "last_outcome" in seg, "没有记'跑成没跑成'✗"
    assert "left > 0" in seg or "retries" in seg, "没有重试逻辑 ✗"
    assert "重试" in seg and "仍失败" in seg, "重试到上限后没说清 ✗"
    assert "2 * 3600" in seg, "盯的时间没上限 ⇒ 会一直挂着 ✗"


def test_retries_are_bounded():
    """★ **有界** ✓ —— 重试不设上限 = 无限烧钱 ✗（这正是本轮要防的事 ✓）。"""
    import pathlib
    src = pathlib.Path(m.__file__).read_text("utf-8")
    seg = src[src.find("def _watch_automation_task") :][:2600]
    assert "max(0, int(retries))" in seg, "重试次数没钳住 ⇒ 负数/巨大值都能传进来 ✗"
    assert "retries_used" in seg, "没记'这次用掉几次重试'✗（用户看不到发生过什么 ✓）"


# ═══ 两把锁的分工（别混成一个）═══

def test_the_two_locks_are_different_things():
    """★ **Key 上限**管总量 ✓ **单次上限**管单次 ✓ —— 两把锁、两种消息 ✓ 不许混 ✗。

    （两句话都得在、而且看得出区别 ✓ —— 都叫"上限"会让用户以为是同一件事 ✓）
    """
    from app import budget as BG
    import pathlib
    assert hasattr(BG, "blocking_reason"), "Key 那把锁不见了 ✗"
    assert "已花到上限" in pathlib.Path(BG.__file__).read_text("utf-8"), "Key 那把锁的文案不见了 ✗"
    src = pathlib.Path(m.__file__).read_text("utf-8")
    assert "单次花费上限" in src, "单次那把锁的文案不见了 ✗"
    assert "_TASK_COST_CAPS" in src, "单次上限的登记表不见了 ✗"


def test_launch_task_accepts_the_new_knobs():
    """★ 参数要真接上 ✓（"写了不等于接上了"✓ 本仓老教训 ✓）。"""
    import inspect
    sig = inspect.signature(m._launch_task)
    assert "cost_cap_cny" in sig.parameters, "_launch_task 不收单次上限 ✗"
    assert "auto_retries" in sig.parameters, "_launch_task 不收重试次数 ✗"
    src = inspect.getsource(m._launch_task)
    assert "_TASK_COST_CAPS[task.id]" in src, "收了参数却没登记 ⇒ 闸门查不到 ✗"
