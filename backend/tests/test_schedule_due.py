"""定时自动化的到期判定 —— P1-14（daily 时区）回归。

真实问题：用户填 `{"kind":"daily","time":"09:00"}`，代码用 UTC 解释，
于是东八区用户在**本地 17:00** 才触发（UTC 09:00）。
本文件锁死"daily 按本地时区解释、interval 按 UTC 算间隔"这条约定。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.main import _schedule_due

TZ8 = timezone(timedelta(hours=8))  # 东八区（用户所在时区）


def _daily(last_fire_date: str | None = None, time_str: str = "09:00") -> dict:
    return {
        "kind": "schedule",
        "schedule": {"kind": "daily", "time": time_str},
        "last_fire_date": last_fire_date,
    }


def _interval(last_run: str | None, minutes: int = 60) -> dict:
    return {
        "kind": "schedule",
        "schedule": {"kind": "interval", "minutes": minutes},
        "last_run": last_run,
    }


# ---------- daily：按本地时区 ----------


def test_daily_fires_at_local_time_not_utc_time():
    """★ 核心回归：本地 09:30（UTC 01:30）必须触发。

    旧实现拿 UTC 判断（now >= UTC 09:00），此刻是不触发的 —— 要等到本地 17:00。
    """
    now_utc = datetime(2026, 9, 30, 1, 30, tzinfo=timezone.utc)
    now_local = now_utc.astimezone(TZ8)  # 本地 09:30
    assert now_local.hour == 9 and now_local.minute == 30
    fire, _ = _schedule_due(_daily(), now_utc, now_local)
    assert fire is True, "本地 09:30 应当触发（旧实现会等到本地 17:00）"


def test_daily_does_not_fire_before_local_target():
    now_utc = datetime(2026, 9, 30, 0, 30, tzinfo=timezone.utc)
    now_local = now_utc.astimezone(TZ8)  # 本地 08:30
    fire, _ = _schedule_due(_daily(), now_utc, now_local)
    assert fire is False


def test_daily_fires_only_once_per_local_day():
    now_utc = datetime(2026, 9, 30, 2, 0, tzinfo=timezone.utc)
    now_local = now_utc.astimezone(TZ8)  # 本地 10:00
    assert _schedule_due(_daily(), now_utc, now_local)[0] is True
    assert _schedule_due(_daily(last_fire_date="2026-09-30"), now_utc, now_local)[0] is False


def test_daily_records_local_date():
    """记账日期必须是**本地**日期，否则跨零点会记错天。"""
    now_utc = datetime(2026, 9, 30, 17, 0, tzinfo=timezone.utc)  # UTC 9/30，本地 10/1 01:00
    now_local = now_utc.astimezone(TZ8)
    fire, date = _schedule_due(_daily(time_str="00:30"), now_utc, now_local)
    assert fire is True
    assert date == "2026-10-01", f"应按本地日期记账，实际 {date}"


def test_daily_bad_time_format_does_not_raise():
    now_utc = datetime(2026, 9, 30, 1, 0, tzinfo=timezone.utc)
    now_local = now_utc.astimezone(TZ8)
    assert _schedule_due(_daily(time_str="九点"), now_utc, now_local)[0] is False


# ---------- interval：按 UTC 算间隔 ----------


def test_interval_first_run_fires():
    now_utc = datetime(2026, 9, 30, 1, 0, tzinfo=timezone.utc)
    assert _schedule_due(_interval(None), now_utc, now_utc.astimezone(TZ8))[0] is True


def test_interval_respects_elapsed_time():
    now_utc = datetime(2026, 9, 30, 1, 0, tzinfo=timezone.utc)
    local = now_utc.astimezone(TZ8)
    assert _schedule_due(_interval((now_utc - timedelta(minutes=30)).isoformat()), now_utc, local)[0] is False
    assert _schedule_due(_interval((now_utc - timedelta(minutes=61)).isoformat()), now_utc, local)[0] is True


def test_interval_malformed_last_run_fires_instead_of_crashing():
    """坏数据不能让整个调度扫描静默中断（原实现会抛异常被外层吞掉）。"""
    now_utc = datetime(2026, 9, 30, 1, 0, tzinfo=timezone.utc)
    assert _schedule_due(_interval("not-a-timestamp"), now_utc, now_utc.astimezone(TZ8))[0] is True


def test_interval_naive_last_run_is_treated_as_utc():
    now_utc = datetime(2026, 9, 30, 1, 0, tzinfo=timezone.utc)
    naive = (now_utc - timedelta(minutes=90)).replace(tzinfo=None).isoformat()
    assert _schedule_due(_interval(naive), now_utc, now_utc.astimezone(TZ8))[0] is True


def test_unknown_schedule_kind_never_fires():
    now_utc = datetime(2026, 9, 30, 1, 0, tzinfo=timezone.utc)
    a = {"kind": "schedule", "schedule": {"kind": "weekly"}, "last_run": None}
    assert _schedule_due(a, now_utc, now_utc.astimezone(TZ8))[0] is False
