"""_iterate 的历史记录行为 —— P0-8（纯文本收尾时回答丢失）。

发现路径（不是读代码读出来的，是真实验收撞出来的）：
  建一条任务 → 模型直接文本回复、没调 task_done → 落盘 history 只有
  [system, system, user]，模型自己的回答不在里面；
  紧接着发续聊消息，模型亲口回答「上一轮任务的上下文未在我这里留存」。

根因：`loop._iterate` 里 `if turn.tool_call is None: await _finish(...); return`
的提前 return 跳过了 assistant 消息的 history 追加
（这段是 HANDOVER §7.8 为修「重复回复」时写的，顺带丢掉了记忆）。

本文件是这条缺陷的护栏。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.approval import ApprovalManager
from app.bus import EventBus
from app.executors.local import LocalExecutor
from app.loop import TaskRun
from app.providers.base import AssistantTurn, ModelProvider
from app.schemas import TaskSummary
from app.store import FsStore

REPLY = "这是一句纯文本回复，没有调用任何工具。"


class _TextProvider(ModelProvider):
    """永远用纯文本收尾（不调用工具）——复刻 MiMo 在那次验收里的行为。"""

    name = "text-only"

    async def next_turn(self, task_input, history, tools, on_delta=None):  # type: ignore[override]
        return AssistantTurn(text=REPLY)


def _mk_run(tmp_path) -> TaskRun:
    store = FsStore(tmp_path / "data")
    task = TaskSummary(
        id="task_20260930_test",
        title="纯文本收尾",
        created_at="2026-09-30T00:00:00Z",
        updated_at="2026-09-30T00:00:00Z",
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
    )
    return TaskRun(
        task,
        "用户的问题",
        store=store,
        bus=EventBus(),
        provider=_TextProvider(),
        executor=LocalExecutor(ex_cfg),
        approval=ApprovalManager(),
        tools=[],
        max_iterations=3,
        timeout_seconds=5.0,
        approval_required=[],
        on_finish=lambda r: None,
    )


def _run_task(tmp_path) -> TaskRun:
    """走真实入口 `_run()`——它才有 finally 里的 `_save_history()`。

    （只调 `_iterate()` 不会落盘；第一版测试就是这么写错的。）
    """
    run = _mk_run(tmp_path)
    asyncio.run(run._run())
    return run


def test_plain_text_reply_enters_history(tmp_path):
    """核心断言：纯文本收尾时，回答必须进 history。"""
    run = _run_task(tmp_path)
    roles = [m["role"] for m in run.history]
    assert "assistant" in roles, f"纯文本回复没有进历史，实际角色序列：{roles}"
    assert run.history[-1]["role"] == "assistant"
    assert REPLY in run.history[-1]["content"]


def test_plain_text_reply_is_persisted_to_disk(tmp_path):
    """落盘断言：_save_history 之后，从磁盘读回来也要能看到这句回答。"""
    run = _run_task(tmp_path)
    saved = run.store.load_history(run.task.id)
    assert saved, "history.json 没有落盘"
    assert any(m["role"] == "assistant" and REPLY in m.get("content", "") for m in saved), (
        f"磁盘上的 history 仍不含模型回答：{[m['role'] for m in saved]}"
    )


def test_history_still_normalizes_after_text_reply(tmp_path):
    """与 P0-7 的规范化不冲突：纯文本结尾不能被补出多余的 tool 回执。"""
    from app.loop import normalize_history

    run = _run_task(tmp_path)
    out = normalize_history(run.history)
    assert out == run.history
    assert not any(m["role"] == "tool" for m in out)


# ═══ 二十六轮 S-003：action 事件的 params 落盘前必须打码 ═══
# 独立复现（验证员 + 本班一致）：`echo sk-…`（Key 在命令里）→ action 事件
# 【明文】进 events.jsonl；observation 有 _redact 面而 action 没有——
# 两条打码管线不互为兜底。修复：emit 前过 _redact_deep（值层递归，
# 打码逻辑复用 redact.py 的 redact_text 一份）。
class _ShellProvider(ModelProvider):
    """第一条 turn 固定发起一次 shell 调用（命令由类属性注入）。"""

    name = "shell-once"
    command = "echo hi"

    async def next_turn(self, task_input, history, tools, on_delta=None):  # type: ignore[override]
        if any(m.get("role") == "tool" for m in history):
            return AssistantTurn(text="done")
        return AssistantTurn(tool_call=SimpleNamespace(name="shell_exec", arguments={"command": self.command}))


def _run_shell_task(tmp_path, command: str) -> list[dict]:
    store = FsStore(tmp_path / "data")
    task = TaskSummary(
        id="task_20261003_ab12",
        title="S-003",
        created_at="2026-10-03T00:00:00Z",
        updated_at="2026-10-03T00:00:00Z",
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
    )
    _ShellProvider.command = command
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
    events = list((tmp_path / "data").rglob("events.jsonl"))
    assert events, "events.jsonl 没有落盘"
    return [json.loads(line) for line in events[0].read_text(encoding="utf-8").splitlines()]


def test_action_event_secret_in_command_is_redacted(tmp_path):
    """形态 B（Key 在命令里）：events.jsonl 明文出现次数必须 = 0。"""
    needle = "sk-envSECRET1234567890abcdef"
    events = _run_shell_task(tmp_path, f"echo {needle}")
    raw = "".join(json.dumps(e, ensure_ascii=False) for e in events)
    assert raw.count(needle) == 0, f"action 事件明文泄漏（出现 {raw.count(needle)} 次）"
    actions = [e for e in events if e["type"] == "action"]
    assert actions, "没有 action 事件——测试没打中目标面"
    assert "[已隐藏" in actions[0]["payload"]["params"]["command"], (
        f"params 已打码掩码应在位：{actions[0]['payload']['params']}")
    # 路径真被走到（规矩⑨）：observation 必须含命令的【真实输出】——
    # 若工具名是桩名（如 "shell"），_validate 报"未知工具"、executor 不执行，
    # observation 只有错误文案，三条打码断言就会在从未走过的路径上假绿。
    obs = [e for e in events if e["type"] == "observation"]
    assert obs and "echo" not in obs[0]["payload"]["result"] and "未知工具" not in str(obs[0]["payload"]["result"]), (
        f"executor 必须真执行（observation 应为命令真实输出）：{obs[0]['payload']['result'] if obs else '无'}")


def test_action_event_env_var_form_untouched(tmp_path):
    """形态 A（Key 只在环境变量名）：%VAR% 不是密钥值，必须原样保留（防过度打码）。"""
    events = _run_shell_task(tmp_path, "echo %DSH_FAKE_API_KEY%")
    actions = [e for e in events if e["type"] == "action"]
    assert actions and actions[0]["payload"]["params"]["command"] == "echo %DSH_FAKE_API_KEY%"
    raw = "".join(json.dumps(e, ensure_ascii=False) for e in events)
    assert raw.count("%DSH_FAKE_API_KEY%") >= 1, "环境变量名形态被误打码"


def test_action_event_plain_command_still_readable(tmp_path):
    """普通任务不受影响：无密钥命令在 action 事件里原样可读。"""
    events = _run_shell_task(tmp_path, "echo hello-world")
    actions = [e for e in events if e["type"] == "action"]
    assert actions and actions[0]["payload"]["params"]["command"] == "echo hello-world"


def test_action_event_nested_params_redacted(tmp_path):
    """嵌套 dict 的值层也要打到（列表/字典包裹的密钥串）。

    【二十六轮第 4 批第 4 处口径对齐】：needle 用【无扩展名】形态——
    文件名形态（needle.txt）在工具通道已被有意豁免（agent 要看真名），
    拿它测"密钥必须打码"与分通道政策自相矛盾；无扩展名形态走强规则。"""
    needle = "sk-NESTED1234567890abcdef"
    events = _run_shell_task(tmp_path, f"sh -c 'cat {needle}'")
    raw = "".join(json.dumps(e, ensure_ascii=False) for e in events)
    assert raw.count(needle) == 0


# ═══ 二十六轮第2批：history 的 tool_calls.arguments 值层打码 ═══
# 独立复现：`echo sk-…` → ①history.json 落盘明文 1 次 ②发上游 payload 明文
# 1 次（events.jsonl 已由 S-003 修为 0）。修复：history.append 前过
# _redact_deep（同源实现）；执行用原 args，打码只进 history 副本。
def test_history_tool_calls_arguments_redacted_on_disk_and_upstream(tmp_path):
    """验收 a+b：落盘与发上游两个泄漏面明文次数都必须 = 0。"""
    needle = "sk-envSECRET1234567890abcdef"
    store = FsStore(tmp_path / "data")
    task = TaskSummary(
        id="task_20261003_ab12", title="hist",
        created_at="2026-10-03T00:00:00Z", updated_at="2026-10-03T00:00:00Z",
    )
    ex_cfg = SimpleNamespace(
        workspace_root=tmp_path / "ws", timeout_seconds=5.0, shell="",
        search_url="", browser_channel="", comfyui_url="", image_checkpoint="",
        allowed_dirs=[],
    )
    upstream: list[str] = []

    class _CaptureProvider(ModelProvider):
        name = "capture"

        async def next_turn(self, task_input, history, tools, on_delta=None):  # type: ignore[override]
            upstream.append(json.dumps(history, ensure_ascii=False, default=str))
            if any(m.get("role") == "tool" for m in history):
                return AssistantTurn(text="done")
            return AssistantTurn(tool_call=SimpleNamespace(
                name="shell_exec", arguments={"command": f"echo {needle}"}))

    run = TaskRun(
        task, "跑命令", store=store, bus=EventBus(), provider=_CaptureProvider(),
        executor=LocalExecutor(ex_cfg), approval=ApprovalManager(), tools=[],
        max_iterations=3, timeout_seconds=5.0, approval_required=[],
        on_finish=lambda r: None,
    )
    asyncio.run(run._run())

    saved = run.store.load_history(run.task.id)
    on_disk = json.dumps(saved, ensure_ascii=False)
    assert on_disk.count(needle) == 0, f"history.json 明文泄漏 {on_disk.count(needle)} 次"
    assert "[已隐藏" in on_disk, "落盘掩码应在位（可读性仍在）"
    in_upstream = sum(s.count(needle) for s in upstream)
    assert in_upstream == 0, f"发上游 payload 明文泄漏 {in_upstream} 次"


def test_action_plain_arguments_still_readable(tmp_path):
    """S-003 验收 c：正常命令在 action 事件（events.jsonl）原样可读。

    【名实更正·二十六轮第3批 P3e】：原名 test_history_plain_arguments_...
    名不副实——断言的是 events.jsonl 的 action 事件，与 history 无关。
    history 侧可读性由 test_history_tool_calls_arguments_redacted_...的
    掩码在位断言覆盖。"""
    run = _run_shell_task(tmp_path, "echo hello-normal-task")
    # events.jsonl 里普通命令原样（S-003 锚点已钉）；这里补 history 侧：
    assert any('"echo hello-normal-task"' in json.dumps(m, ensure_ascii=False) for m in run), (
        "普通命令应在 action 事件里原样可读")


# ═══ 二十六轮第 3 批第 1 处（P0）：观察面形态层打码 ═══
# 端到端实测（真 shell_exec，验证员+本班一致）：loop._redact 此前只做
# 【环境变量值】精确替换、不走 redact.py 形态层——`cat .env`/`echo $KEY`/
# `env` 的 stdout 含 sk- 形态 Key 时，明文进 history/上游/events 三面各 1 次。
# ★ 锚点失明教训（假绿第三形态）：旧锚点用桩工具名 "shell" → _validate 报
#   "未知工具" → executor 从不执行 → observation 面从未被走到。本段全部用
#   真工具名 shell_exec + 【路径真被走到】断言（observation 含真实命令输出、
#   不含"未知工具"）。
def _mk_e2e_run(tmp_path, command: str) -> tuple[list[dict], list[str]]:
    """真 shell_exec 跑一条命令，返回 (events, upstream_payloads)。"""
    store = FsStore(tmp_path / "data")
    task = TaskSummary(
        id="task_20261003_ab12", title="obs-redact",
        created_at="2026-10-03T00:00:00Z", updated_at="2026-10-03T00:00:00Z",
    )
    ex_cfg = SimpleNamespace(
        workspace_root=tmp_path / "ws", timeout_seconds=5.0, shell="",
        search_url="", browser_channel="", comfyui_url="", image_checkpoint="",
        allowed_dirs=[],
    )
    upstream: list[str] = []

    class _ExecProvider(ModelProvider):
        name = "exec-once"

        async def next_turn(self, task_input, history, tools, on_delta=None):  # type: ignore[override]
            upstream.append(json.dumps(history, ensure_ascii=False, default=str))
            if any(m.get("role") == "tool" for m in history):
                return AssistantTurn(text="done")
            return AssistantTurn(tool_call=SimpleNamespace(
                name="shell_exec", arguments={"command": command}))

    run = TaskRun(
        task, "跑命令", store=store, bus=EventBus(), provider=_ExecProvider(),
        executor=LocalExecutor(ex_cfg), approval=ApprovalManager(), tools=[],
        max_iterations=3, timeout_seconds=5.0, approval_required=[],
        on_finish=lambda r: None,
    )
    asyncio.run(run._run())
    events = list((tmp_path / "data").rglob("events.jsonl"))
    assert events, "events.jsonl 没有落盘"
    return [json.loads(line) for line in events[0].read_text(encoding="utf-8").splitlines()], upstream


def _assert_observation_really_ran(events: list[dict], marker: str) -> None:
    """【规矩⑨】路径真被走到的证据：observation 含命令真实输出标记，
    且不含"未知工具"（桩工具名会让 executor 不执行、路径失明）。"""
    obs = [e for e in events if e["type"] == "observation"]
    assert obs, "没有 observation 事件"
    result = str(obs[0]["payload"].get("result", ""))
    assert "未知工具" not in result, f"executor 未执行（桩工具名？）：{result[:80]}"
    assert marker in result, f"observation 应含真实输出标记 {marker!r}：{result[:80]}"


def test_observation_shape_secret_redacted_e2e(tmp_path):
    """echo $KEY 形态：stdout 含 sk- 形态 Key → 三面明文 = 0。"""
    needle = "sk-envSECRET1234567890abcdef"
    events, upstream = _mk_e2e_run(tmp_path, f"echo API_KEY={needle}")
    _assert_observation_really_ran(events, "API_KEY=")
    raw_events = "".join(json.dumps(e, ensure_ascii=False) for e in events)
    raw_up = "".join(upstream)
    assert raw_events.count(needle) == 0, f"events 明文 {raw_events.count(needle)} 次"
    assert raw_up.count(needle) == 0, f"上游明文 {raw_up.count(needle)} 次"
    obs = next(e for e in events if e["type"] == "observation")
    assert "[已隐藏" in str(obs["payload"]["result"]), "观察掩码应在位"


def test_observation_env_dump_redacted_e2e(tmp_path, monkeypatch):
    """`env` 打印环境变量：环境值面（_secret_values）+ 形态面双层都要盖住。"""
    secret_val = "sk-ENVVALUE1234567890abcdef"
    monkeypatch.setenv("SOME_TEST_API_KEY", secret_val)
    # _collect_secrets 在 TaskRun 构造时快照——monkeypatch 后再建 run 即可
    events, upstream = _mk_e2e_run(tmp_path, "env")
    _assert_observation_really_ran(events, "PATH=")
    raw_events = "".join(json.dumps(e, ensure_ascii=False) for e in events)
    raw_up = "".join(upstream)
    assert raw_events.count(secret_val) == 0 and raw_up.count(secret_val) == 0, "环境值明文泄漏"
    # 形态面：redact_text 对该值的 [已隐藏] 掩码已在（与 [已隐藏-疑似密钥] 二选一）
    obs = next(e for e in events if e["type"] == "observation")
    assert "SOME_TEST_API_KEY=sk-ENVVALUE" not in str(obs["payload"]["result"]), "环境变量行应被打码"


def test_observation_env_file_read_redacted_e2e(tmp_path):
    """`cat .env`：工作区文件里的 sk- Key 经 stdout 回显 → 三面 0 明文。

    【路径真被走到】证据：先在工作区写 .env（真实文件），断言 observation
    含文件真实内容的前缀（API_KEY=）——桩路径下不会有该输出。
    """
    store = FsStore(tmp_path / "data")
    # TaskRun.workdir = store.workspace_dir(task.id)（data/tasks/<id>/workspace）
    # ——.env 必须写进【真实执行目录】（写错位置 cat 会报 No such file，路径断言会红）
    ws = store.workspace_dir("task_20261003_ab12")
    (ws / ".env").write_text("API_KEY=sk-envSECRET1234567890abcdef\n", encoding="utf-8")
    task = TaskSummary(
        id="task_20261003_ab12", title="obs-dotenv",
        created_at="2026-10-03T00:00:00Z", updated_at="2026-10-03T00:00:00Z",
    )
    ex_cfg = SimpleNamespace(
        workspace_root=ws, timeout_seconds=5.0, shell="",
        search_url="", browser_channel="", comfyui_url="", image_checkpoint="",
        allowed_dirs=[],
    )
    upstream: list[str] = []

    class _CatProvider(ModelProvider):
        name = "cat-dotenv"

        async def next_turn(self, task_input, history, tools, on_delta=None):  # type: ignore[override]
            upstream.append(json.dumps(history, ensure_ascii=False, default=str))
            if any(m.get("role") == "tool" for m in history):
                return AssistantTurn(text="done")
            return AssistantTurn(tool_call=SimpleNamespace(
                name="shell_exec", arguments={"command": "cat .env"}))

    run = TaskRun(
        task, "读配置", store=store, bus=EventBus(), provider=_CatProvider(),
        executor=LocalExecutor(ex_cfg), approval=ApprovalManager(), tools=[],
        max_iterations=3, timeout_seconds=5.0, approval_required=[],
        on_finish=lambda r: None,
    )
    asyncio.run(run._run())
    events = list((tmp_path / "data").rglob("events.jsonl"))
    events = [json.loads(line) for line in events[0].read_text(encoding="utf-8").splitlines()]
    needle = "sk-envSECRET1234567890abcdef"
    # 路径真被走到：observation 含 .env 真实键名（文件真被 cat 了）
    obs = next(e for e in events if e["type"] == "observation")
    result = str(obs["payload"].get("result", ""))
    assert "API_KEY=" in result, f"cat 未真执行：{result[:80]}"
    assert "未知工具" not in result
    raw_events = "".join(json.dumps(e, ensure_ascii=False) for e in events)
    raw_up = "".join(upstream)
    assert raw_events.count(needle) == 0, f"events 明文 {raw_events.count(needle)} 次"
    assert raw_up.count(needle) == 0, f"上游明文 {raw_up.count(needle)} 次"
    assert "[已隐藏" in result, "观察掩码应在位"


# ═══ 二十六轮第 4 批第 4 处：观察面分通道（文件名/编号形态豁免）═══
# 副作用实测：_redact 接形态层后 agent 读不到自己的文件真名——
#   cat sk-config.yaml → [已隐藏-疑似密钥].yaml（文件"不存在"）
# 分通道：observation 走 redact_text_tool（文件名/编号形态豁免，
# 熵结构论证见 redact.py）；密钥形态与值面强规则不变。
def test_observation_filename_shape_preserved_e2e(tmp_path):
    """【二十六轮第 6 批第 3 处政策反转后的口径】：
    文件名豁免分支已删除（高熵小写串曾穿越）——sk-config.yaml 这类文件名
    走强规则被打码（agent 要真名走审批）；编号形态 SK-2026-001 结构上
    确定不是密钥，仍原样。【路径真被走到】证据：observation 含 echo 的
    真实输出（打码掩码 + 编号原文），非"未知工具"错误。"""
    events, upstream = _mk_e2e_run(
        tmp_path, "echo will read sk-config.yaml and SKU: SK-2026-001 next")
    _assert_observation_really_ran(events, "SKU: SK-2026-001")
    obs = next(e for e in events if e["type"] == "observation")
    result = str(obs["payload"]["result"])
    assert "sk-config.yaml" not in result, f"文件名形态应走强规则：{result[:80]}"
    assert "[已隐藏-疑似密钥].yaml" in result, f"文件名应被打码：{result[:80]}"
    assert "SK-2026-001" in result, f"编号形态应原样：{result[:80]}"


def test_observation_real_secret_still_redacted_tool_channel(tmp_path):
    """弱一档不放松密钥覆盖：无扩展名的真密钥形态在 observation 仍被打码。"""
    needle = "sk-envSECRET1234567890abcdef"
    events, upstream = _mk_e2e_run(tmp_path, f"echo API_KEY={needle}")
    _assert_observation_really_ran(events, "API_KEY=")
    obs = next(e for e in events if e["type"] == "observation")
    assert needle not in str(obs["payload"]["result"]), "真密钥在工具通道泄漏"
    assert "[已隐藏" in str(obs["payload"]["result"])


# ═══ 二十六轮第 5 批第 1 处：弱通道豁免收紧（.yaml 真密钥四面 0）═══
# 架构事实：loop._redact 产物是 events/history/上游三面唯一来源——
# 第 4 批的"任意扩展名"豁免把 sk-….yaml 整类放行（四面各 1 次，实测）。
# 收紧后：真密钥 + 白名单扩展名 → 仍四面 0 明文。
def test_observation_filename_ext_secret_tightened_e2e(tmp_path):
    """真密钥伪装成 .yaml 文件名 → 四面明文必须 = 0（第 5 批收紧判据）。"""
    needle = "sk-envSECRET1234567890abcdef"
    events, upstream = _mk_e2e_run(tmp_path, f"echo API_KEY={needle}.yaml")
    _assert_observation_really_ran(events, "API_KEY=")
    raw_events = "".join(json.dumps(e, ensure_ascii=False) for e in events)
    raw_up = "".join(upstream)
    assert raw_events.count(needle) == 0, f"events 明文 {raw_events.count(needle)} 次"
    assert raw_up.count(needle) == 0, f"上游明文 {raw_up.count(needle)} 次"
    obs = next(e for e in events if e["type"] == "observation")
    assert needle not in str(obs["payload"]["result"]), "observation 明文"


# ═══ 二十六轮第 5 批第 4 处：usage full_label 无条件脱敏 ═══
def test_usage_full_label_unconditionally_redacted():
    """<30 字含密钥的首条消息（或标题）也必须打码——第 4 批版本的路径不对称。"""
    from app.main import _compose_usage_label  # noqa: 导入生产实现
    secret = "sk-envSECRET1234567890abcdef"
    # 路径①：<30 字标题（短任务）含密钥——needle 28 字符 + 1 字符前缀 = 29，
    # 必须真的 <30（第 5 批教训：34 字符的"短标题"会撞进回滚态的 len>=30
    # 分支被兜住，假绿）
    out1 = _compose_usage_label("[" + secret, "")
    assert secret not in out1, f"短标题路径明文泄漏：{out1}"
    # 29 字符（真 <30）；前缀必须是非词字符——"A"+secret 会让 sk- 的 
    # 失效（xxsk- 已知限制同族），测试就测不到打码路径了
    assert len("[" + secret) < 30
    # 路径②：>=30 字标题 + 含密钥首条消息
    out2 = _compose_usage_label("这是一条超过三十个字符的完整任务标题用于触发首条消息分支", f"处理 {secret} 迁移")
    assert secret not in out2, f"首条消息路径明文泄漏：{out2}"
    # 可读性：正常文本不被打没
    out3 = _compose_usage_label("普通任务标题", "")
    assert out3 == "普通任务标题"


# ═══ 二十六轮第 6 批第 1 处：usage label / model / 源头 title 脱敏 ═══
def test_usage_label_and_model_redacted():
    """第 5 批只修了 full_label，label（full_title[:42]）与 by_model 键仍裸奔。"""
    from app.main import _usage_label, _usage_model
    secret = "sk-envSECRET1234567890abcdef"
    title = "[" + secret  # 29 字符（源头 [:30] 内）
    out = _usage_label(title)
    assert secret not in out, f"label 明文泄漏：{out}"
    assert len(out) <= 42
    # 重命名任务（title 可到 100）label 仍截到 42
    long_title = "重" * 100
    assert len(_usage_label(long_title)) == 42
    # by_model 键（来源可以是标题里的 用量（…） 片段）
    assert secret not in _usage_model(f"用量（{secret}）")


def test_task_title_is_a_visible_identification_face():
    """★ 二十六轮第 6 批第 7 处回退锚点：标题是【用户识别面】，源头不得打码。

    历史（为什么回退）：
      第 6 批让源头 title 过 _redact_text，实测后果——输入
      "[sk-envSECRET…"（29 字）→ 列表/详情/落盘全是 "[[已隐藏]"（7 字），
      且 title 属性为 null ⇒ 用户【无处还原】自己的任务名；
      而同一密钥仍明文躺在 events.jsonl、history.json，重命名端点
      （main.py 的 task.title = req.title.strip()[:100]）更是从来不打码
      ⇒ 安全收益≈0，用户代价实打实。

    防泄漏的正确位置是【外发面】：usage 的 label / full_label / by_model
    各自过 _redact_text——那三条由 test_usage_label_and_model_redacted 钉住。

    本锚点的性质如实说明：**它是策略级（源码）断言**，不是行为断言——
    要断言的恰恰是"创建端点里没有那个函数调用"。行为面（用户能看见标题、
    usage 面 0 明文）分别由 test_usage_label_and_model_redacted 与本文件
    的 observation 组覆盖。
    """
    import inspect
    import app.main as m

    src = inspect.getsource(m)
    assert "_redact_text(input_text" not in src, (
        "创建端点又对 title 做源头打码了——标题是用户识别面，会被打成 [已隐藏] "
        "且无处还原（防泄漏请放在 usage 等外发面）"
    )
    assert "title=input_text.strip()[:30]" in src, (
        "创建端点的 title 表达式变了——请确认新写法仍是「原样截断、不打码」"
    )
    # 重命名端点必须与创建端点【口径一致】（此前是不一致的那一半）
    assert "_redact_text(req.title" not in src and "req.title.strip()[:100]" in src, (
        "重命名端点的 title 口径与创建端点不一致"
    )



def test_observation_env_ext_secret_redacted_e2e(tmp_path):
    """【二十六轮第 6 批第 3 处回滚组哨兵】.env + 真密钥：四面 0 明文。
    第 4 批宽豁免（任意扩展名）态下本例四面各 1 次（commit ec42483 实测）——
    redgreen harness 用它做"宽豁免回退"的红例。"""
    needle = "sk-envSECRET1234567890abcdef"
    events, upstream = _mk_e2e_run(tmp_path, f"echo API_KEY={needle}.env")
    _assert_observation_really_ran(events, "API_KEY=")
    raw_events = "".join(json.dumps(e, ensure_ascii=False) for e in events)
    raw_up = "".join(upstream)
    assert raw_events.count(needle) == 0, f"events 明文 {raw_events.count(needle)} 次"
    assert raw_up.count(needle) == 0, f"上游明文 {raw_up.count(needle)} 次"
    obs = next(e for e in events if e["type"] == "observation")
    assert needle not in str(obs["payload"]["result"]), "observation 明文"
