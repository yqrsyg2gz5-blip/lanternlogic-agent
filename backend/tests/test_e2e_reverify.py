"""验证报告 20 要求的两条端到端补验（真跑，不用 stub 执行器）。

① 审批链路：绕过命令（bash -c 结构嫌疑）→ waiting_approval → 批准 → 真实执行成功
② 记忆打码：任务里塞 Key 形态串 → memory.json 中不得出现该串
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from app.approval import ApprovalManager
from app.bus import EventBus
from app.executors.local import LocalExecutor
from app.loop import TaskRun
from app.providers.base import AssistantTurn, ModelProvider, ToolCall
from app.config import load_config
from app.schemas import TaskSummary
from app.store import FsStore
from types import SimpleNamespace


class _Scripted(ModelProvider):
    name = "scripted-e2e"

    def __init__(self, turns: list[AssistantTurn]) -> None:
        self._turns = turns
        self.n = 0
        self.total_usage = {"calls": 1, "input_tokens": 10, "output_tokens": 10}

    async def next_turn(self, task_input, history, tools, on_delta=None):  # type: ignore[override]
        i = min(self.n, len(self._turns) - 1)
        self.n += 1
        return self._turns[i]


def _local_executor(tmp_path: Path) -> LocalExecutor:
    return LocalExecutor(SimpleNamespace(
        type="local", workspace_root=str(tmp_path / "ws"), allowed_dirs=[str(tmp_path / "ws")],
        timeout_seconds=30, sandbox="off", shell=None, cfg_shell="", search_url="", searxng_url="",
        browser_channel="msedge", comfyui_url="http://127.0.0.1:9",
        image_checkpoint="x", sandbox_image=load_config().executor.sandbox_image,
        sandbox_network="none", sandbox_memory="512m",
    ))


def test_resolve_bash_excludes_wsl_launcher(monkeypatch):
    """复验报告 20 的根因：PATH 里 WSL 的 System32\bash.exe 排前时，
    bash -c 会被 WSL 吃掉（execvpe /bin/bash 失败）。解析器必须排除它。"""
    import os
    from app.executors.local import LocalExecutor
    # 空候选环境：where 只能找到 System32 版（Windows 真机）——必须返回 None 而不是 WSL bash
    r = LocalExecutor._resolve_bash("", env={"PATH": "C:" + chr(92) + "Windows" + chr(92) + "System32"})
    assert r is None or "system32" not in r.lower(), f"解析到了 WSL launcher：{r}"
    # 有正常 PATH 时（Windows）：应解析到 Git Bash（若安装）；非 Windows：None
    if os.name == "nt":
        assert LocalExecutor._resolve_bash("") and "system32" not in LocalExecutor._resolve_bash("").lower()


def _mk(tmp_path: Path, turns: list[AssistantTurn], approval_required: list[str],
        task_id: str = "task_20261002_e2e1") -> TaskRun:
    return TaskRun(
        TaskSummary(id=task_id, title="e2e", created_at="2026-10-02T00:00:00Z",
                    updated_at="2026-10-02T00:00:00Z"),
        "端到端验证",
        store=FsStore(tmp_path / "data"),
        bus=EventBus(),
        provider=_Scripted(turns),
        executor=_local_executor(tmp_path),  # 真执行器：命令真的跑
        approval=ApprovalManager(),
        tools=[],
        max_iterations=6,
        timeout_seconds=10.0,
        approval_required=approval_required,
        on_finish=lambda r: None,
    )


async def test_e2e_bypass_cmd_asks_then_runs_after_approval(tmp_path, monkeypatch):
    """① 绕过命令：bash -c（结构嫌疑）→ 弹审批 → once → 命令真实执行出结果。"""
    # 复现用户环境：System32（WSL launcher bash）排 PATH 最前 + shell 未配置——
    # 修复后 _resolve_bash 走 Git Bash 常见路径探测，命令真实执行
    monkeypatch.setenv("PATH", "C:" + chr(92) + "Windows" + chr(92) + "System32;" + os.environ.get("PATH", ""))
    # 内层 rm 才有动词命中；内层纯白名单（echo）的透明包装器按设计放行
    run = _mk(tmp_path, [
        AssistantTurn(tool_call=ToolCall(name="shell_exec",
                                         arguments={"command": "echo start-e2e && bash -c \"rm marker-e2e.txt; echo marker-e2e > marker-e2e.txt\""})),
        AssistantTurn(tool_call=ToolCall(name="task_done", arguments={"message": "done"})),
    ], approval_required=["rm"])
    t = run.start()

    async def approve_when_asked() -> None:
        for _ in range(200):
            if run.approval.resolve(run.task.id, "call_001", "once"):
                return
            await asyncio.sleep(0.05)

    await asyncio.gather(t, approve_when_asked())

    evs = run.store.read_events(run.task.id)
    waiting = [e for e in evs if e.type == "status" and e.payload.get("state") == "waiting_approval"]
    assert waiting, "绕过命令必须真实弹审批（waiting_approval）"
    assert "rm" in str(waiting[0].payload.get("detail", ""))
    obs = [e for e in evs if e.type == "observation" and e.payload.get("call_id") == "call_001"]
    assert obs and obs[0].payload.get("ok"), f"批准后必须真实执行成功：{obs}"
    assert "marker-e2e" in str(obs[0].payload.get("result", "")), "工作区真执行输出必须可见"
    assert run.task.status == "done"


async def test_e2e_memory_redaction_keeps_key_out_of_memory_file(tmp_path, monkeypatch):
    """② 真塞 Key 形态串跑交付 → memory.json 不含该串（事件流里有原文是 BYOK 语义）。"""
    import app.main as m
    KEY = "sk-e2ekey-abcdefghijklmnop-9876"

    class _FakeProvider:
        total_usage = {"calls": 1, "input_tokens": 10, "output_tokens": 10, "estimated": False}

        async def next_turn(self, task_input, history, tools, on_delta=None):
            return AssistantTurn(text=json.dumps(
                [{"content": f"用户偏好：在消息里粘贴过 {KEY}（这条记忆不应包含 Key）"}],
                ensure_ascii=False))

    store = FsStore(tmp_path / "data")
    store.save_history("task_20261002_e2e2", [
        {"role": "user", "content": f"请记住我的 {KEY}"},
        {"role": "assistant", "content": f"收到，你的 {KEY}"},
    ])

    # 独立 MemoryStore 指向测试路径
    from app.memory import MemoryStore
    mem = MemoryStore(tmp_path / "data" / "memory" / "memory.json", max_entries=50)

    # 用 main 的提取逻辑（同款 prompt/parse/二次打码路径）
    from app.memory import _EXTRACT_PROMPT, parse_extraction

    async def go() -> None:
        hist = store.load_history("task_20261002_e2e2")
        user_in = ""
        reply = ""
        for h in hist:
            if h.get("role") == "user":
                user_in = m._redact_text(str(h.get("content") or ""))[:2000]
            elif h.get("role") == "assistant":
                reply = m._redact_text(str(h.get("content") or ""))[:2000]
        prompt = _EXTRACT_PROMPT.replace("{task_input}", user_in[:1500]).replace("{reply}", reply[:1500])
        turn = await _FakeProvider().next_turn("", [{"role": "user", "content": prompt}], [])
        items = parse_extraction(turn.text or "")
        for it in items:
            if isinstance(it, dict) and it.get("content"):
                it["content"] = m._redact_text(str(it["content"]))
        mem.add(items, source_task="task_20261002_e2e2")

    await go()

    raw = (tmp_path / "data" / "memory" / "memory.json").read_text(encoding="utf-8")
    assert KEY not in raw, f"memory.json 里出现了 Key 明文！文件：{raw[:300]}"


# ---------------- 第四轮复验：脚本内容扫描端到端（真实 TaskRun + 真实执行器） ----------------


async def test_e2e_dangerous_script_asks_and_benign_passes(tmp_path):
    """③端到端：危险脚本（os.system rm）→ 必须弹审批；纯 print 脚本 → 放行不误伤。"""
    # 脚本必须放进 **任务真实工作区**（store.workspace_dir），审批 reader 从这里读
    ws = FsStore(tmp_path / "data").workspace_dir("task_20261002_e2e1")
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "evil.py").write_text(
        "import os" + chr(10) + "os.system('rm -rf /c/Users/y/Documents/x')" + chr(10),
        encoding="utf-8")
    (ws / "safe.py").write_text("print('SAFE-RAN')" + chr(10), encoding="utf-8")
    ws2 = FsStore(tmp_path / "data").workspace_dir("task_20261002_e2e9")
    ws2.mkdir(parents=True, exist_ok=True)
    (ws2 / "safe.py").write_text("print('SAFE-RAN')" + chr(10), encoding="utf-8")

    # 危险脚本 → 弹审批（审批后真实执行，因 workdir 是测试隔离目录，rm 目标不存在但不影响判定）
    run = _mk(tmp_path, [
        AssistantTurn(tool_call=ToolCall(name="shell_exec",
                                         arguments={"command": "python evil.py"})),
        AssistantTurn(tool_call=ToolCall(name="task_done", arguments={"message": "done"})),
    ], approval_required=["rm"])
    t = run.start()

    async def approve_when_asked() -> None:
        for _ in range(200):
            if run.approval.resolve(run.task.id, "call_001", "once"):
                return
            await asyncio.sleep(0.05)

    async def go() -> None:
        await asyncio.gather(t, approve_when_asked())

    await go()
    evs = run.store.read_events(run.task.id)
    waiting = [e for e in evs if e.type == "status" and e.payload.get("state") == "waiting_approval"]
    assert waiting, "危险脚本（os.system rm）必须弹审批"
    assert "evil.py" in str(waiting[0].payload.get("detail", "")), f"拒绝原因应点名脚本：{waiting[0].payload}"

    # 安全脚本 → 不弹审批（防误伤）
    run2 = _mk(tmp_path, [
        AssistantTurn(tool_call=ToolCall(name="shell_exec",
                                         arguments={"command": "python safe.py"})),
        AssistantTurn(tool_call=ToolCall(name="task_done", arguments={"message": "ok"})),
    ], approval_required=["rm"], task_id="task_20261002_e2e9")
    await run2._run()
    evs2 = run2.store.read_events(run2.task.id)
    waiting2 = [e for e in evs2 if e.type == "status" and e.payload.get("state") == "waiting_approval"]
    assert not waiting2, f"纯 print 脚本不应误伤：{waiting2}"


# ---------------- 第六轮复验：(b) 结构性保护——交付快照（工作区之外） ----------------

async def test_e2e_delivery_snapshot_created(tmp_path):
    """交付快照：工作区**之外** + 敏感排除 + 工作区被清空后仍完好。

    第六轮复验修正：快照此前放工作区内（`rm -rf .[!.]* *` 一并删）；现在在
    tasks/<id>/snapshots/（工作区同级的独立目录），且 .env/id_rsa 不入快照。
    """
    store = FsStore(tmp_path / "data")
    ws = store.workspace_dir("task_20261002_snap")
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "deliverable.txt").write_text("PRECIOUS-DATA", encoding="utf-8")
    (ws / ".env").write_text("SECRET=1", encoding="utf-8")   # 敏感：不应入快照
    (ws / "id_rsa").write_text("KEY", encoding="utf-8")      # 敏感：不应入快照

    run = _mk(tmp_path, [
        AssistantTurn(tool_call=ToolCall(name="task_done", arguments={"message": "交付"})),
    ], approval_required=[], task_id="task_20261002_snap")
    await run._run()

    snap_root = store.snapshots_dir("task_20261002_snap")
    snaps = list(snap_root.rglob("deliverable.txt"))
    assert snaps, "交付必须留下快照"
    assert snaps[0].read_text(encoding="utf-8") == "PRECIOUS-DATA"
    # 快照在**工作区之外**（否则 rm -rf .[!.]* * 会一并删掉）
    ws_res = ws.resolve()
    assert all(ws_res not in p.resolve().parents for p in snaps), "快照必须在工作区之外"
    # 敏感文件不入快照
    snap_files = {p.name for p in snap_root.rglob("*") if p.is_file()}
    assert ".env" not in snap_files and "id_rsa" not in snap_files, f"敏感文件不应入快照：{snap_files}"
    # 模拟后续任务清空工作区 → 快照完好可人工还原
    (ws / "deliverable.txt").write_text("", encoding="utf-8")
    assert snaps[0].read_text(encoding="utf-8") == "PRECIOUS-DATA", "工作区清空后快照必须完好"
