# -*- coding: utf-8 -*-
"""审批孤儿（十三轮用户实测）：后端重启后磁盘里仍挂着 waiting_approval，
UI 显示审批卡片但 _pending 已随进程消失 → 点"允许一次"报 409 且永远点不通。

修复后的两个契约：
  ① 启动清扫：running/waiting_approval/created 的僵尸运行态 → failed（诚实降级）
  ② approve 端点：无 pending + 磁盘等待态 + 无活运行 → 任务置 failed 并 409
     （UI 卡片随之消失，报错文案讲明原因）
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import _now, _save_index, app, tasks
from app.schemas import TaskSummary


def _make_waiting_task(task_id: str) -> None:
    tasks[task_id] = TaskSummary(
        id=task_id, title="孤儿审批回归", status="waiting_approval",
        created_at=_now(), updated_at=_now(),
    )
    _save_index()


def test_startup_sweep_marks_orphan_waiters_failed():
    """①：僵尸等待态在启动清扫后必须变成 failed（不再显示永远批不了的卡片）。"""
    from app.main import _sweep_orphan_waiters
    _make_waiting_task("task_20261003_or01")
    _sweep_orphan_waiters()
    assert tasks["task_20261003_or01"].status == "failed"
    ev = json.loads(json.dumps([
        e.model_dump() for e in
        __import__("app.main", fromlist=["store"]).store.read_events("task_20261003_or01")
    ]))
    status_events = [e for e in ev if e["type"] == "status"]
    assert status_events, "清扫必须补发 status 事件（UI 同步）"
    assert status_events[-1]["payload"]["state"] == "failed"
    assert "重启" in status_events[-1]["payload"]["detail"]


def test_approve_on_orphan_waiting_degrades_honestly():
    """②：无 pending + 磁盘等待态 → 409 + 任务置 failed + 状态事件（不反复报错）。"""
    _make_waiting_task("task_20261003_or02")
    client = TestClient(app)
    r = client.post("/api/v1/tasks/task_20261003_or02/approve",
                    headers={"Host": "127.0.0.1"},  # 过 DNS-rebinding 护栏（同 test_api_guard 口径）
                    json={"call_id": "call_012", "decision": "once"})
    assert r.status_code == 409
    assert "失效" in r.json()["detail"], r.json()
    assert tasks["task_20261003_or02"].status == "failed", "必须诚实降级，不能留在等待态"
    ev = [e.model_dump() for e in
          __import__("app.main", fromlist=["store"]).store.read_events("task_20261003_or02")]
    last_status = [e for e in ev if e["type"] == "status"][-1]
    assert last_status["payload"]["state"] == "failed"
