"""团队功能修复回归 —— 审计 §6.3 / §6.4 / §6.5。

§6.3：群任务完成监视器是进程内 asyncio，后端重启即失联，交付永不回流
  （实证：02:39 派出的 3 个任务跑了 11/18/17 条事件，群里只有"收到，开始执行"）。
§6.4：广播署名显示裸 id（emp_xxxx）。
§6.5：删员工时改完群 members 却把结果写进 feed 文件——groups.json 没落盘，
  鬼影成员永存。
"""
from __future__ import annotations

from types import SimpleNamespace

from app import main as m
from app.schemas import TaskSummary
from app.team import TeamStore


# ---------------- §6.5 删员工落盘 ----------------


def test_delete_employee_updates_groups_json(tmp_path):
    ts = TeamStore(tmp_path / "team")
    e1 = ts.add_employee({"name": "甲"})
    e2 = ts.add_employee({"name": "乙"})
    g = ts.create_group("测试群", [e1["id"], e2["id"]])

    gid = g["id"] if isinstance(g, dict) else g["id"]
    ts.delete_employee(e2["id"])

    # 直接重读 groups.json —— 不允许鬼影成员
    members = next(x for x in ts.groups() if x["id"] == gid)["members"]
    assert e2["id"] not in members, "groups.json 里还留着已删员工（§6.5 复发）"
    assert e1["id"] in members


# ---------------- §6.3 重启后群任务回流 ----------------


class _FakeTeamStore:
    """只实现 recovery 用到的三个方法；append 同步进 feed 以验证幂等。"""

    def __init__(self, feed: list[dict]) -> None:
        self._feed = feed
        self.appended: list[tuple[str, dict]] = []
        self._groups = [{"id": "grp_test"}]

    def groups(self):
        return self._groups

    def feed(self, gid: str):
        return self._feed

    def append(self, gid: str, **kw):
        self.appended.append((gid, kw))
        self._feed.append({"seq": len(self._feed) + 1, **kw})


def test_recover_group_deliveries_appends_terminal_status(tmp_path, monkeypatch):
    fake = _FakeTeamStore([
        {"seq": 1, "from": "emp:小李", "text": "收到，开始执行",
         "task_id": "task_20261002_rec1", "status": "running"},
    ])
    monkeypatch.setattr(m, "_team_store", fake)
    monkeypatch.setattr(m, "store", SimpleNamespace(read_events=lambda tid: [
        SimpleNamespace(type="message", payload={"role": "assistant", "text": "交付：分析报告完成"}),
    ]))
    monkeypatch.setitem(m.tasks, "task_20261002_rec1", TaskSummary(
        id="task_20261002_rec1", title="群任务", status="done",
        created_at="2026-10-02T00:00:00Z", updated_at="2026-10-02T00:00:00Z",
    ))

    m._recover_group_deliveries()

    assert len(fake.appended) == 1, f"应有 1 条补投，实际 {fake.appended}"
    gid, kw = fake.appended[0]
    assert gid == "grp_test"
    assert kw["from"] == "emp:小李", "补投应沿用原派出者署名"
    assert kw["status"] == "done"
    assert "分析报告" in kw["text"]


def test_recover_group_deliveries_is_idempotent(tmp_path, monkeypatch):
    """重启两次不重复补投：第一次补投后 feed 里已有终态消息 → 第二次跳过。"""
    fake = _FakeTeamStore([
        {"seq": 1, "from": "emp:小李", "text": "收到，开始执行",
         "task_id": "task_20261002_rec2", "status": "running"},
    ])
    monkeypatch.setattr(m, "_team_store", fake)
    monkeypatch.setattr(m, "store", SimpleNamespace(read_events=lambda tid: [
        SimpleNamespace(type="message", payload={"role": "assistant", "text": "交付内容"}),
    ]))
    monkeypatch.setitem(m.tasks, "task_20261002_rec2", TaskSummary(
        id="task_20261002_rec2", title="群任务", status="partial",
        created_at="2026-10-02T00:00:00Z", updated_at="2026-10-02T00:00:00Z",
    ))

    m._recover_group_deliveries()
    m._recover_group_deliveries()  # 模拟再次重启

    assert len(fake.appended) == 1, f"补投必须幂等，实际 {len(fake.appended)} 条"


def test_recover_skips_still_running_tasks(tmp_path, monkeypatch):
    """**真还在跑**的任务不误发（进程里确实有活的 run）。"""
    fake = _FakeTeamStore([
        {"seq": 1, "from": "emp:小李", "text": "收到，开始执行",
         "task_id": "task_20261002_rec3", "status": "running"},
    ])
    monkeypatch.setattr(m, "_team_store", fake)
    monkeypatch.setattr(m, "store", SimpleNamespace(read_events=lambda tid: []))
    monkeypatch.setitem(m.tasks, "task_20261002_rec3", TaskSummary(
        id="task_20261002_rec3", title="群任务", status="running",
        created_at="2026-10-02T00:00:00Z", updated_at="2026-10-02T00:00:00Z",
    ))

    class _Alive:
        class aio_task:                     # 活的 run：句柄存在且没结束
            @staticmethod
            def done() -> bool:
                return False
    monkeypatch.setitem(m.runs, "task_20261002_rec3", _Alive())

    m._recover_group_deliveries()
    assert fake.appended == [], "真还在跑的任务不该被补投、也不该被续跑"


def test_recover_auto_resumes_what_a_restart_killed(tmp_path, monkeypatch):
    """★ 2026-10-05（今晚为装修复重启 3 次，打断了 3 次任务）：

    落盘状态说"在跑"，但**进程里没有活的 run** ⇒ 那是被重启打断的 ⇒ **自动接着跑**；
    以前这里判为"真还在跑"就此卡死 ✗（用户看到的就是"它停了"）。"""
    fake = _FakeTeamStore([
        {"seq": 1, "from": "emp:小李", "text": "收到，开始执行",
         "task_id": "task_20261002_recz", "status": "running"},
    ])
    monkeypatch.setattr(m, "_team_store", fake)
    monkeypatch.setattr(m, "store", SimpleNamespace(read_events=lambda tid: []))
    monkeypatch.setitem(m.tasks, "task_20261002_recz", TaskSummary(
        id="task_20261002_recz", title="【群任务 @小李】做待办", status="running",
        created_at="2026-10-02T00:00:00Z", updated_at="2026-10-02T00:00:00Z",
    ))
    m.runs.pop("task_20261002_recz", None)          # 没有活的 run = 被重启打断
    monkeypatch.setattr(m._team_store, "leader_item_by_task",
                        lambda gid, tid: {"name": "小李"}, raising=False)
    called: list[tuple] = []
    monkeypatch.setattr(m, "_do_resume", lambda gid, tid, nm="": called.append((gid, tid, nm)))
    m._AUTO_RESUME.pop("task_20261002_recz", None)

    m._recover_group_deliveries()
    assert called and called[0][1] == "task_20261002_recz", called
    texts = [str((x[1] if isinstance(x, tuple) else x).get("text") or "") for x in fake.appended]
    assert any("后端重启打断了" in t and "自动接着跑" in t for t in texts), texts
    assert m._AUTO_RESUME.get("task_20261002_recz") == 1
