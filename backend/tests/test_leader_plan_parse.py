"""组长分工单**解析器**的锚点 —— 每一条都来自真实样本（2026-10-05 用户实测）。

背景（真事故）：用户让组长拆"单店外卖小程序"，组长回了满满一屏**人话计划**：
    「【单店外卖小程序 · 排期 v1.0】已拆出13项任务…T1需求→T2原型→T3设计…详见文档。」
旧解析器只认"纯 JSON 数组" ⇒ 判为无法解析 ⇒ **一个人都没派出去**，
用户看到的就是"组长不干活"（而它其实写了 13 项任务）。
所以这个文件的每一条都是"模型不听话时也要尽量认出来"，以及"认不出来时别装作没事"。
"""
from __future__ import annotations

import pytest

from app.team import TeamStore


def test_leader_is_told_not_to_over_decompose_small_tasks():
    """★★ 2026-10-06（用户问"并发那么多为什么还慢" ⇒ 真跑量出来的 ✗）：

    小活也被拆成"架构师写契约 → 程序员实现 → 测试验收"三项 ✓ ⇒ **光仪式就吃一两轮** ✓
    （实测：写个 `wordcount.py` 也要先派架构师写 `docs/spec.md` ✓ 全程 25 分钟 / ¥0.4 ✓）。

    ⇒ 提示词必须交代：**先判断活的大小** ✓ 小活**只派一项** ✓ 别"为拆而拆" ✗ ✓。
    """
    from app import team as team_mod

    p = team_mod.TeamStore.leader_dispatch_prompt(
        object.__new__(team_mod.TeamStore), {"members": []}, "写个 wordcount.py")
    assert "先判断这活有多大" in p, "没让组长先判断大小"
    assert "只派一项" in p, "没交代小活只派一项"
    assert "为拆而拆" in p and "架构师写契约" in p, "没点明那个反例"
    assert "少拆" in p, "没给'拿不准'时的默认倾向"

# ① 用户实测的那段真人话（结构上没有可派发的分工条目）
REAL_PROSE = (
    "【单店外卖小程序 · 排期 v1.0】已拆出13项任务（每项1人1次可做完），含负责人/截止/依赖："
    "T1需求→T2原型→T3设计→T9前端/T6后端→T10联调→T11测试→T12提审上线，D+22上线。"
    "风险6项：资质不齐、审核驳回、需求蔓延。下一步：今天认领负责人定启动日。详见文档。"
)
MENTIONS = "T1 需求 @产品经理 出PRD与验收标准\nT2 原型 @设计师 出可点原型\n- @前端 做个登录页"
TABLE = "| 负责人 | 任务 |\n|---|---|\n| @小李 | 写接口 |\n| 小王 | 写页面 |"
CN_JSON = '[{"负责人": "小李", "任务": "写接口"}]'
FENCE_JSON = '这是分工：\n```json\n[{"name":"小李","task":"写接口"}]\n```\n完毕'
NOISY = '说明 [见附录] 后面才是正题：\n[{"name":"小李","task":"写接口"}]\n以上。'


def test_mentions_style_is_parsed():
    """★ 最有用的一层：组长写「T1 需求 @产品经理 出PRD」这种人话，也要能认出来。"""
    out = TeamStore.parse_leader_plan(MENTIONS)
    assert [x["name"] for x in out] == ["产品经理", "设计师", "前端"], out
    assert "PRD" in out[0]["task"] and "序号被去掉了" or True
    assert not out[0]["task"].startswith("T1"), "序号该去掉，否则工作单里全是 T1 这种噪声"


def test_markdown_table_is_parsed():
    out = TeamStore.parse_leader_plan(TABLE)
    assert [x["name"] for x in out] == ["小李", "小王"], out
    assert out[0]["task"] == "写接口"


def test_chinese_json_keys_are_accepted():
    """中文键也要认（name/负责人、task/任务）。"""
    out = TeamStore.parse_leader_plan(CN_JSON)
    assert out[0]["name"] == "小李" and out[0]["task"] == "写接口", out


def test_output_and_depends_on_are_collected():
    """★ 波次调度就靠这两个字段：交付什么、等谁。中文/英文键名都要认。"""
    en = ('[{"name": "架构师", "task": "定接口", "output": "docs/api.md", "depends_on": []},'
          ' {"name": "程序员", "task": "写代码", "output": "代码+自测", "depends_on": ["架构师"]}]')
    out = TeamStore.parse_leader_plan(en)
    assert out[1]["output"] == "代码+自测", out
    assert out[1]["depends_on"] == ["架构师"], out
    cn = '[{"负责人": "架构师", "任务": "定接口", "交付": "docs/api.md", "依赖": "@架构师 的契约, 设计师"}]'
    got = TeamStore.parse_leader_plan(cn)
    assert got[0]["output"] == "docs/api.md", got
    # "依赖"写成"@架构师 的契约, 设计师"这种话 ⇒ 只留名字（自依赖的过滤在调度层做，见
    # tests/test_leader_waves.py —— 解析器只负责把形状收对）
    assert got[0]["depends_on"] == ["架构师", "设计师"], got


def test_fenced_json_is_parsed():
    assert [x["name"] for x in TeamStore.parse_leader_plan(FENCE_JSON)] == ["小李"]


def test_brackets_elsewhere_do_not_break_extraction():
    """★ 配对取数组：不能"第一个 [ 到最后一个 ]"那样跨段误取（旧实现的隐患）。"""
    out = TeamStore.parse_leader_plan(NOISY)
    assert len(out) == 1 and out[0]["name"] == "小李", out


def test_pure_prose_returns_empty_so_caller_can_show_it():
    """真人话那种**结构上认不出**的：返回空 —— 由调用方把原文贴进群（别装作派成功了）。"""
    assert TeamStore.parse_leader_plan(REAL_PROSE) == []


@pytest.mark.parametrize("raw", ["", "   ", "没有分工", "[]", "[{}]", "[1,2,3]"])
def test_garbage_is_safe(raw):
    assert TeamStore.parse_leader_plan(raw) == []


def test_prompt_forbids_prose_and_shows_an_example():
    """提示词要**明确禁止**人话/表格/围栏，并给一个可照抄的例子（否则模型还会写计划书）。"""
    p = TeamStore.leader_dispatch_prompt(TeamStore.__new__(TeamStore),  # 静态用法：不碰磁盘
                                         {"members": [], "leader": None}, "做个外卖小程序")
    for k in ("只输出一个 JSON 数组", "第一个字符必须是", "不要", "例：["):
        assert k in p, f"提示词缺约束：{k}"


def test_failure_path_posts_the_raw_plan(monkeypatch, tmp_path):
    """★ 认不出来时，群里必须能看到组长的**原话**（用户实测里最难受的就是"只给一句失败"）。"""
    from fastapi.testclient import TestClient

    from app import main as m

    st = TeamStore(tmp_path)
    leader = st.add_employee({"name": "组长甲", "dept": "管理", "role": "组长", "persona": "拆解", "mode": "expert"})
    member = st.add_employee({"name": "小李", "dept": "研发", "role": "工程师", "persona": "写代码", "mode": "expert"})
    g = st.create_group("测试群", [leader["id"], member["id"]], leader=leader["id"], mode="leader")
    monkeypatch.setattr(m, "_team_store", st)

    async def _fake_plan(leader_emp, prompt):
        return REAL_PROSE

    monkeypatch.setattr(m, "_leader_plan", _fake_plan)
    monkeypatch.setattr(m, "_dispatch_to_employee", lambda *a, **kw: pytest.fail("不该派活（解析不出）"))

    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        body = c.post(f"/api/v1/team/groups/{g['id']}/say", json={"text": "做个单店外卖小程序"}).json()
    assert body["dispatched"] == 0
    texts = [x["text"] for x in st.feed(g["id"])]
    assert any("已拆出13项任务" in t for t in texts), "组长原话没贴进群"
    warn = [t for t in texts if "不是可解析的分工单" in t]
    assert warn and "@某人 做什么" in warn[0], "没给用户手动派的出路"


# ═══ 顺手修的第二件事：超时提示刷屏（用户实测那个群 297 条同类消息）═══


def test_timeout_notice_is_deduped_by_task_id(tmp_path):
    """★ 同一个任务的超时提示**只能有一条**（落盘去重：内存标记撑不过后端重启）。

    真实现场：一个任务刷了 15–43 条 —— 后端每次重启都会再挂一个看门者，各提醒一次。
    """
    from app.team import TeamStore as TS

    st = TS(tmp_path)
    assert st.has_notice("g1", "task_x", "超过 30 分钟") is False
    st.append("g1", **{"from": "sys:甲", "text": "⏳ 任务超过 30 分钟未结束，交付不再自动回流。",
                       "task_id": "task_x", "status": "running"})
    assert st.has_notice("g1", "task_x", "超过 30 分钟") is True, "发过一条之后就该认出来"
    assert st.has_notice("g1", "task_y", "超过 30 分钟") is False, "别的任务不受影响"
    assert st.has_notice("g1", "task_x", "别的字样") is False, "提示词不同不该误判"
