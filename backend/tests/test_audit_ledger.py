# -*- coding: utf-8 -*-
"""★ 第 9 项 · 跨任务审计流水 —— 2026-10-07。

## 为什么要它（现状）

每个任务自己的事件流记得挺全 ✓ 但那是**按任务分的** ✗ ⇒ 用户想问的一个都答不上来：
  · "这一个月里 Agent 一共让我批过哪些命令？我批了些什么？"
  · "那几条「以后都别问」我**什么时候**、**为什么**放开的？"
  · "昨天它为什么突然停下来了？"
202 个任务，谁能一个个点进去翻 ✓

## 记什么（**只记"需要有人负责"的** ✓ 不是什么都记 ✗）

`approval`（批了什么命令）· `forever`（放权与收回）· `blocked`（被闸门挡下）· `task`（起止）
**不记**：对话内容 ✗ 工具输出 ✗ 文件内容 ✗ —— 那是任务事件流的活 ✓

## 本文件钉的四条

1. **真记得住** ✓（追加写 ✓ 重启还在 ✓）
2. **有上限** ✓（不设上限 = 磁盘炸弹 ✗）
3. **命令要打码** ✓（命令里带密钥是真实场景 ✓ 与全项目同一套打码 ✓）
4. **永不抛** ✗（记账失败绝不能影响用户正在干的事 ✓ —— 盘满了也要照常干活 ✓）
"""
from __future__ import annotations


import json
import pathlib

import pytest

from app import audit
from app import main as m
from app.providers.base import AssistantTurn, ModelProvider, ToolCall


class _HonestFailure(ModelProvider):
    """模型**如实汇报"没做成"** ✓ —— 专门用来钉住"账本里的状态必须是真值"✗。

    ★ 为什么要这么造：一律写 `done` 的写法在"正常交付"那条路上**看不出来** ✗
      ⇒ 只有走一路"如实汇报失败"才能把它照出来 ✓
      （而"昨天那次到底做成没有"正是这本账要回答的 ✓）
    """

    name = "honest-failure"

    async def next_turn(self, task_input, history, tools, on_delta=None):  # type: ignore[override]
        return AssistantTurn(tool_call=ToolCall(
            name="task_done", arguments={"message": "没做成", "outcome": "failed"}))


@pytest.fixture(autouse=True)
def _ledger(tmp_path):
    audit.bind(tmp_path / "data")
    yield
    audit.bind(pathlib.Path(m._DATA_DIR))


def test_records_and_reads_back():
    """★ 真记得住 ✓（追加写 ✓ 最新在前 ✓）。"""
    audit.record("approval", decision="once", task="task_x", command="rm -rf build")
    audit.record("approval", decision="deny", task="task_y", command="format D:")
    rows = audit.tail(10)
    assert len(rows) == 2
    assert rows[0]["decision"] == "deny", "最新的没排在最前面 ✗（用户想看的就是最近的 ✓）"
    assert rows[0]["ts"] and rows[0]["kind"] == "approval"


def test_filter_by_kind():
    audit.record("approval", decision="once")
    audit.record("blocked", gate="单次花费上限")
    audit.record("approval", decision="all")
    assert len(audit.tail(10, kind="approval")) == 2
    assert len(audit.tail(10, kind="blocked")) == 1
    assert audit.tail(10, kind="不存在的类别") == []


def test_secrets_in_commands_are_redacted():
    """★★ **命令里的密钥必须打码** ✗ —— 而这本账是给用户翻的 ✓
    （`curl -H "Authorization: Bearer sk-…"` 这种命令真实存在 ✓ 原样记下来 = 又开一个泄漏面 ✓）。"""
    audit.record("approval", decision="once", task="t", command='curl -H "Authorization: Bearer sk-abcdef1234567890abcdef" http://x')
    row = audit.tail(1)[0]
    assert "sk-abcdef1234567890abcdef" not in json.dumps(row, ensure_ascii=False), \
        f"密钥原样进了审计账 ✗：{row}"
    assert "curl" in str(row.get("command")), "把整条命令都吃掉 ⇒ 那这本账就没用了 ✓"


def test_never_raises_even_when_the_disk_is_broken(monkeypatch, tmp_path):
    """★★ **记账失败绝不能影响用户正在干的事** ✓ —— 回滚实验：让它抛 ⇒ 本组必红 ✓
    （盘满了/权限没了 ⇒ 该继续干活 ✓ 而不是让任务崩掉 ✓）"""
    audit.bind(tmp_path / "data")
    monkeypatch.setattr(pathlib.Path, "open", lambda *a, **k: (_ for _ in ()).throw(OSError("盘满了")))
    audit.record("approval", decision="once")          # ← 不许抛 ✗
    monkeypatch.undo()
    audit.bind(tmp_path / "data")


def test_corrupt_lines_do_not_break_the_ledger(tmp_path):
    """★ 坏行跳过 ✓ —— 一行坏了不能整本读不出来 ✓（用户翻账时最怕这个 ✓）。"""
    p = tmp_path / "data" / "audit.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"ts":"x","kind":"approval"}\n这不是 JSON\n{"ts":"y","kind":"blocked"}\n', "utf-8")
    rows = audit.tail(10)
    assert len(rows) == 2, rows


def test_entries_are_capped(tmp_path, monkeypatch):
    """★ **有上限** ✓ —— 不设上限就是个磁盘炸弹 ✗。"""
    monkeypatch.setattr(audit, "MAX_ENTRIES", 50)
    audit.bind(tmp_path / "data")
    for i in range(200):
        audit.record("approval", decision="once", task=f"t{i}")
    rows = audit.tail(1000)
    assert len(rows) <= 60, f"没截 ⇒ 攒了 {len(rows)} 条 ✗（上限是 50 ✓）"
    assert rows[0]["task"] == "t199", "截错了头 ⇒ 把最新的丢了 ✗"


def test_stats_are_readable():
    audit.record("approval", decision="once")
    audit.record("blocked", gate="x")
    s = audit.stats()
    assert s["count"] >= 2 and s["by_kind"].get("approval") == 1 and s["path"]


# ═══ 接在真地方（"写了不等于接上了"✓）═══

def test_approval_decisions_are_recorded():
    """★★ 审批决议是**最该留痕**的一件事 ✓ —— 端点、群里的审批卡片**都走 `_do_approve`** ✓
    ⇒ 只接那一处就够 ✓ 而且接在别处反而会漏 ✓（本仓"一处口径"的规矩 ✓）

    ★ 2026-10-07 升级（我自己交代过的短板 ✓）：
      这条原来**只"看代码"** ✗（grep 到那行字就算过 ✓）—— 它钉得住"别被人删掉"✓
      但**证明不了"运行时真的会写"** ✗
      ⇒ 现在**真调一次 `_do_approve`** ✓（起一个等待中的审批 ✓ 走完真路径 ✓ 再看账本 ✓）
    """
    import asyncio as _aio
    from types import SimpleNamespace as _NS

    src = pathlib.Path(m.__file__).read_text("utf-8")
    i = src.find("def _do_approve")
    assert i > 0
    seg = src[i : i + 3000]
    assert 'audit.record("approval"' in seg, "审批决议没进总账 ✗（那用户还是查不到'我批过什么'✓）"

    # ★ 真跑一遍：造一个"正在等审批"的任务 ✓ 塞进真那份待批表 ✓
    tid, cid = "task_真跑一次", "call_真跑一次"
    loop = _aio.new_event_loop()
    try:
        m.approval._pending[(tid, cid)] = loop.create_future()
    finally:
        loop.close()
    m.approval.note_command(tid, cid, "Remove-Item D:\\旧备份 -Recurse")
    m.tasks[tid] = _NS(id=tid, title="清旧备份", status="waiting_approval",
                       updated_at="", created_at="", project_id=None)
    try:
        m._do_approve(tid, cid, "once")           # ← 走真路径 ✓
        rows = [r for r in audit.tail(20) if r.get("task") == tid]
        assert rows, "真调了一次审批，账本里却**一条都没有** ✗（那这条功能就是空转 ✓）"
        row = rows[0]
        assert row["kind"] == "approval" and row["decision"] == "once", row
        assert "旧备份" in str(row.get("command")), \
            f"账本里没有那条命令 ✗（用户实测就是这个洞 ✓）：{row}"
    finally:
        m.tasks.pop(tid, None)
        m.approval._pending.pop((tid, cid), None)
        m.approval._cmds.pop((tid, cid), None)


def test_blocked_gates_are_recorded():
    """★ 被闸门挡下也要留痕 ✓ ——"昨天它为什么突然停下来了？"✓"""
    src = pathlib.Path(m.__file__).read_text("utf-8")
    assert 'audit.record("blocked"' in src, "被挡下没留痕 ✗"
    assert src.count('audit.record("blocked"') >= 2, "两道闸（单次上限 / Key 上限）都要记 ✓"


def test_endpoint_and_binding_exist():
    src = pathlib.Path(m.__file__).read_text("utf-8")
    assert '@app.get("/api/v1/audit")' in src, "没有查询接口 ✗"
    assert "audit.bind(_DATA_DIR)" in src, "没绑定数据目录 ⇒ 账写不进去 ✗"


def test_it_does_not_record_conversation_content():
    """★ **只记"需要有人负责"的** ✓ —— 对话内容/工具输出不进这本账 ✗
    （那是任务事件流的活 ✓ 混进来既臃肿又多一个泄漏面 ✓）。"""
    src = pathlib.Path(audit.__file__).read_text("utf-8")
    for bad in ("messages", "content=", "output=", "history"):
        assert f'"{bad}"' not in src, f"审计账里出现了「{bad}」⇒ 可能会把对话/输出记进来 ✗"


def test_the_command_comes_from_the_server_not_the_client():
    """★★ **账本里那条命令必须由服务端记** ✓ —— 用户实测揪出来的洞：

    他真点了一次审批 ✓ 账本确实多了一行 ✓ **但只有"批过一次"，没有那条命令** ✗
    （界面压根没把命令传上来 ✗ 而"批的是哪条命令"正是这本账存在的理由 ✓）

    ★ 两个方向都要钉死：
      ① 服务端记过 ⇒ 账本里**必须有**那条命令 ✓（回滚实验：去掉 note_command 那行 ⇒ 本条必红 ✓）
      ② 客户端**乱传**一条 ⇒ 覆盖不了服务端那份 ✗（审计内容不能由客户端说了算 ✓）
    """
    import asyncio
    import json as _json

    from app import approval as AP

    mgr = AP.ApprovalManager() if hasattr(AP, "ApprovalManager") else None
    assert mgr is not None, "没找到 ApprovalManager ⇒ 这条测试没在测东西 ✗"
    mgr.note_command("task_z", "call_z", "rm -rf D:\\旧备份")
    assert "旧备份" in mgr.command_of("task_z", "call_z"), "服务端记不下来 ⇒ 账本永远缺这条 ✗"

    # ② 模拟 `_do_approve` 的取法（服务端优先 ✓ 客户端只是提示 ✓）
    got = mgr.command_of("task_z", "call_z") or "客户端乱传的"
    assert "旧备份" in got and "客户端乱传" not in got, f"客户端能覆盖服务端那份 ✗：{got}"
    # ① 真走一遍记账（用真函数 ✓ 不是看代码 ✓）
    audit.record("approval", decision="once", task="task_z", command=got)
    row = audit.tail(1)[0]
    assert "旧备份" in _json.dumps(row, ensure_ascii=False), f"账本里没有那条命令 ✗：{row}"
    # ③ 没记过命令时也不能崩 ✓（返回空串 ✓）
    assert mgr.command_of("task_没有", "call_没有") == ""
    assert asyncio is not None


def test_the_loop_hands_the_command_to_the_server():
    """★ 光有 `note_command` 不算 ✓ —— **loop 得真去调它** ✓（"写了不等于接上了"✓）。"""
    from app import loop as loop_mod
    src = pathlib.Path(loop_mod.__file__).read_text("utf-8")
    assert "note_command(self.task.id, call_id" in src, "loop 没把命令交给服务端 ✗（那账本还是缺这条 ✓）"


def test_resolve_path_clears_the_stored_command():
    """★ 收尾要清 ✓ —— 不然这份映射会随任务数无限涨 ✗（内存里的东西都得有主 ✓）。"""
    src = pathlib.Path(audit.__file__).resolve().parents[1] / "app" / "approval.py"
    txt = src.read_text("utf-8")
    i = txt.find("async def wait_decision")
    seg = txt[i : i + 700]
    assert "_cmds.pop(" in seg, "等待结束后没清那份命令 ⇒ 会一直堆着 ✗"


def test_clearing_data_is_recorded():
    """★★ **清空数据必须留痕** ✗ —— 用户当天亲手演示了为什么：

    他点了「一键清干净」✓ 202 个任务 → 4 个 ✓ 而账本里**一个字都没有** ✗
    ⇒ 事后谁也说不清"数据是什么时候没的、谁清的、挪到哪去了" ✓
    （这功能其实是**挪到备份目录**不是真删 ✓ 所以记账里带上**备份路径** ⇒
      这本账当场就变成"找回东西的线索" ✓ 这是它真正的价值 ✓）
    """
    src = pathlib.Path(m.__file__).read_text("utf-8")
    assert 'audit.record("cleared"' in src, "清空数据没进审计账 ✗"
    i = src.find('audit.record("cleared"')
    seg = src[max(0, i - 400) : i + 300]
    assert "backup" in seg, "记账里没带**备份路径** ✗ —— 那这本账就只是'东西没了'，找不回来 ✓"
    # 界面上也得能筛出来 ✓（与写入点成对 ✓ 见 test_every_filter_option_has_a_writer ✓）
    tsx = (pathlib.Path(m.__file__).resolve().parents[2]
           / "frontend" / "src" / "components" / "SettingsPanel.tsx").read_text("utf-8")
    assert '<option value="cleared">' in tsx, "界面筛不出'清空数据'✗"


def test_deleting_a_task_moves_it_aside_and_records_it():
    """★★ 侧栏那个"叉"**不许再做不可逆销毁** ✗ —— 用户真机上就是这么没的 198 个任务：

    他的原话："我以为那个叉只是表面删除" ✓ —— 而它当时是 `rmtree` ✓
    **永久 · 无确认 · 无备份 · 无留痕** ✗ 连回收站里都没有 ✓
    ⇒ 现在与「一键清干净」**一个做法**：挪到 `_deleted_<时间戳>/` ✓（同盘 rename 瞬时 ✓ 可回滚 ✓）

    这条测试**真造一个任务目录再真删一次** ✓（不碰真数据 ✓ 用临时目录 ✓）：
      ① 删完 ⇒ **原目录没了** ✓（不然界面上删了、盘上还在 ✗ 那是更早那次的坑 ✓）
      ② 但 ⇒ **回收目录里有** ✓（能挪回来 ✓）
      ③ 记账里带上 **task_id 与挪到哪去了** ✓（账本要能当"找回东西的线索"✓）
    """
    import tempfile

    from app.store import FsStore as Store

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="del_test_"))
    try:
        st = Store(tmp)          # ★ 传**数据目录** ✓（FsStore 自己会拼 `tasks/` ✓ 传 tmp/tasks 会变成 tmp/tasks/tasks ✗ 本班实测）
        d = (tmp / "tasks" / "task_要删的")
        d.mkdir(parents=True)
        (d / "events.jsonl").write_text('{"a":1}\n' * 10, "utf-8")
        trash = tmp / "_deleted_"

        info = st.delete_task_files("task_要删的", trash=trash, stamp="20261007-2100")
        assert info["deleted"] is True and info["trashed"] is True, info
        assert info["bytes"] > 0, "没报出释放了多少字节 ✗"
        assert not d.exists(), "原目录还在 ⇒ 界面上删了、盘上留着 ✗（更早那次的坑 ✓）"
        assert (trash / "20261007-2100" / "task_要删的" / "events.jsonl").exists(), \
            "回收目录里没有 ⇒ 那就是**真销毁**了 ✗（用户就是这么没的 198 个任务 ✓）"

        # ② 账本那条（真走 `audit.record` ✓）
        audit.record("deleted_task", task_id="task_要删的", bytes=info["bytes"],
                     trashed=True, where=str(info["path"])[:300])
        row = audit.tail(1)[0]
        assert row["task_id"] == "task_要删的" and row["trashed"] is True, row
        # ★ 2026-10-09（CI 抓的 ✗）：原来直接比字符串前缀 ✗
        #   CI 的 Windows 上 str(WindowsPath(tmp)) 会给 **8.3 短名**（RUNNER~1 ✗）
        #   而账本里记的是**长名**（runneradmin ✗）⇒ 前缀比不过 ⇒ 挂 ✓（本地永远看不到 ✓）
        #   ⇒ 两边都 resolve() 规范化后再比 ✓（同一个目录、写法不同而已 ✓）
        assert str(pathlib.Path(trash).resolve()) in str(pathlib.Path(row["where"]).resolve()), \
            f"账本没记'挪到哪去了' ⇒ 找不回来 ✗：{row}"

        # ③ 想找回 ⇒ 挪回去就行 ✓（这条是在证明"可回滚"不是嘴上说说 ✓）
        back = tmp / "tasks" / "task_要删的"
        (trash / "20261007-2100" / "task_要删的").rename(back)
        assert (back / "events.jsonl").exists(), "挪不回来 ⇒ 那不叫回收 ✓"
    finally:
        import shutil as _sh
        _sh.rmtree(tmp, ignore_errors=True)


def test_deleting_a_task_can_be_filtered_in_the_ui():
    """★★ **账本记了，界面就得能筛出来** ✓ —— 这是我漏的一小步 ✗

    账本**早就在写** `deleted_task` 了 ✓（谁什么时候删了哪个 ✓ 挪到哪去了 ✓）——
    可界面的筛选项里**没有这一项** ✗ ⇒ 用户**筛不出来** ✓（记了等于白记 ✓）。
    ★ 与 `test_every_filter_option_has_a_writer` 是**一体两面**：
      那条管"**能筛的必须真会写**" ✓ 这条管"**真写了的必须能筛**" ✓
      （少了任何一面，那本账就有一块是用户摸不到的 ✓）

    回滚实验：把这一项从界面上删掉 ⇒ 本条必红 ✓（见 scripts/redgreen_check.py）
    """
    tsx = (pathlib.Path(m.__file__).resolve().parents[2]
           / "frontend" / "src" / "components" / "SettingsPanel.tsx").read_text("utf-8")
    assert '<option value="deleted_task">' in tsx, \
        "界面筛不出「删任务」✗（账本里明明记着 ✓ 用户却查不到 ✓）"


def test_starting_a_task_is_recorded(monkeypatch):
    """★★ A-4：**任务起止**的"起" ✓ —— 这本账此前没有这一条 ✗

    "这个任务是什么时候开的"是它最该回答的问题之一 ✓
    ★ **真调一次 `_launch_task`** ✓（`_start_run` 打桩 ⇒ 不真跑模型、不联网 ✓）——
      "写了不等于接上了"✓ 本仓栽过多次的老教训 ✓
    回滚实验：把那行记账去掉 ⇒ 本条必红 ✓
    """
    started: list[str] = []
    monkeypatch.setattr(m, "_start_run", lambda task, text, **kw: started.append(task.id))
    task = m._launch_task("查一下下载目录里最大的十个文件")
    try:
        rows = [r for r in audit.tail(20) if r.get("kind") == "task"]
        assert rows, "真开了一个任务，账本里**一条都没有** ✗（那这条功能就是空转 ✓）"
        row = rows[0]
        assert row["phase"] == "start" and row["task"] == task.id, row
        assert "下载目录" in str(row.get("title")), \
            f"账本里认不出是哪个任务 ✗（只给 id 的话用户还得去别处对 ✓）：{row}"
        assert started == [task.id], "任务没真启动 ⇒ 这条测试没在测东西 ✗"
    finally:
        m.tasks.pop(task.id, None)
        m._save_index()


def _run_once(tmp_path, provider):
    """起一个**真的 TaskRun** 并跑完它 ✓ —— 只够这条测试用 ✓

    ★ 为什么不 `from tests.test_deliverable_guards import _mk_run` ✗ ——
      那写法**在红绿副本里会挂** ✓ 实测（完整门 7 组报"恢复跑=rc=1"✓）：
        本机 site-packages 里被某个包（`gruut` ✓ MeloTTS 那系的依赖）塞了一个
        **顶层 `tests` 包** ✗ ⇒ 而本仓的 `backend/tests` **没有 `__init__.py`**（命名空间包 ✓）
        ⇒ 命名空间包**永远输给**真包 ✓ ⇒ 副本里 `import tests.xxx` 当场 ModuleNotFoundError ✓
      ⇒ 自包含最稳 ✓（测试不该依赖"谁先被 import"这种环境细节 ✓）
    """
    from app.approval import ApprovalManager
    from app.bus import EventBus
    from app.executors.local import LocalExecutor
    from app.loop import TaskRun
    from app.schemas import TaskSummary
    from app.store import FsStore
    from types import SimpleNamespace

    task = TaskSummary(
        id="task_20261008_end1",           # 契约：task_<8位日期>_<4位[a-z0-9]>
        title="任务起止自检",
        created_at="2026-10-08T00:00:00Z",
        updated_at="2026-10-08T00:00:00Z",
    )
    ex_cfg = SimpleNamespace(
        workspace_root=tmp_path / "ws", timeout_seconds=5.0, shell="", search_url="",
        browser_channel="", comfyui_url="", image_checkpoint="", allowed_dirs=[],
    )
    return TaskRun(
        task, "随便做点什么", store=FsStore(tmp_path / "data"), bus=EventBus(),
        provider=provider, executor=LocalExecutor(ex_cfg), approval=ApprovalManager(),
        tools=[], max_iterations=3, timeout_seconds=5.0, approval_required=[],
        on_finish=lambda r: None,
    )


def test_the_end_of_a_task_is_recorded_with_the_real_status(tmp_path):
    """★★ A-4：**任务起止**的"止" ✓ —— 状态必须是**当场那个真值** ✓ 不是猜的 ✗

    ★ 这条专门走"**模型如实汇报没做成**"那一路（`outcome=failed` ✓）：
      一律写 `done` 的写法在这儿**必红** ✓
      —— 而"昨天那次到底做成没有"正是这本账存在的理由 ✓
    回滚实验：把状态写死成 `done` ⇒ 本条必红 ✓
    """
    import asyncio

    run = _run_once(tmp_path, _HonestFailure())
    asyncio.run(run._run())
    assert run.task.status == "failed", "任务自己就没落成 failed ⇒ 这条测试没在测东西 ✗"
    rows = [r for r in audit.tail(20) if r.get("kind") == "task"]
    assert rows, "任务跑完了，账本里没有「任务起止」✗（那这本账还是答不上'什么时候完的'✓）"
    row = rows[0]
    assert row["phase"] == "done" and row["task"] == run.task.id, row
    assert row["status"] == "failed", \
        f"账本里的状态与任务真实状态**不一致** ✗（说好的别猜 ✗）：{row}"


def test_the_ui_can_filter_task_start_and_end():
    """★ 账本真写了 ⇒ 界面就得能筛 ✓（与「删任务」那一小步同一条规矩 ✓）

    ★ 一项一条 `task` + `phase`（start/done）✓ —— **不拆两个 kind** ✗
      （拆了界面就得开两个选项 ✓ 而且两边要一直对齐 ✓ 迟早打架 ✓）
    """
    tsx = (pathlib.Path(m.__file__).resolve().parents[2]
           / "frontend" / "src" / "components" / "SettingsPanel.tsx").read_text("utf-8")
    assert '<option value="task">' in tsx, \
        "界面筛不出「任务起止」✗（账本里记着 ✓ 用户却查不到 ✓）"


def test_the_trash_prune_can_be_filtered_in_the_ui():
    """★ **回收站自动清理**（`trash_pruned`）也得能筛出来 ✓ —— 最后一个同类小洞 ✓

    它是"真删掉东西"里最容易被忽略的一种 ✓：
    用户什么都没点 ✓ 是**删任务时顺手**把回收站里最旧的几次清掉了 ✓
    所以账本里必须留痕 ✓ **而且得让用户查得到** ✓（记了他摸不到 = 白记 ✓）

    ★ 与 `test_every_filter_option_has_a_writer` 一体两面 ✓（见上面那条的注释 ✓）
    回滚实验：把这一项从界面上拿掉 ⇒ 本条必红 ✓（见 scripts/redgreen_check.py）
    """
    tsx = (pathlib.Path(m.__file__).resolve().parents[2]
           / "frontend" / "src" / "components" / "SettingsPanel.tsx").read_text("utf-8")
    assert '<option value="trash_pruned">' in tsx, \
        "界面筛不出「回收站清理」✗（账本里明明记着 ✓ 用户却查不到 ✓）"


def test_the_endpoint_uses_the_trash_not_rmtree():
    """★ 端点也得真接上 ✓（"写了不等于接上了"✓ 本仓老教训 ✓）+ **必须留痕** ✓。"""
    src = pathlib.Path(m.__file__).read_text("utf-8")
    i = src.find("async def delete_task")
    assert i > 0, "找不到删除任务那个端点 ✗"
    seg = src[i : i + 2600]
    assert "trash=" in seg, "端点没用回收目录 ⇒ 还是真删 ✗"
    assert 'audit.record("deleted_task"' in seg, "删除任务没留痕 ✗（谁什么时候删了哪个查不到 ✓）"


def test_it_never_deletes_outside_the_tasks_dir():
    """★ 老护栏**一条都不许丢** ✗ —— 改动删除逻辑最容易把护栏改没 ✓
    （task_id 里的路径分隔符 / `..` 必须一律拒绝 ✓）。"""
    import tempfile

    from app.store import FsStore as Store

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="del_guard_"))
    try:
        st = Store(tmp)          # ★ 传**数据目录** ✓（FsStore 自己会拼 `tasks/` ✓ 传 tmp/tasks 会变成 tmp/tasks/tasks ✗ 本班实测）
        (tmp / "tasks").mkdir(parents=True, exist_ok=True)
        (tmp / "外面的东西").mkdir(parents=True, exist_ok=True)
        for bad in ("../外面的东西", "..\\外面的东西", "..", ".", ""):
            try:
                st.delete_task_files(bad, trash=tmp / "_deleted_", stamp="s")
            except ValueError:
                pass                       # 拒绝 = 对 ✓
            else:
                raise AssertionError(f"非法 task_id 竟然没被拒 ✗：{bad!r}")
        assert (tmp / "外面的东西").exists(), "顺着 `..` 删到外面去了 ✗✗"
    finally:
        import shutil as _sh
        _sh.rmtree(tmp, ignore_errors=True)


def test_the_sidebar_asks_before_deleting():
    """★★ **删之前必须问一句** ✓ —— 用户就是这么没的 198 个任务：

    他的原话："我以为那个叉只是表面删除" ✓ 而那时它**当场永久销毁** ✗
    后端现在改成可回滚了 ✓ 但"**不打招呼就动手**"还是不对 ✓ ——
    用户有权知道"删的是什么、能不能找回"再决定 ✓

    ★ 这句确认里三样缺一不可：**删哪个** ✓ **会连什么一起删** ✓ **能不能找回** ✓
      （只写"确定删除吗？"⇒ 等于没问 ✓ 他上次就是没意识到连工作区一起没的 ✓）
    """
    src = (pathlib.Path(m.__file__).resolve().parents[2]
           / "frontend" / "src" / "components" / "Sidebar.tsx").read_text("utf-8")
    i = src.find("taskitem-del")
    assert i > 0, "找不到那个删除按钮 ✗"
    seg = src[i : i + 1500]
    assert "window.confirm(" in seg, \
        "点那个叉**直接就删** ✗（用户上次就是这么一次点掉 198 个任务的 ✓ 至少要问一句 ✓）"
    assert "onDeleteTask(t.id)" in seg and "if (ok)" in seg, "确认的结果没真正决定删不删 ✗"
    assert "工作区" in seg, "没写清**会连工作区文件一起删** ✗（他上次就没意识到 ✓）"
    assert "_deleted_" in seg, "没告诉他**能找回来** ✗ ⇒ 他会以为还是永久销毁、不敢删 ✓"


def test_every_filter_option_has_a_writer():
    """★★ **界面上的每个筛选项，后端都得真会写它** ✗ —— 这条是自查抓出来的洞 ✓

    我原来在界面上列了「放权/收回」（kind=forever）与「任务起止」（kind=task）✗
    而后端**只写** `approval` 与 `blocked` ✓ ⇒ 选那两项**永远是空的** ✓
    界面还会说"还没有记录"✗ —— 那是**假话** ✓（不是"没有记录"，是"根本没这种东西"✓）

    ⇒ 这条测试把两者**对上** ✓（以后谁再加筛选项，就得同时加写入点 ✓ 或者别加 ✓）
    """
    import re
    root = pathlib.Path(m.__file__).resolve().parents[2]
    tsx = (root / "frontend" / "src" / "components" / "SettingsPanel.tsx").read_text("utf-8")
    offered = set(re.findall(r'<option value="([a-z_]+)">[^<]*</option>\s*(?=\n\s*<option|\n\s*</select>)', tsx))
    # 上面那个正则只圈在审计那块里 ✓ 兜底：直接从审计卡片附近取
    seg = tsx[tsx.find("审计流水（跨任务）") :][:2600]
    offered = set(re.findall(r'<option value="([a-z_]+)"', seg))
    assert offered, "没找到审计的筛选项 ⇒ 这条测试没在测东西 ✗"
    written = set(re.findall(r'audit\.record\("([a-z_]+)"', pathlib.Path(m.__file__).read_text("utf-8")))
    assert written, "没找到任何写入点 ✗"
    dead = offered - written
    assert not dead, (f"界面给了筛选项但**后端从来不写**这些 kind：{sorted(dead)} ✗"
                      f"（选了永远是空的 ⇒ 那是界面骗人 ✓）已写入的只有：{sorted(written)}")
