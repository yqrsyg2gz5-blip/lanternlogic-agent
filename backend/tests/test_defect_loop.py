"""★ 缺陷回环：下游发现**上游**产物有问题 ⇒ 打回给上游修，并让下游复测。

## 现场（2026-10-05 第六轮真群回归）

测试工程师真跑出「37 条用例、**36 通过 / 1 失败**」，把缺陷写进了**自己的交付报告** ✓ ——
而那 1 个失败是**程序员**产物的问题 ✗。系统只验收了"测试这一步交付的报告"（它确实交了报告 ✓）
⇒ 验收通过 ⇒ **整个批次标记完成** ⇒ **那个缺陷没人修** ✗。

也就是说：我们做到了"交付闭环"（谁交了什么、有没有交），但**没做到"质量闭环"**
（下游发现的问题要回到上游去修）。

## 语义

1. 验收人除了判当前这步，还可以把"**别人产物**的问题"结构化写进 `defects`
   （`[{"owner": "程序员", "what": "空列表时要输出表头"}]`）
2. `parse_defects()` 只认**分工单里真实存在**、且**不是当前这一步**的名字
   （防误伤：不能让测试自己背锅，也不能打回一个不存在的人）
3. `leader_reopen()` 把那个人的项**重新打开**（带缺陷清单），并让它**下游依赖它的项**回到待办
   ——下游的抬头写着"前置因缺陷重修过，请复核并复测"（级联只走一层，防雪崩）
4. **通过时也要看缺陷**（第六轮正是"测试通过了，但程序员的问题没人修"）
5. 轮次到上限就不再自动打回（避免无限循环），改为在群里如实说明并交给人
"""
from __future__ import annotations

import asyncio

import pytest

from app import main as m
from app.team import TeamStore

PLAN = [
    {"name": "架构师", "task": "定契约", "output": "docs/api.md", "depends_on": []},
    {"name": "程序员", "task": "按契约实现", "output": "todo.py", "depends_on": ["架构师"]},
    {"name": "测试工程师", "task": "写自检并真跑", "output": "test_todo.py", "depends_on": ["程序员"]},
]


@pytest.fixture()
def room(tmp_path, monkeypatch):
    st = TeamStore(tmp_path)
    ids = {}
    for nm, role in (("架构师", "架构师"), ("程序员", "工程师"), ("测试工程师", "测试工程师")):
        ids[nm] = st.add_employee({"name": nm, "dept": "技术部", "role": role,
                                   "persona": "干活", "mode": "expert"})["id"]
    g = st.create_group("开发群", list(ids.values()), mode="leader")
    monkeypatch.setattr(m, "_team_store", st)
    return {"store": st, "gid": g["id"]}


def test_defects_are_extracted_and_merged(room):
    v = {"defects": [{"owner": "程序员", "what": "空列表要输出表头"},
                     {"owner": "@程序员", "what": "退出码 1 用错"},
                     {"owner": "测试工程师", "what": "自己不算"},          # 当前这步 ⇒ 过滤
                     {"owner": "查无此人", "what": "不存在 ⇒ 过滤"},
                     {"owner": "架构师", "what": "契约没写 rm 行为"}]}
    got = TeamStore.parse_defects(v, ["架构师", "程序员", "测试工程师"], exclude="测试工程师")
    assert [d["owner"] for d in got] == ["程序员", "架构师"], got
    assert got[0]["what"] == "空列表要输出表头；退出码 1 用错", got      # 同一个人的多条合成一条


@pytest.mark.parametrize("raw", [None, "字符串", 42, [1, 2], [{"owner": ""}], {"owner": "程序员"}])
def test_defect_parsing_never_raises(room, raw):
    """垃圾输入只能得到空/合理结果，绝不抛异常（跟其它解析器一个口径）。"""
    got = TeamStore.parse_defects({"defects": raw}, ["程序员"], exclude="测试工程师")
    assert isinstance(got, list)


def test_reopen_reopens_owner_and_dependents(room):
    """★ 打回给上游 + **下游回到待办复测**（下游的抬头写着"前置因缺陷重修过"）。"""
    st, gid = room["store"], room["gid"]
    st.leader_begin(gid, "做待办 CLI", PLAN)
    st.leader_finish_item(gid, "架构师", "done", "契约", ["docs/api.md"])
    st.leader_finish_item(gid, "程序员", "done", "实现", ["todo.py"])
    st.leader_finish_item(gid, "测试工程师", "done", "37 条用例，36 通过 1 失败", ["test_todo.py"])
    out = st.leader_reopen(gid, "程序员", "空列表时该输出表头")
    assert out.get("reopened") == ["程序员", "测试工程师"], out
    g = st.get_group(gid)
    prog = next(i for i in g["leader_plan"] if i["name"] == "程序员")
    qa = next(i for i in g["leader_plan"] if i["name"] == "测试工程师")
    assert prog["status"] in ("pending", "running") and "空列表" in prog["reroll_advice"]
    assert qa["status"] in ("pending", "running") and "复测" in qa["reroll_advice"], qa
    # 重修的工作单里必须带上缺陷清单
    assert "空列表时该输出表头" in st.leader_handoff_text(g, prog)


def test_failed_step_only_records_the_defect_and_does_not_reopen_upstream(room, monkeypatch):
    """★★ 2026-10-06 真群实测（本功能自己捅的娄子）：**当前这步自己没过时，绝不能把上游也打回重修** ✗

    现场：程序员的交付被打回 ⇒ 验收人顺手指出"契约没写自测方式"⇒ 缺陷回环**又把架构师打回** ⇒
    架构师撞上"打回 3 次"上限被判失败 ⇒ **整条链崩掉**（实测：完成 0、失败 1、被前置挡住 2）。
    修法：这种情形**只记录、不回环**（等这一步自己过了再说）。
    """
    st, gid = room["store"], room["gid"]
    monkeypatch.setattr(m, "_team_store", st)
    st.leader_begin(gid, "做待办 CLI", PLAN)
    st.leader_finish_item(gid, "架构师", "done", "契约", ["docs/api.md"])
    st.leader_attach_task(gid, "程序员", "task_p")
    verdict = {"pass": False, "high": "实现和契约对不上",
               "defects": [{"owner": "架构师", "what": "契约没写自测方式"}]}
    out = m._apply_defects(gid, "程序员", verdict, st.get_group(gid), reopen=False)
    assert out is None, out                                  # 不产生回环批次
    arch = next(i for i in st.get_group(gid)["leader_plan"] if i["name"] == "架构师")
    assert arch["status"] == "done", arch                    # 上游**没被动**
    assert int(arch.get("rounds") or 0) == 0, arch
    texts = [x["text"] for x in st.feed(gid)]
    assert any("先记下" in t and "@架构师" in t for t in texts), texts
    assert not any("缺陷回环：" in t for t in texts), texts


def test_passing_step_still_reopens_upstream(room, monkeypatch):
    """对照组：**当前这步过了** ⇒ 缺陷回环照旧生效（第六轮那种情形，别把功能改没了）。"""
    st, gid = room["store"], room["gid"]
    monkeypatch.setattr(m, "_team_store", st)
    st.leader_begin(gid, "做待办 CLI", PLAN)
    st.leader_finish_item(gid, "架构师", "done", "契约", ["docs/api.md"])
    st.leader_finish_item(gid, "程序员", "done", "实现", ["todo.py"])
    st.leader_finish_item(gid, "测试工程师", "done", "36/1", ["test_todo.py"])
    verdict = {"pass": True, "high": "测试交付合格",
               "defects": [{"owner": "程序员", "what": "空列表时该输出表头"}]}
    out = m._apply_defects(gid, "测试工程师", verdict, st.get_group(gid), reopen=True)
    assert out is not None and "程序员" in (out.get("reopened") or []), out
    texts = [x["text"] for x in st.feed(gid)]
    assert any("缺陷回环：" in t for t in texts), texts


def test_apply_defects_posts_the_loop_and_returns_a_batch(room, monkeypatch):
    """端到端一小步：验收通过但指出别人的缺陷 ⇒ 群里出"缺陷回环"并给出新的可派发批次。"""
    st, gid = room["store"], room["gid"]
    monkeypatch.setattr(m, "_team_store", st)
    st.leader_begin(gid, "做待办 CLI", PLAN)
    st.leader_finish_item(gid, "架构师", "done", "契约", ["docs/api.md"])
    st.leader_finish_item(gid, "程序员", "done", "实现", ["todo.py"])
    st.leader_finish_item(gid, "测试工程师", "done", "36/1", ["test_todo.py"])
    verdict = {"pass": True, "high": "测试这一步交付合格",
               "defects": [{"owner": "程序员", "what": "空列表时该输出表头"}]}
    batch = m._apply_defects(gid, "测试工程师", verdict, st.get_group(gid))
    assert batch is not None and "程序员" in (batch.get("reopened") or []), batch
    texts = [x["text"] for x in st.feed(gid)]
    assert any("缺陷回环" in t and "@程序员" in t and "空列表时该输出表头" in t for t in texts), texts


def test_no_defects_means_no_loop(room):
    st, gid = room["store"], room["gid"]
    st.leader_begin(gid, "做待办 CLI", PLAN)
    assert m._apply_defects(gid, "架构师", {"pass": True, "high": "ok"}, st.get_group(gid)) is None
    assert not [t for t in (x["text"] for x in st.feed(gid)) if "缺陷回环" in t]


def test_loop_stops_at_the_round_cap(room, monkeypatch):
    """★ 不许无限来回：轮次到上限就不再自动打回，改为如实说明交给人。"""
    st, gid = room["store"], room["gid"]
    monkeypatch.setattr(m, "_team_store", st)
    st.leader_begin(gid, "做待办 CLI", PLAN)
    # 把程序员的轮次顶到上限
    gs = st.groups()
    for i, it in enumerate(gs[0]["leader_plan"]):
        if it["name"] == "程序员":
            it["rounds"] = TeamStore.MAX_VERIFY_ROUNDS + 1
    st._write(st.root / "groups.json", gs)
    verdict = {"pass": True, "defects": [{"owner": "程序员", "what": "还是不对"}]}
    batch = m._apply_defects(gid, "测试工程师", verdict, st.get_group(gid))
    assert batch is None, batch
    texts = [x["text"] for x in st.feed(gid)]
    assert any("缺陷回环到上限" in t for t in texts), texts


def test_verify_delivery_runs_the_loop_even_on_pass(room, monkeypatch):
    """★ 第六轮真实现场：**验收通过**但指出别人缺陷 ⇒ 也要回环（否则那个缺陷永远没人修）。"""
    st, gid = room["store"], room["gid"]
    monkeypatch.setattr(m, "_team_store", st)
    st.leader_begin(gid, "做待办 CLI", PLAN)
    st.leader_finish_item(gid, "架构师", "done", "契约", ["docs/api.md"])
    st.leader_finish_item(gid, "程序员", "done", "实现", ["todo.py"])
    st.leader_attach_task(gid, "测试工程师", "task_qa")
    ws = st.root / "ws"
    (ws / "test_todo.py").parent.mkdir(parents=True, exist_ok=True)
    (ws / "test_todo.py").write_text("assert True", encoding="utf-8")
    monkeypatch.setattr(m.store, "workspace_dir", lambda tid: ws)

    async def _fake_say(emp, prompt):
        return ('{"pass": true, "low": "在：test_todo.py", "high": "测试交付合格",'
                ' "evidence": "36 通过 / 1 失败",'
                ' "defects": [{"owner": "程序员", "what": "空列表时该输出表头"}]}')

    monkeypatch.setattr(m, "_employee_say", _fake_say)
    monkeypatch.setattr(m, "_dispatch_leader_batch", lambda gid2, batch, ids: [])
    asyncio.run(m._verify_delivery(gid, "测试工程师", True,
                                   "用例清单：N01…N37\n运行结果：36 通过 / 1 失败",
                                   ["test_todo.py"]))
    texts = [x["text"] for x in st.feed(gid)]
    assert any("缺陷回环" in t for t in texts), texts
    g = st.get_group(gid)
    prog = next(i for i in g["leader_plan"] if i["name"] == "程序员")
    assert prog["status"] in ("pending", "running"), prog
