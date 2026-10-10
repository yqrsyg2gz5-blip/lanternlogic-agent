"""★ 验收环节（P0-2）：交付之后**先验收**，通过了才推进下一批。

## 依据（先查后做）

· **MAST**（200+ 轨迹，NeurIPS 2025）："任务验证"这一类占 **21.3%**
  —— 没验证/验证不全 6.8% + 验证做错 6.7%；并指出"现有 verifier 常常只做表面检查（能编译、有注释）"
· 论文的干预实验：在低层检查之外**再加一道高层目标验收** ⇒ 成功率 **+15.6%**（Insight 2：需要多级验证）
· **Anthropic 复盘**：评估看**终态**，不看它有没有按你规定的步骤走

## 所以这里钉住的语义

1. **低层是机器查的**（不花模型钱）：声称交付的文件到底在不在、是不是空文件、有没有越界
2. **高层由另一个成员判**：对照总目标，这一步是否**真的**达成（不是"看起来做了"）
3. **不许自己验自己**：验收人必须不是干活的那个人；群里只有一个人就**如实说没人可验**（不假装）
4. 不通过 ⇒ **打回重做**并附"要改什么"；重做的**轮次有上限**，超了就判失败（依赖它的活被挡住）
5. 验收结论解析不出来 ⇒ **一律当没通过**（宁可严）
"""
from __future__ import annotations

import asyncio
import pathlib

import pytest

from app import main as m
from app.team import TeamStore

PLAN = [
    {"name": "架构师", "task": "定订单服务的 API 契约", "output": "docs/api.md", "depends_on": []},
    {"name": "程序员", "task": "按契约实现订单接口", "output": "代码+自测", "depends_on": ["架构师"]},
]


@pytest.fixture()
def room(tmp_path, monkeypatch):
    st = TeamStore(tmp_path)
    ids = {}
    for nm, role in (("架构师", "架构师"), ("程序员", "工程师"), ("测试", "测试工程师")):
        ids[nm] = st.add_employee({"name": nm, "dept": "技术部", "role": role,
                                   "persona": "干活", "mode": "expert"})["id"]
    g = st.create_group("开发群", list(ids.values()), mode="leader")
    monkeypatch.setattr(m, "_team_store", st)
    return {"store": st, "gid": g["id"], "ids": ids}


# ── 低层：机器查（确定性，不花模型钱）──

def test_low_level_check_requires_the_claimed_files_to_really_exist(room, tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(m.store, "workspace_dir", lambda tid: ws)
    # 声称交了文件，但文件不在 ⇒ 不合格（这就是"只在回复里说做了"）
    ok, detail = m._low_level_check("t1", ["docs/api.md"])
    assert ok is False and "找不到" in detail, detail
    # 真写出来 ⇒ 合格
    (ws / "docs").mkdir()
    (ws / "docs" / "api.md").write_text("# 契约\nPOST /orders", encoding="utf-8")
    ok2, detail2 = m._low_level_check("t1", ["docs/api.md"])
    assert ok2 is True and "在：" in detail2, detail2


def test_low_level_check_flags_empty_and_escaping_paths(room, tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "empty.md").write_text("", encoding="utf-8")
    monkeypatch.setattr(m.store, "workspace_dir", lambda tid: ws)
    ok, detail = m._low_level_check("t1", ["empty.md"])
    assert ok is False and "空文件" in detail, detail
    ok2, detail2 = m._low_level_check("t1", ["../../etc/passwd"])
    assert ok2 is False and "越界" in detail2, detail2


def test_low_level_check_says_so_when_nothing_was_delivered(room, tmp_path, monkeypatch):
    monkeypatch.setattr(m.store, "workspace_dir", lambda tid: tmp_path)
    ok, detail = m._low_level_check("t1", [])
    assert ok is False and "没有交付任何文件" in detail, detail


# ── 高层：另一个成员判 ──

def test_verifier_is_never_the_author(room):
    g = room["store"].get_group(room["gid"])
    arch_id = room["ids"]["架构师"]
    verifier, why = m._pick_verifier(g, arch_id)
    assert verifier is not None and verifier["id"] != arch_id, (verifier, why)
    # 角色像质检的优先（这里是"测试工程师"）
    assert verifier["name"] == "测试", verifier


def test_no_verifier_when_alone(tmp_path, monkeypatch):
    st = TeamStore(tmp_path)
    e = st.add_employee({"name": "独苗", "dept": "技术部", "role": "工程师", "persona": "干活", "mode": "expert"})
    g = st.create_group("单人组", [e["id"]], mode="leader")
    monkeypatch.setattr(m, "_team_store", st)
    verifier, why = m._pick_verifier(g, e["id"])
    assert verifier is None and "没有别人" in why, (verifier, why)


@pytest.mark.parametrize("raw,expect", [
    ('{"pass": true, "low": "文件在", "high": "达成了目标", "fix": ""}', True),
    ('我的结论是 {"pass": false, "low": "缺文件", "high": "没做", "fix": "补上三个接口"} 完毕', False),
    ("这项不通过，接口没定义清楚。", False),
    ("验收通过，产物齐全。", True),
    ("嗯。", False),                      # 认不出来 ⇒ 当没通过
])
def test_verdict_parser_is_strict(raw, expect):
    assert TeamStore.parse_verdict(raw)["pass"] is expect, raw


# ── 端到端（打桩"说话"，不花模型钱）──

def _run_verify(room, name, monkeypatch, verdict_text, ws_files=("docs/api.md",),
                reply=("契约已定稿。\n文件清单：docs/api.md（接口契约）\n"
                       "接口定义：GET /orders、POST /orders\n数据结构：Order{id,items,total}")):
    ws = room["store"].root / "ws"
    ws.mkdir(exist_ok=True)
    for f in ws_files:
        p = ws / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("内容", encoding="utf-8")
    monkeypatch.setattr(m.store, "workspace_dir", lambda tid: ws)
    sent: list[dict] = []
    monkeypatch.setattr(m, "_dispatch_leader_batch",
                        lambda gid, batch, ids: sent.append(batch) or [])
    asked: list[str] = []

    async def _fake_say(emp, prompt):
        asked.append(prompt)
        return verdict_text

    monkeypatch.setattr(m, "_employee_say", _fake_say)
    asyncio.run(m._verify_delivery(room["gid"], name, True, reply, list(ws_files)))
    return sent, asked


def test_pass_advances_to_the_next_wave(room, monkeypatch):
    room["store"].leader_begin(room["gid"], "做外卖小程序", PLAN)
    room["store"].leader_attach_task(room["gid"], "架构师", "task_a")
    # ★ 说"通过"必须引得出证据（下面这条引用了交付摘要里的原话）
    sent, asked = _run_verify(room, "架构师", monkeypatch,
                              '{"pass": true, "low": "在", "high": "契约覆盖了目标要求的接口",'
                              ' "evidence": "契约已定稿", "fix": ""}')
    texts = [x["text"] for x in room["store"].feed(room["gid"])]
    assert any("验收通过" in t for t in texts), texts
    assert sent and {i["name"] for i in sent[0]["ready"]} == {"程序员"}, sent
    assert asked and ("低层" in asked[0] and "引用" in asked[0]), asked[:1]   # 提示词要求"引得出证据"
    st = room["store"].leader_state(room["gid"])
    assert st["done"] == 1, st


def test_reject_sends_the_work_back_with_the_fix(room, monkeypatch):
    """★ 打回要带"要改什么"，并且重做的工作单里必须写上这条意见。"""
    room["store"].leader_begin(room["gid"], "做外卖小程序", PLAN)
    room["store"].leader_attach_task(room["gid"], "架构师", "task_a")
    sent, _ = _run_verify(room, "架构师", monkeypatch,
                          '{"pass": false, "low": "只有标题", "high": "接口没定义", "fix": "补全三个接口的请求响应"}')
    texts = [x["text"] for x in room["store"].feed(room["gid"])]
    assert any("打回" in t and "补全三个接口的请求响应" in t for t in texts), texts
    # 重做是**同一个人**，且工作单里带了验收意见
    assert sent and {i["name"] for i in sent[0]["ready"]} == {"架构师"}, sent
    g = room["store"].get_group(room["gid"])
    arch = next(i for i in g["leader_plan"] if i["name"] == "架构师")
    assert arch["reroll_advice"] == "补全三个接口的请求响应", arch
    assert "补全三个接口的请求响应" in room["store"].leader_handoff_text(g, arch)


def test_too_many_rejections_fail_the_item_and_block_dependents(room, monkeypatch):
    """打回超过上限 ⇒ 判失败，**依赖它的活被挡住**（不再无限来回烧钱）。"""
    room["store"].leader_begin(room["gid"], "做外卖小程序", PLAN)
    room["store"].leader_attach_task(room["gid"], "架构师", "task_a")
    reject = '{"pass": false, "low": "只有标题", "high": "接口没定义", "fix": "补全接口"}'
    sent, _ = _run_verify(room, "架构师", monkeypatch, reject)          # 第 1 次打回
    asyncio.run(m._verify_delivery(room["gid"], "架构师", True, "又交了一版", ["docs/api.md"]))
    asyncio.run(m._verify_delivery(room["gid"], "架构师", True, "第三版", ["docs/api.md"]))
    texts = [x["text"] for x in room["store"].feed(room["gid"])]
    assert any("仍不合格" in t for t in texts), texts
    st = room["store"].leader_state(room["gid"])
    assert st["failed"] == 1 and st["blocked"] == 1, st          # 程序员被挡住


def test_pass_without_evidence_passes_with_a_soft_note(room, monkeypatch):
    """★ 2026-10-05 真群回归后改的口径：**"验收人不行"不等于"活不行"**。

    原实现：验收说通过但引不出证据 ⇒ 打回重做。真群实测的代价是：
    验收人自己调用失败（RuntimeError）⇒ 架构师被冤枉打回、白烧 43698 tok，
    而它的产物其实**机器查过、确实在**。
    现在的口径：**低层（机器查）通过就放行**，只在群里如实标注"高层未核实"（不让人白等、不让人白干）。
    """
    room["store"].leader_begin(room["gid"], "做外卖小程序", PLAN)
    room["store"].leader_attach_task(room["gid"], "架构师", "task_a")
    sent, _ = _run_verify(room, "架构师", monkeypatch,
                          '{"pass": true, "low": "看起来不错", "high": "应该没问题", "fix": ""}')
    texts = [x["text"] for x in room["store"].feed(room["gid"])]
    assert any("按低层结论放行" in t and "高层未核实" in t for t in texts), texts
    # 放行 ⇒ 下一批该被派出去（不是让架构师重做）
    assert sent and {i["name"] for i in sent[0]["ready"]} == {"程序员"}, sent
    assert room["store"].leader_state(room["gid"])["done"] == 1


def test_verifier_crash_does_not_punish_the_worker(room, monkeypatch):
    """★ 验收人**调用失败**时：低层过了就放行（系统故障不该让干活的背锅）。"""
    room["store"].leader_begin(room["gid"], "做外卖小程序", PLAN)
    room["store"].leader_attach_task(room["gid"], "架构师", "task_a")
    ws = room["store"].root / "ws"
    (ws / "docs").mkdir(parents=True, exist_ok=True)
    (ws / "docs" / "api.md").write_text("内容", encoding="utf-8")
    monkeypatch.setattr(m.store, "workspace_dir", lambda tid: ws)

    async def _boom(emp, prompt):
        raise RuntimeError("模型 500")

    monkeypatch.setattr(m, "_employee_say", _boom)
    sent: list[dict] = []
    monkeypatch.setattr(m, "_dispatch_leader_batch", lambda gid, batch, ids: sent.append(batch) or [])
    asyncio.run(m._verify_delivery(room["gid"], "架构师", True,
                                   "文件清单：docs/api.md\n接口定义：GET /orders\n数据结构：Order",
                                   ["docs/api.md"]))
    texts = [x["text"] for x in room["store"].feed(room["gid"])]
    assert any("按低层结论放行" in t and "验收人调用失败" in t for t in texts), texts
    assert sent and {i["name"] for i in sent[0]["ready"]} == {"程序员"}, sent


def test_verifier_crash_with_bad_low_level_still_rejects(room, monkeypatch):
    """但如果**低层也没过**（产物根本不在）⇒ 该打回还是打回（那确实是活的问题）。"""
    room["store"].leader_begin(room["gid"], "做外卖小程序", PLAN)
    room["store"].leader_attach_task(room["gid"], "架构师", "task_a")
    ws = room["store"].root / "ws"
    ws.mkdir(exist_ok=True)
    monkeypatch.setattr(m.store, "workspace_dir", lambda tid: ws)

    async def _boom(emp, prompt):
        raise RuntimeError("模型 500")

    monkeypatch.setattr(m, "_employee_say", _boom)
    sent: list[dict] = []
    monkeypatch.setattr(m, "_dispatch_leader_batch", lambda gid, batch, ids: sent.append(batch) or [])
    asyncio.run(m._verify_delivery(room["gid"], "架构师", True, "我说我交了", ["docs/api.md"]))
    texts = [x["text"] for x in room["store"].feed(room["gid"])]
    assert any("打回" in t for t in texts), texts
    assert room["store"].leader_state(room["gid"])["done"] == 0


def test_grounded_verdict_is_accepted(room):
    """反向：引用了产物名或交付原话 ⇒ 算接地，不该误杀。"""
    item = {"name": "架构师", "task": "定契约", "reply": "契约定稿：POST /orders 返回 id",
            "attachments": ["docs/api.md"]}
    assert TeamStore.verdict_is_grounded(
        {"pass": True, "evidence": "POST /orders 返回 id"}, item, "在：docs/api.md") is True
    assert TeamStore.verdict_is_grounded(
        {"pass": True, "high": "按 docs/api.md 核对了三个接口"}, item, "在：docs/api.md") is True
    assert TeamStore.verdict_is_grounded(
        {"pass": True, "evidence": "我觉得写得挺好"}, item, "在：docs/api.md") is False


def test_unparsable_verdict_is_retried_once_then_soft_passes(room, monkeypatch):
    """★ 真群实测：验收人有时返回**一条 shell 命令**（它想自己去看看文件），解析不出结论。

    ★★ 2026-10-06 改进：以前**第一次说不清就软放行** ✗（等于高层这关白设 ✓）。
    现在：**先复问一次**（"只回那段 JSON、别调工具" ✓）⇒ 还不行才放行并标注 ✓。
    """
    room["store"].leader_begin(room["gid"], "做外卖小程序", PLAN)
    room["store"].leader_attach_task(room["gid"], "架构师", "task_a")
    sent, _ = _run_verify(room, "架构师", monkeypatch,
                          '{"command": "ls -la /w/docs/ && cat /w/docs/api.md"}')
    texts = [x["text"] for x in room["store"].feed(room["gid"])]
    assert any("按低层结论放行" in t and "复问一次仍未给出可解析结论" in t for t in texts), texts
    assert sent and {i["name"] for i in sent[0]["ready"]} == {"程序员"}, sent


def test_the_retry_can_actually_rescue_the_verdict(room, monkeypatch):
    """★★ 复问**真的能救回来** ✓：第一次返回 shell 命令、第二次返回正常 JSON ⇒ 按 JSON 判 ✓。

    （这条是这次改动的意义所在：少一次"高层未核实"的软放行 ✓ ——
     真群那几轮里，终验自己那份交付就是这么被软放行过去的 ✓。）
    """
    room["store"].leader_begin(room["gid"], "做外卖小程序", PLAN)
    room["store"].leader_attach_task(room["gid"], "架构师", "task_a")
    calls = {"n": 0}

    async def _say(emp, prompt):                     # noqa: ARG001
        calls["n"] += 1
        if calls["n"] == 1:
            return '{"command": "ls -la /w/docs/"}'
        return '{"pass": true, "high": "按 docs/api.md 核对了三个接口", "evidence": "docs/api.md"}'

    monkeypatch.setattr(m, "_employee_say", _say)
    # ★ 低层要**先过**才会去问验收人 ✓ —— 本班第一次忘了这步 ⇒ `_employee_say` 一次都没被调用 ✗
    monkeypatch.setattr(m, "_low_level_check", lambda *a, **k: (True, "在：docs/api.md"))
    monkeypatch.setattr(m, "_advance_leader", lambda *a, **k: sent.append(a[0]) or {"ready": []})
    sent: list[str] = []

    async def _go():
        item = next(i for i in room["store"].get_group(room["gid"])["leader_plan"]
                    if i["name"] == "架构师")
        await m._verify_delivery(room["gid"], "架构师", True, item.get("reply") or "交了 docs/api.md",
                                 ["docs/api.md"])

    import asyncio
    asyncio.run(_go())
    assert calls["n"] == 2, f"应该复问一次（实际 {calls['n']} 次）"
    texts = [x["text"] for x in room["store"].feed(room["gid"])]
    assert any("复问一次拿到了" in t for t in texts), texts
    assert not any("高层未核实" in t for t in texts), "复问拿到了就不该再标注未核实 ✗"


def test_verifier_prompt_carries_the_fresh_delivery(room, monkeypatch):
    """★★★ 2026-10-06 **今天大半失败的真根因** ✗✗✗：

    `_verify_delivery` 里原来把 `item`（计划项）直接递给 `verification_prompt` ✓ ——
    而 `item["reply"]` 要等**验收之后**、在 `_advance_leader → leader_finish_item` 里才写入 ✓
    ⇒ **验收那一刻它还是空的** ✗✓ ⇒ 验收人看到的"交付摘要"永远是空 ✓
    ⇒ 它每次都判"没贴输出 / 什么都缺" ✓ **而它说的全是实话** ✓
    （我们前面好几轮怪它太苛刻、还改了三处截断 ✗ —— 全打偏了 ✓）。

    ⇒ 修法：把**刚从任务事件里读出来的那份交付正文**拼进去再问 ✓
      （`judged = dict(item, reply=reply …)` ✓ 不能等计划项 ✓）。

    这条用**行为**验（比查源码结实 ✓ 本班第一版查源码 ⇒ 红绿回滚组观察不到红 ✗）：
    把低层检查与验收人的回答都换成假的 ✓，然后断言**验收人收到的提示词里带着这次交付** ✓。
    """
    from app import main as m

    st, gid = room["store"], room["gid"]
    st.leader_begin(gid, "写个 wordcount.py 并真跑", [
        {"name": "架构师", "task": "写 wordcount.py 并真跑自测", "output": "wordcount.py",
         "depends_on": []},
    ])
    seen: list[str] = []

    async def _fake_say(emp, prompt):                       # noqa: ARG001
        seen.append(prompt)
        return ('{"pass": true, "low": "ok", "high": "ok", '
                '"evidence": "改动文件与自测输出都在", "fix": ""}')

    monkeypatch.setattr(m, "_employee_say", _fake_say)
    # ★ 低层检查默认会去任务工作区找文件（这份假任务里没有 ✗）⇒ 换成"通过" ✓
    #   否则压根走不到"叫验收人"那一步 ⇒ 测不到要测的东西 ✓（本班第一版就栽在这 ✓）
    monkeypatch.setattr(m, "_low_level_check", lambda *a, **k: (True, "假低层：文件在位"))
    delivery = "## 改动文件\n- wordcount.py：新增\n## 自测命令\n$ pytest -q\n11 passed in 0.31s\n"
    asyncio.run(m._verify_delivery(gid, "架构师", True, delivery, ["wordcount.py"]))
    assert seen, "验收人压根没被叫 ✗（说明走到别的分支了）"
    assert "11 passed" in seen[0], \
        "验收人的提示词里没有这次交付的正文 ✗ —— 就是那个真根因 ✓（它读的是验收前还没写 reply 的计划项 ✗）"


def test_all_delivery_truncations_share_one_constant(room):
    """★★★ 2026-10-06 **今天大半失败的单一根因**（**三把刀** ✗✗）——立个守卫别再来第四把 ✓。

    交付正文原来被三处各砍一段：看门 `[:800]` ✓ 存进计划项 `[:800]` ✓ 验收提示词 `[:500]` ✓ ——
    而小节顺序是「改动文件 → 自测命令 → **真实输出**」✓ ⇒
    **那段唯一的实证永远落在刀口之前被丢掉** ✓ ⇒ 验收人每次说"交付摘要为空" ✓
    **而它说的是实话** ✓（前面几轮我们一直怪它太苛刻 ✗ 冤枉了它 ✓）。

    ⇒ 现在三处都走 `DELIVERY_TEXT_MAX` ✓；这条测试**钉住**这一点 ✓：
      只要有人再写一个小数字砍交付，它就红 ✓。
    """
    from app import team as team_mod

    assert team_mod.DELIVERY_TEXT_MAX >= 4000, "交付正文上限被改小了 ✗（实测一份真实交付 2558 字 ✓）"
    src = pathlib.Path(team_mod.__file__).read_text("utf-8")
    # 存计划项那处必须走常量 ✓（它就是验收人读的那份 ✓）
    assert 'it["reply"] = str(reply or "")[:DELIVERY_TEXT_MAX]' in src, \
        "存计划项时又用硬编码的小数字砍交付 ✗（验收人读的就是这份 ✓）"
    assert 'it["reply"] = str(reply or "")[:800]' not in src
    # 验收提示词那处也必须走常量 ✓
    assert "[:DELIVERY_TEXT_MAX]}\" + chr(10)" in src, "验收提示词又硬砍一刀 ✗"
    assert "【交付摘要】" in src, "交付摘要那行被改没了 ✗"
    # 看门那边（main.py）同样放宽 ✓
    m_src = pathlib.Path(m.__file__).read_text("utf-8")
    assert "[:4000]" in m_src, "看门那边还在砍交付 ✗"
    assert 'payload.get("text") or "")[:800]' not in m_src, "看门那边还有 800 的刀口 ✗"


def test_delivery_body_is_not_truncated_before_the_verifier(room):
    """★★ 2026-10-06 **今天大半失败的单一根因** ✗✗：

    交付正文先后被砍两刀：看门那边 `[:800]` ✓ 验收提示词里 `[:500]` ✗ ——
    而交付的**小节顺序**是「改动文件 → 自测命令 → **真实输出**」✓
    ⇒ **"真实输出"正好落在刀口之后** ✗ ⇒ 验收人**永远看不到那段输出** ✓✓
    ⇒ 它每次都说"没贴输出" ✓ **而它说的是实话** ✓（我们前面还怪它太苛刻 ✗）。

    实测某次真实交付正文 **2558 字**，命令与输出全在后半段 ✓。
    ⇒ 看门那边放宽到 4000 ✓、验收提示词放宽到 2500 ✓。
    """
    from app import main as m
    from app import team as team_mod

    src = pathlib.Path(m.__file__).read_text("utf-8")
    assert "[:800]" not in src.replace("`[:800]`", ""), "看门那边还有 800 的刀口 ✗"
    assert "[:4000]" in src, "交付正文该放宽到 4000 ✓"

    body = ("## 改动文件\n- wordcount.py：新增\n" + "（很长的说明）" * 60
            + "\n## 自测命令\n$ python -m pytest test_wordcount.py -q\n11 passed in 0.31s\n")
    p = team_mod.TeamStore.verification_prompt("写 wordcount.py", {
        "task": "写 wordcount.py", "output": "wordcount.py",
        "attachments": ["wordcount.py"], "reply": body}, "在：wordcount.py")
    assert "11 passed" in p, "真实输出还是没进验收人的视野 ✗（就是那个根因 ✓）"


def test_verifier_judges_content_not_placement(room):
    """★★ 2026-10-06（**第 10 轮真跑**：同一项被打回 3 次 ✗ 每次烧 11–16 万 tok ✓✓）：

    现场：最后一次验收意见写着"**在【交付摘要】中**列出改动文件…" ✗ ——
    而交付里**明明写了**改动文件 ✓，只是**没写在它指定的那个位置/标题下** ✓✓。
    这和小节检查那个病**一模一样**（判格式不判内容 ✗），只是发生在**高层验收人**身上 ✓。

    ⇒ 提示词必须说死：**判"有没有这件事"，不判"写在哪、叫什么"** ✓✓。
    """
    p = TeamStore.verification_prompt(
        "写个 wordcount.py 并自测",
        {"task": "写 wordcount.py", "output": "wordcount.py", "attachments": ["wordcount.py"]},
        "在：wordcount.py")
    assert "判内容，不判位置" in p, "没拦住'按位置挑刺'"
    assert "别要求它写在某个标题下" in p, p[-500:]
    assert "别要求它重抄一遍" in p, "没拦住'重抄一遍'这种加码 ✗"
    assert "该判通过" in p, "没给'答不上来就通过'这条兜底判据 ✓"


def test_verifier_judges_this_step_not_the_whole_goal(room):
    """★★ 2026-10-06（**评测台连跑三轮抓到的结构性 bug** ✗✗）：

    现场：组长把总目标拆成"架构 / 实现 / 测试"三项 ✓，实现那项的**工作单**写着
    "只写主程序、不写测试" ✓ —— 可验收人拿**总目标**当尺子 ✗（总目标里有"顺手写 test_xxx.py"）
    ⇒ **永远判"没写测试"** ⇒ 打回 3 次 ⇒ 判失败 ⇒ 依赖它的活被挡住 ✓✓
    （实测连输三轮：46.5 万 / 12.4 万 / 12.8 万 tok ✓ 全栽在这上面 ✗）

    ⇒ 提示词必须说死：**只按「这一项要求」判** ✓；总目标里没人认领的东西 ⇒ 记成**组长的缺陷** ✓。
    """
    p = TeamStore.verification_prompt(
        "写个 wordcount.py，顺手写 test_wordcount.py 并真跑",
        {"task": "实现 wordcount.py（只写主程序、不写测试）",
         "output": "wordcount.py", "attachments": ["wordcount.py"]},
        "在：wordcount.py")
    assert "只按「这一项要求」判" in p, "没交代'只判这一步'"
    assert "别拿总目标里属于别人那一步的东西" in p, "没拦住'拿总目标压这一步'"
    assert "没写测试不算这一步的错" in p, "没给出最典型的那个例子"
    assert "没有任何一项认领" in p and "组长" in p, "总目标漏项该记给组长 ✓"
    # 反面：老的"对照总目标"说法必须消失 ✗（它就是病根 ✓）
    assert "对照总目标，这一步是否真的达成" not in p


def test_verification_prompt_tells_it_not_to_act(room):
    """验收提示词要**明确不要动手**（文件在不在机器已查）—— 否则它会去调工具而给不出结论。"""
    p = TeamStore.verification_prompt("目标", {"task": "t", "attachments": ["a.md"]}, "在：a.md")
    assert "机器已经查过" in p and "不需要动手" in p, p
    assert "不要调用任何工具" in p, p


def test_salvage_pulls_the_answer_out_of_a_tool_call(room):
    """★ 真群连挂 5 轮的根因：模型看到"只输出 JSON"会**直接调 task_done**，把答案塞进工具参数。

    `_leader_plan` 原来只读 text ⇒ 拿到空串或"任务已完成，交付结果见下方 task_done"这种占位语
    ⇒ 抛"空响应" ⇒ 验收永远失败。现在从工具参数里**救回来**。
    """
    from types import SimpleNamespace

    fake_done = SimpleNamespace(tool_call=SimpleNamespace(
        name="task_done",
        arguments={"outcome": "success", "message": {"pass": True, "high": "达成"}}))
    out = m._salvage_from_tool_call(fake_done)
    assert out.startswith("{") and '"pass"' in out, out          # 序列化成 JSON，parse_verdict 能直接读

    fake_text = SimpleNamespace(tool_call=SimpleNamespace(
        name="task_done", arguments={"message": "验收通过，产物齐全"}))
    assert m._salvage_from_tool_call(fake_text) == "验收通过，产物齐全"

    fake_shell = SimpleNamespace(tool_call=SimpleNamespace(
        name="shell_exec", arguments={"command": "ls /w"}))
    assert "ls /w" in m._salvage_from_tool_call(fake_shell)      # 它想去自己看文件 ⇒ 救回命令文本

    assert m._salvage_from_tool_call(SimpleNamespace(tool_call=None)) == ""


def test_missing_file_is_rejected_without_asking_the_model(room, monkeypatch):
    """★ 低层是机器查的：文件根本不在 ⇒ **不打模型调用**就打回（省一次钱、判得还更准）。"""
    room["store"].leader_begin(room["gid"], "做外卖小程序", PLAN)
    room["store"].leader_attach_task(room["gid"], "架构师", "task_a")
    ws = room["store"].root / "ws"
    ws.mkdir(exist_ok=True)
    monkeypatch.setattr(m.store, "workspace_dir", lambda tid: ws)
    asked: list[str] = []

    async def _should_not_be_called(emp, prompt):
        asked.append(prompt)
        return '{"pass": true, "low": "", "high": "", "fix": ""}'

    monkeypatch.setattr(m, "_employee_say", _should_not_be_called)
    sent: list[dict] = []
    monkeypatch.setattr(m, "_dispatch_leader_batch", lambda gid, batch, ids: sent.append(batch) or [])
    asyncio.run(m._verify_delivery(room["gid"], "架构师", True, "我说我交了", ["docs/api.md"]))
    assert asked == [], "低层没过就不该去打模型"
    texts = [x["text"] for x in room["store"].feed(room["gid"])]
    assert any("打回" in t and "真正写进工作区" in t for t in texts), texts
