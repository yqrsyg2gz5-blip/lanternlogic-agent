"""A3（审计台账 · P1 上线阻塞）：`sandbox="off"` = 宿主模式，命令真实作用于用户电脑，
界面上必须有**显式风险告知**。

修复前的原始事实（本批开工时实测，见汇报）：
    >>> repr(await ex._shell(tmp_path, "echo A3-PROBE"))
    'A3-PROBE'          # ← 就这么多：没有任何一句话说明这条命令真在本机跑过
对比沙箱分支：`_shell_sandbox` 会追加 sandbox_note（"沙箱内执行：… /w 是挂载，
其中的删除/改写会真实作用于本机文件"）⇒ **同一能力面，一个告知一个不告知**。

本文件钉四层（缺一层就算没做）：
  ① 后端真执行：宿主模式回执必须带告知（不是"代码里有这句话"，是真跑出来的）
  ② 非零退出也带（失败的命令同样已经改过这台机器）
  ③ 文案不能是沙箱那行抄过来的（否则告知与事实相反）
  ④ ★ 接线：告知必须真的进【用户看得到的那条】——TaskRun 落盘的 observation 事件

再加前端三层静态接线锚（前端无单测 runner，沿用 test_frontend_guards.py 的
"钉住组件真的渲染了它"做法）：
  ⑤ 设置 → 执行环境：沙箱关着时出现告警块
  ⑥ 任务头：常驻"⚠ 本机执行"徽标（且必须是 `!sandboxOn` 条件）
  ⑦ 审批栏：批准前再说一次"这条命令落在真实电脑上"
  ⑧ CSS：三个 class 必须有真规则——**没有样式的告知渲染出来是看不见的**，
     那和没告知等价（这是"假修"的经典形态）。
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

from app.approval import ApprovalManager
from app.bus import EventBus
from app.executors.local import LocalExecutor
from app.loop import TaskRun
from app.providers.base import AssistantTurn, ModelProvider
from app.schemas import TaskSummary
from app.store import FsStore

FRONTEND_SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"

# 告知必须同时含有的三要素（"说了"和"说清"是两件事）：
#   本机执行 = 落在哪台机器；真实电脑 = 不是模拟；不可撤销 = 风险到底在哪
NOTICE_MUST_HAVE = ("本机执行", "真实电脑", "不可撤销")


def _executor(sandbox: str = "off") -> LocalExecutor:
    return LocalExecutor(SimpleNamespace(
        type="local", workspace_root=".", allowed_dirs=[], timeout_seconds=30,
        sandbox=sandbox, shell=None, cfg_shell="", search_url="", searxng_url="",
        browser_channel="msedge", comfyui_url="http://127.0.0.1:9",
        image_checkpoint="x", sandbox_image="python:3.12-slim",
        sandbox_network="none", sandbox_memory="512m",
    ))


# ═══ ① 后端真执行 ═══

def test_host_shell_output_carries_risk_notice(tmp_path):
    """真跑一条命令：回执里必须出现完整告知（三要素齐）。"""
    ex = _executor("off")
    out = asyncio.run(ex._shell(tmp_path, "echo A3-PROBE"))
    assert "A3-PROBE" in out, f"命令本身没跑到（观察面被改坏的迹象）：{out!r}"
    for kw in NOTICE_MUST_HAVE:
        assert kw in out, f"宿主模式回执缺少告知要素「{kw}」：{out!r}"


def test_host_notice_present_on_failure_too(tmp_path):
    """非零退出同样带告知——失败的命令也已经动过这台机器（甚至更该告知）。"""
    ex = _executor("off")
    out = asyncio.run(ex._shell(tmp_path, "definitely-no-such-cmd-a3-probe"))
    for kw in NOTICE_MUST_HAVE:
        assert kw in out, f"失败命令的回执缺少告知：{out!r}"


def test_host_notice_is_not_sandbox_wording(tmp_path):
    """防"假修"：告知不能把沙箱那行抄过来——那会让用户以为命令进了容器。"""
    ex = _executor("off")
    out = asyncio.run(ex._shell(tmp_path, "echo A3-PROBE"))
    assert "沙箱内执行" not in out, f"宿主模式用了沙箱文案（与事实相反）：{out!r}"
    assert "容器" not in out, f"宿主模式回执不该提容器：{out!r}"


# ═══ ④ 接线：告知真的进"用户看得见的那条"（observation 事件） ═══
# 为什么必须钉这一条：本项目有前科——tests/test_ssrf.py 里那条锚点 docstring 写着
# "经 TaskRun 写进 history"，函数体里却根本没有 TaskRun（只在纯函数上断言）。
# 只测 _shell 的返回值同样证明不了"用户看得到"。

class _ShellProvider(ModelProvider):
    """第一轮固定发一次 shell_exec，之后纯文本收尾。"""

    name = "host-notice-shell"
    command = "echo A3-TIMELINE"

    async def next_turn(self, task_input, history, tools, on_delta=None):  # type: ignore[override]
        if any(m.get("role") == "tool" for m in history):
            return AssistantTurn(text="done")
        return AssistantTurn(tool_call=SimpleNamespace(
            name="shell_exec", arguments={"command": self.command}))


def test_notice_reaches_task_timeline_observation(tmp_path):
    """真 TaskRun 跑一次 shell_exec → 落盘的 observation 必须含告知。"""
    store = FsStore(tmp_path / "data")
    task = TaskSummary(
        id="task_20261004_a300",
        title="A3 告知接线",
        created_at="2026-10-04T00:00:00Z",
        updated_at="2026-10-04T00:00:00Z",
    )
    ex_cfg = SimpleNamespace(
        workspace_root=tmp_path / "ws",
        timeout_seconds=5.0,
        shell="",
        search_url="",
        browser_channel="",
        comfyui_url="",
        image_checkpoint="",
        allowed_dirs=[],
        # ★ 显式写 off：这条锚点验的就是宿主模式（缺省也是 off，但不靠缺省）
        sandbox="off",
        sandbox_image="python:3.12-slim",
        sandbox_network="none",
        sandbox_memory="512m",
    )
    run = TaskRun(
        task,
        "跑命令",
        store=store,
        bus=EventBus(),
        provider=_ShellProvider(),
        executor=LocalExecutor(ex_cfg),
        approval=ApprovalManager(),
        tools=[],
        max_iterations=3,
        timeout_seconds=5.0,
        approval_required=[],
        on_finish=lambda r: None,
    )
    asyncio.run(run._run())

    events_files = list((tmp_path / "data").rglob("events.jsonl"))
    assert events_files, "events.jsonl 没有落盘——接线没被走到"
    events = [json.loads(line) for line in
              events_files[0].read_text(encoding="utf-8").splitlines()]
    obs = [e for e in events if e["type"] == "observation"]
    assert obs, "没有 observation 事件——测试没打中用户可见面"
    result = str(obs[0]["payload"]["result"])
    # 先证路径真被走到（否则下面的"含告知"可能是在错误文案上假绿）
    assert "A3-TIMELINE" in result, f"executor 没真执行（observation 非命令输出）：{result!r}"
    for kw in NOTICE_MUST_HAVE:
        assert kw in result, f"用户时间线上的 observation 缺少告知「{kw}」：{result!r}"


# ═══ ⑤⑥⑦ 前端静态接线锚 ═══

def test_settings_panel_warns_when_host_mode():
    tsx = (FRONTEND_SRC / "components" / "SettingsPanel.tsx").read_text("utf-8")
    assert "host-exec-warn" in tsx, "设置页没有宿主模式告警块"
    # 必须是【条件渲染】：沙箱开着还显示"命令在你电脑上跑"就是错误告知
    m = re.search(r"settings\?\.executor\?\.sandbox \?\? 'off'\)\s*!==\s*'docker'\s*&&\s*\(\s*<div className=\"host-exec-warn\"", tsx)
    assert m, "host-exec-warn 不在「沙箱 ≠ docker」条件分支里（会误报或永不显示）"
    assert "真实电脑" in tsx and "不可撤销" in tsx, "设置页告警缺风险要素"


def test_taskview_header_tag_when_host_mode():
    tsx = (FRONTEND_SRC / "components" / "TaskView.tsx").read_text("utf-8")
    m = re.search(r"\{!sandboxOn && \(\s*<span\s+className=\"host-exec-tag\"", tsx)
    assert m, "任务头没有「沙箱关着时」的常驻本机执行徽标（或没挂在 !sandboxOn 上）"
    assert "沙箱已关闭" in tsx, "徽标 title 未说明沙箱状态"


def test_taskview_approval_bar_warns_when_host_mode():
    tsx = (FRONTEND_SRC / "components" / "TaskView.tsx").read_text("utf-8")
    m = re.search(r"\{!sandboxOn && \(\s*<div className=\"approval-host-warn\"", tsx)
    assert m, "审批栏没有宿主模式告知（审批是最后一次拦得住的地方）"
    # 独立成行：塞进 .approval-main（父栏 overflow:hidden，那行已被 title+命令占满）
    # 会被裁掉——被裁掉的告知等于没有告知。
    assert '<div className="approval-host-warn">' in tsx, "审批栏告知必须是独立行（不是行内 span）"


def test_css_defines_the_warning_classes():
    """⑧ 没有样式的告知渲染出来是看不见的——三个 class 都要有真规则。"""
    css = (FRONTEND_SRC / "styles.css").read_text("utf-8")
    for cls in (".host-exec-tag", ".approval-host-warn", ".host-exec-warn"):
        assert re.search(re.escape(cls) + r"\s*\{[^}]*\}", css), f"styles.css 缺少 {cls} 的规则"
