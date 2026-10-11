"""★ 依赖波次（P0-1）：组长拆解**不能一把全并行**。

## 为什么要做（对标研究）

· **MAST**（200+ 轨迹，NeurIPS 2025）："带着错误假设往下做、不问清楚"占 **11.7%**，
  "扣着关键信息不说"占 1.7% —— 用户实测正是这个：组长把"程序员"和"架构师"一起派出去，
  程序员在架构师交付前就开工，只能**猜接口**
· **Anthropic 复盘**：委派必须给"目标 + 输出格式 + 工具 + 边界"；
  并明确说"**彼此依赖很多的任务目前不适合并行**"（点名 coding）

## 所以这里钉住的语义

1. 有前置的活**在前置交付之前不许派**（不是"晚点派"，是**根本不派**）
2. 派它的时候，工作单里**必须带上前置的产物路径与摘要**（让它别猜）
3. 前置**失败**⇒ 依赖它的活变 blocked 并说明原因（不派人去送人头）
4. **不许死锁**：前置成环/名字对不上时全部放行（宁可顺序不完美，也不能永远停着）
5. 全部完成 ⇒ 群里一句"全部完成"，并给出批次统计
"""
from __future__ import annotations

from app.team import TeamStore

PLAN = [
    {"name": "架构师", "task": "定订单服务的 API 契约", "output": "docs/api.md", "depends_on": []},
    {"name": "设计师", "task": "出下单页原型", "output": "docs/ui.md", "depends_on": []},
    {"name": "程序员", "task": "按契约实现订单接口", "output": "代码+自测", "depends_on": ["架构师"]},
    {"name": "测试", "task": "按契约与原型写用例", "output": "tests/", "depends_on": ["架构师", "设计师"]},
]


def _store(tmp_path) -> tuple[TeamStore, str]:
    st = TeamStore(tmp_path)
    ids = []
    for nm in ("架构师", "设计师", "程序员", "测试"):
        ids.append(st.add_employee({"name": nm, "dept": "技术部", "role": nm,
                                    "persona": "干活", "mode": "expert"})["id"])
    g = st.create_group("开发群", ids, mode="leader")     # 建群至少要一名成员
    return st, g["id"]


def test_plan_name_with_at_prefix_still_finds_the_member(tmp_path, monkeypatch):
    """★★ 2026-10-06（评测台第六轮：**整批一项都没开工、静默卡死** ✗✗）：

    组长把名字写成 **`@评测乙`**（带 @ ✓），而派发按名字找人时**没去掉 @** ✗ ⇒ 找不到人 ⇒
    那一项被跳过 ⇒ 群里只留一句"共 1 项，先开工 **0** 项" ✓ —— 用户看到的就是"它卡住了" ✓✓
    （`depends_on` 那边早就 strip 过 @ ✓，名字这边漏了 ✓）。

    修法：名字也去 @（顺带认得全角@和空格 ✓）；真找不到时把**本群成员都列出来** ✓，
    别只留一句"不在本群"让人干瞪眼 ✓。
    """
    from app import main as m

    st, gid = _store(tmp_path)
    # 建群时的成员名是"架构师/设计师/程序员/测试" ✓ —— 这里故意用带 @ 的写法 ✓
    st.leader_begin(gid, "做点小事", [
        {"name": "@程序员", "task": "实现 a.py", "output": "a.py", "depends_on": []},
    ])
    sent: list[str] = []
    monkeypatch.setattr(m, "_team_store", st)
    monkeypatch.setattr(m, "_dispatch_to_employee",
                        lambda gid_, g_, nm, emp, text, **k: sent.append(nm) or
                        type("T", (), {"id": "t1"})())
    g = st.get_group(gid)
    # ★ `leader_begin` 已经把第一批标成 running ⇒ `leader_ready` 此刻是空的 ✗（本班踩过 ✓）
    #   所以这里**自己构造批次** ✓，只验"带 @ 的名字认不认得出人" ✓。
    #   注意 `name_to_id` 要指到**真实员工 id** ✓ —— 假 id 会在 `get_employee` 那步被跳过 ✗（也踩过 ✓）。
    real = {e["name"]: e["id"] for e in st.employees()}
    m._dispatch_leader_batch(gid, {"ready": list(g["leader_plan"])}, real)
    assert sent, "带 @ 的名字没找到人 ⇒ 那一项被跳过（就是那个静默卡死 ✗）"
    texts = [x["text"] for x in st.feed(gid)]
    assert not any("不在本群" in t for t in texts), texts[-3:]


def test_plan_name_with_role_suffix_still_finds_the_member(tmp_path, monkeypatch):
    """★★ 2026-10-06（评测台**第 7 轮**：还是整批 0 开工 ✗✗）：

    第 6 轮修了"名字带 @"✓，第 7 轮组长换了种写法：**`评测乙…（技术部·程序员）`** ——
    名字后面**带职务括号** ✗ ⇒ 精确匹配还是找不到 ⇒ 那一项又被跳过 ✓
    （群里那句提示把成员都列出来了 ✓ 一眼能看出"它明明在群里" ✓）。

    ⇒ 两层兜底：① 去掉尾部 `（…）` ✓；② **前缀模糊匹配取最长的** ✓
    —— 防"张三丰"被误配成"张三" ✗（这条特别验了 ✓）。
    """
    from app import main as m

    st, gid = _store(tmp_path)
    st.leader_begin(gid, "做点小事", [
        {"name": "程序员（技术部·工程师）", "task": "实现 a.py", "output": "a.py", "depends_on": []},
    ])
    sent: list[str] = []
    monkeypatch.setattr(m, "_team_store", st)
    monkeypatch.setattr(m, "_dispatch_to_employee",
                        lambda gid_, g_, nm, emp, text, **k: sent.append(nm) or
                        type("T", (), {"id": "t1"})())
    g = st.get_group(gid)
    real = {e["name"]: e["id"] for e in st.employees()}
    m._dispatch_leader_batch(gid, {"ready": list(g["leader_plan"])}, real)
    assert sent, "带职务括号的名字没认出人来 ⇒ 整批 0 开工（第 7 轮那个 ✗）"
    assert sent[0] == "程序员", f"派发时该用**真名**（实际 {sent[0]!r}）"


def test_name_prefix_match_prefers_the_longest(tmp_path, monkeypatch):
    """前缀模糊匹配的**边界**：有"张三"也有"张三丰"时，"张三丰"不能被配成"张三" ✗。

    （真跑里这种"名字是别人前缀"的情况不常见，但一旦配错就是**派错人** ⇒ 必须取最长 ✓）
    """
    from app import main as m

    st = TeamStore(tmp_path)
    ids = {}
    for nm in ("张三", "张三丰"):
        ids[nm] = st.add_employee({"name": nm, "dept": "技术部", "role": "工程师",
                                   "persona": "干活", "mode": "expert"})["id"]
    g = st.create_group("同名测试群", list(ids.values()), mode="leader")
    st.leader_begin(g["id"], "做点小事", [
        {"name": "张三丰", "task": "实现 a.py", "output": "a.py", "depends_on": []},
    ])
    sent: list[str] = []
    monkeypatch.setattr(m, "_team_store", st)
    monkeypatch.setattr(m, "_dispatch_to_employee",
                        lambda gid_, g_, nm, emp, text, **k: sent.append(nm) or
                        type("T", (), {"id": "t1"})())
    gg = st.get_group(g["id"])
    m._dispatch_leader_batch(g["id"], {"ready": list(gg["leader_plan"])},
                             {e["name"]: e["id"] for e in st.employees()})
    assert sent == ["张三丰"], f"最长前缀优先 ✗（实际派给了 {sent}）"


def test_unknown_member_lists_who_is_available(tmp_path):
    """找不到人时**把本群成员列出来** ✓（原来只一句"不在本群"，用户没法接 ✓）。"""
    from app import main as m

    st, gid = _store(tmp_path)
    st.leader_begin(gid, "做点小事", [
        {"name": "查无此人", "task": "实现 a.py", "output": "a.py", "depends_on": []},
    ])
    import app.main as mm
    mm._team_store = st
    g = st.get_group(gid)
    m._dispatch_leader_batch(gid, {"ready": list(g["leader_plan"])}, {})
    texts = [x["text"] for x in st.feed(gid)]
    assert any("不在本群" in t and "本群成员" in t for t in texts), texts[-3:]


def test_delivery_format_gives_a_hint_per_section(tmp_path):
    """★★ 2026-10-06（评测台第五轮抓到的 ✗）：

    小节名只写「自测命令」⇒ 干活的人就**只写命令、不贴输出** ✗，
    而验收人一直要"完整真实运行输出" ⇒ **打回 3 次 ⇒ 失败** ✓✓（连着两轮都这样 ✓）。

    ⇒ 交付格式里每节**带一句说明** ✓（尤其「自测命令」要写明"连真实输出一起贴" ✓）。
    """
    st, gid = _store(tmp_path)
    st.leader_begin(gid, "写个 wordcount.py 并真跑", [
        {"name": "程序员", "task": "写 wordcount.py 并真跑自测", "output": "wordcount.py",
         "depends_on": []},
    ])
    g = st.get_group(gid)
    h = st.leader_handoff_text(g, g["leader_plan"][0])
    assert "自测命令（**必须连真实输出一起贴**" in h, h[:400]
    assert "别总结" in h, "没点明'别总结'（它就是这么栽的 ✓）"
    assert "改动文件（" in h and "用例清单（" in h, "其余小节也该各带一句说明 ✓"


def test_handoff_gives_a_fill_in_skeleton_from_the_start(tmp_path):
    """★★ 2026-10-06（**稳定性复跑抓到的** ✗）：模板要**一开始就给** ✓，不是等第 2 次打回 ✗。

    实测反复栽在同一处：它**确实跑了**测试 ✓（工作区里有 `.pyc`、`sample.txt` ✓），
    但交付里**只写"测试通过"** ✗，不贴终端原文 ⇒ 验收人连打回 3 次 ⇒ 整项失败 ✓✓
    （此前"照抄格式"要到**第 2 次打回**才出现 ✓ —— 那时候两轮的钱已经烧掉了 ✗）。
    """
    st, gid = _store(tmp_path)
    st.leader_begin(gid, "写个 wordcount.py 并真跑", [
        {"name": "程序员", "task": "写 wordcount.py 并真跑自测", "output": "wordcount.py",
         "depends_on": []},
    ])
    g = st.get_group(gid)
    h = st.leader_handoff_text(g, g["leader_plan"][0])
    assert "照这个骨架填" in h, "第一次派活就该给可照抄的骨架 ✓"
    assert "$ <你跑的那条命令>" in h and "原样出现的输出" in h, h[:600]
    assert "## 改动文件" in h, "骨架里要有改动文件那节 ✓"


def test_second_rewrite_gets_a_concrete_template(tmp_path):
    """★★ 2026-10-06（**评测台第二轮真跑抓到的浪费** ✗）：

    同一个"贴原始输出"的要求**连打回 3 次** ✓，每次整份重做 ✗（那一步 11 次调用 / 9 万 tok ✓）——
    因为三轮说的是**同一句话** ✓，模型始终没懂"到底怎么贴" ✓。

    ⇒ 第 2 轮起把话说到底：**只做这一件事** ✓ + **给出照抄的格式** ✓ + **明确可以省掉什么** ✓。
    """
    st, gid = _store(tmp_path)
    st.leader_begin(gid, "做个小工具", [
        {"name": "程序员", "task": "实现 wordcount.py", "output": "wordcount.py", "depends_on": []},
    ])
    st.leader_finish_item(gid, "程序员", "done", "交了（但没贴输出）", ["wordcount.py"])
    st.leader_reroll(gid, "程序员", "在交付摘要中粘贴自测命令的原样实际输出")
    g = st.get_group(gid)
    it = next(i for i in g["leader_plan"] if i["name"] == "程序员")
    first = st.leader_handoff_text(g, it)
    assert "上一次被打回的原因" in first
    assert "这一轮只做这一件事" not in first, "第 1 次打回还不该上'只做这一件事'（先给一次机会 ✓）"

    # 再打回一次（rounds 变 ≥1）⇒ 该出现格式模板 ✓
    st.leader_finish_item(gid, "程序员", "done", "又交了（还是没贴）", ["wordcount.py"])
    st.leader_reroll(gid, "程序员", "还是没贴原样输出")
    g = st.get_group(gid)
    it = next(i for i in g["leader_plan"] if i["name"] == "程序员")
    second = st.leader_handoff_text(g, it)
    assert "这一轮只做这一件事" in second, second[-600:]
    assert "$ <你跑的命令>" in second, "没给出照抄的格式（它就是因为不知道格式才反复栽 ✓）"
    assert "可以省掉" in second, "没告诉它'跑不起来的可以省'（否则它会去追不可能的东西 ✓）"


def test_duplicate_names_in_the_plan_get_suffixed(tmp_path):
    """★ 2026-10-06 真群实测（"稍复杂需求"那一轮）：**组长会给同一个人派两项** ✓

    后果很硬 ✗：分工单里两个同名项 ⇒ 按名字找人的地方全乱 ——
    那一轮出现"**前置没交付就开工**"的波次违规 ✗，程序员还被反复打回 3 次判失败 ✗。
    修法：重复的名字加序号（`名字·2`），依赖里的旧名字跟着改；派发边界认得后缀 ✓。
    """
    st, gid = _store(tmp_path)
    batch = st.leader_begin(gid, "做记账工具", [
        {"name": "架构师", "task": "定契约", "output": "docs/api.md", "depends_on": []},
        {"name": "程序员", "task": "实现数据层", "output": "store.py", "depends_on": ["架构师"]},
        {"name": "程序员", "task": "实现命令行", "output": "cli.py", "depends_on": ["程序员"]},
    ])
    names = [i["name"] for i in st.get_group(gid)["leader_plan"]]
    assert names == ["架构师", "程序员", "程序员·2"], names
    second = next(i for i in st.get_group(gid)["leader_plan"] if i["name"] == "程序员·2")
    assert second["depends_on"] == ["程序员"], second      # 依赖里的旧名字跟着改了
    # 只有架构师能先开工（程序员那两项都得等它）—— 这一步就是那一轮"波次违规"的正解
    assert [i["name"] for i in batch["ready"]] == ["架构师"], batch


def test_first_wave_is_only_the_ones_without_dependencies(tmp_path):
    st, gid = _store(tmp_path)
    out = st.leader_begin(gid, "做外卖小程序", PLAN)
    assert [i["name"] for i in out["ready"]] == ["架构师", "设计师"], out
    assert {w["name"]: w["after"] for w in out["waiting"]} == {"程序员": ["架构师"], "测试": ["架构师", "设计师"]}, out
    assert out["wave"] == 1 and out["total"] == 4 and out["done"] == 0


def test_dependent_is_dispatched_only_after_its_dependency_delivers(tmp_path):
    """★ 核心语义：架构师交付前，程序员**不在任何一批里**。"""
    st, gid = _store(tmp_path)
    st.leader_begin(gid, "做外卖小程序", PLAN)
    # 设计师先交付：程序员仍不该开工（它还等架构师）
    out = st.leader_finish_item(gid, "设计师", "done", "原型已出", ["docs/ui.md"])
    assert [i["name"] for i in out["ready"]] == [], out
    # 架构师交付：程序员与测试都该开工了
    out = st.leader_finish_item(gid, "架构师", "done", "契约见 docs/api.md", ["docs/api.md"])
    assert {i["name"] for i in out["ready"]} == {"程序员", "测试"}, out
    assert out["wave"] == 2 and out["done"] == 2


def test_handoff_carries_the_upstream_artifacts_not_just_a_promise(tmp_path):
    """★ 交接契约：下一棒的工作单里要有**前置的产物路径 + 摘要**，并明确"别猜，缺了就问"。"""
    st, gid = _store(tmp_path)
    st.leader_begin(gid, "做外卖小程序", PLAN)
    st.leader_finish_item(gid, "架构师", "done", "契约定稿：POST /orders 返回 id", ["docs/api.md"])
    g = st.get_group(gid)
    prog = next(i for i in g["leader_plan"] if i["name"] == "程序员")
    text = st.leader_handoff_text(g, prog)
    assert "docs/api.md" in text and "POST /orders" in text, text
    assert "别再猜" in text or "不要自己假设" in text, text
    assert "总目标" in text and "做外卖小程序" in text
    # 没有前置的那一项：给的是"边界"提示
    arch = next(i for i in g["leader_plan"] if i["name"] == "架构师")
    assert "边界" in st.leader_handoff_text(g, arch)


def test_failed_dependency_blocks_the_dependents(tmp_path):
    """前置失败 ⇒ 依赖它的活**不派**，并说清为什么（不派人去猜、去送人头）。"""
    st, gid = _store(tmp_path)
    st.leader_begin(gid, "做外卖小程序", PLAN)
    out = st.leader_finish_item(gid, "架构师", "failed", "接口没定下来（模型跑偏）")
    assert [i["name"] for i in out["ready"]] == [], out
    assert [b["name"] for b in out["blocked"]] == ["程序员", "测试"], out
    assert all("架构师" in b["why"] for b in out["blocked"]), out
    st2 = st.leader_state(gid)
    assert st2["failed"] == 1 and st2["blocked"] == 2, st2


def test_cycles_are_released_instead_of_deadlocking(tmp_path):
    """★ 前置成环（A 等 B、B 等 A）⇒ 全部放行 + 留痕，绝不让整批活永远停着。"""
    st, gid = _store(tmp_path)
    cyc = [
        {"name": "架构师", "task": "A", "output": "", "depends_on": ["设计师"]},
        {"name": "设计师", "task": "B", "output": "", "depends_on": ["架构师"]},
    ]
    out = st.leader_begin(gid, "环形依赖", cyc)
    assert {i["name"] for i in out["ready"]} == {"架构师", "设计师"}, out
    assert out.get("released"), "应当留下「放行了谁」的痕迹"


def test_unknown_or_self_dependencies_are_dropped(tmp_path):
    """前置写了个不存在的人 / 写了自己 ⇒ 直接去掉（否则永远等不到）。"""
    st, gid = _store(tmp_path)
    plan = [
        {"name": "架构师", "task": "A", "output": "", "depends_on": ["查无此人", "架构师"]},
        {"name": "程序员", "task": "B", "output": "", "depends_on": ["架构师"]},
    ]
    out = st.leader_begin(gid, "x", plan)
    g = st.get_group(gid)
    arch = next(i for i in g["leader_plan"] if i["name"] == "架构师")
    assert arch["depends_on"] == [], arch
    assert [i["name"] for i in out["ready"]] == ["架构师"], out      # 程序员仍要等


def test_all_done_is_reported(tmp_path):
    st, gid = _store(tmp_path)
    st.leader_begin(gid, "做外卖小程序", PLAN)
    st.leader_finish_item(gid, "架构师", "done", "契约", ["docs/api.md"])
    st.leader_finish_item(gid, "设计师", "done", "原型", ["docs/ui.md"])
    st.leader_finish_item(gid, "程序员", "done", "代码", [])
    out = st.leader_finish_item(gid, "测试", "done", "用例", [])
    assert out["done"] == 4 and out["total"] == 4 and out["ready"] == [], out
    assert out["wave"] == 3, out      # 第 1 批两人、第 2 批两人、第 3 批无人（用于收口判断）


def test_pending_task_ids_lists_only_running_items(tmp_path):
    """给"补认领"用的清单：只列**还在跑且已派出任务**的项（Jev 判定指出的头号漏洞）。"""
    st, gid = _store(tmp_path)
    st.leader_begin(gid, "做外卖小程序", PLAN)          # 架构师/设计师 → running
    assert st.leader_pending_task_ids(gid) == []        # 还没派任务（没有 task_id）
    st.leader_attach_task(gid, "架构师", "task_a")
    st.leader_attach_task(gid, "设计师", "task_b")
    assert {n for n, _ in st.leader_pending_task_ids(gid)} == {"架构师", "设计师"}
    st.leader_finish_item(gid, "设计师", "done", "原型", [])
    assert [n for n, _ in st.leader_pending_task_ids(gid)] == ["架构师"], "交付过的项不该再列出来"


def test_reconcile_advances_the_chain_when_the_watcher_is_lost(tmp_path, monkeypatch):
    """★ Jev 指出的头号漏洞：看门任务丢了（后端重启）⇒ 交付没人认领 ⇒ 整条链停住。

    现在由 `_reconcile_leader` 对账补上：任务已终态 ⇒ 当作交付，继续推进下一批（且幂等）。
    """
    from types import SimpleNamespace

    from app import main as m

    st, gid = _store(tmp_path)
    monkeypatch.setattr(m, "_team_store", st)
    st.leader_begin(gid, "做外卖小程序", PLAN)
    st.leader_attach_task(gid, "架构师", "task_a")
    st.leader_attach_task(gid, "设计师", "task_b")
    # 假装看门丢了：任务其实已经跑完，但没人认领
    monkeypatch.setitem(m.tasks, "task_a", SimpleNamespace(status="done"))
    monkeypatch.setattr(m.store, "read_events", lambda tid: [
        SimpleNamespace(type="message", payload={"role": "assistant",
                                                 "text": "契约已定稿", "attachments": ["docs/api.md"]})])
    sent: list[dict] = []

    def _fake_batch(gid2, batch, ids):
        sent.append(batch)
        return []

    monkeypatch.setattr(m, "_dispatch_leader_batch", _fake_batch)
    # 验收环节单独测（tests/test_leader_verify.py）；这里只验"对账能否把漏掉的交付补回来"
    monkeypatch.setattr(m, "_verify_delivery_sync",
                        lambda gid2, nm, ok, rep, att: m._advance_leader(gid2, nm, ok, rep, att))

    m._reconcile_leader(gid)
    texts = [x["text"] for x in st.feed(gid)]
    assert any("补认领" in t for t in texts), texts
    assert sent, "对账之后应当派出下一批"
    assert {i["name"] for i in sent[0]["ready"]} == {"程序员"}, sent[0]
    m._reconcile_leader(gid)          # 幂等：不会重复补
    assert sum(1 for t in st.feed(gid) if "补认领" in t["text"]) == 1
