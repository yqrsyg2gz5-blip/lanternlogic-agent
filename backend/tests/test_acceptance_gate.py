"""★ 项目级完成判据（2026-10-05 诊断第 3 条）：收口前必须有"项目验收"这道门。

## 现场

第七轮真群回归：分工单三项全 `done`、账单 **51 万 tok**，群里写「✅ 全部完成」——
而这个项目**到底能不能跑，没有任何人验过** ✗。
（第八轮更明显：测试真跑出「36 通过 / 1 失败」，系统照样把批次标记完成。）

也就是说：我们算的是"**每项都交了东西**"，不是"**这东西能用**"。

## 做法（不新造机器，复用现成的波次 + 两级验收）

在收口那一刻插一道门：前面所有项都到终态、且还没有"项目验收"这一项时 ⇒
**往分工单追加一项「项目验收」**，交给**测试类成员**（没有就组长），它的任务书要求：

1. 写一个**验收脚本**（真的跑主流程，不是看文件在不在）
2. **真执行**它
3. 把**实际命令与真实输出**贴进交付（照抄，不转述）
4. 通过写「✅ 项目验收通过」；不通过写「❌ 项目验收不通过」**并指出是谁的产物的问题**
   —— 这样**缺陷回环**能接手（打回给那个负责人重修）

**加进去之后立刻派出去**，绝不在验收之前喊"全部完成"。
群里没有测试类成员也没组长时：**如实说明跳过**，并写明「✅ 全部完成」只代表每项都交了东西、
**不代表项目能跑**（不许含糊过去）。
"""
from __future__ import annotations

from app import main as m
from app.team import TeamStore

PLAN = [
    {"name": "架构师", "task": "定契约", "output": "docs/api.md", "depends_on": []},
    {"name": "程序员", "task": "按契约实现", "output": "todo.py", "depends_on": ["架构师"]},
    {"name": "测试工程师", "task": "写自检", "output": "test_todo.py", "depends_on": ["程序员"]},
]


def _room(tmp_path, monkeypatch, with_qa: bool = True):
    st = TeamStore(tmp_path)
    ids = []
    for nm, role in (("架构师", "架构师"), ("程序员", "工程师"),
                     ("测试工程师", "测试工程师")):
        if nm == "测试工程师" and not with_qa:
            continue
        ids.append(st.add_employee({"name": nm, "dept": "技术部", "role": role,
                                    "persona": "干活", "mode": "expert"})["id"])
    g = st.create_group("开发群", ids, mode="leader")
    monkeypatch.setattr(m, "_team_store", st)
    return st, g["id"]


def test_acceptance_item_is_appended_once_when_everything_settled(tmp_path, monkeypatch):
    st, gid = _room(tmp_path, monkeypatch)
    st.leader_begin(gid, "做待办 CLI", PLAN)
    for nm in ("架构师", "程序员", "测试工程师"):
        st.leader_finish_item(gid, nm, "done", f"{nm} 交了", [f"{nm}.txt"])
    st0 = st.leader_state(gid)
    assert m._all_settled(st0)
    item = m._ensure_acceptance(gid, st0)
    assert item is not None and item["kind"] == "acceptance", item
    assert item["name"] == "测试工程师·验收", item    # 优先交给测试类成员；名字带"·验收"避免与它原有的项同名
    # 验收要等**所有**前面项交付（包括那个人自己那一项）；它自己的名字带后缀，所以不被自己挡
    assert set(item["depends_on"]) == {"架构师", "程序员", "测试工程师"}, item
    assert "真跑" in item["task"] and "实际命令" in item["task"], item["task"]
    assert "是谁的产物的问题" in item["task"], item["task"]
    # 幂等：再加一次不会重复
    assert m._ensure_acceptance(gid, st.leader_state(gid)) is None
    texts = [x["text"] for x in st.feed(gid)]
    assert any("最后一道门" in t and "项目验收" in t for t in texts), texts


def test_acceptance_does_not_block_when_nothing_was_delivered(tmp_path, monkeypatch):
    """一项都没做成 ⇒ 没什么可验收的（别假装有门）。"""
    st, gid = _room(tmp_path, monkeypatch)
    st.leader_begin(gid, "做待办 CLI", PLAN)
    st.leader_finish_item(gid, "架构师", "failed", "", [])
    assert m._ensure_acceptance(gid, st.leader_state(gid)) is None


def test_acceptance_falls_back_to_the_leader(tmp_path, monkeypatch):
    """没有测试类成员 ⇒ 交给组长（而不是偷偷跳过）。"""
    st, gid = _room(tmp_path, monkeypatch, with_qa=False)
    st.leader_begin(gid, "做待办 CLI", [
        {"name": "架构师", "task": "定契约", "output": "docs/api.md", "depends_on": []},
        {"name": "程序员", "task": "按契约实现", "output": "todo.py", "depends_on": ["架构师"]},
    ])
    st.leader_finish_item(gid, "架构师", "done", "契约", ["docs/api.md"])
    st.leader_finish_item(gid, "程序员", "done", "实现", ["todo.py"])
    item = m._ensure_acceptance(gid, st.leader_state(gid))
    assert item is not None and item["name"] == "架构师·验收", item   # 组长顶上来（同样带后缀去重）


def test_acceptance_is_honest_when_nobody_can_do_it(tmp_path, monkeypatch):
    """群里没人可派 ⇒ 如实说明"跳过"，并写明「全部完成」不代表项目能跑。"""
    st = TeamStore(tmp_path)
    e = st.add_employee({"name": "程序员", "dept": "研发部", "role": "工程师",
                         "persona": "干活", "mode": "expert"})["id"]
    g = st.create_group("开发群", [e], mode="leader", leader=e)
    monkeypatch.setattr(m, "_team_store", st)
    st.leader_begin(g["id"], "做待办 CLI", [
        {"name": "程序员", "task": "实现", "output": "todo.py", "depends_on": []}])
    st.leader_finish_item(g["id"], "程序员", "done", "实现", ["todo.py"])
    # 组长就是程序员自己 ⇒ 会交给它（有 leader 兜底）；这里构造"连组长都没有"的情形
    gs = st.groups()
    gs[0]["leader"] = None
    gs[0]["members"] = []
    st._write(st.root / "groups.json", gs)
    assert m._ensure_acceptance(g["id"], st.leader_state(g["id"])) is None
    texts = [x["text"] for x in st.feed(g["id"])]
    assert any("只能跳过" in t and "不代表项目能跑" in t for t in texts), texts


def test_both_close_paths_go_through_the_gate(tmp_path, monkeypatch):
    """★ 2026-10-05 验收跑实测的坑：收口有**两个**分支（`_verify_delivery` 一处、
    `_advance_leader` 一处），我一开始只给一处加了门 ⇒ 那一轮三项全 done、93 万 tok，
    却**一道验收门都没过** ✗。现在两处都走 `_close_batch_or_gate` —— 用源码位置钉死。"""
    src = (__import__("pathlib").Path(__file__).resolve().parents[1] / "app" / "main.py").read_text("utf-8")
    assert src.count("_close_batch_or_gate(gid, st)") == 2, (
        f"两个收口分支都必须走统一入口，实际 {src.count('_close_batch_or_gate(gid, st)')} 处")
    # 而且不能再有"绕开入口直接喊全部完成"的地方
    assert "if st[\"total\"] and st[\"done\"] + st[\"failed\"] + st[\"blocked\"] >= st[\"total\"]:" not in src, \
        "还有一处收口没走统一入口"


def test_close_gate_returns_true_when_it_adds_acceptance(tmp_path, monkeypatch):
    """门加上并派出去 ⇒ 返回 True（这一轮不算收口）；没有可验收的东西 ⇒ 返回 False（可以收口）。"""
    st, gid = _room(tmp_path, monkeypatch)
    st.leader_begin(gid, "做待办 CLI", PLAN)
    for nm in ("架构师", "程序员", "测试工程师"):
        st.leader_finish_item(gid, nm, "done", f"{nm} 交了", [f"{nm}.txt"])
    sent: list[dict] = []
    monkeypatch.setattr(m, "_dispatch_leader_batch",
                        lambda gid2, batch, ids: sent.append(batch) or [])
    assert m._close_batch_or_gate(gid, st.leader_state(gid)) is True
    assert sent and any(i.get("kind") == "acceptance" for i in sent[0]["ready"]), sent
    # 门已经在分工单里了 ⇒ 再调一次不会再拦（可以收口）
    assert m._close_batch_or_gate(gid, st.leader_state(gid)) is False


def test_acceptance_step_has_a_small_step_budget():
    """★★ 2026-10-06（第 9 轮真跑量出来的 ✗）：**验收那一步不该逛** ✓。

    实测：验收单步 **10 次调用 / 83,784 tok**，占全场 **18%** ✓ ——
    而它该干的只有"跑一两条命令 + 给结论" ✓。给 60 步它就会慢慢逛 ✓。

    ⇒ 验收类任务步数掐到 **12** ✓；**普通测试活不受影响**（还是 60 ✓ 别误伤 ✓）。
    """
    from app import main as m

    full = m._project_acceptance_task("做个小工具", "a.py")
    light = m._project_acceptance_task("做个小工具", "a.py", has_failures=True)
    assert m._budget_for("测试工程师", full) == 20, "完整终验该掐到 20 步 ✓（12 太紧：实测验收人被掐断过 ✗）"
    assert m._budget_for("测试工程师", light) == 20, "确认性验收也该 20 步 ✓"
    # 别误伤：普通的"写用例并真跑"还是 60 ✓
    assert m._budget_for("测试工程师", "写用例并真跑") == 60
    assert m._budget_for("程序员", "实现 wordcount.py") == 60
    assert m._budget_for("文案", "写一段介绍") == 25
    # 确认性那条还要明说"别探索" ✓
    assert "别探索" in light and "别探索" not in full


def test_acceptance_is_lightweight_when_upstream_already_failed():
    """★★ 2026-10-06 **评测台第一轮就抓到的浪费** ✗（`scripts/eval_suite.py` ✓）：

    现场：程序员那步被判失败之后，**终验仍跑了一整套** —— **32 次调用 / 441,853 tok** ✗
    （占那一整个任务的 **95%** ✗），最后写出一份"这项目不能用"的长报告 ✓
    —— 而**结论在判失败那一刻就定了** ✓。

    修法：上游有 failed/blocked ⇒ 终验改走**确认性验收**（跑一下 ✓ 一句话结论 ✓ ≤5 条断言 ✓
    别写长报告 ✗）。这一条光省下来的钱就够跑十几轮评测 ✓。
    """
    from app import main as m

    light = m._project_acceptance_task("做个小工具", "wordcount.py", has_failures=True)
    assert "确认性" in light and "一句话结论" in light, light[:200]
    assert "不要**新写验收脚本" in light or "不要" in light, light
    assert "≤5 条" in light or "5 条" in light, light
    # 反面：没有失败项时，仍然是**完整**验收（硬要求都在 ✓）
    full = m._project_acceptance_task("做个小工具", "a.py", has_failures=False)
    assert "范围（硬要求" in full and "跑工作区里已有的测试" in full
    assert "确认性" not in full

def test_acceptance_brief_has_a_cost_budget(tmp_path, monkeypatch):
    """★ 2026-10-06 降本第三刀：给终验**划范围**。

    实测那一跑单独烧了 **84 万 tok** ✗（从零写了 31 条用例，比实现那一步还重）——
    质量上去了钱也上去了 ⇒ 任务书里必须写明：**先复用已有测试**、新用例 ≤15 条、只答三件事。
    """
    st, gid = _room(tmp_path, monkeypatch)
    st.leader_begin(gid, "做待办 CLI", PLAN)
    for nm in ("架构师", "程序员", "测试工程师"):
        st.leader_finish_item(gid, nm, "done", f"{nm} 交了", [f"{nm}.txt"])
    item = m._ensure_acceptance(gid, st.leader_state(gid))
    assert item is not None
    t = item["task"]
    assert "第一步：跑工作区里已有的测试" in t, t               # 硬要求：先跑已有测试
    assert "不要新写验收脚本" in t and "不超过 15 条" in t, t   # 别从零写一套
    assert "已有测试跑没跑通" in t and "产物是否符合契约" in t, t
    # ★ 2026-10-06：还要验"照交付里写的那节，产物真能打开吗"（用户现场："我打不开" ✗）
    assert "真的能打开/跑起来吗" in t and "对不上就算不合格" in t, t
    assert "别顺手重写实现" in t, t


def test_advance_leader_dispatches_acceptance_before_declaring_done(tmp_path, monkeypatch):
    """★ 收口那一刻的行为：**先派验收**，绝不能在这之前喊「全部完成」。"""
    st, gid = _room(tmp_path, monkeypatch)
    st.leader_begin(gid, "做待办 CLI", PLAN)
    for nm in ("架构师", "程序员"):
        st.leader_finish_item(gid, nm, "done", f"{nm} 交了", [f"{nm}.txt"])
    dispatched: list[dict] = []
    monkeypatch.setattr(m, "_dispatch_leader_batch",
                        lambda gid2, batch, ids: dispatched.append(batch) or [])
    m._advance_leader(gid, "测试工程师", True, "自检写好了", ["test_todo.py"])
    texts = [x["text"] for x in st.feed(gid)]
    assert any("最后一道门" in t for t in texts), texts
    assert not any(t.startswith("✅ 全部完成") for t in texts), texts   # 还没到喊完成的时候
    assert dispatched and any(i.get("kind") == "acceptance" for i in dispatched[0]["ready"]), dispatched
