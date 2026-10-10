# -*- coding: utf-8 -*-
"""回归盲区补测（验证报告 24）：automations secret 打码 + 重启后 resume 拦截。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import time
import pytest
from fastapi.testclient import TestClient

from app.main import app, automations, tasks, _save_automations


@pytest.fixture()
def client():
    with TestClient(app, base_url="http://127.0.0.1:8642") as c:
        yield c


def test_automations_list_masks_secret(client):
    """盲区①（复审）：GET /automations 不得回传明文 secret（明文只在创建/轮换出现一次）。"""
    aid = f"auto_mask{int(time.time())}"
    automations[aid] = {
        "id": aid, "name": "mask", "kind": "hook", "task_input": "x",
        "enabled": True, "secret": "topsecret-value", "created_at": "2026-10-02T00:00:00Z",
        "last_run": None, "last_task_id": None,
    }
    _save_automations()
    try:
        r = client.get("/api/v1/automations")
        assert r.status_code == 200
        row = next(a for a in r.json() if a["id"] == aid)
        assert row["secret"] == "***", f"明文 secret 泄漏：{row['secret']}"
        assert "topsecret-value" not in r.text
    finally:
        automations.pop(aid, None)
        _save_automations()


def test_resume_rejected_when_disk_status_running(client, tmp_path):
    """盲区②（复审）：重启后 runs 内存为空、磁盘 status=running → resume 必须拒绝。"""
    tid = f"task_20261002_zomb{int(time.time()) % 1000:02d}"
    from app.schemas import TaskSummary
    t = TaskSummary(id=tid, title="僵尸", status="running",
                    created_at="2026-10-02T00:00:00Z", updated_at="2026-10-02T00:00:00Z")
    tasks[tid] = t
    from app.main import store as _store
    _store.save_index(list(tasks.values()))
    try:
        assert t.status == "running"
        r = client.post(f"/api/v1/tasks/{tid}/resume", json={"text": "继续"})
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is False, f"重启后 running 任务被 resume 放行：{body}"
        # 验证报告 24 第7条：钉住具体拒绝文案（grep '磁盘' 可命中），防止护栏被改坏
        assert "磁盘" in str(body.get("error", "")), f"拒绝原因不符：{body}"
    finally:
        tasks.pop(tid, None)
        _store.save_index(list(tasks.values()))
