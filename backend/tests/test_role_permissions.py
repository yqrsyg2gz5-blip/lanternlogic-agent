# -*- coding: utf-8 -*-
"""★「按角色限权」—— 2026-10-07 用户点名要的那条：

> "**按角色限权**（测试只能看/跑 ✗ 不能改 ✓；现在审批**只按命令判** ✗ 不按"谁"判 ✗）"

## 要解决的真问题

审批**只看命令本身** ✓ —— 同一句 `rm -rf dist`，「程序员」跑和「测试工程师」跑待遇一样 ✗。
可在团队里这两件事**不是一回事**：
  · 程序员删自己刚生成的构建产物 = 正常干活 ✓
  · **测试**去改被测代码 = **把"独立验证"这件事本身废掉了** ✗✗
    （他刚亲手改了被验的东西，然后说"我验过了" ✓ 这个"验过"还有什么意义 ✓）

## 本文件钉住的五条

1. **默认 = 现状** ✓✓ —— 没身份 / 认不出的角色 ⇒ **一律不受限** ✓
   （这条保证了这个功能**不可能**弄坏任何现有行为 ✓）
2. **只读角色拦得住"改"** ✓（覆盖已有文件 ✗ / 删移装包越界 ✗）
3. **只读角色仍然能干活** ✓（读 ✓ 跑测试 ✓ **新建文件** ✓ —— 测试要能写自己的用例 ✓）
   ★ 这条最容易被做过头：一刀切成"什么都不能写" ⇒ 角色直接废掉 ⇒ **功能做成摆设** ✓
4. **拦住时要说人话** ✓（模型收到"换成程序员身份"✓ 事件流里用户也看得见 ✓）
5. **"全部允许"盖不过角色** ✗ —— 那是"这条命令信不信得过" ✓ 角色管的是"谁" ✓ 两回事 ✓
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main as m
from app import permissions as P
from app.approval import ApprovalManager, Verdict
from app.bus import EventBus
from app.executors.local import LocalExecutor
from app.loop import TaskRun
from app.providers.base import AssistantTurn, ModelProvider, ToolCall
from app.schemas import TaskSummary
from app.store import FsStore

READONLY_ROLE = "测试工程师"


# ═══ ① 默认 = 现状（最要紧的一条 ✓）═══

@pytest.mark.parametrize("role", ["", "   ", "默认助手", "某个没见过的角色", "程序员", "文案策划", "设计师"])
def test_unknown_or_normal_roles_are_unrestricted(role):
    """★★ **认不出来 ⇒ 不受限** ✓ —— 这是整个功能"不可能弄坏现有行为"的保证 ✓。

    回滚实验：把 `profile_of` 改成"未知角色也算只读" ⇒ 本组立刻变红 ✓
    （那种改法会让**所有单聊任务突然不能写文件** ✗✗ —— 正是最坏的那种回归 ✓）。
    """
    assert P.profile_of(role) == P.FULL, f"「{role}」被限制住了 ✗（默认必须等于现状 ✓）"
    assert P.is_readonly(role) is False


def test_only_the_three_gatekeeper_roles_are_readonly():
    """只读只给**把关类**三个角色 ✓ —— 多收一个就是白白废掉一个角色的能力 ✗。"""
    assert {r for r, p in P.ROLE_PROFILE.items() if p == P.READONLY} == \
        {"测试工程师", "安全顾问", "审校编辑"}, P.ROLE_PROFILE
    # 干活的那几个角色一个都不许被限 ✗
    for r in ("程序员", "架构师", "运维工程师", "文案策划", "设计师", "通用助理", "项目经理"):
        assert not P.is_readonly(r), f"「{r}」是干活的角色，不该被限 ✗"


# ═══ ② 工具级：改**已有**文件 ⇒ 拒；新建 ⇒ 放行 ═══

def test_readonly_role_cannot_overwrite_an_existing_file():
    why = P.block_reason(READONLY_ROLE, "file_write",
                         {"path": "src/app.py", "content": "x"}, target_exists=lambda p: True)
    assert why and "不能覆盖/修改已有文件" in why, why
    assert "程序员" in why, "没说清「那该怎么办」✗（挡住却不说路 = 让模型瞎试 ✓）"


def test_readonly_role_may_create_a_new_file():
    """★ 测试要能**写自己的用例** ✓ —— 一刀切禁写 = 把角色做成摆设 ✗。"""
    assert P.block_reason(READONLY_ROLE, "file_write",
                          {"path": "tests/test_new.py", "content": "x"},
                          target_exists=lambda p: False) is None


def test_readonly_role_refuses_when_target_existence_is_unknown():
    """★ 核不实（越界/变量路径/符号链接逃逸）⇒ **fail-closed 拦下** ✓
    —— 与审批层同一条口径（说不清就当"会改到东西"✓）。"""
    why = P.block_reason(READONLY_ROLE, "file_write", {"path": "$HOME/x"}, target_exists=lambda p: None)
    assert why, "核不实竟然放行了 ✗（那是这条线唯一的漏洞面 ✓）"


def test_non_writing_tools_are_never_touched():
    """★ 只读角色**只**被拦"写"这一件 ✓ —— 读/列目录/跑/搜/联网一律不碰 ✓。"""
    for tool in ("file_read", "list_dir", "kb_search", "web_search", "web_fetch",
                 "shell_exec", "browser_navigate", "code_check", "update_plan", "speak"):
        assert P.block_reason(READONLY_ROLE, tool, {"path": "x"}, target_exists=lambda p: True) is None, tool


# ═══ ③ 命令级：复用审批层的结论（不自己另写一套 ✗）═══

def test_shell_write_commands_are_denied_for_readonly():
    v = Verdict("删除类命令", "rm:x", "ask")
    why = P.shell_block_reason(READONLY_ROLE, v)
    assert why and "角色权限" in why, why


def test_pure_readonly_out_of_bounds_is_allowed():
    """★ 判"纯只读越界"的（`ls` 工作区外面）本来就是"看" ✓ 放行 ✓。"""
    assert P.shell_block_reason(READONLY_ROLE, Verdict("越界只读", "ro:x", "allow_readonly")) is None


def test_allow_all_cannot_override_the_role():
    """★★ 「本任务全部允许」是**对某条命令的信任** ✓ 角色管的是**谁** ✗ —— 前者盖不过后者 ✓。

    回滚实验：让 `allow_all` 直接放行 ⇒ 本组变红 ✓
    （那等于"测试点一下全部允许就能改代码"✗ 限权当场失效 ✓）
    """
    for action in ("allow_all", "allow_forever"):
        why = P.shell_block_reason(READONLY_ROLE, Verdict("用户放行过", "k", action))
        assert why, f"{action} 竟然盖过了角色限制 ✗（那限权就被一个按钮废掉了 ✓）"


def test_clean_command_passes():
    assert P.shell_block_reason(READONLY_ROLE, None) is None      # 审批层说没事 ⇒ 放行（跑测试就是这类 ✓）


# ═══ ④ 接线：真跑一遍 loop，看它**在工具执行前**就拦住了 ═══

class _ToolProvider(ModelProvider):
    """第一轮就想调 file_write（覆盖已有文件）——复刻"测试去改被测代码"那个动作 ✓。"""

    name = "tool-caller"

    def __init__(self) -> None:
        self.calls = 0
        self.total_usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0, "estimated": False}

    async def next_turn(self, task_input, history, tools, on_delta=None):  # type: ignore[override]
        self.calls += 1
        self.total_usage["calls"] += 1
        if self.calls == 1:
            # ★ 必须是**真的 ToolCall 数据类** ✓ —— 第一版我随手写了个 dict，
            #   loop 那边 `turn.tool_call.name` 直接取不到 ⇒ **工具一次都没被派发** ✗
            #   于是"只读角色没写成文件"那条**假绿**了 ✗（全靠对照组抓出来 ✓）。
            return AssistantTurn(tool_call=ToolCall(name="file_write",
                                                    arguments={"path": "app.py", "content": "改过了"}))
        return AssistantTurn(text="好，我不改了")


class _SpyExecutor(LocalExecutor):
    """记账用的执行器：只读角色下**它一次都不该被调到** ✓（拦住必须发生在执行之前 ✓）。"""

    def __init__(self, cfg) -> None:      # noqa: ANN001
        super().__init__(cfg)
        self.ran: list[str] = []

    async def run_tool(self, name, args, workdir):    # type: ignore[override]
        self.ran.append(name)
        return await super().run_tool(name, args, workdir)


def _mk_run(tmp_path, role: str, *, exists: bool = True):
    store = FsStore(tmp_path / "data")
    (store.workspace_dir("task_20261007_role") ).mkdir(parents=True, exist_ok=True)
    if exists:
        (store.workspace_dir("task_20261007_role") / "app.py").write_text("原样", "utf-8")
    task = TaskSummary(id="task_20261007_role", title="角色限权",
                       created_at="2026-10-07T00:00:00Z", updated_at="2026-10-07T00:00:00Z")
    ex_cfg = SimpleNamespace(workspace_root=tmp_path / "ws", timeout_seconds=5.0, shell="",
                             search_url="", browser_channel="", comfyui_url="",
                             image_checkpoint="", allowed_dirs=[])
    ex = _SpyExecutor(ex_cfg)
    run = TaskRun(task, "改一下 app.py", store=store, bus=EventBus(), provider=_ToolProvider(),
                  executor=ex, approval=ApprovalManager(), tools=[], max_iterations=3,
                  timeout_seconds=5.0, approval_required=[], on_finish=lambda r: None, role=role)
    return run, ex


def test_readonly_role_never_reaches_the_executor(tmp_path):
    """★★ **拦在工具执行之前** ✓ —— 回滚实验：把 `permissions.block_reason` 那一小段去掉 ⇒ 本组红 ✓。"""
    run, ex = _mk_run(tmp_path, READONLY_ROLE)
    asyncio.run(run._run())
    assert "file_write" not in ex.ran, "只读角色竟然真把文件写了 ✗✗（独立验证当场作废 ✓）"
    assert (run.store.workspace_dir("task_20261007_role") / "app.py").read_text("utf-8") == "原样"


def test_readonly_role_gets_an_actionable_explanation(tmp_path):
    """★ 拦住必须**说人话** ✓：模型要知道"换身份"，用户要在事件流里看得见 ✓。"""
    run, _ = _mk_run(tmp_path, READONLY_ROLE)
    asyncio.run(run._run())
    evs = run.store.read_events("task_20261007_role")
    obs = [str((e.payload or {}).get("result") or (e.payload or {}).get("output") or "")
           for e in evs if e.type == "observation"]
    joined = "\n".join(obs)
    assert "角色权限" in joined and "程序员" in joined, f"没说清为什么被挡、该怎么办 ✗：{joined[:300]}"
    det = [str((e.payload or {}).get("detail") or "") for e in evs if e.type == "status"]
    assert any("角色权限" in x for x in det), "事件流里没有可见痕迹（用户不知道为什么没改成 ✓）"


def test_default_role_still_writes_normally(tmp_path):
    """★★ **没身份 = 照旧** ✓ —— 这一条是"零影响"的正面证明 ✓（回滚态也该是绿的 ✓）。"""
    run, ex = _mk_run(tmp_path, "")
    asyncio.run(run._run())
    assert "file_write" in ex.ran, "默认助手竟然写不了文件 ✗✗（那就是把普通单聊弄坏了 ✓）"
    assert (run.store.workspace_dir("task_20261007_role") / "app.py").read_text("utf-8") == "改过了"


# ═══ ⑤ 身份要落盘、要跟着走（群里的接力是新开一次运行 ✓）═══

def test_identity_is_persisted_and_read_back(tmp_path):
    st = FsStore(tmp_path / "data")
    assert st.identity("task_x") == "", "没设过就该是空 ✓（= 默认助手 = 不受限 ✓）"
    st.set_identity("task_x", READONLY_ROLE)
    assert st.identity("task_x") == READONLY_ROLE
    st.set_identity("task_x", "")
    assert st.identity("task_x") == "", "切回默认助手要真的清掉 ✗（否则身份粘住了 ✓）"


def test_roles_endpoint_tells_the_ui_before_you_choose(monkeypatch):
    """★ 身份选择器要**选之前**就知道哪个不能改 ✓（选完才发现 = 用户以为界面坏了 ✗）。"""
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        roles = {r["role"]: r for r in c.get("/api/v1/team/roles").json()["roles"]}
    assert roles["测试工程师"]["can_write"] is False, roles["测试工程师"]
    assert roles["测试工程师"]["label"], "没给一句人话说明 ✗"
    assert roles["程序员"]["can_write"] is True


def test_identity_endpoint_persists_for_the_next_run():
    """★ 切换身份要**落盘** ✓ —— 群里的接力/续跑是**新开一次运行** ✓ 不落盘就"恢复默认"了 ✗。

    ★ 用 conftest 那份隔离 store ✓（`AGENT_SHELL_CONFIG` 已把 data_dir 指到临时目录 ✓）——
      本班第一版**自己另指了一个目录**（`backend/data/_t_identity` ✗）⇒ 往**用户的真数据目录**
      里写东西 ✗（本仓的规矩：测试不许碰用户真数据 ✓ 当场改成用现成的隔离 ✓）。
    """
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        tid = c.post("/api/v1/tasks", json={"input": "身份落盘"}).json()["id"]
        r = c.post(f"/api/v1/tasks/{tid}/identity", json={"role": READONLY_ROLE})
        assert r.status_code == 200 and r.json()["can_write"] is False, r.text
        assert m.store.identity(tid) == READONLY_ROLE, "没落盘 ⇒ 下一轮就恢复默认了 ✗"
