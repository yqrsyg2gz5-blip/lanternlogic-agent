"""★ P0-4 失败可续跑：从出错处继续，不从头烧钱。

## 依据

· **Anthropic 多智能体复盘**原话："错误会累积……**要从出错处续跑，不能从头重来**：
  重跑既贵又让用户难受"；并且"把'工具在失败'告诉模型，它往往能自己绕过去"。
· 我们自己的现场：三个群任务烧完 25 步失败（或被我重启打断）后，
  用户唯一能做的是**重新发一次目标** —— 等于把前面烧掉的钱再烧一遍，
  而工作区里已有的产物**明明还在**。

## 语义

1. **同一个任务**（因此同一个工作区、同一份历史）接着跑 —— 不是新建任务
2. 工作单必须写清三件：**为什么停下** / **已经有什么产物（逐个列出）** / **别从头再来**
3. 群里**就地能续**：交付卡片上有「接着跑」，或者直接在群里说「接着跑」
4. 续跑后**看门重新挂上**（波次/接力的推进照旧认领得回来）
5. 群里没有可续的活时，**如实说没有**，而不是假装启动了一个
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main as m
from app.team import TeamStore


@pytest.fixture()
def shop(tmp_path, monkeypatch):
    st = TeamStore(tmp_path)
    emp = st.add_employee({"name": "程序员", "dept": "研发部", "role": "工程师",
                           "persona": "写代码", "mode": "expert"})
    g = st.create_group("开发群", [emp["id"]], mode="manual")
    monkeypatch.setattr(m, "_team_store", st)
    return {"store": st, "gid": g["id"], "emp": emp}


def _fake_task(task_id="task_old01", title="【群任务 @程序员】做登录接口"):
    return SimpleNamespace(id=task_id, title=title, status="failed")


def test_resume_prompt_says_why_and_what_exists(shop, tmp_path, monkeypatch):
    """★ 三件必须齐全：为什么停下 / 已经有什么 / 别从头再来。"""
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "login.py").write_text("def login(): ...", encoding="utf-8")
    (ws / "README.md").write_text("说明", encoding="utf-8")
    monkeypatch.setattr(m.store, "workspace_dir", lambda tid: ws)
    monkeypatch.setattr(m.store, "read_events", lambda tid: [
        SimpleNamespace(type="error", payload={"code": "max_iterations", "message": "达到最大迭代次数（25）"}),
        SimpleNamespace(type="message", payload={"role": "assistant", "text": "登录接口写了一半，还没写测试"}),
    ])
    p = m._resume_prompt("task_old01")
    assert "接着上次的进度继续" in p
    assert "步数用完" in p, p                      # 为什么停下（复用失败原因映射）
    assert "login.py" in p and "README.md" in p, p   # 已经有什么产物（路径分隔符跨平台，别写死 /）
    assert "登录接口写了一半" in p, p                # 上次说到哪了
    assert "不要从头再来" in p and "已有的产物直接用" in p, p


def test_resume_prompt_is_honest_when_workspace_is_empty(shop, tmp_path, monkeypatch):
    ws = tmp_path / "empty"
    ws.mkdir()
    monkeypatch.setattr(m.store, "workspace_dir", lambda tid: ws)
    monkeypatch.setattr(m.store, "read_events", lambda tid: [])
    p = m._resume_prompt("t1")
    assert "还没有产物" in p, p


def test_do_resume_reuses_the_same_task_and_rearms_the_watcher(shop, monkeypatch):
    """★ 续跑 = **同一个任务 id**（同一个工作区）+ 重挂看门，不是新建任务。"""
    started: list[tuple] = []
    watched: list[tuple] = []
    monkeypatch.setattr(m, "_get_task", lambda tid: _fake_task(tid))
    monkeypatch.setattr(m, "_start_run", lambda task, text, **kw: started.append((task.id, text, kw)))
    monkeypatch.setattr(m, "_spawn_bg", lambda coro: (watched.append(1), coro.close()))
    monkeypatch.setattr(m.store, "workspace_dir", lambda tid: shop["store"].root)
    monkeypatch.setattr(m.store, "read_events", lambda tid: [])
    out = m._do_resume(shop["gid"], "task_old01", "程序员")
    assert out["task_id"] == "task_old01" and out["name"] == "程序员", out
    assert started and started[0][0] == "task_old01", started       # 同一个任务
    assert "接着上次的进度继续" in started[0][1], started
    assert watched, "看门没重挂（续跑完就没人认领交付了）"
    texts = [x["text"] for x in shop["store"].feed(shop["gid"])]
    assert any("接着跑" in t and "不从头再来" in t for t in texts), texts


def test_group_command_resumes_the_latest_unfinished(shop, monkeypatch):
    """★ 群里直接说「接着跑」就能续（用户不需要去任务页）。"""
    shop["store"].append(shop["gid"], **{"from": "emp:程序员", "text": "（任务结束）",
                                         "task_id": "task_old01", "status": "failed"})
    called: list[tuple] = []
    monkeypatch.setattr(m, "_do_resume", lambda gid, tid, nm: (called.append((gid, tid, nm)),
                                                               {"task_id": tid, "name": nm})[1])
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.post(f"/api/v1/team/groups/{shop['gid']}/say", json={"text": "接着跑"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("mode") == "resume" and body.get("task_id") == "task_old01", body
    assert called and called[0][1] == "task_old01" and called[0][2] == "程序员", called


def test_group_command_says_so_when_nothing_to_resume(shop, monkeypatch):
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.post(f"/api/v1/team/groups/{shop['gid']}/say", json={"text": "接着跑"})
    body = r.json()
    assert body.get("task_id") is None, body
    texts = [x["text"] for x in shop["store"].feed(shop["gid"])]
    assert any("没有需要接着跑的活" in t for t in texts), texts


def test_resume_endpoint_requires_a_known_group(shop, monkeypatch):
    monkeypatch.setattr(m, "_do_resume", lambda gid, tid, nm: {"ok": True})
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.post("/api/v1/team/groups/grp_不存在/resume", json={"task_id": "t1"})
    assert r.status_code == 404, r.text


def test_failure_line_tells_the_user_they_can_resume(shop):
    """失败时群里要告诉用户"可以接着跑"（否则他只会重发一遍目标 = 从头烧钱）。"""
    src = (__import__("pathlib").Path(__file__).resolve().parents[1] / "app" / "main.py").read_text("utf-8")
    assert "接着跑" in src and "_resume_prompt(" in src
    assert "🔁 接着跑" in src


def test_resume_marks_the_item_running_again(shop, monkeypatch):
    """★ 2026-10-05 全量试跑抓到的真 bug：续跑了，但分工单里那一项还是 failed
    ⇒ 看门/对账立刻判定"活已结束"并把**整批收工**（用户看到"收工了"，实际它还在干 ✗）。"""
    from app.team import TeamStore

    st = TeamStore(shop["store"].root)
    monkeypatch.setattr(m, "_team_store", st)
    emp_id = shop["emp"]["id"]
    nm = shop["emp"]["name"]
    g = st.create_group("开发群2", [emp_id], mode="leader", leader=emp_id)
    st.leader_begin(g["id"], "做待办 CLI", [{"name": nm, "task": "写测试",
                                            "output": "test.py", "depends_on": []}])
    st.leader_attach_task(g["id"], nm, "task_old01")
    st.leader_finish_item(g["id"], nm, "failed", "", ["test.py"])
    assert st.leader_state(g["id"])["failed"] == 1

    monkeypatch.setattr(m, "_get_task", lambda tid: _fake_task(tid))
    monkeypatch.setattr(m, "_start_run", lambda task, text, **kw: None)
    monkeypatch.setattr(m, "_spawn_bg", lambda coro: coro.close())
    monkeypatch.setattr(m.store, "workspace_dir", lambda tid: shop["store"].root)
    monkeypatch.setattr(m.store, "read_events", lambda tid: [])
    m._RESUME_LAST.pop("task_old01", None)
    out = m._do_resume(g["id"], "task_old01", nm)
    assert out.get("skipped") is None, out
    st2 = st.leader_state(g["id"])
    assert st2["failed"] == 0, st2                       # 不再算"失败"
    item = next(i for i in (st.get_group(g["id"])["leader_plan"]) if i["name"] == nm)
    assert item["status"] == "running", item             # 改回"进行中"，别让群里提前收工


def test_ui_offers_resume_on_failed_deliveries():
    """界面锚点：失败/部分完成的交付旁边有「接着跑」，且打的是群内接口。"""
    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    ui = (root / "frontend" / "src" / "components" / "TeamView.tsx").read_text("utf-8")
    assert "resume-row" in ui and "接着跑" in ui, "群里没有续跑入口"
    assert "api.teamResume(" in ui, "没接上群内续跑接口"
    assert "m.status === 'failed'" in ui and "m.status === 'partial'" in ui, "只有失败/部分完成才该显示"
    api = (root / "frontend" / "src" / "api.ts").read_text("utf-8")
    assert "/team/groups/${gid}/resume" in api, "api.ts 里没有续跑接口"


# ═══ Jev 判定点名的边界：并发续跑 ═══

def test_resume_is_refused_while_the_task_is_running(shop, monkeypatch):
    """★ 同一任务正在跑时再点"接着跑" ⇒ 不再排一轮（两轮抢同一个工作区只会越弄越乱）。"""
    monkeypatch.setattr(m, "_get_task", lambda tid: _fake_task(tid))
    monkeypatch.setattr(m, "_start_run", lambda *a, **kw: pytest.fail("正在跑就不该再排一轮"))
    monkeypatch.setattr(m, "_spawn_bg", lambda coro: coro.close())
    m.runs["task_run01"] = SimpleNamespace(aio_task=SimpleNamespace(done=lambda: False))
    try:
        out = m._do_resume(shop["gid"], "task_run01", "程序员")
    finally:
        m.runs.pop("task_run01", None)
    assert out.get("skipped") == "running", out
    texts = [x["text"] for x in shop["store"].feed(shop["gid"])]
    assert any("现在正在跑" in t for t in texts), texts


def test_repeated_resume_clicks_are_ignored(shop, monkeypatch):
    """★ 20 秒内重复点（手抖/连点）⇒ 忽略，并如实说明。"""
    started: list[str] = []
    monkeypatch.setattr(m, "_get_task", lambda tid: _fake_task(tid))
    monkeypatch.setattr(m, "_start_run", lambda task, text, **kw: started.append(task.id))
    monkeypatch.setattr(m, "_spawn_bg", lambda coro: coro.close())
    monkeypatch.setattr(m.store, "workspace_dir", lambda tid: shop["store"].root)
    monkeypatch.setattr(m.store, "read_events", lambda tid: [])
    m._RESUME_LAST.pop("task_once01", None)
    first = m._do_resume(shop["gid"], "task_once01", "程序员")
    second = m._do_resume(shop["gid"], "task_once01", "程序员")
    assert first.get("skipped") is None and second.get("skipped") == "recent", (first, second)
    assert started == ["task_once01"], started
    texts = [x["text"] for x in shop["store"].feed(shop["gid"])]
    assert any("20 秒内重复点会被忽略" in t for t in texts), texts
