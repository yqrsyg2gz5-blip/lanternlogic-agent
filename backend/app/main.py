"""HTTP/SSE 边界（契约二）—— REST + SSE，路径 /api/v1。

wire 顺序（Phase 2 最小闭环）：POST /tasks → GET /tasks/{id}/events/stream（SSE 事件名 agent_event）
→ messages / cancel / approve / events / files。
本文件不做业务：状态机在 loop.py，执行在 executors/，模型在 providers/。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import re as _re
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from .approval import ApprovalManager
from .bus import EventBus
from . import pricing          # ★ 成本估算（P0-5）：没配单价就只报 token，绝不编数字
from . import budget           # ★ 2026-10-07：每个 Key 的花费上限（用户点名要的那道闸 ✓）
from . import permissions      # ★ 2026-10-07：按角色限权（测试只能看/跑，不能改 ✓）
from . import version          # ★ 2026-10-07：版本号唯一来源（版本检查 + 升级提示 ✓）
from . import author           # ★ 2026-10-08：作者卡（只读 + 防伪签名 ✓）
from . import login_guard      # ★ 2026-10-07：访问密码防爆破限流（第 5 项 ✓）
from . import audit            # ★ 2026-10-07：跨任务审计流水（第 9 项 ✓）
from . import envfacts         # ★ 环境交底：把「本机哪些工具真能用」写进工作单（真群回归暴露的坑）
from .config import load_config
from .config import config_path  # 写回路径与读同源（测试隔离的根）
from .executors import create_executor
from .loop import TaskRun, normalize_history
from .providers import create_provider
from .tts import get_tts_backend
from .skills import SkillRegistry
from pydantic import BaseModel, ValidationError

from .schemas import (
    ApproveReq,
    GroupApproveReq,
    CreateTaskReq,
    EventEnvelope,
    MessageReq,
    AutomationReq,
    ProjectReq,
    WideReq,
    TaskSummary,
    TTSReq,
)
from .store import FsStore
from .tools import openai_tools


def _make_console_encoding_safe() -> None:
    """把标准流转成 errors="replace"——从根上消除「emoji 打死启动」这一类。

    中文 Windows 控制台默认 GBK（ACP=936），print 里任何非 GBK 码位（⚠️/✅/箭头）
    都会抛 UnicodeEncodeError。若该异常出现在 **except 处理器内部**，它会冲出
    `_lifespan` ⇒ Starlette startup failed ⇒ 进程退出 —— 恰好把「设置页」
    这个唯一的自助修复入口关死，而那正是最需要它活着的时刻（首次运行/换机/Key 撤销）。

    受影响的已知点：main.py 自检失败横幅、store.py 索引损坏、kb.py 分片损坏。
    逐个删 emoji 治标；一次性改流编码治本，且对后续新增日志同样免疫。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except Exception:
            # pytest capsys / 已被替换的非 TextIOWrapper / 只读流：
            # 它们是无编码约束的内存缓冲，本就安全。
            pass


def _safe_print(*args: Any, **kwargs: Any) -> None:
    """永不抛出的 print：先探测目标编码，不可编码则降级（replace）而非抛出。

    专供 **except 处理器内部** 使用——那里抛出等于让启动失败；降级只损失一个
    符号，不影响用户读到「模型提供者不可用 + 怎么修」这句关键信息。
    """
    text = " ".join(str(a) for a in args)
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(enc)
    except (UnicodeEncodeError, LookupError):
        text = text.encode(enc, "replace").decode(enc, "replace")
    print(text, **kwargs)


_make_console_encoding_safe()


def _memory_block() -> str:
    return _memory_store.format_for_prompt()


from .redact import redact_text as _redact_text  # 打码管线已下沉为独立模块（验证报告 24）
# Phase 1 ①：Key 的【User 级环境变量】落点（HKCU\Environment；默认不写、回读校验、不回显）
from .uservenv import write_user_env
# ★ 2026-10-10 第二期：凭据库（照 DSH 的做法：一个文件、按名字存、改动前备份）
#   与上面那条**并存** ✓ —— 环境变量那条路一个字没动 ✓（老的 Key 不迁移、不删除）
from . import credentials as credentials_store
# Phase 2 ④：统一的「能力槽 + 提供者」声明表（一处声明，处处一致）
from .capabilities import status as capabilities_status
from .capabilities import fallback_hint




async def _extract_memory(task_id: str, provider: Any) -> None:
    """交付后自动提取长期记忆（第 41 班）。失败静默——记忆是增强，不是依赖。"""
    try:
        evs = store.read_events(task_id)
        # §2.1 打码：先取原始文本，再过 _redact
        user_raw = next((str(e.payload.get("text") or e.payload.get("content") or "") for e in evs
                        if e.type == "message" and e.payload.get("role") == "user"), "")[:2000]
        user_in = _redact_text(user_raw)
        # 群任务（员工派发）的语境不进全局记忆——员工人设/群内格式是临时身份，不提取
        if "【群任务" in user_in:
            print(f"[记忆] 群任务跳过提取（临时语境不进全局记忆）: {task_id}", flush=True)
            return
        asst = [e for e in evs if e.type == "message" and e.payload.get("role") == "assistant"]
        reply = _redact_text(str(asst[-1].payload.get("text") or "")[:2000]) if asst else ""
        if not user_in.strip():
            return
        from .memory import _EXTRACT_PROMPT, parse_extraction
        prompt = _EXTRACT_PROMPT.replace("{task_input}", user_in[:1500]).replace("{reply}", reply[:1500])
        turn = await provider.next_turn("", [{"role": "user", "content": prompt}], [])
        items = parse_extraction(turn.text or "")
        # 复审：对提取结果二次打码（纵深防御——提取器复述漏网 Key 时拦在落盘前）
        for it in items:
            if isinstance(it, dict) and it.get("content"):
                it["content"] = _redact_text(str(it["content"]))
        added = _memory_store.add(items, source_task=task_id)
        if added:
            print(f"[记忆] 从任务 {task_id} 提取并保存 {added} 条长期记忆", flush=True)
    except Exception as e:
        print(f"[记忆] 提取失败（不影响任务）：{type(e).__name__}: {e}", flush=True)

# ---------- 装配（配置解析失败 → 此处即抛，启动报错不静默回退） ----------

cfg = load_config()
# ★ 第 8f 处：记下**进程启动时真正绑定的地址**（uvicorn 用的就是当时的 cfg.server.host）。
#   运行中改配置不会改绑定，所以"是否已局域网可访问"必须看这个常量，不看配置。
_BOUND_HOST = cfg.server.host
store = FsStore(cfg.storage.data_dir)
bus = EventBus()
approval = ApprovalManager()
executor = create_executor(cfg.executor)
from .memory import MemoryStore
from .team import TeamStore, MAX_EMPLOYEES, MAX_MEMBERS
from .kb import KBStore

# 审计 §8：此前 KB/团队/记忆/TTS 目录硬编码 Path(__file__).parents[1]/"data"——
# PyInstaller 冻结后 __file__ 指向临时解包目录（sys._MEIPASS），桌面版每次重启数据清零。
# 统一从 store（即 cfg.storage.data_dir）派生，与任务数据同源可迁移。
_DATA_DIR = store.tasks_dir.parent
from .license import LicenseState as _LicenseState
_lic = _LicenseState(_DATA_DIR)
_lic.ensure_first_run()

_kb_store = KBStore(_DATA_DIR / "kb")
_team_store = TeamStore(_DATA_DIR / "team")
# ★ 2026-10-07（第 7 项）：任务 → **单次花费上限**（元）✓ 只记在内存里 ✓
#   为什么不用落盘：它来自"某个自动化这一次的运行" ✓ 任务结束/后端重启就该没了 ✓
#   （配置本身在 `automations.json` 里 ✓ 那份才是持久的 ✓）
_TASK_COST_CAPS: dict[str, float] = {}
# ★ 2026-10-07（第 9 项）：跨任务审计流水落在这个数据目录里 ✓（与其它存储同源 ✓ 可一起搬迁 ✓）
audit.bind(_DATA_DIR)
credentials_store.bind(_DATA_DIR)     # ★ 2026-10-10 第二期：凭据库落点（与其它存储同源 ✓）
_memory_store = MemoryStore(_DATA_DIR / "memory" / "memory.json",
                            max_entries=cfg.memory.max_entries)

tasks: dict[str, TaskSummary] = {t.id: t for t in store.load_index()}
runs: dict[str, TaskRun] = {}
projects: dict[str, dict[str, Any]] = {p["id"]: p for p in store.load_projects()}
skill_registry = SkillRegistry(cfg.skills.skills_dir)
automations: dict[str, dict[str, Any]] = {a["id"]: a for a in store.load_automations()}

# 会话级权限模式（对标 ZCode「完全访问/自动编辑/变更前确认」）
# 审计 §8.4：权限模式此前是进程级全局且不持久化——重启回 auto_edit，且影响所有在跑任务。
# 现持久化到数据目录（重启恢复上次选择；"影响在跑任务"是文档语义，前端有提示）。
def _load_access_mode() -> str:
    f = _DATA_DIR / "access_mode.txt"
    try:
        v = f.read_text("utf-8").strip()
        return v if v in ("full", "auto_edit", "confirm") else "auto_edit"
    except Exception:
        return "auto_edit"


def _save_access_mode(mode: str) -> None:
    try:
        (_DATA_DIR / "access_mode.txt").write_text(mode, "utf-8")
    except Exception:
        pass


access_mode: str = _load_access_mode()  # full | auto_edit | confirm
wide: dict[str, dict[str, Any]] = {w["id"]: w for w in store.load_wide()}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save_index() -> None:
    store.save_index(list(tasks.values()))


def _save_projects() -> None:
    store.save_projects(list(projects.values()))

def _save_automations() -> None:
    store.save_automations(list(automations.values()))

def _save_wide() -> None:
    store.save_wide(list(wide.values()))


def _new_task_id() -> str:
    """生成任务 id：`task_<日期>_<8 位 hex>`。

    ★ 第 9 批（C7 老账）：原来只有 **4 位 hex**（`token_hex(2)` ＝ 65536 种/天）。
      按用户真实数据（日均 43.4 个、最忙一天 77 个）算，**每天 1.4%～4.4% 概率撞 id**，
      一年累积约 **99.5%** 至少撞一次。撞了的后果是**静默毁数据**：
      两个任务共用同一个目录 ⇒ `events.jsonl`/`history.json` 混在一起、
      索引里互相覆盖，用户只会觉得"这任务怎么串了"。
      现在两件事一起做：
        ① 随机位加倍到 **8 位 hex**（42.9 亿种/天）；
        ② **仍然显式防碰撞**（撞了就重试）—— "概率低"不等于"不会发生"，
           而这里出事的代价是数据损坏，不值得赌。
    """
    for _ in range(50):                       # 正常情况下第一次就返回
        tid = f"task_{datetime.now():%Y%m%d}_{secrets.token_hex(4)}"
        if tid not in tasks and not (store.tasks_dir / tid).exists():
            return tid
    raise RuntimeError("生成任务 id 连续 50 次碰撞——极不正常（检查系统时钟或随机源）")


# ★ 步数预算分档（2026-10-05）：治"代码活必然被砍在半路"。
#   依据：MetaGPT 的做法是"工程师自己写单测、真跑、看报错、改"直到通过（上限 3 次重试）——
#   那是**迭代**，25 步放不下。实测：一个测试任务跑了 280 条事件仍在调试，被步数/熔断掐死 ✗。
_BUDGETS = {
    # 角色关键词 → 步数预算
    "测试": 60, "QA": 60,
    "工程": 60, "开发": 60, "程序": 60, "前端": 60, "后端": 60, "全栈": 60,
    "架构": 40, "设计": 40,
    "文案": 25, "文档": 25, "运营": 25, "调研": 30,
}


def _budget_for(role: str, task_text: str = "") -> int:
    """按角色给步数预算（"写-跑-改"的活给足；纯写字/查资料的活不用给太多）。

    ★★ 2026-10-06 加一档：**项目终验（验收）步数掐到 12** ✓ ——
      实测（第 9 轮真跑 ✓）：验收那一步 **10 次调用 / 83,784 tok**，占全场 **18%** ✓；
      它该干的只有"跑一两条命令 + 给结论" ✓，**不该探索、不该写东西** ✗。
      给 60 步它就会慢慢逛 ✓；给 12 步足够"跑一下 + 报结论"，真跑不完也说明工作区有问题 ✓。
    """
    hay = f"{role} {task_text}"
    # ★ 验收类先判（它的任务书里带"验收"，而角色可能就是"测试工程师" ⇒ 会被下面按 60 步放走 ✗）
    # ★★ 2026-10-06 当晚修正：**12 步太紧** ✗ —— 真跑实测：验收人在第 12 步被掐断，
    #   群里的原话是"步数用完了（达到最大迭代次数）"✓，而它当时**已经跑了 12 次调用 / 10.2 万 tok**
    #   还没写完结论 ✓ ⇒ 整项判失败、验收门"没开到" ✗✓（那一跑干活那项本来是 1/0/0 满分 ✓）。
    #   验收要干的事比我想的多：列文件 ✓ 跑测试 ✓ 跑主命令 ✓ 对不上还得复跑 ✓ 再写结论 ✓。
    #   ⇒ 放宽到 **20**（仍远小于普通活儿的 60 ✓ 但够它跑完并出结论 ✓）。
    if "项目验收" in hay or ("验收" in hay and "确认性" in hay):
        return 20
    for key, n in _BUDGETS.items():
        if key in hay:
            return n
    return 40          # 认不出来的活给个中庸值（比 25 宽，但不至于失控）


def _task_spend_cny(task_id: str) -> float:
    """这个任务**到目前为止**花了多少钱（含"正在跑"的那份快照 ✓ 与 `/usage` 同一口径 ✓）。

    ★ 单次成本上限要靠它 ✓ —— 而"跑到一半"也必须算进去 ✓
      （不算的话，一个跑飞的任务在**跑完之前**永远看不到自己超了 ✗ 那就拦不住 ✓）
    """
    spent = 0.0
    for row in budget.spend([task_id], store.read_events).values():
        spent += float(row.get("cny") or 0.0)
    live = _inflight_usage(task_id)
    if live and int(live.get("calls") or 0) > 0 and str(live.get("run_id") or "") not in _event_run_ids(task_id):
        spent += float(budget.event_cny(live) or 0.0)
    return round(spent, 4)


def _event_run_ids(task_id: str) -> set[str]:
    """这个任务里**已经正式记账**的那些运行 id ✓（用来避免与快照重复计 ✓ 同 `_task_usage` ✓）。"""
    out: set[str] = set()
    try:
        for e in store.read_events(task_id):
            if e.type != "knowledge":
                continue
            u = (e.payload or {}).get("usage")
            if isinstance(u, dict) and u.get("run_id"):
                out.add(str(u["run_id"]))
    except Exception:                                # noqa: BLE001
        pass
    return out


def _budget_block_reason(task_id: str = "") -> str | None:
    """花钱的两道闸：**单次成本上限**（第 7 项）+ **每个 Key 上限**（第 1 项）✓。

    ★ 两把锁管的是两件事 ✓（不是重复 ✗）：
      · 每个 Key 上限 ⇒ 管**总量**（这个月这把钥匙一共别超过多少 ✓）
      · 单次成本上限 ⇒ 管**单次**（定时任务最怕"某一次跑飞了"✓ 一次烧掉一天额度 ✓）
    两者都用**同一份账**（任务里的用量事件 + 那份"正在跑"的快照 ✓）✓ 不另记一本 ✗。

    ★ 失败一律**放行** ✓（读不到账/配置坏了 ⇒ 返回 None ⇒ 不拦 ✓）：
      宁可漏报，也不能因为查账出错就把用户正在干的任务无缘无故掐掉 ✗。
    """
    try:
        # ① 单次成本上限（只对**配了**的任务生效 ✓ 没配 = 不拦 ✓ 全项目"默认等于现状" ✓）
        cap = float(_TASK_COST_CAPS.get(task_id, 0.0) or 0.0)
        if cap > 0 and task_id:
            spent = _task_spend_cny(task_id)
            if spent >= cap:
                why = (f"这个自动化配了「**单次花费上限** ¥{cap:.2f}」，本次已花约 ¥{spent:.4f} —— "
                       "**这一步没有发出去** ✓（免得一次跑飞了烧掉一天的额度 ✓）。\n"
                       "三条路：① 把那个自动化的「单次上限」调大 ✓ "
                       "② 把任务描述写小一点 / 拆成几步 ✓ "
                       "③ 先手动跑一次，看看它到底卡在哪 ✓")
                audit.record("blocked", gate="单次花费上限", task=task_id, detail=f"已花 ¥{spent:.4f} / 上限 ¥{cap:.2f}")
                return why
        # ② 每个 Key 的上限（第 1 项那把锁 ✓ 口径一个字没改 ✓）
        _key_why = budget.blocking_reason(cfg, list(tasks.keys()), store.read_events)
        if _key_why:
            audit.record("blocked", gate="每个 Key 花费上限", task=task_id, detail=_key_why[:200])
        return _key_why
    except Exception:                                # noqa: BLE001
        return None


def _start_run(
    task: TaskSummary,
    input_text: str,
    system_extra: str | None = None,
    provider: Any | None = None,
    max_iterations: int | None = None,   # ★ 步数预算（群任务按角色分档；不给用配置默认）
    role: str | None = None,             # ★ 当前身份（限权用）；不给 = 读落盘的那个 ✓
) -> TaskRun:
    """每个运行新建 provider 实例（mock 有游标状态，不能跨任务共享）。

    provider 允许由调用方预先实例化后传入（`_launch_task` 就是这么做的）——
    这样"缺 Key / 配置错"能在任务落盘**之前**报出来，不会留下卡在 created 的僵尸任务。
    """
    run = TaskRun(
        task,
        input_text,
        store=store,
        bus=bus,
        provider=provider if provider is not None else create_provider(cfg.model),
        executor=executor,
        approval=approval,
        tools=openai_tools(),  # 现取：MCP 工具在 lifespan 才注册，模块级快照会漏（第 41 班修）
        # ★ 2026-10-05：群任务按角色分档给步数（见 _budget_for）—— 代码活要「写-跑-改」迭代，
        #   25 步必然被砍在半路（实测：一个测试任务跑了 280 条事件仍没做完，被步数/熔断掐死）。
        max_iterations=(max_iterations if max_iterations is not None else cfg.model.max_iterations),
        max_tokens=cfg.model.max_tokens,
        max_context_tokens=cfg.model.max_context_tokens,
        timeout_seconds=cfg.executor.timeout_seconds,
        approval_required=cfg.executor.approval_required,
        system_extra=system_extra,
        memory_block=_memory_block() if cfg.memory.enabled else None,
        skills_meta=skill_registry.list_skills(),
        launch_wide=lambda inp, items: _start_wide(inp, items, task.project_id),
        video_cfg=cfg.video.model_dump() if cfg.video.provider else None,
        image_cfg=cfg.image.model_dump(),  # 第 41 班：video 独立配置段（原 model_extra 读法是死路）
        access_mode_getter=lambda: access_mode,
        skill_registry=skill_registry,
        # ★ 2026-10-07「每个 Key 花费上限」+「单次成本上限」那道闸（第 1、7 项）——
        #   账只有一份：任务里的**用量事件**（就是 /usage 读的那份 ✓）见 app/budget.py ✓
        #   ★ 这里**绑定这个任务的 id** ✓ —— 单次上限是"按任务"的 ✓ 不绑就判不了 ✓
        budget_check=(lambda _tid=task.id: _budget_block_reason(_tid)),
        # ★ 2026-10-07「按角色限权」（用户点名要的）：这个运行**以谁的身份**跑 ✓
        #   不给就读落盘的那个 ✓（群里的接力/续跑是新开一次运行 ⇒ 身份必须跟着走 ✓）
        #   空 = 默认助手 = 不受限 ✓（普通单聊一律走这条 ✓ 对现有行为零影响 ✓）
        role=(str(role) if role is not None else store.identity(task.id)),
        on_finish=lambda r: (_save_index(), runs.pop(task.id, None), _notify_done(task.title, task.id, r),
                             approval.forget_task(task.id),  # §8.9：审批记忆随任务结束清理（防累积泄漏）
                             _spawn_bg(_extract_memory(task.id, r.provider)) if cfg.memory.enabled and getattr(r, "provider", None) else None),
    )
    runs[task.id] = run
    run.aio_task = run.start()
    return run


def _get_task(task_id: str) -> TaskSummary:
    task = tasks.get(task_id)
    if task is None:        raise HTTPException(404, f"任务不存在：{task_id}")
    return task


def _local_ip() -> str:
    """本机局域网 IP（手机直连用）。"""
    import socket as _s
    try:
        s = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


async def _leader_plan(leader_emp: dict[str, Any], prompt: str) -> str:
    """用某个员工绑定的大脑跑一次**纯文本**调用，返回原始文本（供 JSON 解析或直接展示）。

    ★ 2026-10-05 用户实测：开会收口时这里抛 `RuntimeError: 组长大脑返回空结果`，
      结果纪要变成一句报错、用户"最后什么都没拿到"。空响应是**上游偶发**（与任务那条路
      已有的"空响应重试"同源），所以这里补一次重试；报错文案也改成角色中立的说法
      （开会时它是"主持"，不一定是组长）。
    """
    mc = cfg.model
    if leader_emp.get("provider"):
        mc = cfg.model.model_copy(update={
            "provider": leader_emp["provider"],
            "model_name": leader_emp.get("model_name") or mc.model_name,
            "base_url": leader_emp.get("base_url") or mc.base_url,
            "api_key_env": leader_emp.get("api_key_env") or mc.api_key_env,
        })
    last_err = ""
    for attempt in (1, 2):
        provider = create_provider(mc)
        turn = await provider.next_turn("", [{"role": "user", "content": prompt}], [])
        result = (turn.text or "").strip()
        # ★ 2026-10-05 真群回归连挂 5 轮的根因：模型看到"只输出 JSON"这类指令会**直接调 task_done**，
        #   把答案塞进**工具参数**里（实测：text 是空或"任务已完成，交付结果见下方 task_done"这种占位语，
        #   而 arguments = {'outcome': 'success', 'message': {'ok': True}}）。
        #   这里把答案从工具参数里**救回来**（能救就不算失败，也就不会再抛"空响应"）。
        salvaged = _salvage_from_tool_call(turn)
        if salvaged:
            return salvaged
        if result and "task_done" not in result and "任务已完成，交付结果见下方" not in result:
            return result
        last_err = "模型返回空结果"
        if attempt == 1:
            await asyncio.sleep(1.2)          # 上游偶发空响应：等一下再要一次
    raise RuntimeError(last_err or "模型没有返回内容")


def _salvage_from_tool_call(turn: Any) -> str:
    """从工具调用里把答案救回来（真群实测：模型把 JSON 塞进 task_done 的 message 参数）。

    只认"看起来像答案"的内容：message 是 dict/list ⇒ 序列化成 JSON（让 parse_verdict /
    parse_leader_plan 能直接解析）；是字符串 ⇒ 原样用；否则退回整个参数对象。
    拿不到有用内容就返回空串（调用方照旧走重试/报错，不假装成功）。
    """
    tc = getattr(turn, "tool_call", None)
    if tc is None:
        return ""
    args = getattr(tc, "arguments", None)
    if not isinstance(args, dict) or not args:
        return ""
    for key in ("message", "text", "content", "result", "summary", "answer"):
        payload = args.get(key)
        if isinstance(payload, (dict, list)) and payload:
            return json.dumps(payload, ensure_ascii=False)
        if isinstance(payload, str) and payload.strip():
            return payload.strip()
    # 参数本身就是答案（比如 {"ok": true} 或 {"pass": true, ...}）
    if len(args) <= 8:
        return json.dumps(args, ensure_ascii=False)
    return ""


_GROUP_TIMEOUT_NOTICE = (
    "⏳ 任务超过 30 分钟未结束，交付不再自动回流（任务仍在后台）。"
    "可到任务页查看进展（本条只提醒一次）。"
)

# ★ 看门器续挂：任务还活着就继续盯着（只为播报审批），最多续这么多轮（每轮 = 一次 timeout_s）。
#   意义：用户实测"超过 30 分钟后群里不再提示审批 ⇒ 任务静默卡死，看起来像停了"。
_GROUP_WATCH_MAX_REARMS = 8
_GROUP_WATCH_REARMS: dict[str, int] = {}

# ★ 2026-10-06：验收人"没说清结论"时的**复问前缀** ✓
#   实测它会返回一条 shell 命令想自己去看文件 ✗；这一句把话说死（只要那段 JSON ✓）。
_STRICT_VERDICT_NUDGE = (
    "【重要】上一条你没有给出可解析的结论。**只回下面那一段 JSON** ✓，"
    "不要任何解释、不要 Markdown 代码块以外的文字、**不要调用任何工具** ✗。"
    + chr(10) + chr(10)
)


def _group_watch_rearms(task_id: str) -> int:
    """当前任务已续挂了几轮（内存计数；重启后归零 = 允许再盯，符合"别让群里瞎掉"的目标）。"""
    n = _GROUP_WATCH_REARMS.get(task_id, 0)
    _GROUP_WATCH_REARMS[task_id] = n + 1
    return n


def _has_broadcast_batch(gid: str) -> bool:
    """这个群当前有没有**广播批次** ✓。

    ★ 写成独立函数是为了**容错** ✗：看门者在测试里常被塞一个**精简的假 store**
    （只有 `append`/`feed` 之类），直接 `_team_store.get_group(...)` 会 AttributeError ✓
    —— 本班加广播门时就当场把一条老测试搞红了 ✓。
    """
    try:
        g = _team_store.get_group(gid) or {}
    except Exception:                                       # noqa: BLE001
        return False
    return bool(isinstance(g, dict) and g.get("broadcast_batch"))


async def _broadcast_after_delivery(gid: str, task_id: str, name: str, reply: str) -> bool:
    """★ 广播模式的**项目终验门**（2026-10-06）✓ —— "做完 ≠ 能跑"与模式无关 ✓。

    两种情形：
    1. **这道终验自己交付了** ⇒ 按交付正文宣布 ✅/❌ ✓（并在不通过时说清后果 ✓）
    2. **普通广播任务交付了** ⇒ 划掉一个 ✓；**全交齐时**派一道项目终验 ✓（走现成的任务+看门者 ✓）

    返回 True 表示"这条交付归广播门管" ✓（看门者据此不再走别的分支 ✓）。
    """
    # 1) 终验交付 ⇒ 宣布结论
    if _team_store.broadcast_gate_done(gid, task_id):
        ok = "✅ 项目验收通过" in reply or "项目验收通过" in reply
        bad = "❌ 项目验收不通过" in reply or "项目验收不通过" in reply
        head = ("✅ **项目验收通过** —— 这个项目现在是能跑的 ✓"
                if ok else "❌ **项目验收不通过** —— 群里已按缺陷回环打回给责任方重修" if bad
                else "⚠️ **项目验收这道门没给出明确结论** —— 结果见上面的交付，需要人看一眼 ✓")
        _team_store.append(gid, **{"from": "system", "text": (
            head + chr(10)
            + "（这道门回答的是「这东西到底能不能跑」—— 广播模式下**每个人都交了**不等于能跑 ✓）"
        ), "task_id": task_id})
        return True
    # 2) 普通任务交付 ⇒ 划掉；最后一个 ⇒ 派终验
    batch = _team_store.broadcast_settle(gid, task_id)
    if batch is None:
        return False
    g = _team_store.get_group(gid) or {}
    members = [e for e in (_team_store.get_employee(m) for m in (g.get("members") or [])) if e]
    who = None
    for e in members:
        if any(k in f"{e.get('role', '')} {e.get('name', '')}" for k in ("测试", "QA", "质量")):
            who = e
            break
    who = who or (members[0] if members else None)
    if who is None:
        _team_store.append(gid, **{"from": "system", "text": (
            "⚠️ 广播这批活都交了，本该做**项目验收**（真跑一遍看能不能用），但群里没人可派 ⇒ 跳过。"
            "（「都交了」**不代表项目能跑** ✓）"
        )})
        return True
    text = _project_acceptance_task(str(batch.get("goal") or ""), "")
    _team_store.append(gid, **{"from": "system", "text": (
        f"🔎 广播这批都交了 —— 最后一道门：**项目验收**（交给 @{who['name']}）" + chr(10)
        + "不再只看「每项都交了」，而是**在群工作区里真跑一遍**，确认这东西真的能用 ✓"
    )})
    try:
        task = _launch_task(text, None, workdir=store.group_workspace(gid))   # 工作区要指对 ✗
    except Exception as e:                                  # noqa: BLE001
        _team_store.append(gid, **{"from": "system", "text": f"⚠️ 项目验收派不出去：{type(e).__name__}"})
        return True
    _team_store.broadcast_attach_gate(gid, task.id)
    _spawn_bg(_watch_group_delivery(task.id, who["name"], gid, broadcast_gate=True))
    return True


async def _watch_group_delivery(task_id: str, name: str, gid: str,
                                timeout_s: float = 1800, poll_s: float = 5.0,
                                relay_pos: int | None = None,
                                leader_name: str | None = None,
                                broadcast_gate: bool = False) -> None:
    """盯住一个派给员工的群任务：结束就把交付回流到群聊；**超时才提醒一次**。

    ★ 二十六轮第 7 批第 7a 处（用户报"他就不停的提醒这个东西"）：
      原先这段逻辑在 `_dispatch_to_employee` 与 `POST groups/{gid}/say` 里
      **各写了一份**，而两份都是：
          timed_out = True                      # ← 一开始就是真
          deadline = now + 1800
          while now < deadline:
              sleep(5); if 任务结束: 交付; break
              if timed_out: 追加("⏳ 超过 30 分钟未结束…")   # ← 每 5 秒一条
      ⇒ 任务刚派出去 5 秒就喊"超过 30 分钟"，30 分钟能刷 360 条。
      现在：一份实现 + 正确语义（结束即回流；真满 30 分钟才提醒一次，且只提醒一次）。

    timeout_s / poll_s 可注入，是为了让单测不必真等 30 分钟
    （见 tests/test_group_watch.py）。默认值即生产口径：1800 秒 / 5 秒。
    """
    from datetime import datetime as _dt, timezone as _tz
    deadline = _dt.now(_tz.utc).timestamp() + timeout_s
    while _dt.now(_tz.utc).timestamp() < deadline:
        await asyncio.sleep(poll_s)
        t = tasks.get(task_id)
        # ★ 2026-10-05 用户要求：**审批就地能批**（"他们的工作内容、允许一次都该在群里，
        #   我还得上外面点，多麻烦"）。所以看门的同时把"待审批"搬进群：
        #   一次审批只播报一次（落盘去重，重启也不会重复），点群里的按钮即放行。
        if t is not None and t.status == "waiting_approval":
            await _announce_group_approval(gid, name, task_id)
        if t is None or t.status in ("done", "idle", "failed", "cancelled", "partial"):
            evs = store.read_events(task_id)
            asst = [e for e in evs if e.type == "message" and e.payload.get("role") == "assistant"]
            # ★★ 2026-10-06（**今天大半失败的单一根因** ✗✗）：
            #   这里原来 `[:800]` 砍交付正文 ✓，`verification_prompt` 里又 `[:500]` 再砍一刀 ✗ ——
            #   而交付的**小节顺序**是「改动文件 → 自测命令 → 真实输出」✓ ⇒
            #   **"真实输出"正好落在 800 字之后** ✗ ⇒ 验收人**永远看不到那段输出** ✓✓
            #   ⇒ 它每次都说"没贴输出" ✓ **而它说的是实话** ✓（我们前面还怪它太苛刻 ✗）。
            #   实测某次真实交付正文 **2558 字**，命令和输出全在后半段 ✓。
            #   ⇒ 放宽到 4000（群里显示有折叠 ✓ 长一点没关系 ✓）。
            reply = str(asst[-1].payload.get("text") or "")[:4000] if asst else "（任务结束）"
            atts = (asst[-1].payload.get("attachments") if asst else None) or []
            _team_store.append(gid, **{"from": f"emp:{name}", "text": reply,
                                       "task_id": task_id, "status": t.status if t else "done",
                                       "attachments": atts})
            # ★ 失败要说清"为什么" + "那怎么办"（用户实测：任务跑到一半没了，群里只有一句空话）
            if t is not None and t.status in ("failed", "partial"):
                why = _task_failure_reason(task_id)
                if why:
                    _team_store.append(gid, **{"from": f"sys:{name}", "text": (
                        f"❌ @{name} 这一步没做成：{why}"
                    ), "task_id": task_id, "status": t.status})
            # ★ P0-5 成本可见：每一步花了多少，群里直接看得到（多智能体约 15× 普通对话的 token，
            #   用户有权知道钱花在哪）
            cost = _usage_line(task_id, f"@{name} 这一步")
            if cost:
                _team_store.append(gid, **{"from": "system", "text": cost, "task_id": task_id})
            # ★ 接力模式：这一棒交付了 ⇒ 把**交付内容**当上游，派给下一棒
            if relay_pos is not None:
                _relay_after_delivery(gid, name, reply, int(relay_pos), str(t.status if t else "done"))
            # ★ 组长波次（P0-1）+ 验收（P0-2）：交付后**先验收**，通过才推进下一批
            elif leader_name:
                await _verify_delivery(gid, leader_name,
                                       (t is None or t.status not in ("failed", "cancelled")),
                                       reply, list(atts or []))
            # ★ 2026-10-06：**广播模式的终验门** ✓ —— 非组长路径也得有人回答"能不能跑" ✓
            #   （`broadcast_gate` = 这道任务本身就是广播批次的终验 ✓；
            #     后半句 = 这条交付属于某个广播批次 ⇒ 划掉一个、全交齐就派终验 ✓）
            elif broadcast_gate or _has_broadcast_batch(gid):
                await _broadcast_after_delivery(gid, task_id, name, reply)
            return
    # 走到这里 = 满 timeout 仍未结束
    # ★ 2026-10-05（全量试跑抓到的真 bug）：**只要任务还活着，就不能让群里"瞎掉"**。
    #   实测现场：测试那一步超过 30 分钟后群里只剩一句"交付不再自动回流"，
    #   随后它**卡在一次审批上**（`$(mktemp -d)` 这类无法静态判定的命令）——
    #   而审批卡**再也没人播报到群里**，用户那边看起来就是"它停了"（事件文件 6 分钟没动）。
    #   所以：任务还在跑 ⇒ **重新挂一轮看门**（继续播报审批；超时提醒靠落盘去重，不会重复刷屏）。
    _run = runs.get(task_id)
    _alive = (_run is not None and getattr(_run, "aio_task", None) is not None
              and not _run.aio_task.done())
    if _alive and _group_watch_rearms(task_id) < _GROUP_WATCH_MAX_REARMS:
        _team_store.append(gid, **{"from": "system", "text": (
            f"⏳ @{name} 这一步超过 30 分钟还没结束，**任务仍在后台跑**。" + chr(10)
            + "· 群里仍会提示**审批**（它卡在审批上时你点一下就行）；交付不再自动回流。" + chr(10)
            + "· 想看细节去任务页；不想等了可以说「接着跑」重新排它。"
        ), "task_id": task_id, "status": "running"})
        if relay_pos is not None:
            _team_store.relay_stop(gid, f"第 {relay_pos} 棒超时未结束")
        _spawn_bg(_watch_group_delivery(task_id, name, gid, timeout_s=timeout_s,
                                        poll_s=poll_s, relay_pos=relay_pos,
                                        leader_name=leader_name))   # ★ 见下：漏传它是真 bug
        return
    # ★★ 2026-10-06 根因修复（现场："终验交付了，群里没反应，我发一句话才补上" ✗）：
    #   **续挂时必须把 leader_name 原样传下去** ✗ —— 之前漏传 ⇒ 续挂后的看门者交付时
    #   走的是"没有组长"那条分支 ⇒ **不验收、不推进波次** ✗✗，那一项就一直停在 running，
    #   直到有人再往群里说一句话触发对账才被补认领 ✓（用户看到的就是"偶尔认领不到"）。
    # ★ 2026-10-05：落盘去重。用户实测同一任务刷了 15–43 条（那个群共 297 条）——
    #   后端每次重启都会再挂一个看门者，各发各的；内存标记撑不过重启，所以查群记录。
    if not _team_store.has_notice(gid, task_id, "超过 30 分钟"):
        _team_store.append(gid, **{"from": f"sys:{name}", "text": _GROUP_TIMEOUT_NOTICE,
                                   "task_id": task_id, "status": "running"})
    # ★ 接力最怕"卡住不动"：超时也要**明说停在哪里**并把位置归零，否则整条链看着像还在跑
    if relay_pos is not None:
        _team_store.relay_stop(gid, f"第 {relay_pos} 棒超时未结束")
        _team_store.append(gid, **{"from": "system", "text": (
            f"⚠️ 接力停在第 {relay_pos} 棒（@{name} 超时未结束）。"
            "要接着跑：把它的活重派一次，或再发一次目标重新开始接力。"
        )})


def _dispatch_leader_batch(gid: str, batch: dict[str, Any],
                           name_to_id: dict[str, str]) -> list[dict[str, Any]]:
    """派一批（波次）活：每项的工作单都带"目标 + 交付要求 + 边界 + 前置产物"。

    ★ P0-1 的核心：**有前置的活只有在前置交付之后才会出现在 ready 里**（由 team.leader_ready 决定）。
    """
    g = _team_store.get_group(gid) or {}
    out: list[dict[str, Any]] = []
    for item in batch.get("ready") or []:
        nm = str(item.get("name") or "").strip()
        # ★★ 2026-10-06（评测台第六轮：**整批一项都没开工、静默卡死** ✗✗）：
        #   组长把名字写成 **`@评测乙`**（带 @ ✓），而按名字找人时没去掉 @ ✗ ⇒
        #   找不到人 ⇒ 那一项被跳过 ⇒ **"共 1 项，先开工 0 项"** ✓（用户看到的就是"它卡住了" ✓）。
        #   `depends_on` 那边早就 strip 过 @ ✓，名字这边漏了 ✓ ⇒ 补上，并且**兜一下**
        #   "带后缀/带空格"的写法（`名字·验收` 已有别名容错 ✓ 这里再把 @ 和全角空格一起清 ✓）。
        nm = nm.lstrip("@＠ ").strip()
        # ★★ 2026-10-06（评测台第 7 轮：**还是整批 0 开工** ✗✗）：
        #   现场：组长把名字写成 **`评测乙…（技术部·程序员）`** —— 名字后面**带了职务括号** ✗，
        #   而按名字找人用的是**精确匹配** ⇒ 找不到 ⇒ 那一项被跳过 ✓
        #   （它其实就**在**群里 ✓ —— 群里那句提示把成员都列出来了，一眼就能看出是这么回事 ✓）。
        #   处理两层：① 去掉尾部的 `（…）` / `(…)` 说明 ✓；② 再不行就**前缀模糊匹配** ✓
        #   （取"以某个成员名开头"里**最长**的那个 ✓ —— 防"张三丰"被误配成"张三" ✓）。
        nm = re.sub(r"[（(][^）)]*[）)]\s*$", "", nm).strip()
        if nm not in name_to_id:
            for _cand in sorted(name_to_id, key=len, reverse=True):
                if nm.startswith(_cand):
                    nm = _cand
                    break
        # ★ 别名容错（2026-10-05）：验收项叫「<人名>·验收」（避免与那个人原有的项同名 ✗），
        #   派发时要认得它对应的是谁。按"·"前的主名兜一下即可。
        if nm not in name_to_id:
            base = nm.split("·")[0].strip()
            if base in name_to_id:
                name_to_id = {**name_to_id, nm: name_to_id[base]}
        if nm not in name_to_id:
            # ★ 兜底也要**认得出来**：把所有成员名都列出来（人是可以照着改的 ✓，
            #   总比一句"不在本群"让人干瞪眼强 ✓）
            _team_store.append(gid, **{"from": "system", "text": (
                f"⚠️ 分工单里的 @{nm} 不在本群（或已删除），这一项跳过。" + chr(10)
                + f"（本群成员：{'、'.join(sorted(name_to_id)) or '（空）'} —— "
                  "如果你认得出来是谁，回一句「@<真名> 做 …」就能接上 ✓）"
            )})
            continue
        emp = _team_store.get_employee(name_to_id[nm])
        if emp is None:
            continue
        try:
            task = _dispatch_to_employee(gid, g, nm, emp, _team_store.leader_handoff_text(g, item),
                                         leader_name=nm)
            _team_store.leader_attach_task(gid, nm, task.id)
            out.append({"name": nm, "task_id": task.id, "task_summary": str(item.get("task"))[:100],
                        "after": item.get("depends_on") or []})
        except Exception as e:
            print(f"[组长] 派发 {nm} 失败: {type(e).__name__}: {e}", flush=True)
            _team_store.append(gid, **{"from": "system", "text": (
                f"⚠️ 派给 @{nm} 失败：{type(e).__name__}: {str(e)[:120]}"
            )})
    if batch.get("blocked"):
        _team_store.append(gid, **{"from": "system", "text": (
            "⛔ 这些活被前置挡住了，不会派人去瞎做：" + chr(10)
            + chr(10).join(f"- @{b['name']}：{b['why']}" for b in batch["blocked"])
            + chr(10) + "（修好前置后，重新发一次目标即可继续）"
        )})
    if batch.get("released"):
        _team_store.append(gid, **{"from": "system", "text": (
            "⚠️ 分工单里有互相等待（成环）的项，已全部放行：" + "、".join("@" + n for n in batch["released"])
        )})
    return out


_PATH_RE = re.compile(r"[A-Za-z0-9_\-./\\]+\.(?:py|md|txt|json|js|ts|tsx|html|css|ya?ml|toml|ini|cfg|sh|ps1|csv|log)\b")


def _paths_from_reply(reply: str) -> list[str]:
    """从交付正文里兜出**文件路径**（附件清单为空时的救命稻草）。

    ★ 2026-10-06 真群实测：程序员的 `todo.py` **明明写进了工作区** ✓，
      可它这一版**没声明附件** ✗ ⇒ 低层验收直接判"没有交付任何文件"⇒ 整步失败 ✗
      （那一轮它因此连挂 3 次被判失败、下游全被挡住，账单还多烧 24 万 tok）。
      所以：附件空的时候，从正文里找路径 —— 但**只认工作区里真实存在的**，
      免得把"我打算建 xxx.py"这种话当成交付（宁可漏认，不可误认）。
    """
    out: list[str] = []
    for m in _PATH_RE.finditer(str(reply or "")):
        raw = m.group(0).replace("\\", "/").lstrip("./")
        if raw and raw not in out:
            out.append(raw)
    return out[:20]


def _low_level_check(task_id: str, attachments: list[str],
                     reply: str = "", item: dict[str, Any] | None = None) -> tuple[bool, str]:
    """**低层验收**（机器查，不花模型钱）：交付里声称的产物，文件到底在不在。

    依据：MAST 的 FC3（任务验证占 21.3%）里，"验证只做表面功夫"是主因之一；
    所以第一关必须是**确定性的**：声称给了 `docs/api.md`，就去工作区看这个文件在不在、是不是空文件。

    ★ 2026-10-05 第 4 条（结构化交付）：这一关还多查一件确定的事 ——
      **该有的小节有没有**（设计类要"文件清单/接口定义/数据结构"、实现类要"改动文件/自测命令"、
      测试类要"用例清单/运行结果"）。缺了就是不合格，直接打回，不用模型判（省钱且不会放水）。
    """
    try:
        need = TeamStore.required_sections(str((item or {}).get("task") or ""),
                                           str((item or {}).get("output") or ""))
    except Exception:
        need = []
    missing_secs = TeamStore.missing_sections(reply, need)
    if missing_secs:
        return (False, "交付里缺必需小节：" + "、".join(missing_secs)
                + "（按工作单的【交付格式】补上再交）")
    # ★ 2026-10-06：附件清单为空时，先试试**从交付正文里兜路径**（且只认工作区里真实存在的）——
    #   实测：文件明明写进工作区了，可这一版没声明附件 ⇒ 被判"没有交付任何文件"⇒ 连挂 3 次失败 ✗
    if not attachments and reply:
        try:
            _ws = store.workspace_dir(task_id).resolve()
            _guessed = [p for p in _paths_from_reply(reply) if (_ws / p).is_file()]
        except Exception:
            _guessed = []
        if _guessed:
            attachments = _guessed
            _note_guess = "（附件清单为空，已按交付正文里的路径核对到：" + "、".join(_guessed[:5]) + "）"
        else:
            _note_guess = ""
    else:
        _note_guess = ""
    if not attachments:
        return (False, "没有交付任何文件（若这一步本该产出文件，则不合格）")
    try:
        ws = store.workspace_dir(task_id).resolve()
    except Exception as e:
        return (False, f"取工作区失败：{type(e).__name__}")
    miss, empty, ok = [], [], []
    for rel in attachments[:20]:
        p = (ws / str(rel)).resolve()
        try:
            p.relative_to(ws)                    # 不许越界
        except ValueError:
            miss.append(f"{rel}（路径越界）")
            continue
        if not p.exists():
            miss.append(rel)
        elif p.is_file() and p.stat().st_size == 0:
            empty.append(rel)
        else:
            ok.append(rel)
    parts = []
    if _note_guess:
        parts.append(_note_guess)
    if ok:
        parts.append(f"在：{'、'.join(ok)}")
    if empty:
        parts.append(f"空文件：{'、'.join(empty)}")
    if miss:
        parts.append(f"**找不到**：{'、'.join(miss)}")
    return (not miss and not empty, "；".join(parts))


def _pick_verifier(g: dict[str, Any], author: str) -> tuple[dict[str, Any] | None, str]:
    """挑验收人：**不能是干活的本人**（自己说自己合格不算验收）。

    优先级：角色像质检的（测试/审校/审查/质检/QA）→ 组长 → 群里其他任一成员。
    群里只有他一个人 ⇒ 返回 (None, 原因)，调用方要**如实说明没人可验**，不许假装验过。
    """
    members = [m for m in (g.get("members") or []) if m != author]
    if not members:
        return (None, "群里没有别人可当验收人")
    emps = [e for e in (_team_store.get_employee(m) for m in members) if e]
    if not emps:
        return (None, "群成员资料取不到")
    qa_words = ("测试", "质检", "审校", "审查", "审核", "验收", "QA", "qa")
    for e in emps:
        if any(w in f"{e.get('role', '')}{e.get('name', '')}{e.get('persona', '')}" for w in qa_words):
            return (e, "")
    leader = _team_store.get_employee(g.get("leader") or "") if g.get("leader") else None
    if leader is not None and leader["id"] != author:
        return (leader, "")
    return (emps[0], "")


def _verify_delivery_sync(gid: str, name: str, ok: bool, reply: str,
                          attachments: list[str]) -> None:
    """给"不在事件循环里"的调用方（比如启动时的对账线程）用的入口。"""
    try:
        asyncio.run(_verify_delivery(gid, name, ok, reply, attachments))
    except RuntimeError:
        # 已经在事件循环里 ⇒ 起个后台任务，别阻塞
        _spawn_bg(_verify_delivery(gid, name, ok, reply, attachments))


async def _verify_delivery(gid: str, name: str, ok: bool, reply: str,
                           attachments: list[str]) -> None:
    """★ P0-2：交付之后**先验收**，通过了才推进下一批。

    两级验收（论文里 +15.6% 的那个改动）：
      · 低层（机器，不花钱）：产物在不在、是不是空文件
      · 高层（另一个成员当验收人）：对照总目标，这一步是不是**真的**达成了
    不合格 ⇒ 打回给负责人并附意见重做（有轮次上限）；超过上限 ⇒ 判失败，依赖它的活被挡住。
    """
    g = _team_store.get_group(gid) or {}
    item = next((i for i in (g.get("leader_plan") or []) if i["name"] == name), None)
    if item is None:
        return
    if not ok:
        # 任务本身就没做成 ⇒ 不用验了，直接判失败（依赖它的活会被挡住）
        _team_store.append(gid, **{"from": "system", "text": (
            f"❌ @{name} 这一步没做成，不再验收；依赖它的活已挡住。"
        )})
        _advance_leader(gid, name, False, reply, attachments)
        return

    low_ok, low_detail = _low_level_check(str(item.get("task_id") or ""), list(attachments or []),
                                         reply=reply or "", item=item)
    verifier, why = _pick_verifier(g, name)
    verdict = {"pass": low_ok, "low": low_detail, "high": "", "fix": ""}
    if not low_ok:
        verdict["high"] = "低层没过，先不看高层"
        verdict["fix"] = "把声称的产物真正写进工作区（别只在回复里说做了）"
    elif verifier is None:
        _team_store.append(gid, **{"from": "system", "text": (
            f"⚠️ {why} —— 这一步没有独立验收（只有低层机器检查：{low_detail}）"
        )})
    else:
        try:
            # ★★★ 2026-10-06（**今天大半失败的真根因** ✗✗✗）：
            #   原来这里传的是 `item` —— 而 `item["reply"]` 要等**验收之后**在 `_advance_leader`
            #   里由 `leader_finish_item` 才写进去 ✓ ⇒ **验收那一刻它还是空的** ✗✓
            #   ⇒ 验收人看到的"交付摘要"永远是空 ✓ ⇒ 它每次都判"没贴输出/什么都缺" ✓
            #   **而它说的全是实话** ✓（我们前面好几轮怪它太苛刻、还改了三处截断 ✗ 全打偏了 ✓）。
            #   ⇒ 把**这次的交付正文**拼进去再问 ✓（`reply` 就是刚从任务事件里读出来的那份 ✓）。
            judged = dict(item, reply=reply or item.get("reply") or "")
            prompt = _team_store.verification_prompt(
                str(g.get("leader_goal") or ""), judged, low_detail)
            raw = await _employee_say(verifier, prompt)
            verdict = _team_store.parse_verdict(raw)
            if verdict.get("unparsed") and low_ok:
                # ★★ 2026-10-06：**先重问一次，再考虑软放行** ✓
                #   真群实测里"验收人没给出可解析结论"出现得不少（它会返回一条 shell 命令
                #   想自己去看文件 ✗），而每次软放行都等于**高层这一关白设** ✗。
                #   这里补一次**更硬的复问**（只回那段 JSON、不要解释、不要调工具 ✓）：
                #   只多花一次调用、且只在这一种情况花 ✓；再不行才软放行并如实标注 ✓。
                raw2 = await _employee_say(verifier, _STRICT_VERDICT_NUDGE + prompt)
                v2 = _team_store.parse_verdict(raw2)
                if not v2.get("unparsed"):
                    verdict = v2
                    _team_store.append(gid, **{"from": "system", "text": (
                        "🔁 验收人第一次没说清结论，**复问一次拿到了** ✓"
                    ), "task_id": item.get("task_id")})
            if verdict.get("unparsed"):
                # ★ 验收人没给出可解析的结论（真群实测：它会返回一条 shell 命令想自己去看文件）——
                #   这不是"活不行"，按"验收人没给出结论"处理：低层过了就放行并标注。
                if low_ok:
                    verdict = {"pass": True, "low": low_detail,
                               "high": "⚠️ 高层未核实（**复问一次仍未给出可解析结论**）",
                               "evidence": "", "fix": "", "soft": True}
            if verdict.get("pass"):
                # ★ 防"互相背书"（Jev 判定指出的头号漏洞）：说通过就得**引得出证据**；
                #   引不出来当没通过（同一个模型的验收人最容易顺手放行）。
                #   证据要在**这次交付的正文/产物/机器结论**里找得到 —— 用传进来的 reply（就是这次的交付）
                judged = dict(item, reply=reply or item.get("reply") or "")
                #   ★ 已经是"软放行"（验收人失败/没给出可解析结论）就别再接地校验 ——
                #     否则会把原因覆盖成"没引得出证据"，群里看到的信息就不准了（本班踩过）
                if not verdict.get("soft") and not _team_store.verdict_is_grounded(verdict, judged, low_detail):
                    # ★ 2026-10-05 真群回归实测：**这是"验收人不行"，不是"活不行"** ——
                    #   旧实现在这里打回，结果让干活的冤枉重做一遍（白烧 43698 tok）。
                    #   现在：只要**低层（机器查）通过**就放行，并在群里如实标注"高层未核实"。
                    verdict = {"pass": True, "low": low_detail,
                               "high": "⚠️ 高层未核实（验收人没引得出证据）",
                               "evidence": verdict.get("evidence") or "", "fix": "",
                               "soft": True}
                else:
                    verdict["low"] = low_detail      # 低层以机器结论为准（模型说的不算）
        except Exception as e:
            # ★ 2026-10-05 真群回归实测：**验收人自己调用失败，不该让干活的背锅**。
            #   旧实现在这里直接判"打回 ⇒ 重做"，实测白烧 43698 tok（低层其实已经通过了）。
            #   现在的口径：**低层通过就放行**，群里如实标注"高层验收没能完成"；
            #   低层也没过才打回（那才是活的问题）。
            if low_ok:
                verdict = {"pass": True, "low": low_detail,
                           "high": f"⚠️ 高层未核实（验收人调用失败：{type(e).__name__}）",
                           "evidence": "", "fix": "", "soft": True}
            else:
                verdict = {"pass": False, "low": low_detail,
                           "high": f"验收人没给出结论（{type(e).__name__}），且低层也没过",
                           "fix": "把声称的产物真正写进工作区（别只在回复里说做了）"}

    who = verifier["name"] if verifier else "（无人）"
    if verdict["pass"]:
        _team_store.append(gid, **{"from": f"emp:{who}", "text": (
            (f"🟡 按低层结论放行（@{name} 的交付）" if verdict.get("soft")
             else f"✅ 验收通过（@{name} 的交付）") + chr(10)
            + f"· 低层：{verdict.get('low') or '（无）'}" + chr(10)
            + f"· 高层：{verdict.get('high') or '（无）'}"
            + (chr(10) + "（这次高层没验成，但机器查过产物确实在 —— 先让它往下走，不让你白等）"
               if verdict.get("soft") else "")
        )})
        batch = _team_store.leader_set_verdict(gid, name, True, str(verdict.get("high") or ""))
        # ★ 缺陷回环：**通过也要看** —— 它可能在别人的产物里发现了问题（第六轮就是这种）
        loop_batch = _apply_defects(gid, name, verdict, _team_store.get_group(gid) or g)
        if loop_batch is not None:
            batch = loop_batch
        st = _team_store.leader_state(gid)
        if batch.get("ready"):
            name_to_id = {e["name"]: e["id"] for e in _team_store.employees()
                          if e["id"] in (g.get("members") or [])}
            _team_store.append(gid, **{"from": "system", "text": (
                f"▶️ 第 {batch.get('wave')} 批开工（{st['done']}/{st['total']} 已完成）："
                + "、".join(f"@{i['name']}" for i in batch["ready"])
            )})
            _dispatch_leader_batch(gid, batch, name_to_id)
            return
        if _all_settled(st):
            if _close_batch_or_gate(gid, st):
                return                      # ★ 门加了并派出去了 ⇒ 这一轮不算收口
            head = "✅ 全部完成（且都过了验收）" if st["failed"] == 0 and st["blocked"] == 0 else "🟡 收工（有失败/被挡）"
            _invoice = _invoice_line(gid)
            _team_store.append(gid, **{"from": "system", "text": (
                f"{head}：共 {st['total']} 项，完成 {st['done']}、失败 {st['failed']}、被挡住 {st['blocked']}。"
                + (chr(10) + _invoice if _invoice else "")
            )})
        return

    # 不通过：打回（有轮次上限）
    rounds = int(item.get("rounds") or 0) + 1
    fix = str(verdict.get("fix") or "").strip() or "按验收意见重做"
    if rounds > _team_store.MAX_VERIFY_ROUNDS:
        _team_store.append(gid, **{"from": "system", "text": (
            f"⛔ @{name} 的活被打回 {rounds} 次仍不合格，判定失败；依赖它的活已挡住。" + chr(10)
            + f"最后一次验收意见：{fix}" + chr(10) + "（人工看一眼，或把这项拆小一点再派）"
        )})
        _advance_leader(gid, name, False, reply, attachments)
        return
    _team_store.append(gid, **{"from": f"emp:{who}", "text": (
        f"❌ 打回（@{name} 的交付，第 {rounds} 次）" + chr(10)
        + f"· 低层：{verdict.get('low') or '（无）'}" + chr(10)
        + f"· 高层：{verdict.get('high') or '（无）'}" + chr(10)
        + f"· **要改什么**：{fix}"
    )})
    batch = _team_store.leader_set_verdict(gid, name, False, fix)
    # ★ 2026-10-06 经验注入（记的那一侧 ✓）：把"这类活栽在哪"记进经验库 ✓ ——
    #   下次派**同类**活时自动带上（见 `TeamStore.leader_handoff_text` ✓）。
    #   实测同一个坑（交付不贴真实输出）反复出现 ✓ 每次重烧十几万 tok ✗，
    #   而"上次为什么栽"系统本来就知道 ✓ 只是从没带给下一个人 ✗。
    _team_store.record_lesson(
        f"{g.get('leader_goal') or ''} {item.get('task') or ''}", fix or str(verdict.get("high") or ""),
        who=name)
    # ★ 这一步自己没过 ⇒ 缺陷**只记录、不回环**（两条线一起搅会把上游也打挂，2026-10-06 实测）
    loop_batch = _apply_defects(gid, name, verdict, _team_store.get_group(gid) or g, reopen=False)
    batch = _team_store.leader_reroll(gid, name, fix)
    if loop_batch is not None:
        batch = loop_batch
    gg = _team_store.get_group(gid) or {}
    name_to_id = {e["name"]: e["id"] for e in _team_store.employees()
                  if e["id"] in (gg.get("members") or [])}
    _team_store.append(gid, **{"from": "system", "text": f"🔁 @{name} 重做一次（验收意见已写进工作单）"})
    _dispatch_leader_batch(gid, batch, name_to_id)


def _apply_defects(gid: str, current: str, verdict: dict[str, Any],
                   g: dict[str, Any], reopen: bool = True) -> dict[str, Any] | None:
    """★ 缺陷回环：验收人指出**别人产物**的问题 ⇒ 打回给那个负责人，并让依赖它的项复测。

    现场（2026-10-05 第六轮真群回归）：测试工程师真跑出「36 通过 / 1 失败」，
    把缺陷写进**自己的交付报告**，而系统只验收了"测试这一步交付的报告" ⇒ **那 1 个失败没人修** ✗。
    现在：验收人把它结构化写进 `defects` ⇒ 这里打回给对应的人（并把下游拉回复测）。

    ★★ 2026-10-06（**本功能自己捅的娄子，真群实测**）：`reopen=False` 是必须的 ——
      当**当前这一步自己就没过**时（它已经在重做了 ✗），**绝不能再把上游也打回重修** ✗：
      两条线一起搅 ⇒ 上游撞上"打回 3 次"上限被判失败 ⇒ **整条链崩掉**
      （实测：三项全废，`完成 0、失败 1、被前置挡住 2`）。
      所以：**只有当前这步通过了，才回环去修别人**；当前这步没过时，缺陷只作为**提示**记在群里，
      等这一步过了再说。
    """
    plan = list(g.get("leader_plan") or [])
    known = [i["name"] for i in plan]
    defects = _team_store.parse_defects(verdict, known, exclude=current)
    if not defects:
        return None
    by_name = {i["name"]: i for i in plan}
    batch: dict[str, Any] | None = None
    for d in defects:
        owner = d["owner"]
        if not reopen:
            _team_store.append(gid, **{"from": "system", "text": (
                f"📝 @{current} 在验收里提到 **@{owner}** 的产物有问题（**先记下**，等这一步自己过了再回环）："
                + chr(10) + f"· {d['what'][:300]}"
            )})
            continue
        item = by_name.get(owner) or {}
        rounds = int(item.get("rounds") or 0)
        if rounds > _team_store.MAX_VERIFY_ROUNDS:
            _team_store.append(gid, **{"from": "system", "text": (
                f"⛔ 缺陷回环到上限：@{owner} 这一项已经重修 {rounds} 次，不再自动打回。" + chr(10)
                + f"最后一次缺陷：{d['what'][:200]}" + chr(10) + "（人工看一眼，或把这项拆小一点再派）"
            )})
            continue
        _team_store.append(gid, **{"from": "system", "text": (
            f"🔁 缺陷回环：@{current} 在验收里指出 **@{owner}** 的产物有问题 —— 已打回重修，"
            "依赖它的项回到待办**复测**。" + chr(10) + f"· 要改什么：{d['what'][:300]}"
        )})
        batch = _team_store.leader_reopen(gid, owner, d["what"]) or batch
    return batch


def _reconcile_leader(gid: str) -> None:
    """把"没被看门认领回来"的交付补上 —— 关掉 Jev 判定里的头号漏洞。

    ★ 问题（我原来只靠看门任务推进批次）：后端一重启，`_watch_group_delivery` 就没了，
      那一项的交付**永远不会被认领**，依赖它的活也就**永远不动**（用户表现："群聊卡住了"）。
    ★ 现在：谁都可以触发一次对账（发消息前、启动恢复时）—— 拿"还在跑且有 task_id 的项"
      去核任务终态，终态了就当交付补上并推进下一批。幂等：已经 done 的项不会再动。
    """
    try:
        pend = _team_store.leader_pending_task_ids(gid)
    except Exception:
        return
    for name, task_id in pend:
        if not task_id:
            continue
        t = tasks.get(task_id)
        if t is None or t.status not in ("done", "partial", "failed", "cancelled", "idle"):
            continue
        reply, atts = "", []
        try:
            evs = store.read_events(task_id)
            asst = [e for e in evs if e.type == "message" and e.payload.get("role") == "assistant"]
            if asst:
                # ★ 同上看门那处：交付正文别砍到 800 ✗（真实输出常在后半段 ✓）
                reply = str(asst[-1].payload.get("text") or "")[:4000]
                atts = list(asst[-1].payload.get("attachments") or [])
        except Exception:
            pass
        ok = t.status not in ("failed", "cancelled")
        _team_store.append(gid, **{"from": "system", "text": (
            f"🔄 补认领：@{name} 的活其实已经{'交付' if ok else '结束（未成功）'}了"
            "（看门任务在后端重启时丢了，这里补上，继续往下推进）"
        ), "task_id": task_id, "status": t.status})
        _verify_delivery_sync(gid, name, ok, reply, atts)


def _close_batch_or_gate(gid: str, st: dict[str, Any]) -> bool:
    """**收口的唯一入口**：全都到终态时，先补"项目验收"这道门。

    返回 True = 门已经加上并派出去了（**还不能喊"全部完成"**）；False = 可以收口了。

    ★ 为什么要有这个函数（2026-10-05 验收跑实测）：收口有**两个**分支
      （`_verify_delivery` 里一处、`_advance_leader` 里一处），我一开始只给其中一处加了门 ✗
      ⇒ 那一轮三项全 done、账单 93 万 tok，却**一道验收门都没过** ✗。
      现在两处都走这里 —— 从结构上让它不可能再被跳过。
    """
    if _ensure_acceptance(gid, st) is None:
        return False
    batch = _team_store.leader_ready(gid)
    if batch.get("ready"):
        g = _team_store.get_group(gid) or {}
        name_to_id = {e["name"]: e["id"] for e in _team_store.employees()
                      if e["id"] in (g.get("members") or [])}
        _team_store.append(gid, **{"from": "system", "text": (
            "▶️ 最后一批开工（" + "、".join(f"@{i['name']}" for i in batch["ready"])
            + "）—— 项目验收，真跑一遍再收口"
        )})
        _dispatch_leader_batch(gid, batch, name_to_id)
    return True


def _all_settled(st: dict[str, Any]) -> bool:
    """分工单里所有项是不是都到终态了。"""
    return bool(st.get("total")) and (
        st.get("done", 0) + st.get("failed", 0) + st.get("blocked", 0) >= st["total"])


def _project_acceptance_task(goal: str, outputs: str, *, has_failures: bool = False) -> str:
    """★ **项目终验的任务书**（单一真相 ✓ —— 组长模式与广播模式共用这一份 ✓）。

    它必须回答一件事：**这东西到底能不能跑** ✓（而不是"每个人都交了" ✓）。

    · 硬要求：**先跑工作区里已有的测试**并贴真实输出 ✓；**不要新写验收脚本** ✗
      （第一版只写"优先复用"，实测没约束住 ✗：那一轮从零写了 46 条、单步烧 104 万 tok ✗✗）
    · 还要照交付里「怎么打开 / 怎么用」那节**真的试一次** ✓（现场：用户"我打不开" ✓）
    · 通过写「✅ 项目验收通过」；不通过写「❌ 项目验收不通过」并指出是谁的产物的问题 ✓
      （这样缺陷回环能接手 ✓）

    ★★ 2026-10-06 加 `has_failures`（**评测台跑第一轮就抓到的浪费** ✗）：
      上游已经有项**被判失败/被挡住**时，终验**不该再写一份完整验收报告** ✗ ——
      实测那一轮：程序员那步判失败之后，终验仍跑了 **32 次调用 / 44 万 tok** ✓（占全任务 95% ✗），
      最后写出一份"这项目不能用"的长报告 ✓ —— **结论早在判失败那一刻就定了** ✓。
      所以这种情况改派**确认性验收**：跑一下、一句话结论、别超过 5 条断言、别写长报告 ✓。
    """
    if has_failures:
        return (
            "**项目验收（确认性 —— 上游已有项失败，别再写完整报告）**" + chr(10)
            + f"· 总目标：{goal[:200]}" + chr(10)
            + f"· 已交付的产物：{outputs or '（未标注）'}" + chr(10)
            + "**这一步只做三件事**（硬要求，超了算不合格）：" + chr(10)
            + "① 在工作区里**跑一下已有产物**（有测试就跑测试；没有就直接跑主命令 ✓），"
              "把**实际命令 + 真实输出**贴出来 ✓；" + chr(10)
            + "② 给**一句话结论**：`❌ 项目验收不通过：<谁的什么产物> 不能用，因为 <一句话>` ✓"
              "（或者万一能跑，就写 `✅ 项目验收通过` ✓）；" + chr(10)
            + "③ **不要**新写验收脚本 ✗、**不要**写长报告 ✗、**不要**补断言（≤5 条都不必 ✓）——"
              "上游已经判失败了，结论不取决于你写多少 ✓。" + chr(10)
            + "④ **别探索** ✗：跑一两条命令、拿到真实输出就够 ✓（实测那种「到处翻目录」的验收"
              "单步能烧 8 万 tok ✓ 而现在给你 **12 步**预算 ✓ 逛不完 ✓）。" + chr(10)
            + "（这一条是为了**别把已经失败的活再烧一遍钱** ✓ 实测能省几十万 tok ✓）"
        )
    return (
        "**项目验收（最后一道门）**：在群共享工作区里**真跑一遍端到端验收**，"
        "确认这个项目到底能不能用。" + chr(10)
        + f"· 总目标：{goal[:300]}" + chr(10)
        + f"· 已交付的产物：{outputs or '（未标注）'}" + chr(10)
        + "要求：① 写一个**验收脚本**（真的去跑主流程，不是只看文件在不在）；② **真执行**它；"
          "③ 把**实际命令与真实输出**贴进交付里（照抄，别转述）；"
          "④ 通过就明确写「✅ 项目验收通过」，不通过写「❌ 项目验收不通过」**并指出是谁的产物的问题**"
          "（系统会据此打回给那个人重修）。" + chr(10)
        + "**范围（硬要求，超了算不合格）**：" + chr(10)
        + "· **第一步：跑工作区里已有的测试**（`test_*.py` / `tests/` 下的脚本，用 "
          "`python -m pytest -q` 或直接 `python test_xxx.py`）—— 把**那条命令和它的真实输出**贴进交付 ✓；" + chr(10)
        + "· **不要新写验收脚本** ✗；已有的测试如果没覆盖主流程，才补一个**最小**脚本，"
          "**断言不超过 15 条**；" + chr(10)
        + "· 验收只回答三件事：**已有测试跑没跑通**、**产物是否符合契约**、**有没有明显缺陷**；" + chr(10)
        + "· 还要验一件事：**照交付里「怎么打开 / 怎么用」那一节，产物真的能打开/跑起来吗** ✓"
          "（网页类就确认文件在且能开 ✓；小程序类确认目录结构齐、说明里写了用微信开发者工具 ✓；"
          "命令行类确认命令真能跑通 ✓）——**对不上就算不合格** ✗；" + chr(10)
        + "· 别顺手重写实现、别做大重构、别把已有测试再抄一遍、别为了「更全面」扩用例 ✗。"
    )


def _ensure_acceptance(gid: str, st: dict[str, Any]) -> dict[str, Any] | None:
    """★ 项目级完成判据（2026-10-05 诊断第 3 条）。

    现场：第七轮三项全 done、账单 51 万 tok，而"这个项目到底能不能跑"**没人验** ✗。
    这一条把"项目验收"做成**分工单里的最后一项**（走现成的波次与两级验收，不新增机器）：
    · 前面所有项都到终态、且还没有"项目验收"这一项时 ⇒ 追加一项
    · 执行者优先选**测试类**成员（没有就选组长，再没有就如实说明跳过）
    · 它的任务书要求：在**群共享工作区**里真跑一遍端到端验收，把**实际命令与输出**贴出来，
      通过写 ✅、不通过写 ❌ **并指出是谁的产物的问题**（这样缺陷回环能接手）
    返回"新追加的那一项"（已存在或没人可派时返回 None）。
    """
    plan = list(st.get("items") or [])
    if any(i.get("kind") == "acceptance" for i in plan):
        return None
    done_items = [i for i in plan if i.get("status") == "done"]
    if not done_items:
        return None                       # 什么都没做成 ⇒ 没什么可验收的
    g = _team_store.get_group(gid) or {}
    members = [e for e in (_team_store.get_employee(m) for m in (g.get("members") or [])) if e]
    def _pick(keys: tuple[str, ...]) -> dict[str, Any] | None:
        for e in members:
            hay = f"{e.get('role', '')} {e.get('name', '')} {e.get('dept', '')}"
            if any(k in hay for k in keys):
                return e
        return None
    who = _pick(("测试", "QA", "质量")) or None
    leader_id = g.get("leader")
    leader = _team_store.get_employee(leader_id) if leader_id else None
    if who is None and leader is not None:
        who = leader
    if who is None and members:
        who = members[0]        # 再退一步：群里随便哪位（有门总比没门强），并在群里说清是谁
    if who is None:
        _team_store.append(gid, **{"from": "system", "text": (
            "⚠️ 这一步本该做**项目验收**（真跑一遍看这个项目到底能不能用），"
            "但群里没有测试类成员、也没组长 ⇒ 只能跳过。"
            "（「✅ 全部完成」只代表每项都交了东西，**不代表项目能跑**。）"
        )})
        return None
    outputs = "、".join(str(i.get("output") or "").strip() for i in done_items if i.get("output"))[:300]
    # ★ 名字去重（2026-10-05 实测踩到）：验收项的默认执行者很可能**已经有一项**在分工单里
    #   （比如"测试工程师：写自检脚本"）⇒ 两项同名 ⇒ 派发按名字找人就分不清是谁 ✗。
    #   所以验收项用「<人名>·验收」这种**可读且唯一**的名字；派发边界认得这个别名。
    who_name = f"{who['name']}·验收"
    # ★ 上游有没有**已经失败/被挡住**的项 ⇒ 终验改走"确认性"版本 ✓（别把失败的活再烧一遍钱 ✗）
    has_failures = any(i.get("status") in ("failed", "blocked") for i in plan)
    if has_failures:
        _team_store.append(gid, **{"from": "system", "text": (
            "⚠️ 上游已经有项**判失败/被挡住** ⇒ 终验只做**确认性验收**（跑一下 + 一句话结论 ✓），"
            "不再写完整报告（实测那样会白烧几十万 tok ✗）"
        )})
    task = _project_acceptance_task(str(g.get("leader_goal") or ""), outputs,
                                    has_failures=has_failures)
    try:
        item = _team_store.leader_append_item(gid, who_name, task,
                                              "验收记录（含实际命令与输出）",
                                              [i["name"] for i in done_items], kind="acceptance")
    except Exception:
        return None
    _team_store.append(gid, **{"from": "system", "text": (
        "🔎 最后一道门：**项目验收**（交给 @" + who_name + "）——" + chr(10)
        + "不再只看「每项都交了」，而是**在群工作区里真跑一遍**，确认这东西真的能用。"
    )})
    return item


def _advance_leader(gid: str, name: str, ok: bool, reply: str,
                    attachments: list[str]) -> None:
    """某一项交付/失败 ⇒ 推进批次（★ P0-1 的另一半：前置交付后，下一批才开工）。"""
    batch = _team_store.leader_finish_item(
        gid, name, "done" if ok else "failed", reply, attachments)
    st = _team_store.leader_state(gid)
    if batch.get("ready"):
        g = _team_store.get_group(gid) or {}
        name_to_id = {e["name"]: e["id"] for e in _team_store.employees() if e["id"] in g.get("members")}
        _team_store.append(gid, **{"from": "system", "text": (
            f"▶️ 第 {batch.get('wave')} 批开工（{st['done']}/{st['total']} 已完成）："
            + "、".join(f"@{i['name']}" for i in batch["ready"])
            + (chr(10) + "（它们拿到了前置的交付物，不用猜）" if any(i.get("depends_on") for i in batch["ready"]) else "")
        )})
        _dispatch_leader_batch(gid, batch, name_to_id)
        return
    if _all_settled(st):
        # ★ 收口统一走这一个入口（两处收口都必须过"项目验收"这道门，别只改一处 —— 本班踩过）
        if _close_batch_or_gate(gid, st):
            return
        head = "✅ 全部完成" if st["failed"] == 0 and st["blocked"] == 0 else "🟡 收工（有失败/被挡）"
        _team_store.append(gid, **{"from": "system", "text": (
            f"{head}：共 {st['total']} 项，完成 {st['done']}、失败 {st['failed']}、被前置挡住 {st['blocked']}。"
            + ("" if st["failed"] == 0 and st["blocked"] == 0 else chr(10) + "把失败的活修好后再发一次目标即可继续。")
        )})


# ★ 续跑防重入表：task_id → 上次排续跑的时间（Jev 判定点名的"并发续跑"边界）
_RESUME_LAST: dict[str, float] = {}

# ★ 自动续跑计数（重启打断后的自愈）：task_id → 已经自动续了几次。
#   上限 2 次：防"一直重启一直续"把预算烧光；到顶就在群里如实说明并让人来决定。
_AUTO_RESUME: dict[str, int] = {}


def _inflight_usage(task_id: str) -> dict[str, Any]:
    """读"这次运行到目前为止"的用量快照 ✓ 没有就当空 ✓（绝不抛 ✗）。

    ★ 为什么用 `getattr` 兜一道：真 store（FsStore）一直有这个方法 ✓，
      但**测试里的假 store** 不一定有 ✓ —— 那不是产品毛病，是假对象没跟上 ✓。
      口径上"读不到快照" = "没有正在跑的那一份" ✓ 完全正确 ✓（不是静默吞错 ✗）。
      （真 store 那个方法**另有测试钉着** ✓ 见 tests/test_usage_accounting.py ✓
        所以这里放松不会把"记账坏了"藏起来 ✓）
    """
    fn = getattr(store, "read_usage_inflight", None)
    if not callable(fn):
        return {}
    try:
        got = fn(task_id)
        return got if isinstance(got, dict) else {}
    except Exception:                                    # noqa: BLE001
        return {}


def _task_usage(task_id: str) -> dict[str, Any] | None:
    """读一个任务的**累计**用量（把该任务所有用量事件**加起来** ✓，外加"正在跑"的那一份 ✓）。

    ★★ 2026-10-07 修（第 1 项"统计口径三条"查出来的真 bug ✗✗）：
      原来只取**最后一条** ✗ —— 理由是"最后一条才是全量累计" ✗ **这句是错的** ✓：
      "续聊/接力"会给同一个任务**再开一次运行** ✓ 每次运行各发一条用量 ✓
      （`provider.total_usage` 是**每次运行新建的实例** ✓ 所以各条互不重叠 ✓ 求和不会重复计 ✓）
      ⇒ 只取最后一条 = **只算了最后一次运行** ✗
      实测差多少（真数据 ✓ 2026-10-07 量）：
        · 「介绍一下你自己」：求和 输入 4,676,537 ｜ 取末条只有 3,031,020 ⇒ **少报 35%** ✗
        · 全体 202 个任务：求和 34,649,204 ｜ 取末条 32,477,504 ⇒ **少 217 万**（6%）✗
      ⇒ 后果是**同一个"花了多少"，群里那行和使用统计页对不上** ✗
        —— 正是本项目最忌讳的"多处口径打架" ✓（这是第五处 ✓）。
      ⇒ 现在与 `/usage` **同一口径：求和** ✓ 外加下面那份"跑到一半也被记下来"的 ✓。

    ★ 2026-10-07 第二处修（"被强杀的任务账会丢" ✗）：量到 12 个任务被**重启强杀** ⇒
      它们跑过（合计 175 次动作）但**账没落** ✗（粗估丢了约 230 万 tok ✓）。
      现在 loop 每调一次模型就往 `usage_inflight.json` 记一次快照 ✓ 被掐死也留得下 ✓
      （见 `store.read_usage_inflight` ✓；跑完发了正式事件就把快照删掉 ✓ **不会重复计** ✓）。
    """
    try:
        evs = store.read_events(task_id)
    except Exception:
        return None
    total: dict[str, Any] | None = None
    seen_runs: set[str] = set()
    for e in evs:
        if e.type != "knowledge":
            continue
        u = (e.payload or {}).get("usage")
        if not isinstance(u, dict) or int(u.get("calls") or 0) <= 0:
            continue
        if u.get("run_id"):
            seen_runs.add(str(u["run_id"]))
        if total is None:
            total = {"model": u.get("model"), "calls": 0, "input_tokens": 0,
                     "output_tokens": 0, "cached_tokens": 0, "estimated": bool(u.get("estimated"))}
        total["calls"] += int(u.get("calls") or 0)
        total["input_tokens"] += int(u.get("input_tokens") or 0)
        total["output_tokens"] += int(u.get("output_tokens") or 0)
        total["cached_tokens"] += int(u.get("cached_tokens") or 0)
        total["estimated"] = bool(total["estimated"] or u.get("estimated"))
        if u.get("model"):
            total["model"] = u.get("model")      # 换过模型就报最后用的那个（与旧行为一致 ✓ 不假装是同一个 ✓）
    # ★ "正在跑的那一份"：只有它对应的 run **还没**发正式事件时才计入 ✓（同 run_id ⇒ 不重复 ✓）
    live = _inflight_usage(task_id)
    if live and int(live.get("calls") or 0) > 0 and str(live.get("run_id") or "") not in seen_runs:
        if total is None:
            total = {"model": live.get("model"), "calls": 0, "input_tokens": 0,
                     "output_tokens": 0, "cached_tokens": 0, "estimated": bool(live.get("estimated"))}
        total["calls"] += int(live.get("calls") or 0)
        total["input_tokens"] += int(live.get("input_tokens") or 0)
        total["output_tokens"] += int(live.get("output_tokens") or 0)
        total["cached_tokens"] += int(live.get("cached_tokens") or 0)
        total["inflight"] = True                 # 让调用方知道"这里面有一段还在跑" ✓
    return total


def _usage_line(task_id: str, label: str = "这一步") -> str:
    """给群里拼一行"花了多少"（token 永远报；钱只在配了单价时报 —— 不许编）。"""
    u = _task_usage(task_id)
    if not u:
        return ""
    tok_in = int(u.get("input_tokens") or 0)
    tok_out = int(u.get("output_tokens") or 0)
    calls = int(u.get("calls") or 0)
    cached = int(u.get("cached_tokens") or 0)
    est = "（估算）" if u.get("estimated") else ""
    money = pricing.money(pricing.estimate(str(u.get("model") or ""), tok_in, tok_out, cached))
    parts = [f"📊 {label}：{calls} 次调用", f"输入 {tok_in} + 输出 {tok_out} = {tok_in + tok_out} tok{est}"]
    if cached:
        parts.append(f"缓存命中 {cached} tok")
    if money:
        parts.append(money + "（按你在设置里填的单价估算）")
    else:
        parts.append("（未填单价，只报 token）")
    line = " · ".join(parts)
    # 明显偏多时给一句可行动的提示（多智能体实测约 15× 普通对话的 token）
    if calls >= 20 or (tok_in + tok_out) >= 150_000:
        line += chr(10) + "⚠️ 这一步烧得比平常多 —— 可以考虑把它拆小一点再派（拆小后每步都更容易验收）"
    return line


def _invoice_line(gid: str) -> str:
    """整批账单：把所有已派出去的任务合起来算一次（收口时贴）。"""
    try:
        g = _team_store.get_group(gid) or {}
        items = [i for i in (g.get("leader_plan") or []) if i.get("task_id")]
    except Exception:
        return ""
    tok_in = tok_out = calls = 0
    cny_total = 0.0
    priced = False
    for it in items:
        u = _task_usage(str(it["task_id"]))
        if not u:
            continue
        ti, to = int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)
        tok_in += ti
        tok_out += to
        calls += int(u.get("calls") or 0)
        c = pricing.estimate(str(u.get("model") or ""), ti, to, int(u.get("cached_tokens") or 0))
        if c is not None:
            cny_total += c
            priced = True
    if not calls:
        return ""
    line = f"📊 本次分工合计：{calls} 次调用 · 输入 {tok_in} + 输出 {tok_out} = {tok_in + tok_out} tok"
    if priced:
        line += f" · {pricing.money(round(cny_total, 4))}（估算）"
    return line


def _resume_prompt(task_id: str) -> str:
    """给"接着跑"拼工作单：**为什么停下 + 已经有什么 + 别从头再来**。

    ★ 依据（Anthropic 复盘的原话）："错误会累积……**要从出错处续跑，不能从头重来**：
      重跑既贵又让用户难受"。我们此前只有"重新发一次目标"（等于从头烧一遍钱），
      而工作区里的产物其实还在 —— 那才是最该被利用的东西。
    """
    why = _task_failure_reason(task_id) or "上一次没有跑完"
    lines = [f"上一次你没能跑完（{why}）。现在**接着上次的进度继续**："]
    # 已经产出的东西：列工作区里的文件（上限 20 个，按修改时间新的在前）
    try:
        ws = store.workspace_dir(task_id)
        files = sorted((p for p in ws.rglob("*") if p.is_file()),
                       key=lambda p: p.stat().st_mtime, reverse=True)[:20]
        if files:
            lines.append("【工作区里已经有的产物（先读它们，别重复造）】")
            for p in files:
                try:
                    rel = p.relative_to(ws)
                except ValueError:
                    rel = p.name
                lines.append(f"· {rel}（{p.stat().st_size} 字节）")
        else:
            lines.append("【工作区里还没有产物】那就从上次停下的那一步开始。")
    except Exception:
        lines.append("【工作区读取失败】请先确认现在有什么，再决定从哪继续。")
    # 上次说到哪了
    try:
        evs = store.read_events(task_id)
        asst = [e for e in evs if e.type == "message" and e.payload.get("role") == "assistant"]
        if asst:
            last = " ".join(str(asst[-1].payload.get("text") or "").split())
            if last:
                lines.append(f"【上次你最后说的是】{last[:300]}")
    except Exception:
        pass
    lines.append("要求：① **不要从头再来**，已有的产物直接用；② 只把没做完的部分做完；"
                 "③ 结束后给出可核对的交付（文件路径或可运行的结果）。")
    return chr(10).join(lines)


def _latest_resumable(gid: str) -> dict[str, Any] | None:
    """找这个群里"最该被接着跑"的那件事：最近一条失败/部分完成的交付。"""
    try:
        msgs = _team_store.feed(gid)
    except Exception:
        return None
    for m in reversed(msgs[-60:]):
        tid = str(m.get("task_id") or "")
        st = str(m.get("status") or "")
        if tid and st in ("failed", "partial"):
            name = str(m.get("from") or "")
            name = name[4:] if name.startswith("emp:") else ""
            return {"task_id": tid, "name": name, "status": st}
    return None


def _do_resume(gid: str, task_id: str, name: str = "") -> dict[str, Any]:
    """执行一次"接着跑"：同一个任务、同一个工作区、带着已有产物继续（不是新建任务）。

    ★ Jev 判定点名的边界：**并发续跑**。同一任务正在跑时再点一次，会在同一份历史上再排一轮 ⇒
      两轮抢同一个工作区（越弄越乱）。所以：正在跑 ⇒ 如实说"它已经在跑"；20 秒内重复点 ⇒ 忽略。
    """
    task = _get_task(task_id)
    run = runs.get(task_id)
    if run is not None and getattr(run, "aio_task", None) is not None and not run.aio_task.done():
        _team_store.append(gid, **{"from": "system",
                                   "text": f"⏳ {task_id} 现在正在跑，等它这一轮结束再续（别让两轮抢同一个工作区）",
                                   "task_id": task_id, "status": "running"})
        return {"ok": True, "task_id": task_id, "name": name, "skipped": "running"}
    if time.time() - _RESUME_LAST.get(task_id, 0.0) < 20:
        _team_store.append(gid, **{"from": "system",
                                   "text": "⏳ 刚刚已经排过一次「接着跑」了（20 秒内重复点会被忽略）",
                                   "task_id": task_id, "status": "running"})
        return {"ok": True, "task_id": task_id, "name": name, "skipped": "recent"}
    _RESUME_LAST[task_id] = time.time()
    g = _team_store.get_group(gid) or {}
    # 谁来做：优先该项原来的人；否则按任务标题里的 @名字 找
    emp = None
    if not name:
        mm = re.search(r"@([^\s\]】]+)", str(getattr(task, "title", "") or ""))
        name = mm.group(1) if mm else ""
    if name:
        for m in (g.get("members") or []):
            e = _team_store.get_employee(m)
            if e and e["name"] == name:
                emp = e
                break
    sys_extra = _team_store.persona_for(emp) if emp else None
    # ★ 2026-10-05（全量试跑抓到的真 bug）：**续跑必须把分工单里那一项也改回"进行中"**。
    #   实测现场：群里说「接着跑」→ 任务真的重新跑起来了（事件文件 5 秒前还在写），
    #   但那一项在分工单里仍是 failed ⇒ 看门/对账同一时刻判定"活已结束" ⇒ 群里回一句
    #   "❌ 这一步没做成，不再验收" + "🟡 收工（有失败/被挡）" —— 用户看到的是"收工了"，
    #   实际它还在干 ✗（这就是"感觉它停了/乱了"的来源）。
    item = _team_store.leader_item_by_task(gid, task_id)
    if item is not None:
        _team_store.leader_mark_running(gid, item["name"])
    _start_run(task, _resume_prompt(task_id), system_extra=sys_extra)
    _team_store.append(gid, **{"from": "system", "text": (
        f"🔁 接着跑：{'@' + name if name else task_id} 带着工作区里已有的产物继续"
        "（同一个任务、同一个工作区，不从头再来）"
    ), "task_id": task_id, "status": "running"})
    # 重新挂上看门（波次/接力的推进也照旧认领得回来）
    _spawn_bg(_watch_group_delivery(task_id, name or "成员", gid,
                                    leader_name=(item or {}).get("name")))
    return {"ok": True, "task_id": task_id, "name": name}


def _task_failure_reason(task_id: str) -> str:
    """任务失败时给群里一句**能行动**的原因（用户实测："跑到一半没了，也不知道为什么"）。

    读事件里最后一条 error，把机器码翻成"那我该干什么"——
    尤其是 `max_iterations`（步数用完了），实测失败里它占大头。
    """
    try:
        errs = [e for e in store.read_events(task_id) if e.type == "error"]
    except Exception:
        return ""
    if not errs:
        return ""
    p = errs[-1].payload or {}
    code = str(p.get("code") or "")
    msg = str(p.get("message") or "").strip()
    if code == "max_iterations":
        return ("步数用完了（达到最大迭代次数）—— 这个活对一次任务来说偏大。"
                "建议：① 把活拆小一点再派；② 或在设置里把「最大迭代次数」调高。")
    if code == "step_repetition":
        # P0-3：模型在同一件事上打转（MAST 里这是最高频失败：步骤重复 17.1%）。
        # 系统已提前收工并如实交付已有产物，这里告诉用户该怎么办。
        return ("它卡在同一个动作上反复重试，系统已提前收工（已产出的东西照常交付）。"
                "建议：① 把这一步拆小一点再派；② 或在群里 @ 它，明确说换哪种做法。")
    if code == "loop_failed":
        return f"执行中报错：{msg[:200]}（这条发给开发者可定位）"
    return msg[:200] or (f"任务失败（{code}）" if code else "")


def _approval_summary(task_id: str) -> tuple[str, str]:
    """给群里的审批卡片凑一句"到底要批准什么"：工具名 + 参数摘要。

    取任务事件里最后一条 action（就是它正要执行的那一步）——
    没有就退回通用文案（宁可少说，也不要编一个看起来很像的命令）。
    """
    try:
        acts = [e for e in store.read_events(task_id) if e.type == "action"]
    except Exception:
        acts = []
    if not acts:
        return ("某个动作", "")
    p = acts[-1].payload or {}
    tool = str(p.get("tool") or "动作")
    params = p.get("params") or {}
    if tool in ("host_exec", "exec", "shell", "bash", "run"):
        cmd = str(params.get("command") or params.get("cmd") or params.get("script") or "")
        return (tool, cmd[:300])
    if tool in ("write_file", "edit", "apply_patch"):
        return (tool, str(params.get("path") or params.get("file") or "")[:200])
    if tool in ("delete_path", "rm"):
        return (tool, str(params.get("path") or params.get("target") or "")[:200])
    keys = ("query", "url", "path", "to", "text", "subject")
    for k in keys:
        if params.get(k):
            return (tool, str(params[k])[:200])
    return (tool, "")


async def _announce_group_approval(gid: str, name: str, task_id: str) -> None:
    """把一个"等待审批"搬进群聊（每个 call_id 只播报一次）。"""
    try:
        calls = approval.pending_for(task_id)
    except Exception:
        return
    for call_id in calls:
        tool, detail = _approval_summary(task_id)
        # ★ 2026-10-05（用户实测："我点一下允许，它就停住了，一直停着没跑"）：
        #   去重**不能只看 call_id** —— 续跑后 call_id 会从 001 重新计数（新命令又叫 call_012），
        #   只按 call_id 去重会把新命令误判成"已经播报过" ⇒ **卡片永远不出现** ⇒ 任务干等 ✗。
        #   判据（has_notice 是"关键词出现在消息文本里"）：call_id 播过 **且** 这条命令也播过。
        _marker = (str(detail) or str(tool) or "")[:40]
        if _team_store.has_notice(gid, task_id, f"🔐{call_id}") and (
                not _marker or _team_store.has_notice(gid, task_id, _marker)):
            continue                       # 已播报过（重启后也不会再发一遍）
        # ★ 真群实测（2026-10-05）：一个写脚本的任务会连着要十几次批准（每条 shell 一次），
        #   每次都点「允许一次」很烦。同一任务第二次要批准时，直接把"本任务都允许"这条出路说出来。
        repeat = any((m.get("approval") or {}).get("call_id") and m.get("task_id") == task_id
                     for m in _team_store.feed(gid))
        tip = ("\n（这个任务又要点一次：**点「本任务都允许」可以一次放行它这一类**，不用每条都点）"
               if repeat else "")
        _team_store.append(gid, **{
            "from": f"sys:{name}",
            "text": (f"🔐{call_id} @{name} 需要你批准才能继续：\n"
                     f"工具：{tool}" + (f"\n内容：{detail}" if detail else "")
                     + "\n（就在群里点下面按钮即可，不用去任务页）" + tip),
            "task_id": task_id,
            "status": "waiting_approval",
            "approval": {"call_id": call_id, "tool": tool, "detail": detail},
        })


def _gid_of_task(task_id: str) -> str:
    """反查这个任务属于哪个群（审批卡片在群里 ✓ 反查不到返回空串 ✓）。

    ★ 只扫每个群**最近 200 条** ✓ —— 审批卡片一定是刚出现的 ✓，
      没必要为了发一句提示去把整本历史翻一遍 ✗。
    """
    if not task_id:
        return ""
    try:
        for g in _team_store.groups():
            for m in _team_store.feed(g["id"])[-200:]:
                if m.get("task_id") == task_id:
                    return str(g["id"])
    except Exception:                                       # noqa: BLE001
        pass
    return ""


def _do_approve(task_id: str, call_id: str, decision: str, command: str = "") -> None:
    """审批的唯一执行路径（任务页端点与**群里的审批卡片**共用这一份）。

    抽出来是因为两边语义必须一致：包括"重启后审批失效"的诚实降级 ——
    要是各写一份，迟早出现"群里能批、任务页不能批"这种鬼故事。

    ★ 2026-10-06 新增 `decision="forever"`（**这类以后都别问** ✓ 跨任务、落盘 ✓）：
      用户在一个命令上按过之后，**同一个程序**（命令首词 ✓ 如 `python`/`pytest`/`git`）
      以后都不再问 ✓ —— 实测一个 10 分钟的小活要点 2–5 次审批 ✓，等审批常常比干活还久 ✗。
      安全边界：破坏性动词 ✗ 与结构性命令 ✗ **永不记忆**（见 `ApprovalManager.allow_forever` ✓），
      应用目录的硬保护也在它**之前**就判了 ✓。
    """
    _get_task(task_id)
    # ★★ 2026-10-07（第 9 项）**跨任务审计流水**：审批决议是**最该留痕**的一件事 ✓
    #   （现在每个任务自己的事件流里都有 ✓ 但那是按任务分的 ✗
    #    用户想问"这一个月我批过哪些命令"—— 202 个任务谁能翻得动 ✓）
    #
    #   ★ 补（同日，用户实测揪出）：命令**以服务端记的那份为准** ✓
    #     原来只用 `command` 参数 ✗ 而界面压根没传它 ⇒ 账本里只剩"批过一次"✗
    #     —— 那这本账就白记了 ✓（"批的是哪条命令"正是它存在的理由 ✓）
    #     ★ 方向不许反 ✗：客户端传来的只当**提示** ✓ 取不到服务端那份才用它 ✓
    #       审计账的内容**不能由客户端说了算** ✓ 否则这本账就不可信了 ✓
    _srv_cmd = ""
    try:
        _srv_cmd = approval.command_of(task_id, call_id)
    except Exception:                                   # noqa: BLE001
        _srv_cmd = ""
    audit.record("approval", decision=decision, task=task_id,
                 command=(_srv_cmd or command or "")[:200] or None)
    if decision == "forever":
        kept = approval.allow_forever(command)
        if not kept:
            # 记不住就别装作记住了 ✗ —— 如实说，并且**按"只允许这次"放行** ✓（别让任务白等 ✓）
            try:
                _team_store.append(_gid_of_task(task_id), **{"from": "system", "text": (
                    "ℹ️ 这条命令**类型不适合记成「以后都别问」**（它带删除/移动/覆盖写，"
                    "或者带管道/命令替换这类看不懂的结构）—— 这次给你放行 ✓，但下次还会问 ✓"
                )})
            except Exception:                               # noqa: BLE001
                pass
        else:
            try:
                _team_store.append(_gid_of_task(task_id), **{"from": "system", "text": (
                    f"✅ 记住了：「**{kept}** 这类以后都不再问你」✓（设置页随时能收回 ✓）"
                )})
            except Exception:                               # noqa: BLE001
                pass
        decision = "all"                                    # 这次照放 ✓
    if not approval.resolve(task_id, call_id, decision):
        task = tasks.get(task_id)
        run = runs.get(task_id)
        live = run and run.aio_task and not run.aio_task.done()
        if task is not None and task.status == "waiting_approval" and not live:
            task.status = "failed"
            task.updated_at = _now()
            _save_index()
            detail = "后端重启，原等待中的审批已失效——请重新发起任务（此审批无法跨重启恢复）"
            _emit_standalone(task_id, "status", {"state": "failed", "detail": detail})
            raise HTTPException(409, f"审批已失效（{detail}）task={task_id} call_id={call_id}")
        raise HTTPException(409, f"没有等待中的审批：task={task_id} call_id={call_id}")


def _meeting_rounds_from(text: str, default: int = 2) -> int:
    """消息里写「3轮」「3 轮」就按 3 轮；否则默认 2；一律夹在 1..MAX 之间（省钱也防空转）。"""
    m = re.search(r"(\d+)\s*轮", str(text or ""))
    n = int(m.group(1)) if m else default
    return max(1, min(_team_store.MAX_MEETING_ROUNDS, n))


# ★ 2026-10-06：会议"收口跑题"时的**复问前缀** ✓
#   真跑实测（开会那轮）：纪要被判"与讨论记录对不上（疑似跑题）" ✗ ⇒ 落到兜底整理 ✓。
#   这和验收人"说不清结论"是同一类病 ✓ ⇒ 同样先复问一次、再退兜底 ✓。
_STRICT_SUMMARY_NUDGE = (
    "【重要】上一条纪要**被认为跑题了** ✗ —— 请重写，并严格遵守：" + chr(10)
    + "· **只写讨论里真实出现过的东西** ✓，点名引用具体发言人说过的话（原话片段即可）✓；" + chr(10)
    + "· 讨论里**没有**的内容一律不要写 ✗（不要补充常识、不要替他们想方案）；" + chr(10)
    + "· 结构固定三段：**结论 / 分歧与各方立场 / 待办与负责人** ✓；" + chr(10)
    + "· 仍然分歧的，如实写「未收敛」✓，别硬凑一个结论 ✗。" + chr(10) + chr(10)
)


async def _employee_say(emp: dict[str, Any], prompt: str) -> str:
    """让某个员工**用自己的大脑说一句话**（无工具、无沙箱）—— 开会模式专用。

    复用 `_leader_plan` 那条"按员工绑定选 provider"的路（组长拆解也走它），
    这样会上发言用的模型与这个员工平时干活用的模型一致（BYOK 的语义不破）。

    ★★ 2026-10-06「模型分级」（**默认关着** ✓）：
      这条路只服务**只做判断、不动手**的几处（开会发言 / 收口 / 主持人"够了没"）✓ ——
      它们不需要强模型 ✓ 而量大 ✓。配置里填了 `model.simple_model` 才生效 ✓
      （不填 = 完全等于没这功能 ✓）。
      **故意不接**：验收人的判定 ✗（它决定能不能过 ✓ 用弱模型省的钱会几倍还回去 ✓）。
    """
    simple = str(getattr(cfg.model, "simple_model", "") or "").strip()
    if simple and simple != str(cfg.model.model_name):
        # 只在**没有单独绑模型**的员工身上生效 ✓ —— 员工自己配了模型就尊重它（BYOK 语义不破 ✓）
        if not emp.get("provider") and not emp.get("model_name"):
            emp = {**emp, "model_name": simple}
    return await _leader_plan(emp, prompt)


async def _run_meeting(gid: str, order: list[str], rounds: int) -> None:
    """开会的执行体：绕议题来回讨论 → 主持人每轮判"够了没" → 收口成纪要。

    每一步都**实时进群聊**（用户要的是"看着他们讨论"，不是等一个黑盒结论）。
    单个成员发言失败不中断整场会（记一条 ⚠ 继续），否则三个人的会会被一个人的网络抖动卡死。
    """
    g = _team_store.get_group(gid)
    if g is None:
        return
    moderator = _team_store.get_employee(g.get("leader") or "") if g.get("leader") else None
    if moderator is None:
        first = _team_store.get_employee(order[0]) if order else None
        moderator = first
    stopped_early = ""
    for r in range(1, rounds + 1):
        g = _team_store.get_group(gid) or g
        _team_store.append(gid, **{"from": "system", "text": f"—— 第 {r}/{rounds} 轮 ——"})
        for eid in order:
            emp = _team_store.get_employee(eid)
            if emp is None:
                continue
            g = _team_store.get_group(gid) or g
            try:
                say = await _employee_say(emp, _team_store.meeting_speak_prompt(g, emp, r))
            except Exception as e:
                _team_store.append(gid, **{"from": "system", "text": (
                    f"⚠️ {emp['name']} 这轮没说出话（{type(e).__name__}: {str(e)[:120]}），继续下一位"
                )})
                continue
            say = (say or "").strip()
            if not say:
                continue
            _team_store.append(gid, **{"from": f"emp:{emp['name']}", "text": say})
            _team_store.meeting_append(gid, emp["name"], say, r)
        # 每轮结束问主持人：够了没（提前收口就是省钱；这也是"动态调度"的落点）
        if moderator is not None and r < rounds:
            g = _team_store.get_group(gid) or g
            try:
                verdict = (await _employee_say(moderator, _team_store.meeting_moderator_prompt(g, r)) or "").strip()
            except Exception:
                verdict = "CONTINUE（主持人判断失败，按原计划继续）"
            head = verdict.upper()[:12]
            _team_store.append(gid, **{"from": f"emp:{moderator['name']}", "text": f"（主持）{verdict[:300]}"})
            if "DONE" in head:
                stopped_early = f"主持人第 {r} 轮判定已可下结论"
                break
    g = _team_store.get_group(gid) or g
    summary = ""
    if moderator is not None:
        # ★ 收口要**尽力拿到东西**：先正常要；失败就用更短的记录再要一次；
        #   还失败就退化成"从记录里自动整理的要点"，绝不让用户只看到一句报错
        #   （2026-10-05 用户实测：收口那一步空响应 ⇒ 纪要变成 "（收口失败：…）"，白开一场会）。
        try:
            summary = str(await _employee_say(moderator, _team_store.meeting_summary_prompt(g)) or "").strip()
        except Exception as e:
            short = dict(g)
            tr = list(g.get("meeting_transcript") or [])
            short["meeting_transcript"] = tr[-6:] if len(tr) > 6 else tr      # 短一点，减少空响应概率
            try:
                summary = str(await _employee_say(moderator, _team_store.meeting_summary_prompt(short)) or "").strip()
            except Exception:
                summary = _meeting_fallback_digest(g, type(e).__name__)
    else:
        summary = _meeting_fallback_digest(g, "没有主持人")
    if not summary.strip():
        summary = _meeting_fallback_digest(g, "模型返回空纪要")
    # ★ 接地校验：纪要与讨论记录几乎没有交集 = 跑题（用户实测过一次）⇒ 当失败处理
    if _summary_grounding(g, summary) < SUMMARY_GROUNDING_MIN and moderator is not None:
        # ★★ 2026-10-06：**先带着原因复问一次，再退兜底** ✓
        #   真跑实测（开会那轮）：正常要来的纪要被判"对不上（疑似跑题）" ✗ ⇒ 直接落兜底整理 ✓。
        #   这和"验收人说不清结论"是同一类病 ✓ —— 把**失败原因**写进复问里，
        #   只多花一次调用 ✓，能救回来的话用户拿到的就是**真纪要**而不是"仅供参考" ✓。
        try:
            again = str(await _employee_say(
                moderator, _STRICT_SUMMARY_NUDGE + _team_store.meeting_summary_prompt(g)) or "").strip()
        except Exception:                                       # noqa: BLE001
            again = ""
        if again and _summary_grounding(g, again) >= SUMMARY_GROUNDING_MIN:
            summary = again
            _team_store.append(gid, **{"from": "system", "text": (
                "🔁 纪要第一次被判跑题，**复问一次拿到了接地气的版本** ✓"
            )})
        else:
            summary = _meeting_fallback_digest(g, "纪要内容与讨论记录对不上（疑似跑题，复问一次仍不合格）")
    _team_store.append(gid, **{"from": "system", "text": "📋 会议纪要" + chr(10) + summary})
    _team_store.meeting_finish(gid, summary, stopped_early)
    if stopped_early:
        _team_store.append(gid, **{"from": "system", "text": f"（提前收口：{stopped_early}）"})

def _summary_grounding(g: dict[str, Any], summary: str) -> float:
    """纪要里有多少内容**能在讨论记录里找到**（8 字不重叠分块命中率）。

    ★ 2026-10-05 用户实测：收口那次模型**编了一份与议题完全无关的纪要**
    （用户问"一天赚 300 能不能做"，纪要写成"复购口径 / 项目 vs 劳务"——记录里根本没这些词）。
    这种"看着像纪要、其实跑题"的东西比没有纪要更糟：用户会以为大家真讨论过。
    所以生成后做一次**接地校验**：太低就按失败处理（重试 → 再用自动整理兜底）。
    """
    def norm(s: str) -> str:
        # 用集合过滤而不是正则：正则里那堆引号/方括号转义很容易写出 SyntaxWarning（本轮就踩了）
        drop = set(" \t\r\n，。！？、；：""''（）【】《》,.!?;:'\"()[]<>·—…-")
        return "".join(ch for ch in (s or "") if ch not in drop)

    na = norm(summary)
    nb = norm(TeamStore.meeting_transcript_text(g))
    if not na:
        return 0.0
    if not nb:
        return 1.0                     # 没有记录可比（不该发生）：不因此判失败
    if na in nb:
        return 1.0
    win = 6
    hits = sum(1 for i in range(0, max(1, len(na) - win + 1), 2) if na[i : i + win] in nb)
    total = max(1, len(na) // win)
    cov = hits / total
    # 短纪要（几十个字）用比例很吃亏：只要出现**一处 6 字以上的引用**就算接地
    # （跑题那种一个片段都对不上；而"先做最小可用版"这种真引用会命中）。
    if hits >= 1 and len(na) < 120:
        return 1.0
    return max(cov, 1.0 if hits >= 2 else 0.0)


SUMMARY_GROUNDING_MIN = 0.25          # 低于这个比例 = 明显跑题（宁可给"自动整理"那份）


def _meeting_fallback_digest(g: dict[str, Any], why: str) -> str:
    """收口失败时的**兜底纪要**：从讨论记录里自动整理"每人最后说了什么 + 分歧关键词"。

    宁可给一份"机器整理的要点"，也不要给用户一句"收口失败" ——
    前者他还能用，后者等于这场会白开了（用户原话："最后也是没有任何东西"）。
    """
    tr = list(g.get("meeting_transcript") or [])
    last: dict[str, str] = {}
    for x in tr:
        last[str(x.get("name"))] = str(x.get("text") or "")
    keys = ("分歧", "不同意", "风险", "建议", "结论", "但是", "问题是")
    picked: list[str] = []
    for x in tr:
        for sent in re.split(r"[。；;\n]", str(x.get("text") or "")):
            s = sent.strip()
            if s and any(k in s for k in keys) and len(picked) < 6:
                picked.append(f"- **{x.get('name')}**：{s[:160]}")
    lines = [f"（⚠️ 自动收口没成功：{why}；下面按发言记录自动整理，仅供参考）", ""]
    lines += ["1. **结论**", "   - 未能自动收敛出统一结论。下面是各方最终立场，供你判断。"]
    lines += ["", "2. **分歧 / 各方立场**"]
    for nm, tx in last.items():
        one = " ".join(tx.split())
        lines.append(f"   - **{nm}**：{one[:200]}{'…' if len(one) > 200 else ''}")
    if picked:
        lines += ["", "   *带「分歧/结论/风险」字样的原话：*"] + ["   " + p for p in picked]
    lines += ["", "3. **待办**", "   - 需你手动确认（自动收口失败，未生成）"]
    return chr(10).join(lines)


def _dispatch_relay_gate(gid: str, goal: str) -> None:
    """★ 接力完成后的**项目终验门**（2026-10-06）✓ —— 最后一棒交完 ≠ 东西能跑 ✓。

    做法与广播一致：派一个真跑终验的任务 + 挂看门者（`broadcast_gate=True` 复用它宣布结论 ✓）。
    """
    g = _team_store.get_group(gid) or {}
    members = [e for e in (_team_store.get_employee(m) for m in (g.get("members") or [])) if e]
    who = None
    for e in members:
        if any(k in f"{e.get('role', '')} {e.get('name', '')}" for k in ("测试", "QA", "质量")):
            who = e
            break
    who = who or (members[0] if members else None)
    if who is None:
        _team_store.append(gid, **{"from": "system", "text": (
            "⚠️ 接力跑完了，本该做**项目验收**（真跑一遍看能不能用），但群里没人可派 ⇒ 跳过。"
            "（「接力完成」**不代表项目能跑** ✓）"
        )})
        return
    _team_store.append(gid, **{"from": "system", "text": (
        f"🔎 接力跑完了 —— 最后一道门：**项目验收**（交给 @{who['name']}）" + chr(10)
        + "不再只看「每一棒都交了」，而是**在群工作区里真跑一遍**，确认这东西真的能用 ✓"
    )})
    try:
        task = _launch_task(_project_acceptance_task(str(goal or ""), ""), None,
                            workdir=store.group_workspace(gid))
    except Exception as e:                                  # noqa: BLE001
        _team_store.append(gid, **{"from": "system", "text": f"⚠️ 项目验收派不出去：{type(e).__name__}"})
        return
    _spawn_bg(_watch_group_delivery(task.id, who["name"], gid, broadcast_gate=True))


def _relay_after_delivery(gid: str, name: str, reply: str, pos: int, status: str) -> None:
    """接力交接：上一棒交付后把交付内容喂给下一棒；最后一棒完成则收口。

    `status` 是任务终态：失败/取消**不接棒**（否则下一棒在残缺基础上干活，还难查是谁的问题）
    —— 群里明说停在哪一棒，并给出"怎么接着跑"。
    """
    g2 = _team_store.get_group(gid)
    if g2 is None:
        return
    if status in ("failed", "cancelled"):
        _team_store.relay_stop(gid, f"第 {pos} 棒 {status}")
        _team_store.append(gid, **{"from": "system", "text": (
            f"⚠️ 接力停在第 {pos} 棒：@{name} 的任务{('失败' if status == 'failed' else '被取消')}。"
            "修好后可以再发一次目标重新开始接力。"
        )})
        return
    step = _team_store.relay_advance(gid, pos)
    if step is None:
        return                          # 位置对不上（重复回调/用户已重置）⇒ 不动
    if step.get("finished"):
        _team_store.append(gid, **{"from": "system", "text": (
            f"✅ 接力完成（共 {step['total']} 棒）。上面各位的交付按顺序就是最终结果。"
        )})
        # ★★ 2026-10-06：**最后一棒交完 ≠ 这东西能跑** ✗ —— 接力也要过"项目终验门" ✓
        #   （和广播同一个道理："做完 ≠ 能跑"与模式无关 ✓）
        _dispatch_relay_gate(gid, str(g2.get("relay_goal") or ""))
        return
    emp = _team_store.get_employee(step["emp_id"])
    if emp is None:
        _team_store.relay_stop(gid, "下一棒员工不存在")
        _team_store.append(gid, **{"from": "system", "text": "⚠️ 接力的下一棒员工不存在，已停下"})
        return
    nm = emp["name"]
    _team_store.append(gid, **{"from": "system", "text": (
        f"🔁 接力 {step['pos']}/{step['total']}：@{nm} 接棒（基于 @{name} 的交付）"
    )})
    try:
        _dispatch_to_employee(
            gid, g2, nm, emp,
            _team_store.relay_prompt(str(g2.get("relay_goal") or ""), name, reply,
                                     step["pos"], step["total"]),
            relay_pos=step["pos"],
        )
    except Exception as e:
        _team_store.relay_stop(gid, f"派发下一棒失败：{type(e).__name__}")
        _team_store.append(gid, **{"from": "system", "text": (
            f"⚠️ 接力在派发第 {step['pos']} 棒时失败：{type(e).__name__}: {str(e)[:120]}"
        )})


def _dispatch_to_employee(gid: str, g: dict[str, Any], nm: str, emp: dict[str, Any], task_text: str,
                          relay_pos: int | None = None, leader_name: str | None = None) -> Any:
    """把一个工作单派给指定员工（独立大脑+人设），完成自动回流群聊。

    `relay_pos` 只有接力模式会给：它是**这一棒的位置**，交付回流时用它防"串棒"
    （重复回调/乱序/用户中途重置都不该把同一棒推进两次，见 TeamStore.relay_advance）。
    """
    sys_extra = (
        f"你是「{emp['name']}」，团队{emp.get('dept', '')}的{emp.get('role', '成员')}。" + chr(10)
        + _team_store.persona_for(emp) + chr(10)
        + "在沙箱/工作区内完成任务后，用 task_done 交付，交付消息要简洁、可直接贴进群聊。"
    )
    mc = cfg.model
    if emp.get("provider"):
        mc = cfg.model.model_copy(update={
            "provider": emp["provider"],
            "model_name": emp.get("model_name") or mc.model_name,
            "base_url": emp.get("base_url") or mc.base_url,
            "api_key_env": emp.get("api_key_env") or mc.api_key_env,
        })
    provider = create_provider(mc)
    # ★ 2026-10-05 真群回归暴露：每个任务一个独立工作区 ⇒ 同群的人**互相看不到对方的产物** ⇒
    #   「按文件交接」永远不可能成功（架构师的契约在它自己的工作区里，程序员那边是空的）。
    #   现在同群共享一个工作区，并登记映射（文件接口 / 低层验收靠它解析相对路径）。
    gws = store.group_workspace(gid)
    _budget = _budget_for(str((emp or {}).get("role") or ""), task_text)
    task = _launch_task(f"【群任务 @{nm}】{task_text}", None, provider=provider,
                        system_extra=sys_extra + chr(10) + chr(10) + envfacts.facts(str(gws)),
                        workdir=gws, max_iterations=_budget,
                        # ★ 2026-10-07 按角色限权（用户点名要的）：**群任务以员工的身份跑** ✓
                        #   于是"测试工程师"这位同事**改不动**已有文件 ✓
                        #   —— 这正是"独立验证"成立的前提 ✓（把关的人不能自己改被测物 ✓）
                        role=str(emp.get("role") or ""))
    store.set_task_workdir(task.id, gws)
    _team_store.append(gid, **{"from": f"emp:{nm}", "text": f"收到，开始执行：{task_text[:100]}", "task_id": task.id, "status": "running"})

    # ★ 二十六轮第 7 批第 7a 处：原先这里内联了一份 30 分钟看门逻辑，groups/{gid}/say
    #   里又抄了一份，而两份都带同一个 bug（见 _watch_group_delivery 的注释）。
    #   现已合并成模块级函数：一份实现、可被单测直接驱动。
    _spawn_bg(_watch_group_delivery(task.id, nm, gid, relay_pos=relay_pos, leader_name=leader_name))
    return task


def _notify_done(title: str, task_id: str, run: Any) -> None:
    """任务交付时按 notify 配置推送 Slack/邮件（第 41 班）。失败只打日志，绝不影响任务。"""
    try:
        if not (cfg.notify.slack_webhook or cfg.notify.email):
            return
        from .notify import notify_task_done

        status = run.task.status if hasattr(run, "task") else "done"
        summary = ""
        try:
            evs = store.read_events(task_id)
            asst = [e for e in evs if e.type == "message" and e.payload.get("role") == "assistant"]
            if asst:
                # 复审 P1：推送外发前过打码——通知是唯一"出网"通道，不能绕过打码管线
                summary = _redact_text(str(asst[-1].payload.get("text", "")))[:600]
        except Exception:
            pass
        for note in notify_task_done(cfg.notify.model_dump(), title, status, summary):
            print(f"[通知] {note}", flush=True)
    except Exception as e:
        print(f"[通知] 推送失败（不影响任务）：{type(e).__name__}: {e}", flush=True)


from contextlib import asynccontextmanager




def _recover_group_deliveries() -> None:
    """§6.3（审计）：群任务完成监视器是进程内 asyncio，后端重启即失联——
    已派发的群任务永远停在「收到，开始执行」，交付永不回流。
    启动时扫各群 feed：有 task_id 仍标 running、且之后没有同 task_id 的终态消息
    → 按任务落盘状态补一条交付回流（重复重启幂等：有终态消息就跳过）。"""
    try:
        groups = _team_store.groups()
    except Exception:
        return
    for g in groups:
        gid = g.get("id")
        if not gid:
            continue
        try:
            msgs = _team_store.feed(gid)
        except Exception:
            continue
        delivered = {
            m.get("task_id")
            for m in msgs
            if m.get("task_id") and m.get("status") in ("done", "partial", "failed", "cancelled", "idle")
        }
        pending: dict[str, str] = {}  # task_id -> 原派出者（emp:名字）
        for m in msgs:
            tid = m.get("task_id")
            if not tid or tid in delivered:
                continue
            if m.get("status") == "running":
                pending.setdefault(tid, str(m.get("from") or "system"))
        for tid, sender in pending.items():
            t = tasks.get(tid)
            status = t.status if t else None
            if status in ("running", "created", None, "waiting_approval"):
                # ★ 2026-10-05（今晚实测：为装修复而重启 3 次，打断了 3 次任务）：
                #   落盘状态说"在跑"，可**进程里已经没有活的 run 了** ⇒ 那是被重启打断的 ✗。
                #   这里**自动接着跑**（同一个任务、同一个工作区，工作单写明"为什么停下的"），
                #   并且每 2 次才自动续一次（防"一直重启一直续"把预算烧光，超了就在群里如实说）。
                _run = runs.get(tid)
                _alive = (_run is not None and getattr(_run, "aio_task", None) is not None
                          and not _run.aio_task.done())
                if _alive:
                    continue                       # 真还在跑 —— 不误发，交给常规流程
                _item = _team_store.leader_item_by_task(gid, tid)
                if _item is None:
                    continue                       # 不是组长波次的活（点名/接力）⇒ 走原来的补投
                if _AUTO_RESUME.get(tid, 0) >= 2:
                    _team_store.append(gid, **{"from": "system", "text": (
                        f"⏳ @{_item['name']} 的活被重启打断过 {_AUTO_RESUME[tid]} 次，"
                        "不再自动接着跑（怕一直重启一直续）。说「接着跑」可以手动续它。"
                    ), "task_id": tid, "status": "running"})
                    continue
                _AUTO_RESUME[tid] = _AUTO_RESUME.get(tid, 0) + 1
                _team_store.append(gid, **{"from": "system", "text": (
                    f"🔁 后端重启打断了 @{_item['name']} 的活 —— **已自动接着跑**"
                    "（同一个任务、同一个工作区；工作单里写明了它为什么停下、已经有什么）"
                ), "task_id": tid, "status": "running"})
                try:
                    _do_resume(gid, tid, str(_item.get("name") or ""))
                    print(f"[群回流] {gid} 自动续跑 {tid}", flush=True)
                except Exception as e:
                    print(f"[群回流] {gid} 自动续跑 {tid} 失败：{type(e).__name__}: {e}", flush=True)
                continue
            try:
                evs = store.read_events(tid)
            except Exception:
                continue
            asst = [e for e in evs if e.type == "message" and e.payload.get("role") == "assistant"]
            # ★ 别的地方同理：交付正文放宽到 4000（真实输出常在后半段被 800 砍掉 ✗）
            reply = str(asst[-1].payload.get("text") or "")[:4000] if asst else "（任务结束）"
            atts = (asst[-1].payload.get("attachments") if asst else None) or []
            try:
                _team_store.append(gid, **{"from": sender, "text": reply,
                                           "task_id": tid, "status": status, "attachments": atts})
                print(f"[群回流] {gid} 补投 {tid}（{status}）", flush=True)
            except Exception as e:
                print(f"[群回流] {gid} 补投 {tid} 失败：{type(e).__name__}: {e}", flush=True)
        # ★ 组长波次的对账（Jev 判定里的头号漏洞）：看门在后端重启时丢了 ⇒ 漏掉的交付在这里补上，
        #   下一批该派的才会派出去（幂等：已经 done 的项不会被再动）
        try:
            _reconcile_leader(gid)
        except Exception as e:
            print(f"[群回流] {gid} 组长对账失败：{type(e).__name__}: {e}", flush=True)


# 复审 P2：事件循环对 Task 只持弱引用——保存强引用防监视/提取协程被 GC 静默回收
_BG_TASKS: set = set()


def _spawn_bg(coro):
    t = asyncio.create_task(coro)
    _BG_TASKS.add(t)
    t.add_done_callback(_BG_TASKS.discard)
    return t


async def _task_watchdog():
    """任务看门狗（第 41 班，用户实测"检查 C 盘卡死 9 分钟无人管"）：
    running 任务超过 10 分钟没有任何新事件 → 自动取消并明确告知。"""
    from datetime import datetime as _dt, timezone as _tz
    while True:
        try:
            await asyncio.sleep(60)
            now = _dt.now(_tz.utc)
            for tid, run in list(runs.items()):
                if run.aio_task is None or run.aio_task.done():
                    continue
                if tasks.get(tid) is None or tasks[tid].status != "running":
                    continue
                evs = store.read_events(tid)
                if not evs:
                    continue
                last = evs[-1].ts
                dt = _dt.fromisoformat(last.replace("Z", "+00:00"))
                idle_s = (now - dt).total_seconds()
                if idle_s > 600:
                    print(f"[看门狗] {tid} {idle_s:.0f}s 无进展，自动取消", flush=True)
                    # 复审修正：置 takeover 让 loop 的 CancelledError 处理器不再发
                    # 第二条 cancelled（此前两条同 seq 同 id，SSE 游标会吞掉一条）
                    run.takeover = True
                    run.cancel()
                    tasks[tid].status = "cancelled"
                    tasks[tid].updated_at = _now()
                    _save_index()
                    _emit_standalone(tid, "status", {
                        "state": "cancelled",
                        "detail": f"任务超过 {int(idle_s/60)} 分钟没有任何进展，已自动取消（可发消息重试，或把大任务拆小）",
                    })
                    runs.pop(tid, None)
        except Exception as e:
            print(f"[看门狗] 异常（继续运行）：{type(e).__name__}: {e}", flush=True)


def _sweep_orphan_waiters() -> None:
    """启动清扫（十三轮用户实测 bug）：磁盘 index 里仍处于 running/waiting_approval
    的任务，其运行循环只活在旧进程内存里——后端重启后：
      · approve 必 409"没有等待中的审批"（_pending 随进程消失）
      · UI 却还挂着审批卡片 → 用户点"允许一次"报错，看起来像卡死
    诚实处理：统一置为 failed 并补一条 status 事件，UI 不再显示永远批不了的卡片。
    （cancel 端点早有"僵尸运行态"分支；本函数把同样的诚实性提前到启动时。）"""
    swept = 0
    for tid, task in tasks.items():
        if task.status in ("running", "waiting_approval", "created"):
            task.status = "failed"
            task.updated_at = _now()
            _emit_standalone(tid, "status", {
                "state": "failed",
                "detail": "后端重启，运行被中断——原等待中的审批已失效（此为诚实降级，非静默丢弃）",
            })
            swept += 1
    if swept:
        _save_index()
        print(f"[启动清扫] {swept} 个重启遗留的运行态任务已置为 failed（审批孤儿诚实降级）", flush=True)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # ★★ 2026-10-07（体检真跑抓到的 ✗✗）：**先把系统里的 Key 补进本进程** ✓
    #   用户原话："我已经把那个 API 贴上去了" ✓ —— 结果后端还说"没设置" ✗
    #   原因：用户级/机器级环境变量**只对新开的进程生效** ✓
    #   而"重启后端"若从一个**更早启动的父进程**发出 ✓ 继承的还是老环境 ✗✓
    #   ⇒ 这里主动去系统里补一道 ✓（只补缺失 ✓ 不覆盖进程内的临时设置 ✓）
    #   补上之后，全项目十几处 `os.environ.get("XXX_API_KEY")` **一行不用改就都好了** ✓✓
    try:
        from .envkeys import migrate_into_credentials, sync_user_env
        added = sync_user_env()
        if added:
            _safe_print(f"[agent-shell] 从系统环境补齐了 {len(added)} 个变量："
                        f"{sorted(added)}（来源不打印 ✓）", flush=True)
        # ★ 2026-10-10 第二期（照 DSH 的做法）：把"系统里已经有、凭据库里还没有"的 Key
        #   收进 `data/credentials.yaml` ✓ —— **只搬不删** ✗（注册表/环境变量那份原样留着 ✓）
        #   之后凭据库就是"按名字存、看得见、写前有备份"的真相源 ✓
        moved = migrate_into_credentials()
        if moved:
            _safe_print(f"[agent-shell] 已把 {len(moved)} 把 Key 收进凭据库"
                        f"（data/credentials.yaml）：{sorted(moved)}（值不打印 ✓）", flush=True)
    except Exception as e:                                   # noqa: BLE001
        _safe_print(f"[agent-shell] ⚠️ 系统环境同步失败（不影响启动）：{type(e).__name__}: {e}",
                    flush=True)
    # P1-15 启动自检：provider 不可用时给出明确指引。
    # 注意**故意不让服务起不来**——设置页可以就地填 Key / 换提供者；
    # 直接退出反而把唯一的自助修复入口也关掉了。真正的保护在 _launch_task（503 且不留僵尸任务）。
    if cfg.server.host in ("0.0.0.0", "::") and not cfg.server.access_token:
        raise RuntimeError(
            "安全拦截：host=0.0.0.0（局域网/远程可达）时必须设置 server.access_token 访问密码——"
            "否则任何同网设备都能操控你的电脑。请在 config.json 设置密码，或把 host 改回 127.0.0.1。"
        )
    asyncio.create_task(_automation_loop())
    asyncio.create_task(_task_watchdog())
    # K6a：provider 启动自检【接回】——旧实现死在 _automation_loop 的 while True
    # 之后（main.py 历史死代码），永远不会执行；本注释（P1-15）声称的自检其实
    # 从未发生过。失败行为：**只警告不拒绝启动**——设置页是唯一的自助修复入口，
    # 拒绝启动等于把入口也关掉；真正的保护在 _launch_task（503 且不留僵尸任务）。
    try:
        create_provider(cfg.model)
        _safe_print(f"[agent-shell] provider={cfg.model.provider} 就绪", flush=True)
    except Exception as e:
        # 二十六轮 K6b：这里【必须】用 _safe_print——本块在 except 内部，
        # 任何 print 异常都会冲出 _lifespan ⇒ 服务拒绝启动 ⇒ 与"只警告不拒绝
        # 启动"的设计承诺相反，并关死设置页这个自助修复入口（GBK 控制台下
        # ⚠️ 就足以触发，实测进程 rc=3、端口从未监听）。
        _safe_print("=" * 72, flush=True)
        _safe_print(f"[agent-shell] ⚠️  模型提供者不可用：{type(e).__name__}: {e}", flush=True)
        _safe_print("   服务仍会启动（可在界面「设置」里填 Key 或切换提供者）。", flush=True)
        _safe_print("   在修好之前：新建任务会返回 503 并附带原因，不会静默失败、也不会留下僵尸任务。", flush=True)
        _safe_print("=" * 72, flush=True)
    _recover_group_deliveries()  # §6.3：重启后补投失联的群任务交付
    _sweep_orphan_waiters()  # 十三轮用户实测：重启后等待审批变孤儿 → 诚实终结
    for wid in [w["id"] for w in wide.values() if w.get("status") == "running"]:
        _spawn_bg(_wide_monitor(wid))  # 重启后续监视未完成的 Wide Research
    # MCP 万物皆可插（第 41 班）：后台装载外部 server 并注入工具表。
    # **必须 create_task**：npx 冷启动可到 1-2 分钟，同步 await 会卡住整个服务启动。
    # 工具注册完成前建的任务看不到 MCP 工具（一般几十秒内完成，可接受）。
    if cfg.mcp.enabled and cfg.mcp.servers:
        async def _load_mcp_bg() -> None:
            try:
                from .mcp import load_mcp_servers
                await load_mcp_servers({k: v.model_dump() for k, v in cfg.mcp.servers.items()})
            except Exception as e:
                print(f"[MCP] 装载失败（不影响运行）：{type(e).__name__}: {e}", flush=True)

        asyncio.create_task(_load_mcp_bg())
    yield
    # 关停：回收 MCP 子进程（否则每次重启都留下孤儿 npx/node，挤占 npm 缓存——第 41 班实测）
    try:
        from .mcp import get_mcp_registry
        await get_mcp_registry().close_all()
    except Exception:
        pass

app = FastAPI(title="LanternLogic Agent", version="0.1.0", lifespan=_lifespan)

# ---------- P2-6：本地 API 的防跨站护栏 ----------
#
# 问题（第一轮评估发现，一直未修）：本地 API **没有任何鉴权、也不校验来源**。
#   · CSRF：无 body 的 POST 属于"简单请求"，**浏览器不发预检**，
#     于是任意网页都能 `fetch('http://127.0.0.1:8642/api/v1/tasks/x/cancel', {method:'POST'})`
#     取消或接管你正在跑的任务；
#   · DNS rebinding：攻击者把自己的域名解析到 127.0.0.1，
#     同源策略就被绕开，可以**读走全量数据**（任务、事件、文件）。
#
# 修法（三道闸，都不需要改前端、也不会挡住 curl 与自己写的脚本）：
#   1. **Host 必须是回环名** —— 直接掐死 DNS rebinding（浏览器按域名发 Host）；
#   2. **Sec-Fetch-Site 不能是 cross-site** —— 浏览器自动带的头，页面脚本改不了，最硬的一条；
#   3. **Origin 若存在，必须是回环源** —— 挡住普通跨站 CSRF。
# 说明：没有 Origin 的请求（curl、本地脚本、vite 代理）一律放行 —— 它们不是浏览器跨站场景。
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
# 桌面壳（Tauri 2）webview 的源：Windows 上是 http://tauri.localhost。
# 复审补验（安装包冒烟实测 403）：不收录则装出来的应用前端连不上自己的后端。
_LOOPBACK_HOSTS |= {"tauri.localhost"}


def _host_of(value: str | None) -> str:
    """从 Host 或 Origin 里取出主机名（小写，去端口与方括号）。"""
    if not value:
        return ""
    v = value.strip()
    if "://" in v:  # Origin 形态
        v = v.split("://", 1)[1]
    v = v.split("/", 1)[0]
    if v.startswith("["):  # IPv6 [::1]:8642
        return v.split("]", 1)[0].lstrip("[").lower()
    return v.rsplit(":", 1)[0].lower() if ":" in v else v.lower()


# 第 41 班：手机直连（局域网模式）——host=0.0.0.0 且设置了 access_token 时：
#   允许局域网 Host（不再限回环）；所有 API 要求 X-Auth-Token 或 ?token= 匹配。
# 回环模式（默认）行为完全不变；跨站防护两种模式下都保持。
def _lan_mode() -> bool:
    """现在**真正**处于局域网可访问状态吗？

    ★ 第 8f 处（本班自查出的严重潜伏 bug）：判据必须是【进程启动时真正绑定的地址】，
      而不是配置里写的值。原因：
        · 监听地址是 uvicorn 启动时定下的，运行中改配置【不会】改变实际绑定；
        · 而 `_save_config()`（任何一次设置保存都会调它）是拿**内存里的 cfg**
          整体覆盖写文件的 —— 如果配置说 0.0.0.0 而内存还是 127.0.0.1，
          用户点一次设置就把"已开通手机直连"**静默打回**，下次重启手机就连不上了
          （本班真实踩到：config.json 被改回 127.0.0.1，只因进程还绑着 0.0.0.0 才没当场出事）。
      所以：**内存 cfg 也一起改**（防被设置保存覆盖），而"是否已生效"一律看 `_BOUND_HOST`。
    """
    return _BOUND_HOST in ("0.0.0.0", "::") and bool(cfg.server.access_token)


@app.middleware("http")
async def guard_local_origin(request: Request, call_next):  # type: ignore[no-untyped-def]
    host = _host_of(request.headers.get("host"))
    lan = _lan_mode()
    if host and not lan and host not in _LOOPBACK_HOSTS:
        return JSONResponse(
            status_code=403,
            content={"detail": f"拒绝非本机 Host：{host}（本 API 只服务本机，防 DNS rebinding）"},
        )
    if lan:
        # 复审 P0：此前 `lan and host`——无 Host 头的裸请求（HTTP/1.0）整条跳过
        # token 校验 = 同网匿名操控。Host 缺失一律 403（HTTP/1.1 Host 本必填）。
        if not host:
            return JSONResponse(status_code=403, content={"detail": "缺少 Host 头"})
        # ★ 第 8d 处（真局域网实测抓到的）：**界面静态资源不能要密码**。
        #   此前是"除 auth/check 外全都校验"，于是手机扫码打开页面后，浏览器去取
        #   `/assets/index-xxx.js` 被 401 挡掉 ⇒ **网页白屏**（实测 body 只有 448 字节
        #   ＝ index.html 本身，React 根本没跑起来；而这个 bug 走 vite 开发服务器
        #   永远测不到——那里静态资源由 vite 出，只有 /api 落到后端）。
        #   口径：**只有 /api/* 需要密码**；界面资源（HTML/JS/CSS/图标）与
        #   /api/v1/auth/check 公开。数据面全在 /api/* 之下（含产物读取 /api/v1/files），
        #   所以这样放开是安全的：能打开界面 ≠ 能拿到数据。
        path_lan = request.url.path
        # ★★ 2026-10-06（用户实测："Webhook 这个能触发吗？" ⇒ **真去试了 ⇒ 根本触发不了** ✗✗）：
        #   现象：带上**正确的 HMAC 签名**去 POST `/api/v1/hooks/<id>/<secret>` ⇒
        #   照样 `401 需要访问密码（token 不匹配）` ✓ —— 被**达大门的密码**挡在外面了 ✓。
        #   而 Webhook 的调用方是**外部系统** ✓ 它**不可能知道**你本机的访问密码 ✓
        #   ⇒ 这条路等于**永远打不开** ✗（"定时 / Webhook 触发"里的后一半是摆设 ✓）。
        #
        #   ⇒ 放行 `/api/v1/hooks/`（**只这一条** ✓），理由：
        #     · 它**自带一套更严的鉴权** ✓：URL 里的 `secret` + `X-AgentShell-Sign`
        #       （HMAC-SHA256(secret, "时间戳.请求体")）+ 时间戳偏差 >300s 拒（防重放）
        #       + 连错 5 次限流 ✓（见 `fire_hook`）
        #     · 拿到 URL 也**不等于**能操控 Agent ✓ —— 没有 secret 与签名一律 401 ✓
        #     · 而且这条路**只在开了"手机直连"（局域网）时**外部才够得着 ✓
        #       默认（只服务本机）时外网连门都摸不到 ✓
        #   ★ 除它以外**一条都不放** ✗（`/api/v1/auth/check` 那条是原有口径 ✓）
        needs_token = (path_lan.startswith("/api/")
                       and not path_lan.startswith("/api/v1/auth/check")
                       and not path_lan.startswith("/api/v1/hooks/"))
        if needs_token:
            # ★★ 2026-10-07（第 5 项）**防爆破限流，且限流先于比对** ✓ ——
            #   查证：这块**一个都没有** ✗ 开了手机直连之后，同一个 WiFi 下任何人都能**无限次**敲门 ✓
            #   密码默认是 32 字符随机串（撞不上 ✓）但**用户自己可以改** ✓（只拦"至少 8 位"✗
            #   ⇒ 8 位纯数字那种，不设防爆破几下就试出来了 ✓）
            #   口径照抄 webhook 那处（"连错 5 次冷却 60 秒、限流先于比对"✓）见 app/login_guard.py ✓
            _ip_lan = request.client.host if request.client else ""
            _wait = login_guard.retry_after(_ip_lan)
            if _wait > 0:
                return JSONResponse(
                    status_code=429,
                    headers={"Retry-After": str(int(_wait) + 1)},
                    content={"detail": f"密码错了太多次，请等 {int(_wait) + 1} 秒后再试（防爆破限流）"},
                )
            token = request.headers.get("x-auth-token") or request.query_params.get("token") or ""
            # 复审 P2：token 用常量时间比较（防时序侧信道）
            import hmac as _hmac_g
            if not _hmac_g.compare_digest(str(token), str(cfg.server.access_token or "")):
                login_guard.record_fail(_ip_lan)
                return JSONResponse(status_code=401, content={"detail": "需要访问密码（token 不匹配）"})
            login_guard.record_ok(_ip_lan)      # 输对了就清账 ✓（不然昨天的错会一直压着 ✓）
    elif _lan_mode() is False and (request.headers.get("x-auth-token") or request.query_params.get("token")):
        # 只服务本机时带着密码来 ⇒ 也算一次"用密码敲门" ✓ 输对了顺手清账 ✓（口径一致 ✓）
        login_guard.record_ok(request.client.host if request.client else "")
    if (request.headers.get("sec-fetch-site") or "").lower() == "cross-site":
        # 桌面壳（Tauri 2 webview）的源是 http://tauri.localhost，对本机 API 就是
        # cross-site——Origin 落在回环/tauri 白名单里的放行（Origin 由浏览器/WebView
        # 控制，页面伪造不了；真正的外站 Origin 依然被拒）。
        origin = request.headers.get("origin")
        if not origin or _host_of(origin) not in _LOOPBACK_HOSTS:
            # ★ 2026-10-09（用户实测撞上 ✓ 被这条拦住但看不懂 ✓）：
            #   原来只回一句「拒绝跨站请求（Sec-Fetch-Site: cross-site）」✗
            #   —— 用户拿着这句话**不知道该干什么** ✓ 只觉得"网页坏了" ✗
            #   ⇒ 改成**可照做的提示** ✓（说清：该用哪个地址打开 ✓ 不要双击 dist 里的 html ✓）
            #   ★ 判据一个字没放松 ✗ —— 只把"为什么被拒/该怎么办"讲明白 ✓
            return JSONResponse(
                status_code=403,
                content={"detail": (
                    "拒绝跨站请求（Sec-Fetch-Site: cross-site）"
                    "　—— 这条是**安全设计** ✓ 不是故障 ✓"
                    f"　★ 请用这个地址打开界面：http://127.0.0.1:{cfg.server.port}/ "
                    "（别直接双击 frontend/dist/index.html ✗ 那样页面来自 file:// "
                    "⇒ 所有接口调用都会被判跨站 ✓）"
                    "　★ 若你确实是从 127.0.0.1 / localhost 打开的，"
                    "请把 F12 → Network 里那条被拒请求的 Origin 发给我们 ✓"
                )},
            )
    origin = request.headers.get("origin")
    if origin and not lan:
        oh = _host_of(origin)
        if oh not in _LOOPBACK_HOSTS:
            return JSONResponse(
                status_code=403,
                content={"detail": f"拒绝跨站来源：{origin}（本 API 只接受本机页面的请求）"},
            )
    return await call_next(request)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    # 复审 §1.9：直接访问后端端口时浏览器仍会要 favicon——返回前端产物里的图标
    #
    # ★ 2026-10-09 修正 ✗（CI 第 8 轮 + 干净副本本机复现，两处同一条）：
    #   原来这里**自己拼了一条写死的路径** ✗
    #       Path(__file__).resolve().parents[2] / "frontend" / "dist" / "favicon.ico"
    #   而**界面静态挂载用的是 `_UI_DIST`** ✓ —— 同一份产物、两处各写各的 ✗
    #   开发机上 frontend/dist 存在（构建过）⇒ 200 ✓
    #   CI / 干净副本里 **dist 没被提交**（.gitignore 挡着 ✓）⇒ 404 ✗
    #   ⇒ 统一到 `_UI_DIST` ✓（它是模块级变量、在文件后半段才定义 ✓
    #      但函数体里取的是**调用时**的全局 ✓ 所以没问题 ✓
    #      而且测试把 `_UI_DIST` 指到临时目录时，这里**也跟着走** ✓ 行为一致了 ✓）
    #   冻结态（exe）保留原路 ✓ —— 那种布局下 dist 与 exe 同级 ✓
    if getattr(sys, "frozen", False):
        icon = Path(sys.executable).resolve().parent / "favicon.ico"
    else:
        icon = _UI_DIST / "favicon.ico"
    if not icon.is_file():
        raise HTTPException(404, "no favicon")
    return FileResponse(icon, media_type="image/x-icon")


@app.get("/api/v1/auth/check")
async def auth_check(request: Request, token: str = "") -> dict[str, Any]:
    """手机直连登录探测：token 对则 ok；错则 401（前端据此显示密码页）。

    ★★ 2026-10-07（第 5 项）：这条**被中间件豁免了**（界面资源要公开 ✓ 见中间件那段注释 ✓）
    ⇒ 它自己比对 ✓ 所以**防爆破也得在这儿单独加一道** ✗
    （不然整条中间件都加了限流，唯独"登录探测"这个最像登录口的没加 ✓ 那就白做了 ✓）
    """
    if not _lan_mode():
        return {"ok": True, "lan": False}
    ip = request.client.host if request.client else ""
    wait = login_guard.retry_after(ip)
    if wait > 0:
        raise HTTPException(
            status_code=429,
            detail=f"密码错了太多次，请等 {int(wait) + 1} 秒后再试（防爆破限流）",
            headers={"Retry-After": str(int(wait) + 1)},
        )
    if token != cfg.server.access_token:
        login_guard.record_fail(ip)
        raise HTTPException(401, "访问密码不正确")
    login_guard.record_ok(ip)
    return {"ok": True, "lan": True}

# ---------- 任务 CRUD 与输入 ----------


def _launch_task(
    input_text: str,
    project_id: str | None = None,
    provider: Any | None = None,
    system_extra: str | None = None,
    workdir: Any = None,   # ★ 群共享工作区（同群所有人写同一处）
    max_iterations: int | None = None,   # ★ 步数预算（群任务按角色分档；不给用配置默认）
    role: str = "",        # ★ 以谁的身份跑（限权用 ✓ 空 = 默认助手不受限 ✓）
    cost_cap_cny: float = 0.0,   # ★ 2026-10-07（第 7 项）：**单次花费上限**（0 = 不拦 ✓）
    auto_retries: int = 0,       # ★ 自动化用：失败后自动重试几次（0 = 不重试 ✓ 只记账 ✓）
) -> TaskSummary:
    """创建任务并启动运行（HTTP 端点与 Automations 调度共用）。"""
    # 复审 P1：License 闸在此统一把关（此前只在 POST /tasks，webhook/定时/
    # wide/团队派发/语音全部旁路——到期后照烧 LLM）。allow_unlicensed 仅供
    # 试用到期后的紧急停机提示路径使用。
    #
    # ★★ 2026-10-09：**这一闸在 AGPL 版必须关掉** ✗→✓（`config.license.enforce` ✓）
    #   AGPL 第 10 条禁止附加限制 ✗ —— 而"试用 30 天后不许创建任务"就是附加限制 ✓
    #   ⇒ 默认 `enforce = False`（公开仓库跟着这个默认值走 ✓ 谁 clone 都不带锁 ✓）
    #   ⇒ 商用版把 `enforce = True` 打开 ✓ = 双授权的标准做法 ✓
    if bool(getattr(cfg.license, "enforce", False)):
        allowed, why = _lic.can_create_task()
        if not allowed:
            raise HTTPException(402, why)
    proj = projects.get(project_id or "")
    if project_id and proj is None:
        raise HTTPException(404, f"项目不存在：{project_id}")
    # P1-15：先实例化 provider，**再**落盘任务。
    # 否则缺 Key / 配置错时：请求失败但任务已经进了索引 → 永久卡在 created 的僵尸任务
    # （index.json 里那 3 条 created 就是这么来的）。现在改为返回 503 + 可执行指引。
    try:
        provider = provider or create_provider(cfg.model)
    except Exception as e:
        # ★ Phase 2 ④：报错要带**三条降级路径**（填 Key / 换本地 / 先用演示），
        #   而且那三条由 `app/capabilities.py` 的声明表生成 —— 不许在这里手写一遍
        #   （手写就会像 A5 那样，哪天提供者/Key 变量名变了，这里是最后才知道的）。
        #   首行保持原样：既有测试与用户熟悉的措辞都认它。
        raise HTTPException(
            503,
            fallback_hint(cfg, "chat",
                          f"模型提供者不可用（provider={cfg.model.provider}）：{type(e).__name__}: {e}"),
        ) from e
    # provider 非 None（团队员工卡注入）时也做一次可用性校验——缺 Key 早失败早提示
    task = TaskSummary(
        id=_new_task_id(),
        # 二十六轮第 6 批第 7 处【回退】：title 是【用户识别面】——任务列表/详情
        # 靠它认任务，必须原样可见。第 6 批曾在此处过 _redact_text，实测后果：
        # "…sk-…"（29 字）被打成 "[[已隐藏]"（7 字），且 title 属性为 null ⇒
        # 无处还原；而同一密钥仍明文躺在 events.jsonl / history.json / 重命名端点
        # （本文件 L783 从来不打码）⇒ 安全收益≈0、用户代价实打实。
        # 防泄漏的正确位置是【外发面】：usage 的 label/full_label/by_model 各自
        # 过 _redact_text（见 _usage_label / _compose_usage_label）。
        # ★ 口径更新（2026-10-04 B10 批，上面那句"别处仍明文"的理由已经过期）：
        #   ① events.jsonl / history.json 自 A1（291357b）起**落盘即打码**；
        #   ② 出网面（Slack/邮件通知）自 B10 起在 notify.py 咽喉点打码；
        #   ③ usage 的 label/full_label/by_model 早已打码。
        #   ⇒ 今天 title 是**仅存的不打码副本**，但它只经【本机界面 / 带密码的本机
        #     API】可见（局域网模式所有 /api/* 都要 token），不外发、不上上游
        #     （重建 run 用的是 input_text 原文，title 不进 prompt）。
        #   ⇒ 结论不变：**title 保持原样可读**；新增出网渠道时必须在咽喉点打码
        #     （锚点：tests/test_notify_redact.py，回滚组见 scripts/redgreen_check.py）。
        title=input_text.strip()[:30] or "未命名任务",
        status="created",
        created_at=_now(),
        updated_at=_now(),
        project_id=proj["id"] if proj else None,
    )
    tasks[task.id] = task
    _save_index()
    # ★★ A-4（2026-10-07）：**审计账本加"任务起止"** —— 这是"起" ✓
    #   账本此前只有四类（审批 ✓ 被闸门挡下 ✓ 清空数据 ✓ 删任务 ✓）✗
    #   ⇒ "这个任务是什么时候开的"**查不到** ✓（用户要的正是这一条 ✓）
    #   ★ 放在**落盘之后** ✓ —— 任务真进了索引才配说"开了" ✓
    #     （落盘前抛异常 ⇒ 根本没有这个任务 ⇒ 就不该有这一条 ✓ 与 P1-15"不留僵尸"同一条规矩 ✓）
    #   ★ 只记 **id + 标题前 60 字** ✓ —— **不记任务正文** ✗：
    #     正文属于任务事件流 ✓ 混进总账既臃肿又多一个泄漏面 ✓
    #     （`tests/test_audit_ledger.py::test_it_does_not_record_conversation_content` 钉着这条 ✓）
    #   ★ 命令/文本类过 `redact_text` ✓（记账函数自带 ✓ 这里不用手动打码 ✓）
    audit.record("task", phase="start", task=task.id, title=input_text[:60])
    # ★ 群共享工作区：**必须在 _start_run 之前**登记（TaskRun 构造时会读
    #   store.workspace_dir(task.id) 决定自己写在哪 —— 晚一步就落回任务私有目录，
    #   同群的人又互相看不见了；真群回归踩过这个坑）。
    if workdir is not None:
        store.set_task_workdir(task.id, workdir)
    # ★ 2026-10-07（第 7 项）：**单次花费上限**登记到任务上 ✓（闸门每轮问一次 ✓ 见 `_budget_block_reason` ✓）
    if cost_cap_cny and cost_cap_cny > 0:
        _TASK_COST_CAPS[task.id] = float(cost_cap_cny)
    _start_run(task, input_text.strip(),
               system_extra=system_extra or (proj["master_prompt"] if proj else None),
               provider=provider, max_iterations=max_iterations,
               role=role)     # ★ 2026-10-07：身份一路带到 run（限权靠它 ✓）
    return task


@app.post("/api/v1/tasks", status_code=201)
async def create_task(req: CreateTaskReq) -> TaskSummary:
    sys_extra = None  # License 闸已下沉 _launch_task（覆盖全部旁路）
    role = ""
    if req.role:
        from .roles import ROLE_LIBRARY
        lib = ROLE_LIBRARY.get(req.role)
        if lib:
            sys_extra = f"[身份] 你现在以「{req.role}」的身份执行任务。" + chr(10) + lib["persona"]
            role = str(req.role)     # ★ 认得出的身份才落（限权只认角色库里的 ✓）
    return _launch_task(req.input, req.project_id, system_extra=sys_extra, role=role)


@app.get("/api/v1/tasks")
async def list_tasks() -> list[TaskSummary]:
    return sorted(tasks.values(), key=lambda t: t.created_at, reverse=True)


@app.get("/api/v1/tasks/{task_id}")
async def get_task(task_id: str) -> TaskSummary:
    return _get_task(task_id)


def _prune_trash(keep: int = 20) -> int:
    """回收站只留**最近 N 次**删除 ✓ 返回这次清掉了几个 ✓。

    ★ 为什么必须有（这是我自己今天新加的删除功能**带出来的新债** ✗）：
      侧栏那个叉现在**不真删**、挪到 `data\\_deleted_<时间戳>\\` ✓ —— 这是对的 ✓
      但**不设上限**的话，删久了它自己就变成一个新的垃圾堆 ✗
      —— 跟当年那 **213 个"界面上已删、硬盘还在"的目录共 470.5 MB** ✗ **同一个病** ✓
      本项目栽过一次的事，不该因为"这次是挪不是删"就再栽第二次 ✓

    ★ 三条：
      · 按**目录名**排序（就是时间戳 ✓ `_deleted_20261007-2230` ✓）⇒ 留新的、清旧的 ✓
      · 清理本身**也记一笔账** ✓（`trash_pruned` ✓ —— 真删掉东西这种事必须留痕 ✓
        与"清空数据""删任务"同一类"需要有人负责"的事 ✓）
      · 全程**不抛** ✗（清理失败绝不能影响用户正在做的删除 ✓）
    """
    root = _DATA_DIR / "_deleted_"
    if not root.is_dir():
        return 0
    try:
        dirs = sorted([d for d in root.iterdir() if d.is_dir()],
                      key=lambda d: d.name, reverse=True)          # 名字即时间戳 ⇒ 新的在前 ✓
    except OSError:
        return 0
    n = 0
    for d in dirs[max(1, int(keep)) :]:
        try:
            import shutil as _sh
            size = sum(p.stat().st_size for p in d.rglob("*") if p.is_file())
            _sh.rmtree(d)
            n += 1
            audit.record("trash_pruned", where=str(d)[:200], bytes=size)
        except Exception:                                          # noqa: BLE001
            continue
    return n


@app.delete("/api/v1/tasks/{task_id}")
async def delete_task(task_id: str) -> dict[str, Any]:
    """删除任务：从列表移除 **并真正删掉落盘文件**。

    ★ 二十六轮第 7 批第 7d 处：此前这里只 `tasks.pop()` + 存索引，**一个文件都不删** ——
      实测后果是 `data/tasks/` 里积了 213 个"界面上已删、硬盘还在"的目录共 470.5 MB，
      其中还有一个明文出现过 API Key 的任务（用户以为删了，其实原封不动躺在盘上）。
    ★ 任务**仍在运行**时不删文件（正在写盘，删了会互相打架）：先取消，文件留给下次删除。
      回执里如实说明 reason，不含糊。
    """
    _get_task(task_id)  # 存在性校验（不存在 → 404）
    run = runs.get(task_id)
    still_running = bool(run and run.aio_task and not run.aio_task.done())
    if still_running:
        run.cancel()
    tasks.pop(task_id, None)
    _save_index()

    if still_running:
        return {"ok": True, "files": {"deleted": False, "bytes": 0,
                                      "reason": "任务仍在运行（正在写盘）——文件已保留，稍后再删一次即可"}}
    # ★★ 2026-10-07（同「一键清干净」对齐）：**不真删，挪到 `_deleted_<时间戳>/`** ✓
    #   为什么改（用户真机实测）：侧栏那个小叉看起来像"从列表里拿掉"✓
    #   实际却是 `rmtree` 永久销毁 ✗ ⇒ 他 198 个任务真没了 ✓ 且**回收站里也没有** ✗
    #   而"更吓人"的「一键清干净」反而有备份 ✓ ⇒ 轻重倒挂 ✓ 现在两边一个做法 ✓
    stamp = _now().replace("-", "").replace(":", "").replace("T", "-")[:15]
    trash = _DATA_DIR / "_deleted_"
    try:
        info = store.delete_task_files(task_id, trash=trash, stamp=stamp)
    except Exception as e:  # 删文件失败不能把"已从列表移除"这件事也判失败
        info = {"deleted": False, "bytes": 0, "reason": f"{type(e).__name__}: {str(e)[:120]}"}
    # ★ 删除任务也要**留痕** ✓ —— 与"清空数据"同一类"需要有人负责"的事 ✓
    #   （记下 task_id 与大小 ✓ 以及**挪到哪去了** ✓ ⇒ 账本同时是"找回东西的线索" ✓）
    audit.record("deleted_task", task_id=task_id, bytes=info.get("bytes") or 0,
                 trashed=bool(info.get("trashed")), where=str(info.get("path") or "")[:300])
    if info.get("trashed"):
        info = dict(info, note=(f"文件已**挪到** {trash}\\{stamp}\\ ✓ 想找回就把那个目录挪回 "
                                f"{_DATA_DIR / 'tasks'}\\ ✓（不是真删 ✓）"))
    # ★ 顺手收拾回收站 ✓ —— 只留最近 20 次 ✓（不然它自己会变成新的垃圾堆 ✗
    #   与当年"213 个孤儿目录 470MB"同一个病 ✓ 本项目栽过一次的事不栽第二次 ✓）
    pruned = _prune_trash(keep=20)
    if pruned:
        info = dict(info, trash_pruned=pruned,
                    trash_note=f"回收站只留最近 20 次 ⇒ 顺手清掉了最旧的 {pruned} 次 ✓")
    return {"ok": True, "files": info}

@app.post("/api/v1/tasks/{task_id}/messages")
async def send_message(task_id: str, req: MessageReq) -> dict[str, Any]:
    task = _get_task(task_id)
    run = runs.get(task_id)
    # 身份选择（第 41 班）：带 role 的续聊 → 专家人设注入为 system 消息（进历史持续生效）
    sys_extra = None
    if req.role:
        from .roles import ROLE_LIBRARY
        lib = ROLE_LIBRARY.get(req.role)
        if lib:
            sys_extra = f"[身份切换] 你现在以「{req.role}」的身份执行后续任务。" + chr(10) + lib["persona"]
    if run and run.aio_task and not run.aio_task.done():
        # ★ 运行中不许改写历史：正在跑的 loop 手里握着**自己的那份** history，
        #   这时删/改落盘历史，跑完还会把它自己那份覆盖回来 ⇒ 用户以为改了，其实没改。
        if req.edit_of_seq is not None:
            raise HTTPException(409, "任务正在运行，等它跑完再改这一句（改写会与运行中的对话打架）")
        if sys_extra:
            run.inject_system(sys_extra)  # 排队（§3.1）：工具执行期直插 system 同样破坏配对
        run.inject_user(req.text)  # 运行中：进队列，下一轮迭代开头（安全点）入历史
        return {"ok": True, "mode": "injected"}

    if req.edit_of_seq is not None:
        info = _rewind_history_to(task_id, req.edit_of_seq)
        # 事件流只追加（契约）：先说清"从哪重跑、后面作废了"，再走正常的新一轮
        _emit_standalone(task_id, "status", {
            "state": "running",
            "detail": f"已在第 {info['user_index']} 条用户消息处改写并重跑（其后 {info['dropped']} 条对话已作废）",
        })
        _start_run(task, req.text, system_extra=sys_extra)
        return {"ok": True, "mode": "edited_rerun", **info}

    _start_run(task, req.text, system_extra=sys_extra)
    return {"ok": True, "mode": "new_run"}


def _rewind_history_to(task_id: str, edit_of_seq: int) -> dict[str, Any]:
    """★ 改一句重发（Phase 3 ⑦）：把那一条用户消息**及其之后**的对话作废，只保留它之前的历史。

    怎么把"事件 seq"对上"历史条目"：两者都按时间顺序、且用户消息一一对应 ——
    数出**seq ≤ edit_of_seq 的用户消息事件**有几条（= k），落盘历史里的第 k 条 user 消息就是目标。
    找不到（seq 不是用户消息 / 超出范围）就 422，别乱删。
    """
    evs = store.read_events(task_id)
    user_seqs = [e.seq for e in evs if e.type == "message"
                 and str((e.payload or {}).get("role")) == "user"]
    if edit_of_seq not in user_seqs:
        raise HTTPException(422, f"第 {edit_of_seq} 条不是这个任务里的用户消息，无法改写")
    k = user_seqs.index(edit_of_seq) + 1                 # 第几条用户消息（从 1 数）

    hist = normalize_history(store.load_history(task_id))
    seen = 0
    cut = None
    for i, m in enumerate(hist):
        if m.get("role") == "user":
            seen += 1
            if seen == k:
                cut = i
                break
    if cut is None:
        raise HTTPException(422, f"落盘历史里找不到第 {k} 条用户消息（历史可能已被清理）")
    kept, dropped = hist[:cut], hist[cut:]
    store.save_history(task_id, kept)
    return {"user_index": k, "dropped": len(dropped), "kept": len(kept),
            "note": f"已作废第 {k} 条及其之后的 {len(dropped)} 条对话，并从这里重跑。"}

# ---------- Projects（配置注入） ----------


@app.get("/api/v1/projects")
async def list_projects() -> list[dict[str, Any]]:
    return sorted(projects.values(), key=lambda p: p["created_at"], reverse=True)


@app.post("/api/v1/projects", status_code=201)
async def create_project(req: ProjectReq) -> dict[str, Any]:
    pid = f"proj_{secrets.token_hex(3)}"
    p = {
        "id": pid,
        "name": req.name.strip(),
        "master_prompt": req.master_prompt.strip(),
        "created_at": _now(),
    }
    projects[pid] = p
    _save_projects()
    return p


@app.get("/api/v1/skills")
async def list_skills() -> list[dict[str, Any]]:
    return skill_registry.list_skills()

@app.delete("/api/v1/projects/{project_id}")
async def delete_project(project_id: str) -> dict[str, Any]:
    if projects.pop(project_id, None) is None:
        raise HTTPException(404, f"项目不存在：{project_id}")
    _save_projects()
    return {"ok": True}


# ---------- 取消 / 审批 ----------


def _emit_standalone(task_id: str, type_: str, payload: dict[str, Any]) -> EventEnvelope:
    """无运行体时补发事件（seq 从落盘续），保证前端状态同步。"""
    seq = store.last_seq(task_id) + 1
    ev = EventEnvelope(
        id=f"evt_{seq:06d}", seq=seq, task_id=task_id, type=type_,  # type: ignore[arg-type]
        version=1, ts=_now(), payload=payload,
    )
    store.append_event(ev)
    bus.publish(ev)
    # 复审 P1：对存活 run 补发后回写其 seq 缓存——否则 run 下一次 emit 复用已占用 seq
    r = runs.get(task_id)
    if r is not None:
        r.seq = max(r.seq, seq)
    return ev


class TaskUpdateReq(BaseModel):
    title: str | None = None
    pinned: bool | None = None


@app.post("/api/v1/tasks/{task_id}/update")
async def update_task_meta(task_id: str, req: TaskUpdateReq) -> dict[str, Any]:
    """重命名 / 置顶（第 41 班）。"""
    task = _get_task(task_id)
    if req.title is not None and req.title.strip():
        task.title = req.title.strip()[:100]
    if req.pinned is not None:
        task.pinned = req.pinned
    task.updated_at = _now()
    _save_index()
    return {"ok": True, "title": task.title, "pinned": task.pinned}


@app.post("/api/v1/tasks/{task_id}/cancel")
async def cancel_task(task_id: str) -> dict[str, Any]:
    task = _get_task(task_id)
    run = runs.get(task_id)
    if run and run.aio_task and not run.aio_task.done():
        run.cancel()  # CancelledError → loop 发 cancelled 事件
        return {"ok": True, "mode": "cancelled"}
    if task.status in ("running", "waiting_approval"):  # 重启遗留的僵尸运行态
        task.status = "cancelled"
        task.updated_at = _now()
        _save_index()
        _emit_standalone(task_id, "status", {"state": "cancelled", "detail": "运行态已失效，取消"})
        return {"ok": True, "mode": "stale"}
    return {"ok": True, "mode": "noop"}


# ---------- Take Over（人工接管） ----------


@app.post("/api/v1/tasks/{task_id}/takeover")
async def takeover_task(task_id: str) -> dict[str, Any]:
    """人工接管：暂停 Agent（取消当前运行，保留事件流与记忆），浏览器窗口留给用户操作。"""
    task = _get_task(task_id)
    run = runs.get(task_id)
    if not (run and run.aio_task and not run.aio_task.done()):
        raise HTTPException(409, "任务不在运行中，无需接管")
    run.takeover = True
    task.status = "waiting_approval"
    task.updated_at = _now()
    _save_index()
    run.cancel()
    _emit_standalone(task_id, "status", {"state": "waiting_approval", "detail": "人工接管中——你可以直接操作浏览器窗口，完成后点「交还 Agent」"})
    return {"ok": True, "note": "Agent 已暂停。浏览器窗口保留，请直接操作；完成后恢复任务。"}


@app.post("/api/v1/tasks/{task_id}/resume")
async def resume_task(task_id: str, req: MessageReq) -> dict[str, Any]:
    """交还 Agent：附带人工接管期间做了什么（注入记忆），重新拉起运行。"""
    task = _get_task(task_id)
    # §2.4 并发护栏：running 状态不允许 resume（防双 run → seq 重号）
    run = runs.get(task_id)
    if run and run.aio_task and not run.aio_task.done():
        return {"ok": False, "error": "任务仍在运行中，不允许交还"}
    # 复审盲区（验证报告 24）：后端重启后 runs 内存为空，但磁盘 index 里 status
    # 仍是 running/waiting_approval——此时 resume 会开出第二个 run（seq 重号）。
    if task.status in ("running", "waiting_approval"):
        return {"ok": False, "error": "任务状态在磁盘中仍为运行中（可能刚重启）——请先取消该任务再交还"}
    # 修补被打断的 history：末尾若是悬空的 tool_calls，补一条中断说明的 tool 回执
    hist = store.load_history(task_id)
    if hist and hist[-1].get("role") == "assistant" and hist[-1].get("tool_calls"):
        for tc in hist[-1]["tool_calls"]:
            hist.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": "（执行被人工接管中断——用户已在浏览器中完成操作，继续即可。）",
            })
        store.save_history(task_id, hist)
    _start_run(task, req.text)  # 续聊记忆会带上接管前的对话
    return {"ok": True, "note": "Agent 已恢复，人工操作说明已注入。"}

@app.post("/api/v1/tasks/{task_id}/approve")
async def approve_task(task_id: str, req: ApproveReq) -> dict[str, Any]:
    _get_task(task_id)
    # P1-6：审批按 (task, call_id) 隔离——在 A 任务界面批准不会误放行 B 任务
    # ★ 与群里的审批卡片共用 `_do_approve`（两边语义必须一致，尤其是重启后的诚实降级）
    _do_approve(task_id, req.call_id, req.decision, getattr(req, "command", ""))
    return {"ok": True}


class GroupResumeReq(BaseModel):
    """★ 群里"接着跑"（P0-4）：指名要续哪个任务（群里可能同时有好几件没做完的）。

    ★ 必须定义在**端点之前**：FastAPI 在装饰时就解析注解，类还没定义的话
      它会把 req 当成 query 参数 ⇒ 请求报 422「Field required: query.req」（本班踩过）。
    """
    task_id: str
    name: str | None = None


@app.post("/api/v1/team/groups/{gid}/resume")
async def team_group_resume(gid: str, req: GroupResumeReq) -> dict[str, Any]:
    """★ 群里"接着跑"（P0-4）：失败 / 被中断 / 被熔断的活，**带着已有产物继续**，不从头烧钱。

    依据（Anthropic 复盘原话）："错误会累积……要从出错处续跑，不能从头重来 —— 重跑既贵又让用户难受"。
    做法：**同一个任务**（因此同一个工作区、同一份历史）+ 一段"为什么停下 / 已经有什么 / 别从头再来"
    的工作单，并把看门重新挂上（波次与接力的推进照旧认领得回来）。
    """
    if _team_store.get_group(gid) is None:
        raise HTTPException(404, "群不存在")
    _reconcile_leader(gid)
    return _do_resume(gid, req.task_id, req.name or "")


@app.post("/api/v1/team/groups/{gid}/approve")
async def team_group_approve(gid: str, req: GroupApproveReq) -> dict[str, Any]:
    """★ 在**群里**直接批（用户要求：他们的活、要点的允许，都该在群里，不该跑外面去点）。

    语义与任务页完全一致（共用 `_do_approve`），只是多做了两件"群里该有"的事：
      · 批准/拒绝后**在群里留一句**（谁批的、批的什么）—— 群里其他人也知道这一步过了
      · 让看门的那条链继续跑（审批完成 ⇒ 任务继续 ⇒ 交付照旧回流到群里）
    """
    g = _team_store.get_group(gid)
    if g is None:
        raise HTTPException(404, "群不存在")
    # ★ 先补认领（后端重启丢看门 ⇒ 卡住的链在用户下次说话时自动往前走）
    _reconcile_leader(gid)
    task_id, call_id, decision = req.task_id, req.call_id, (req.decision or "once")
    # ★ 2026-10-05「本任务全部允许（含新命令）」：一条明确的例外授权 ——
    #   平时结构性命令（$()/heredoc/eval）**故意不记忆**（审计 P1：一次豁免 = 整类任意执行），
    #   结果一个写脚本的任务每条命令都要人点一次（实测一晚 25 次 ✗，无人值守不可能）。
    #   用户在这个**具体任务**上按了这个按钮 ⇒ 它的命令不再逐条询问；只在内存，重启失效。
    _all_ok = decision == "all"
    if _all_ok:
        _allow = getattr(approval, "allow_all", None)
        if callable(_allow):
            _allow(task_id)
        decision = "once"                    # 当前这一张卡照旧按"允许一次"放行
    _do_approve(task_id, call_id, decision, getattr(req, "command", ""))
    name = ""
    try:
        for m in reversed(_team_store.feed(gid)):
            ap = m.get("approval") or {}
            if ap.get("call_id") == call_id and m.get("task_id") == task_id:
                name = str(m.get("from") or "").replace("sys:", "")
                break
    except Exception:
        name = ""
    word = {"once": "允许一次", "always": "本任务都允许", "all": "本任务全部允许（含新命令）",
            "deny": "拒绝"}.get(req.decision or decision, decision)
    _team_store.append(gid, **{"from": "system", "text": (
        f"✅ 已在群里处理：{word}（{('@' + name) if name else task_id}）—— 任务继续"
        + ("\n（这个任务的命令**不再逐条询问**了，包括 $()/heredoc 这类；后端重启后失效）"
           if _all_ok else "")
        if decision != "deny" else
        f"⛔ 已在群里处理：拒绝（{('@' + name) if name else task_id}）—— 这一步不会执行"
    ), "task_id": task_id, "status": "approved" if decision != "deny" else "denied"})
    return {"ok": True, "decision": decision}


# ---------- 事件拉取 / SSE ----------


@app.get("/api/v1/tasks/{task_id}/events")
async def get_events(task_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
    """裸数组——前端 HttpApi.getEvents 与 MockApi 同签名（契约二）。"""
    _get_task(task_id)
    return [e.model_dump() for e in store.read_events(task_id, after_seq=after_seq)]


def _sse(ev: EventEnvelope) -> str:
    return f"id: {ev.seq}\nevent: agent_event\ndata: {ev.model_dump_json()}\n\n"


@app.get("/api/v1/tasks/{task_id}/events/stream")
async def stream_events(task_id: str, request: Request, after_seq: int = 0):
    _get_task(task_id)
    try:
        cursor = int(request.headers.get("last-event-id", str(after_seq)))
    except ValueError:
        cursor = after_seq
    q = bus.subscribe(task_id)  # 先订阅再回放，避免竞态窗口丢事件

    async def gen() -> AsyncIterator[str]:
        nonlocal cursor  # gen 内有对 cursor 的赋值，不声明会被当成未绑定的局部变量
        try:
            for ev in store.read_events(task_id, after_seq=cursor):  # 断线补发
                cursor = max(cursor, ev.seq)
                yield _sse(ev)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # SSE 保活注释帧
                    continue
                if ev.seq <= cursor:  # 订阅窗口内与回放重叠的部分
                    continue
                cursor = ev.seq
                yield _sse(ev)
        finally:
            bus.unsubscribe(task_id, q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# ---------- Automations（定时 / Webhook 触发） ----------


@app.get("/api/v1/automations")
async def list_automations() -> list[dict[str, Any]]:
    # §2.3 + 复审 P1：列表不回传明文 secret（明文只在创建/轮换响应出现一次）。
    # 此前修过但被后续对 main.py 的改动覆盖——以整函数替换防止再被冲掉。
    out = []
    for a in sorted(automations.values(), key=lambda x: x["created_at"], reverse=True):
        b = dict(a)
        if b.get("secret"):
            b["secret"] = "***"
        out.append(b)
    return out


@app.post("/api/v1/automations", status_code=201)
async def create_automation(req: AutomationReq) -> dict[str, Any]:
    if req.kind not in ("schedule", "hook", "watch"):
        raise HTTPException(422, f"kind 必须是 schedule / hook / watch，得到：{req.kind}")
    if req.kind == "schedule" and not (req.schedule or {}).get("kind"):
        raise HTTPException(422, "kind=schedule 时必须提供 schedule 字段")
    if req.kind == "schedule":
        # ★ 2026-10-07（第 7 项）：**建的时候就校验频率** ✓ —— 与"watch 路径建时就校验"同一条规矩 ✓
        #   存一个认不出的 kind 进去 ⇒ 调度循环里 `_schedule_due` 永远返回 False
        #   ⇒ **建了个永远不会触发的自动化** ✗ 而用户以为它在跑 ✓（比报错更糟 ✓）
        _sch = req.schedule or {}
        _kinds = ("interval", "daily", "hourly", "weekly", "monthly")
        if str(_sch.get("kind")) not in _kinds:
            raise HTTPException(422, f"schedule.kind 必须是 {' / '.join(_kinds)} 之一，得到：{_sch.get('kind')}")
        if _sch.get("kind") not in ("interval",):
            _t = str(_sch.get("time") or "09:00")
            try:
                _h, _mi = _t.split(":")[:2]
                if not (0 <= int(_h) <= 23 and 0 <= int(_mi) <= 59):
                    raise ValueError
            except (TypeError, ValueError):
                raise HTTPException(422, f"时间要写成 HH:MM（00:00–23:59），得到：{_t!r}") from None
        if _sch.get("kind") == "weekly":
            try:
                if not (1 <= int(_sch.get("weekday") or 1) <= 7):
                    raise ValueError
            except (TypeError, ValueError):
                raise HTTPException(422, "weekly 的 weekday 要填 1–7（1=周一 … 7=周日）") from None
        if _sch.get("kind") == "monthly":
            try:
                if not (1 <= int(_sch.get("day") or 1) <= 31):
                    raise ValueError
            except (TypeError, ValueError):
                raise HTTPException(422, "monthly 的 day 要填 1–31（31 在短月会算作月末 ✓）") from None
    wpath = ""
    if req.kind == "watch":
        # ★ 2026-10-06：**建的时候就校验路径** ✓ —— 存一个不存在的目录进去，
        #   等于建了个永远不会触发的自动化 ✓ 而用户会以为它在盯 ✗（比报错更糟 ✓）。
        raw = str(req.watch_path or "").strip()
        if not raw:
            raise HTTPException(422, "kind=watch 时必须提供 watch_path（要盯哪个文件夹）")
        wpath = str(Path(raw).expanduser())
        if not Path(wpath).is_dir():
            raise HTTPException(422, f"这个文件夹不存在（或不是文件夹）：{wpath}")
    aid = f"auto_{secrets.token_hex(3)}"
    a = {
        "id": aid,
        "name": req.name.strip(),
        "kind": req.kind,
        "task_input": req.task_input.strip(),
        "project_id": req.project_id,
        "schedule": req.schedule,
        # ★ 2026-10-07（第 7 项）：**单次花费上限** + **失败自动重试**（都可不填 ✓ 默认不拦/不重试 ✓）
        "max_cost_cny": float(req.max_cost_cny or 0) if (req.max_cost_cny or 0) > 0 else None,
        "retries": min(5, max(0, int(req.retries or 0))),   # ★ 钳在 0..5 ✓ 不许无限重试烧钱 ✗
        "watch_path": wpath or None,
        "watch_seconds": max(10, int(req.watch_seconds or 60)) if req.kind == "watch" else None,
        # ★ `None` = **还没扫过**（与"扫过但目录是空的" `{}` 区分开 ✓ 见 `_watch_changed` ✓）
        "watch_seen": None if req.kind == "watch" else None,
        "enabled": True,
        "secret": secrets.token_hex(8),
        "created_at": _now(),
        "last_run": None,
        "last_task_id": None,
    }
    automations[aid] = a
    _save_automations()
    return a


@app.post("/api/v1/automations/{automation_id}/toggle")
async def toggle_automation(automation_id: str) -> dict[str, Any]:
    a = automations.get(automation_id)
    if a is None:
        raise HTTPException(404, f"自动化不存在：{automation_id}")
    a["enabled"] = not a.get("enabled", True)
    _save_automations()
    return {"ok": True, "enabled": a["enabled"]}


@app.delete("/api/v1/automations/{automation_id}")
async def delete_automation(automation_id: str) -> dict[str, Any]:
    if automations.pop(automation_id, None) is None:
        raise HTTPException(404, f"自动化不存在：{automation_id}")
    _save_automations()
    return {"ok": True}


@app.post("/api/v1/automations/{automation_id}/rotate")
async def rotate_webhook_secret(automation_id: str) -> dict[str, Any]:
    """轮换 webhook secret（webhook 管理 §尾巴）：旧 URL 立即失效。
    明文 secret 只在创建/轮换的响应里出现一次，列表接口永远返回 "***"（§2.3）。"""
    a = automations.get(automation_id)
    if a is None:
        raise HTTPException(404, f"自动化不存在：{automation_id}")
    if a.get("kind") != "hook":
        raise HTTPException(422, "只有 hook 类自动化有 webhook secret")
    a["secret"] = secrets.token_hex(8)
    a["secret_created_at"] = _now()
    _save_automations()
    return {"ok": True, "secret": a["secret"]}


# webhook 爆破限流（内存态，重启清零）：同一 automation 连错 N 次 → 冷却期
_HOOK_FAILS: dict[str, list[float]] = {}
_HOOK_FAIL_LIMIT = 5
_HOOK_COOLDOWN_S = 60.0
_HOOK_SIGN_MAX_SKEW = 300  # 签名时间戳允许的时钟偏差（秒），过期即拒——防重放


def _hook_rate_ok(aid: str) -> bool:
    import time as _t
    now = _t.monotonic()
    fails = [x for x in _HOOK_FAILS.get(aid, []) if now - x < _HOOK_COOLDOWN_S]
    _HOOK_FAILS[aid] = fails
    return len(fails) < _HOOK_FAIL_LIMIT


def _hook_record_fail(aid: str) -> None:
    import time as _t
    _HOOK_FAILS.setdefault(aid, []).append(_t.monotonic())


@app.post("/api/v1/hooks/{automation_id}/{secret}", status_code=201)
async def fire_hook(automation_id: str, secret: str, request: Request) -> dict[str, Any]:
    """Webhook 触发（外部系统调用）。

    两种模式（webhook 管理补齐，审计"无轮换/无重放保护"）：
    · 强签名：请求带 X-AgentShell-Timestamp（unix 秒）+ X-AgentShell-Sign
      （= HMAC-SHA256(secret, "{ts}.{body}") 十六进制）。时间戳偏离 >300s 拒绝
      —— 重放的请求会因时间戳过期被丢弃。
    · 裸模式：不带签名头则按 URL secret 比对；同一 automation 连错 5 次冷却 60s
      （防爆破），60s 内全部 429。
    """
    import hmac as _hmac
    import hashlib as _hashlib
    import time as _t
    # 限流先于比对：连错 5 次后一律 429（不给"secret 是否正确"的探测机会）
    if not _hook_rate_ok(automation_id):
        raise HTTPException(429, "失败次数过多，请 1 分钟后再试（防爆破限流）")
    a = automations.get(automation_id)
    _secret_ok = False
    try:
        _secret_ok = bool(a is not None and a.get("kind") == "hook" and _hmac.compare_digest(
            str(a.get("secret", "")).encode("utf-8"), secret.encode("utf-8")))
    except TypeError:
        _secret_ok = False  # compare_digest 不支持的输入按不匹配处理（计入限流）
    if not _secret_ok:
        _hook_record_fail(automation_id)
        raise HTTPException(404, "自动化不存在或密钥不符")
    if not a.get("enabled", True):
        raise HTTPException(409, "自动化已暂停")

    ts = request.headers.get("x-agentshell-timestamp")
    sign = request.headers.get("x-agentshell-sign")
    if ts or sign:  # 带了任一签名头 → 按强签名模式完整校验
        try:
            ts_i = int(ts or "")
        except ValueError:
            raise HTTPException(401, "X-AgentShell-Timestamp 不是 unix 秒")
        if abs(_t.time() - ts_i) > _HOOK_SIGN_MAX_SKEW:
            raise HTTPException(401, "签名时间戳过期（>300s）——拒绝重放")
        body = await request.body()
        expect = _hmac.new(
            a["secret"].encode(), f"{ts_i}.".encode() + body, _hashlib.sha256
        ).hexdigest()
        if not sign or not _hmac.compare_digest(expect, sign.strip().lower()):
            _hook_record_fail(automation_id)
            raise HTTPException(401, "签名不符")

    task = _launch_task(a["task_input"], a.get("project_id"))
    a["last_run"] = _now()
    a["last_task_id"] = task.id
    _save_automations()
    return {"ok": True, "task_id": task.id}


def _schedule_due(a: dict[str, Any], now_utc: datetime, now_local: datetime) -> tuple[bool, str]:
    """判断一个 schedule 自动化此刻是否该触发；返回 (是否触发, 记账用的本地日期)。

    抽成纯函数是为了**可测**——原来这段逻辑内联在 `while True` 的调度循环里，无法单测。

    时区约定（2026-09-30 修正）：
      · `interval` 用 UTC 算间隔（`last_run` 存的是 UTC ISO），不受时区影响；
      · 其余几种**按系统本地时区**解释用户填的 HH:MM。
        此前两条都用 UTC，导致东八区用户填 `09:00` 实际在**本地 17:00** 才触发。

    ★★ 2026-10-07（第 7 项）：**补上"每小时 / 每周 / 每月"** ✓ ——
      在此之前只有两种：`interval`（隔 N 分钟 ✓）与 `daily`（每天某个点 ✓）
      ⇒ 用户想表达"**每小时整点跑一次**"✗"**每周一早上 9 点**"✗"**每月 1 号**"✗
        **一个都表达不出来** ✓（只能拿 interval 凑"每 60 分钟"✓ 那和整点不是一回事 ✓）
      ⇒ 现在四种并列 ✓ 每种都用一个"记账字段"防重复触发 ✓（见下 ✓）：
        · `hourly`  ：每小时的第 MM 分（默认整点 ✓）⇒ 记账 `%Y-%m-%d %H`
        · `weekly`  ：每周几 + HH:MM（周几按 ISO：1=周一…7=周日 ✓）⇒ 记账 `%Y-%m-%d`
        · `monthly` ：每月几号 + HH:MM（默认 1 号 ✓）⇒ 记账 `%Y-%m`
      ★ 三种都**只在"到点之后"触发一次** ✓（不是"恰好那一秒"✗ —— 调度是每 20 秒扫一次 ✓
        要求精确到秒会漏 ✓）记账字段保证**同一天/同一小时/同一月只跑一次** ✓
    """
    today_local = now_local.strftime("%Y-%m-%d")
    sch = a.get("schedule") or {}

    if sch.get("kind") == "interval":
        mins = max(1, int(sch.get("minutes", 60)))
        last = a.get("last_run")
        if not last:
            return True, today_local
        try:
            last_dt = datetime.fromisoformat(last)
        except (TypeError, ValueError):
            return True, today_local  # 坏数据：当作没跑过（并在外层记录）
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        return (now_utc - last_dt) >= timedelta(minutes=mins), today_local

    if sch.get("kind") in ("daily", "weekly", "monthly", "hourly"):
        try:
            hh, mm = (sch.get("time") or "09:00").split(":")[:2]
            hour, minute = int(hh), int(mm)
        except (TypeError, ValueError):
            return False, today_local  # 时间格式坏 → 本 tick 不触发，等用户改配置
        # ★ 每小时那档：时间只取**分** ✓（用户填 09:30 里的 09 没有意义 ⇒ 不参与判断 ✓
        #   但也不报错 ✗ —— 界面上那一栏本来就写着"第几分"✓）
        if sch.get("kind") == "hourly":
            if now_local.minute < minute:
                return False, today_local
            slot = now_local.strftime("%Y-%m-%d %H")
            if a.get("last_fire_slot") != slot:
                return True, slot
            return False, today_local
        try:
            target = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        except ValueError:
            return False, today_local
        if sch.get("kind") == "weekly":
            want = int(sch.get("weekday") or 1)          # ISO：1=周一 … 7=周日
            want = min(7, max(1, want))
            if now_local.isoweekday() != want:
                return False, today_local
        if sch.get("kind") == "monthly":
            want_day = min(28, max(1, int(sch.get("day") or 1)))   # 1..28：不碰"31 号在 2 月"这种坑 ✓
            if now_local.day != want_day:
                return False, today_local
        if now_local >= target and a.get("last_fire_date") != today_local:
            return True, today_local
        return False, today_local

    return False, today_local


async def _automation_loop() -> None:
    """调度循环：每 20s 扫一次 —— 到期的定时任务开跑 ✓；盯着的文件夹有变化也开跑 ✓。

    ★ 2026-10-06 加 `watch`（**文件夹变动** ✓）：用户真正每天会用的那件事
      （"下载目录来新文件就整理"✓）本质是**文件变动** ✓ ——
      既不需要邮箱配置 ✗ 也不需要外部服务 ✗，本地看一眼就够 ✓。
    """
    while True:
        try:
            now_utc = datetime.now(timezone.utc)  # interval 的间隔计算（last_run 是 UTC）
            now_local = datetime.now().astimezone()  # daily 的 HH:MM 按本地时区解释
            for a in list(automations.values()):
                if not a.get("enabled", True):
                    continue
                if a["kind"] == "watch":
                    # ★ 防抖：两次触发之间至少隔 watch_seconds ✓（否则复制 100 个文件会开 100 个任务 ✗）
                    last = a.get("last_run")
                    if last:
                        try:
                            gap = (now_utc - datetime.fromisoformat(str(last))).total_seconds()
                            if gap < int(a.get("watch_seconds") or 60):
                                continue
                        except Exception:                   # noqa: BLE001
                            pass
                    changed = _watch_changed(a)
                    if changed:
                        a["last_run"] = now_utc.isoformat()
                        try:
                            t = _launch_task(a["task_input"], a.get("project_id"))
                            a["last_task_id"] = t.id
                            a["last_error"] = None
                            print(f"[automation] 文件夹有变化（{len(changed)} 个新/改文件）⇒ 已开跑：{a['name']}")
                        except Exception as e:
                            a["last_error"] = f"{type(e).__name__}: {e}"
                        _save_automations()
                    continue
                if a["kind"] != "schedule":
                    continue
                fire, fire_date = _schedule_due(a, now_utc, now_local)
                if fire:
                    a["last_run"] = now_utc.isoformat()
                    a["last_fire_date"] = fire_date
                    a["last_fire_slot"] = fire_date      # ★ hourly 用的小时槽 ✓（见 _schedule_due ✓）
                    try:
                        t = _launch_task(a["task_input"], a.get("project_id"),
                                         cost_cap_cny=float(a.get("max_cost_cny") or 0),
                                         auto_retries=int(a.get("retries") or 0))
                        a["last_task_id"] = t.id
                        a.pop("last_error", None)
                        # ★★ 2026-10-07（第 7 项）：**开出去之后要看它跑成没跑成** ✓ ——
                        #   原来只管"开出去"成不成功 ✗（`last_error` 只记启动异常 ✓）
                        #   任务真跑起来、跑到一半失败了 ⇒ **自动化这边一声不吭** ✗✓
                        #   ⇒ 用户以为"每天 9 点那个活一直在跑"✓ 其实天天失败 ✓
                        _spawn_bg(_watch_automation_task(a["id"], t.id, int(a.get("retries") or 1)))
                    except Exception as e:
                        a["last_error"] = f"{type(e).__name__}: {e}"
                    _save_automations()
        except Exception as e:
            # 循环永不退出，但**不能静默**：出问题时至少要留下线索
            print(f"[automation] 调度循环异常（已忽略，循环继续）：{type(e).__name__}: {e}")
        await asyncio.sleep(20)


def _watch_automation_task(aid: str, task_id: str, retries: int) -> None:
    """★ 2026-10-07（第 7 项）**失败重试 + 如实记账** ✓。

    为什么必须有：调度循环原来只管"**开出去**"成不成功 ✗
    （`last_error` 只记 `_launch_task` 抛的异常 ✓）⇒ 任务真跑起来、跑到一半失败 ✓
    **自动化这边一声不吭** ✗✓ ⇒ 用户以为"每天 9 点那个活一直在跑"✓ 其实天天失败 ✓
    —— 这正是本项目一直在收拾的那类"静默失败"✓

    做法（**有界** ✓ 不会无限重试烧钱 ✗）：
      · 盯这个任务的状态（每 5 秒看一眼，最多 2 小时 ✓）
      · 跑成 `done` ⇒ 记 `last_outcome=ok` ✓ 清掉错误 ✓
      · 失败/部分完成、且还有重试额度 ⇒ **再开一次**（同一句话 ✓ 同一个项目 ✓）并记下 ✓
      · 重试额度用完还是失败 ⇒ 记 `last_outcome=failed` + 一句**能照做**的话 ✓
    ★ 只**记账 + 说清**，不自动改用户的配置 ✗（要不要继续重试是用户的事 ✓）
    """
    async def _poll() -> None:
        deadline = time.time() + 2 * 3600
        left = max(0, int(retries))
        tid = task_id
        while time.time() < deadline:
            try:
                t = tasks.get(tid)
                st = str(getattr(t, "status", "") or "")
                if st in ("done", "failed", "partial", "cancelled"):
                    a = automations.get(aid)
                    if a is None:
                        return
                    if st == "done":
                        a["last_outcome"] = "ok"
                        a["last_outcome_at"] = _now()
                        a.pop("last_error", None)
                        a["retries_used"] = 0
                        _save_automations()
                        return
                    if left > 0:
                        left -= 1
                        a["retries_used"] = int(a.get("retries_used") or 0) + 1
                        try:
                            nt = _launch_task(a["task_input"], a.get("project_id"),
                                              cost_cap_cny=float(a.get("max_cost_cny") or 0),
                                              auto_retries=0)
                            tid = nt.id
                            a["last_task_id"] = tid
                            a["last_outcome"] = f"retry（第 {int(a.get('retries_used'))} 次重试中）"
                            _save_automations()
                            continue
                        except Exception as e:                  # noqa: BLE001
                            a["last_outcome"] = "failed"
                            a["last_error"] = f"重试都开不出去：{type(e).__name__}: {str(e)[:120]}"
                            _save_automations()
                            return
                    a["last_outcome"] = "failed"
                    a["last_error"] = (f"跑了但没做成（{st}）—— 重试 {int(a.get('retries_used') or 0)} 次仍失败。"
                                       "看看那个任务里卡在哪一步；改完可以手动再跑一次。")
                    _save_automations()
                    return
            except Exception:                                   # noqa: BLE001
                pass
            await asyncio.sleep(5)

    _spawn_bg(_poll())


def _watch_changed(a: dict[str, Any]) -> list[str]:
    """盯着那个文件夹：返回**新增或刚改过**的文件名（并更新快照 ✓）。

    只看**一层**（不递归 ✗）—— 递归扫大目录会拖慢每一轮 ✓ 而"下载目录"这类用法一层就够 ✓。
    快照存 `名字 -> (mtime, size)` ✓ 最多记 300 个 ✓（防内存无限长 ✓）。
    """
    p = Path(str(a.get("watch_path") or ""))
    if not p.is_dir():
        return []
    # ★ 用**显式标记**区分"还没扫过"与"扫过但是空的" ✓ ——
    #   本班第一版拿"快照非空"当"扫过了" ✗ ⇒ 盯一个**空目录**时，
    #   第一个放进来的文件被当成"第一次扫描"⇒ **不触发** ✓（正好把最常用的用法废掉 ✗）。
    raw_seen = a.get("watch_seen")
    first_scan = raw_seen is None
    seen: dict[str, Any] = dict(raw_seen or {})
    changed: list[str] = []
    try:
        entries = [e for e in p.iterdir() if e.is_file()][:300]
    except Exception:                                       # noqa: BLE001
        return []
    for e in entries:
        try:
            st = e.stat()
        except Exception:                                   # noqa: BLE001
            continue
        sig = [int(st.st_mtime), int(st.st_size)]
        if seen.get(e.name) != sig:
            # 第一次建这个自动化时**不算变化** ✓（否则一建就立刻跑一次，用户会莫名其妙 ✗）
            if not first_scan:
                changed.append(e.name)
            seen[e.name] = sig
    a["watch_seen"] = seen
    return changed


# （K6a：此处原有 ~15 行死代码——while True 之后永不可达的 provider 自检 /
# 重复 _automation_loop / 重复 wide 监视。自检已接回 _lifespan（见上），
# 后两者 _lifespan 里本就有，整体删除。）


# ---------- Wide Research（并行子 Agent） ----------


def _start_wide(input_text: str, items: list[str], project_id: str | None = None) -> dict[str, Any]:
    """把一个主题拆成 N 个要点，每要点并行派一个子任务；全部完成后自动生成汇总任务。

    大规模版（第 41 班）：上限 8 → 64，新增**并发闸**——同时最多 wide.max_concurrent
    个子任务在跑，其余排队，出一个空位补一个（防止 64 个任务同时打爆 API 限流）。
    """
    wid = f"wide_{secrets.token_hex(3)}"
    max_conc = max(1, int(cfg.executor.wide_max_concurrent))
    task_ids: list[str] = []
    for i, item in enumerate(items):
        if i < max_conc:
            sub = _launch_task(
                f"【Wide 子任务】针对主题「{input_text}」研究要点：{item}。"
                "**必须先联网检索至少 1 次**（web_search，或直接 web_fetch 权威来源）再作答，"
                "不得仅凭已有记忆作答；用 200 字以内简要作答，"
                "并在 task_done 的 sources 字段里附上你本次实际访问过的链接。",
                project_id,
            )
            task_ids.append(sub.id)
        else:
            task_ids.append("")  # 排队占位：监视器见空位即补发
    w = {
        "id": wid,
        "input": input_text,
        "items": items,
        "task_ids": task_ids,
        "pending_items": items[max_conc:],  # 排队中的要点
        "max_concurrent": max_conc,
        "agg_task_id": None,
        "status": "running",
        "created_at": _now(),
        "project_id": project_id,
    }
    wide[wid] = w
    _save_wide()
    _spawn_bg(_wide_monitor(wid))
    return w


def _wide_promote(w: dict[str, Any]) -> int:
    """有子任务终局时，从队列补发下一个要点。返回本次补发数。"""
    promoted = 0
    pending = w.get("pending_items") or []
    if not pending:
        return 0
    # 复审 P1：TaskSummary(status="created") 缺必填字段必抛 ValidationError——
    # 用显式哨兵判断；已删除/不存在的子任务计为"非运行"
    running = sum(
        1 for tid in w["task_ids"]
        if tid and tasks.get(tid) is not None
        and tasks[tid].status in ("running", "waiting_approval", "created")
    )
    while pending and running < int(w.get("max_concurrent", 8)):
        item = pending.pop(0)
        sub = _launch_task(
            f"【Wide 子任务】针对主题「{w['input']}」研究要点：{item}。"
            "**必须先联网检索至少 1 次**（web_search，或直接 web_fetch 权威来源）再作答，"
            "不得仅凭已有记忆作答；用 200 字以内简要作答，"
            "并在 task_done 的 sources 字段里附上你本次实际访问过的链接。",
            w.get("project_id"),
        )
        # 顶替最早的排队占位（保持 items↔task_ids 对齐关系由 pending 顺序保证）
        try:
            idx = w["task_ids"].index("")
            w["task_ids"][idx] = sub.id
        except ValueError:
            w["task_ids"].append(sub.id)
        running += 1
        promoted += 1
    return promoted


async def _wide_monitor(wide_id: str) -> None:
    try:
        await _wide_monitor_inner(wide_id)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        # 复审 P1：监视协程死亡 = 队列永不补发、汇总永不生成——落盘 failed 并留痕
        print(f"[wide] 监视器异常退出 {wide_id}: {type(e).__name__}: {e}", flush=True)
        w = wide.get(wide_id)
        if w is not None:
            w["status"] = "failed"
            w["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            _save_wide()
        return


async def _wide_monitor_inner(wide_id: str) -> None:
    """监视子任务：有空位补发队列、全部终局（或 30 分钟超时/10 分钟无进展）后，取各自最终回答拼给汇总任务。"""
    w = wide.get(wide_id)
    if w is None:
        return
    deadline = datetime.now(timezone.utc) + timedelta(minutes=30)
    while datetime.now(timezone.utc) < deadline:
        await asyncio.sleep(3)
        # 并发闸：有子任务终局且队列还有要点 → 补发
        if w.get("pending_items"):
            _wide_promote(w)
            _save_wide()
        subs_all = [(tid, tasks.get(tid)) for tid in w["task_ids"] if tid]
        # 还有排队要点未派发 → 不可能终局
        if w.get("pending_items"):
            continue
        subs = [t for _, t in subs_all]
        if len(subs) < len(w["items"]):
            continue  # 占位尚未全部转正
        if any(t is None for t in subs):
            continue
        if all(t.status in ("done", "failed", "cancelled") for t in subs):
            break
    w["status"] = w.get("status", "running")
    results = []
    for item, tid in zip(w["items"], w["task_ids"]):
        text = "（子任务无结果）"
        srcs: list[str] = []
        evs = store.read_events(tid)
        asst = [e for e in evs if e.type == "message" and e.payload.get("role") == "assistant"]
        if asst:
            text = str(asst[-1].payload.get("text", ""))[:1500]
            # P0-6：把子任务**已核实的来源**一起带进汇总，否则汇总报告无法给出可追溯的引用
            for s in asst[-1].payload.get("sources") or []:
                if isinstance(s, dict) and s.get("url"):
                    srcs.append(f"{s.get('title', '')} {s['url']}".strip())
        block = f"【{item}】{text}"
        if srcs:
            block += "\n  该要点的来源：" + "；".join(srcs)
        else:
            block += "\n  ⚠️ 该要点未提供来源（未经核实，引用时必须标注）"
        results.append(block)
    agg_input = (
        f"以下是对「{w['input']}」的 {len(results)} 份并行研究结果：\n\n"
        + "\n\n".join(results)
        + "\n\n请把它们汇总成一份结构化综合报告（开头给结论，中间分要点对比，可用表格），以 task_done 交付。"
        "**必须保留来源**：把各要点的来源链接整理进 task_done 的 sources 字段；"
        "对标注了「未提供来源」的要点，在正文里明确写「未经核实」，不要当成事实陈述。"
    )
    t = _launch_task(agg_input, w.get("project_id"))
    w["agg_task_id"] = t.id
    w["status"] = "done"
    _save_wide()


@app.get("/api/v1/wide")
async def list_wide() -> list[dict[str, Any]]:
    return sorted(wide.values(), key=lambda w: w["created_at"], reverse=True)


@app.get("/api/v1/wide/{wide_id}")
async def get_wide(wide_id: str) -> dict[str, Any]:
    w = wide.get(wide_id)
    if w is None:
        raise HTTPException(404, f"Wide Research 不存在：{wide_id}")
    return w


@app.post("/api/v1/wide", status_code=201)
async def create_wide(req: WideReq) -> dict[str, Any]:
    items = [i.strip() for i in (req.items or []) if i.strip()]
    if len(items) < 2:
        raise HTTPException(422, "items 至少需要 2 个研究要点")
    return _start_wide(req.input.strip(), items[:64], req.project_id)


# ---------- P2-7：产物预览的安全响应头 ----------
#
# 问题（第一轮评估发现，一直未修）：
#   1. 越界访问 `/files/raw` 时 `PermissionError` 无人接 → 冒泡成 **500**，而不是干脆的 403；
#   2. `FileResponse(p)` 按扩展名猜 Content-Type → **Agent 生成的 HTML 会以同源方式直接渲染**，
#      里面的 <script> 能拿到完整 API 权限（读任务、发命令、改配置）
#      —— 这是**本地存储型 XSS**：只要 Agent 被外部内容诱导写出一个恶意 HTML 就够了。
#
# 修法：
#   · 只有**可以安全渲染**的类型（图片/音视频/文本/HTML）内联；
#   · 内联的一律加 `X-Content-Type-Options: nosniff` + `Content-Security-Policy: sandbox`
#     —— HTML 仍能预览，但脚本不执行、且处于不可信源（拿不到同源 API 权限）；
#   · 其余（js/exe/压缩包/未知类型）**强制下载**，浏览器不解释、不执行。
_NOSNIFF = {"X-Content-Type-Options": "nosniff"}
_INLINE_OK_SUFFIXES = {
    # 媒体：预览本来就需要
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svg",
    ".mp4", ".webm", ".mp3", ".wav", ".ogg", ".m4a",
    # 文本类：渲染出来就是文本，不会执行
    ".html", ".htm", ".css", ".txt", ".md", ".json", ".csv", ".log",
}


def _raw_response(p: Path) -> FileResponse:
    """产物文件的响应：能安全渲染的内联（带 CSP sandbox），其余强制下载。"""
    if p.suffix.lower() in _INLINE_OK_SUFFIXES:
        return FileResponse(
            p, headers={**_NOSNIFF, "Content-Security-Policy": "sandbox"}
        )
    return FileResponse(
        p,
        media_type="application/octet-stream",
        filename=p.name,  # 触发 Content-Disposition: attachment
        headers=dict(_NOSNIFF),
    )


@app.get("/api/v1/tasks/{task_id}/files/raw")  # 产物原始文件（Studio 预览：html/css/图直接可打开）
async def raw_file(task_id: str, path: str = "index.html"):
    try:
        p = _safe_path(task_id, path)
    except PermissionError:
        # P2-7：旧实现这里没人接 PermissionError，越界会返回 500
        raise HTTPException(403, f"路径越界（沙箱外）：{path}")
    if p.is_dir() or not p.exists():
        raise HTTPException(404, f"文件不存在：{path}")
    return _raw_response(p)


@app.get("/api/v1/data/browser-cache")
async def browser_cache_status() -> dict[str, Any]:
    """浏览器实况缓存垃圾现在攒了多少 ✓（**只读** ✗ 什么都不动 ✓）。

    ★ 2026-10-10（用户实测炸出来的那件事的副产品 ✓）：
      浏览器实况工具每开一次就在任务/组工作区里留一个 Chromium profile ✗
      本机实测 **20+ 个 / 约 2 GB / 1.5 万个文件** ✓ 而且会被交付快照整套复制 ⇒ 越滚越多 ✓
    """
    from . import housekeeping
    return housekeeping.scan(_DATA_DIR)


@app.post("/api/v1/data/browser-cache/clean")
async def browser_cache_clean() -> dict[str, Any]:
    """清掉 `_edgeprof*` ✓ —— **只删这一类目录** ✗ 其它一个字节都不碰 ✓。

    · 必须在 data 目录里（`housekeeping` 里 resolve 后校验 ✓ 防路径穿越 ✓）
    · 正被占用的**跳过并如实报** ✗（绝不假装清干净 ✓ 与全项目同一条规矩 ✓）
    · 记一笔审计 ✓（带释放字节数 ✓ 以后追查得到 ✓）
    · ★ 放到线程里删：几万个文件会占住事件循环 ✗（与备份卡死那次同一个教训 ✓）
    """
    from . import housekeeping
    got = await asyncio.to_thread(housekeeping.clean, _DATA_DIR)
    audit.record("cleaned", what="浏览器实况缓存 _edgeprof*",
                 removed=got["removed"], freed_mb=got["freed_mb"],
                 failed=got["failed_count"])
    return got


@app.get("/api/v1/backup")
async def download_backup(full: bool = False) -> Any:
    """★ 一键备份：把整个 data 目录打成一个 zip 下载 ✓（用户换电脑/重装/手滑前的安全网 ✓）

    · 默认**跳过**三类重货（snapshots=备份的备份 ✓ 历史清理备份 ✓ 朗读音频 ✓）
      ⇒ 清单里**如实写清跳过了什么** ✗（不让用户以为备份是全的 ✓）
    · `?full=true` ⇒ 连快照和音频一起打 ✓
    · ★ 不碰网络 ✓ 不依赖外部工具（标准库 zipfile ✓）
    """
    from .backup import make_backup
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = _DATA_DIR / "_backups_" / f"backup-{stamp}.zip"
    # ★★ 2026-10-10（用户实测炸出来的 ✗✗）：**必须放到线程里压** ✗
    #   现场：本机 data = 7.9 GB / 30070 个文件（光浏览器 profile _edgeprof* 就约 2 GB ✓）
    #   而这里是**同步**压 ⇒ 事件循环被占死 ⇒ `GET /version` 都超时 ✗
    #   用户看到的是"点一下备份，整个应用死了" ✗（zip 一路涨到 3 GB+ 还没写完 ✓）
    #   ⇒ 一律 `asyncio.to_thread` ✓：打包期间别的接口照常响应 ✓
    #   ⇒ 另加 `include_heavy`（full=true 才带上浏览器缓存与模型文件 ✓ 默认跳过 ✓）
    got = await asyncio.to_thread(
        make_backup, _DATA_DIR, out,
        include_snapshots=full, include_audio=full, include_heavy=full)
    if not got["ok"]:
        raise HTTPException(400, got["reason"])
    # ★ 如实回报"跳过了多少"（清单里也有 ✓）—— 别让用户以为备份是全的 ✗
    _note = got["reason"]
    return FileResponse(str(out), media_type="application/zip",
                        filename=f"lanternlogic-backup-{stamp}.zip",
                        headers={"X-Backup-Note": _note[:900].encode("ascii", "replace").decode()})


@app.post("/api/v1/backup/restore")
async def upload_restore(file: UploadFile = File(...)) -> dict[str, Any]:
    """★ 一键恢复：上传备份 zip ✓ —— 三条硬规矩（写在 backup.py 里 ✓ 这里执行 ✓）

      ① **先把现有数据挪走** ✗（不是删 ✓ 挪到 `_pre_restore_<时间戳>/` ✓ 可人工找回 ✓）
      ② ★ **有任务在跑就拒绝** ✗ —— 一边跑一边换数据目录必然写坏 ✓
      ③ 不是本应用的备份（没有 MANIFEST ✓）或含越界路径 ⇒ **整包拒绝** ✗ 一个文件都不解 ✓
    """
    from .backup import restore_backup
    running = [t.id for t in store.load_index() if str(getattr(t, "status", "")) == "running"]
    if running:
        raise HTTPException(409, f"有任务正在运行（{len(running)} 个）⇒ 拒绝恢复 ✗ "
                                 f"请等它跑完或先取消 ✓（一边跑一边换数据目录必然写坏 ✓）")
    # ★★ 2026-10-09 修（测试抓到的真 bug ✓）：
    #   原来把上传文件写在 `_DATA_DIR/_backups_/` 里 ✗
    #   而恢复的第一步是**把整个 _DATA_DIR 挪到一边** ✓ ⇒ 上传的那个 zip 跟着被挪走 ✗
    #   ⇒ 解包时找不到文件 ⇒ 恢复失败 ✗
    #     （★ 好消息：那次失败里"原数据没丢 ✓ 在 _pre_restore_... ✓"的提示是对的 ✓
    #       安全网生效了 ✓ —— 但这仍是个必须修的 bug ✓ 因为恢复压根没成功 ✗）
    #   ⇒ 上传件一律写到**系统临时目录** ✓（在 _DATA_DIR 之外 ✓ 挪 data 时不受影响 ✓）
    import tempfile
    tmp = Path(tempfile.gettempdir()) / f"lanternlogic-restore-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    try:
        tmp.write_bytes(await file.read())
    except OSError as e:
        raise HTTPException(400, f"上传内容写不进去：{e}")
    got = await asyncio.to_thread(restore_backup, tmp, _DATA_DIR)   # ★ 同上：挪/解包都可能很慢 ✓
    try:
        tmp.unlink(missing_ok=True)          # 用完就清 ✓（备份内容敏感 ✓ 不留临时副本 ✓）
    except OSError:
        pass
    if not got["ok"]:
        raise HTTPException(400, got["reason"])
    return {"ok": True, "detail": got["reason"], "moved_aside": got["moved_aside"]}


@app.post("/api/v1/demo/seed")
async def demo_seed() -> dict[str, Any]:
    """★ 种入**示例任务** ✓ —— 让第一次打开的人看到"它长什么样" ✓ 拍演示也用得上 ✓

    ★ 安全边界（写死在这里 ✗ 不靠调用方自觉 ✓）：
      · **只在"一个任务都没有"时才种** ✗ —— 有任何一个任务（含回收站 ✓）就拒绝 ✓
        ⇒ 用户的真实工作记录**绝不会**被掺进假数据 ✓
      · 每条标题都带「【示例】」标记 ✓ 一眼认出 ✓ 可直接删（走回收站 ✓ 可恢复 ✓）
      · **不碰模型、不花钱** ✓ 只写任务记录 + 一份说明文件 ✓
    """
    from .demo_seed import seed as _seed
    return _seed(store)


@app.get("/api/v1/tasks/{task_id}/open-in-browser")
async def open_in_browser(task_id: str, path: str = "index.html") -> dict[str, Any]:
    """★ 用**系统默认浏览器**打开产物 —— 让"交付的网页真能点" ✓ 且**不加安全风险** ✗

    ## 为什么需要它（2026-10-09 用户实测踩的坑 ✓）

    用户点开交付的 `index.html` ⇒ **样子正常但点任何按钮都没反应** ✗
    查下去发现：`/files/raw`（预览通道）**故意**带了
        `Content-Security-Policy: sandbox` + `X-Content-Type-Options: nosniff` ✓
    ⇒ **脚本不执行** ✓ —— 那是**有意为之的安全设计** ✓（见 raw_file 上方那段复审注释 ✓：
      Agent 生成的 HTML 若以同源方式直接渲染 ⇒ 里面的 `<script>` 能拿到**完整 API 权限** ✗
      ⇒ 本地存储型 XSS ✓ 所以预览**必须**是死的 ✓）

    ⇒ 于是正确做法不是"把响应头改成 text/html"✗（那等于**亲手打开漏洞** ✗）
      而是**另开一条通道**：交给**系统浏览器**用 `file://` 打开 ✓
        · 脚本**正常执行** ✓（用户终于能真试 ✓）
        · 但它**不是同源**（file:// 拿不到 http://127.0.0.1 的 API 权限 ✓）⇒ ★ **安全与可用兼得** ✓

    ## 为什么用 GET（一般"有副作用的动作"该用 POST ✗）

    这是**本机单人桌面应用** ✓ 用 GET 才能让用户**直接粘一条 URL 就能打开** ✓
    （界面上也会给按钮 ✓ —— 这条 URL 只是给"懒得等按钮"的场合留的后门 ✓）

    ## 防护

    路径仍走 `_safe_path` ✓ —— 越界（沙箱外）一律 403 ✓ 与预览通道**同一套判据** ✓
    """
    try:
        p = _safe_path(task_id, path)
    except PermissionError:
        raise HTTPException(403, f"路径越界（沙箱外）：{path}")
    if p.is_dir() or not p.exists():
        raise HTTPException(404, f"文件不存在：{path}")
    if os.name != "nt":
        raise HTTPException(501, "目前只实现了 Windows 的“用系统浏览器打开”")
    try:
        os.startfile(str(p))  # noqa: S606 —— 本机桌面应用的正常行为（打开用户自己的产物 ✓）
    except OSError as e:
        raise HTTPException(500, f"调起系统浏览器失败：{e}")
    return {
        "ok": True,
        "opened": str(p),
        "detail": "已在系统默认浏览器中打开（脚本可正常执行 ✓ 且不是同源 ⇒ 拿不到本机 API 权限 ✓）",
    }

# ---------- 设置（config.json 读写） ----------


class AccessModeReq(BaseModel):
    mode: str  # full | auto_edit | confirm



@app.post("/api/v1/access-mode")
async def set_access_mode(req: AccessModeReq) -> dict[str, Any]:
    """切换会话级权限模式：full=完全访问 / auto_edit=自动编辑 / confirm=变更前确认。"""
    global access_mode
    if req.mode not in ("full", "auto_edit", "confirm"):
        raise HTTPException(422, f"未知模式：{req.mode}（可用：full / auto_edit / confirm）")
    access_mode = req.mode
    _save_access_mode(access_mode)  # §8.4：重启后恢复
    return {"ok": True, "mode": access_mode}


@app.get("/api/v1/access-mode")
async def get_access_mode() -> dict[str, Any]:
    return {"mode": access_mode}


@app.get("/api/v1/license")
async def license_status() -> dict[str, Any]:
    """授权状态（设置页显示）：试用期剩余天数 / 已授权信息。

    ★★ 2026-10-09：加了 `enforced` 与 `mode` ✓ —— **AGPL 版必须让人一眼看出"这版没有使用限制"** ✓
      · `enforced = false`（AGPL 公开版 ✓ 默认 ✓）⇒ 界面显示「开源版（AGPL-3.0）· 无使用限制」✓
        并给出**源码地址** ✓（AGPL 第 13 条：通过网络用它的人必须能拿到源码 ✓）
      · `enforced = true`（商用版 ✓）⇒ 老样子显示试用期/授权信息 ✓
    为什么不能含糊 ✗：AGPL 版**带着"试用到期"四个字**就已经在暗示"有使用限制"了 ✓
      而那是**违反第 10 条**的暗示 ✓ ⇒ 两档必须分得清清楚楚 ✓
    """
    enforced = bool(getattr(cfg.license, "enforce", False))
    return {
        "enforced": enforced,
        "mode": "commercial" if enforced else "agpl",
        "note": ("商用版：试用期与授权闸生效 ✓" if enforced
                 else str(getattr(cfg.license, "note", "") or "开源版（AGPL-3.0）· 无使用限制")),
        # ★ 源码地址（AGPL 第 13 条的兑现方式 ✓ 界面里也必须有 ✓ 光写在 README 里不够 ✓）
        "source_url": version.SOURCE_URL,
        "license": "AGPL-3.0-only",
        "trial_days_left": _lic.trial_days_left() if enforced else -1,
        "trial_expired": _lic.trial_expired() if enforced else False,
        "activated": bool(_lic.key),
        "kind": _lic.license_kind_label(),
        "name": (_lic.info or {}).get("name") or "",
        "exp": (_lic.info or {}).get("exp") or "",
    }


class LicenseReq(BaseModel):
    key: str


@app.post("/api/v1/license")
async def license_activate(req: LicenseReq) -> dict[str, Any]:
    ok, msg = _lic.activate(req.key)
    if not ok:
        raise HTTPException(422, msg)
    return {"ok": True, "message": msg}


def _usage_label(full_title: str) -> str:
    """二十六轮第 6 批第 1 处：usage 的 label（列表显示用）——此前
    full_label 脱敏而 label 裸奔（label = full_title[:42]，首条消息贴 Key
    的任务经 GET /api/v1/usage 明文回传前 30~42 字）。与 full_label 同源
    _redact_text。"""
    return _redact_text(full_title)[:42]


def _usage_model(t_model: str) -> str:
    """by_model 分组键 / by_task[].model——来源可能是任务标题里的
    `用量（…）` 片段，同面打码。"""
    return _redact_text(t_model)


def _compose_usage_label(full_title: str, first_user: str) -> str:
    """二十六轮第 5 批第 4 处：usage 的 full_label 组装（纯函数，便于锚点直测）。

    脱敏【无条件】——第 4 批版本只在 len>=30 分支打码，<30 字含密钥的
    首条消息会原样进外发面（路径不对称，验证员实测指出）。规则：
    标题达到源头截断长度（>=30，说明可能被 [:30] 裁过）且首条 user 消息
    存在 → 用首条消息（更完整）；否则用标题。两者都过 _redact_text。"""
    base = first_user if (len(full_title) >= 30 and first_user) else full_title
    # [:120]：title（含 task_id 后缀共 ~150 字）原生 tooltip 过重（第 5 批第 7-3）
    return _redact_text(base)[:120] if base else "（无标题）"


@app.get("/api/v1/usage")
async def get_usage() -> dict[str, Any]:
    """全局用量统计：扫描所有任务的用量事件累加（输入/输出/总计 tok、调用次数、任务数）。

    数据来源两代兼容（第 41 班起）：优先读事件 payload 里结构化的 `usage` 字段
    （loop.py 落盘），旧任务没有该字段时回退解析 `content` 文本（正则）。
    """
    
    total_in = total_out = calls = 0
    total_cached = 0
    tasks_with_usage = 0
    by_model: dict[str, dict[str, int]] = {}
    by_task: list[dict[str, Any]] = []
    for tid in list(tasks.keys()):
        found = False
        t_in = t_out = t_calls = t_cached = 0
        t_model = "unknown"
        seen_runs: set[str] = set()
        for ev in store.read_events(tid):
            if ev.type != "knowledge":
                continue
            title = str(ev.payload.get("title", ""))
            if "用量" not in title:
                continue
            content = str(ev.payload.get("content", ""))
            found = True
            u = ev.payload.get("usage")
            if isinstance(u, dict):
                # 结构化（新）
                if u.get("run_id"):
                    seen_runs.add(str(u["run_id"]))
                t_model = str(u.get("model") or "unknown")
                t_calls += int(u.get("calls") or 0)
                t_in += int(u.get("input_tokens") or 0)
                t_out += int(u.get("output_tokens") or 0)
                t_cached += int(u.get("cached_tokens") or 0)
            else:
                # 回退：解析 "LLM 调用 N 次 | 输入 X tok + 输出 Y tok"
                m = _re.search(r"调用\s*(\d+)\s*次", content)
                if m:
                    t_calls += int(m.group(1))
                mi = _re.search(r"输入\s*(\d+)\s*tok", content)
                mo = _re.search(r"输出\s*(\d+)\s*tok", content)
                if mi:
                    t_in += int(mi.group(1))
                if mo:
                    t_out += int(mo.group(1))
                mm = _re.search(r"用量[（(]([^）)]+)[）)]", title)
                t_model = mm.group(1) if mm else "unknown"
        # ★★ 2026-10-07（第 1 项查证量出来的洞 ✗）：把"**正在跑的那一份**"也算进来 ✓
        #   与 `_task_usage` **同一口径** ✓（同一份快照、同一个 run_id 去重规则 ✓）
        #   —— 否则"跑着的时候"使用统计页和群里那行**又会对不上** ✗（刚修好的口径又裂开 ✓）
        _live = _inflight_usage(tid)
        if _live and int(_live.get("calls") or 0) > 0 and str(_live.get("run_id") or "") not in seen_runs:
            found = True
            t_calls += int(_live.get("calls") or 0)
            t_in += int(_live.get("input_tokens") or 0)
            t_out += int(_live.get("output_tokens") or 0)
            t_cached += int(_live.get("cached_tokens") or 0)
            if t_model == "unknown":
                t_model = str(_live.get("model") or "unknown")
        if found:
            tasks_with_usage += 1
            calls += t_calls
            total_in += t_in
            total_out += t_out
            total_cached += t_cached
            t_model = _usage_model(t_model)
            slot = by_model.setdefault(t_model, {"input": 0, "output": 0, "calls": 0, "cached": 0})
            slot["input"] += t_in
            slot["output"] += t_out
            slot["calls"] += t_calls
            slot["cached"] += t_cached
            full_title = (tasks[tid].title or "").strip().replace("\n", " ")
            # 二十六轮第 3 批第 7 处：任务创建时 title 在源头被 [:30] 截断
            # （main.py:601），仅靠 title 无法还原全名——从首条 user 消息补全。
            # 二十六轮第 4 批第 2 处：full_label 进 /api/v1/usage（外发面）——
            # 必须过 redact_text（与 history/action/observation 同源单一实现）；
            # 首条 user 消息可能含密钥/隐私，此前 [:200] 原文裸出（暴露量 ≈6.7×）。
            first_user = ""
            if len(full_title) >= 30:
                try:
                    hist = store.load_history(tid)
                    first_user = next(
                        (str(m.get("content", "")).strip().replace("\n", " ")
                         for m in hist if m.get("role") == "user" and str(m.get("content", "")).strip()),
                        "",
                    )
                except Exception:
                    pass
            # 二十六轮第 5 批第 4 处：脱敏提成纯函数（无条件）——第 4 批版本
            # 只在 len>=30 分支打码，<30 字含密钥的首条消息原样进外发面
            # （路径不对称）；label 恢复 [:42]——重命名端点（main.py:726）
            # 允许 title 长到 100，[:42] 对重命名任务是活代码。
            full_label = _compose_usage_label(full_title, first_user)
            label = _usage_label(full_title)
            by_task.append(
                {
                    "task_id": tid,
                    "label": label or "（无标题）",
                    # 不截断全名（脱敏后）
                    "full_label": full_label or "（无标题）",
                    "model": t_model,
                    "calls": t_calls,
                    "input_tokens": t_in,
                    "output_tokens": t_out,
                    "cached_tokens": t_cached,
                    "total_tokens": t_in + t_out,
                }
            )
    by_task.sort(key=lambda x: x["total_tokens"], reverse=True)
    return {
        "input_tokens": total_in,
        "output_tokens": total_out,
        "total_tokens": total_in + total_out,
        "cached_tokens": total_cached,  # 前缀缓存命中（第 41 班）——折扣计费的部分
        "calls": calls,
        "tasks_with_usage": tasks_with_usage,
        "task_count": len(tasks),
        "by_model": by_model,
        "by_task": by_task[:20],
    }


# ---------- 一键清干净（⚠️ 破坏性 ⇒ 必须二次确认 ✓） ----------
#
# ★★ 2026-10-07 用户点名要的（"**一键清干净**（⚠️破坏性 ⇒ 必须二次确认 ✓）"）。
#
# 三条设计原则（每一处都在回答"万一用户手滑了怎么办" ✓）：
#   ① **先备份、再清** ✓ —— 不删，是**改名挪到** `data/_cleared_<时间戳>/` ✓
#      （同一个盘上是瞬时的 ✓ 而且**可回滚** ✓ —— 用户那套规矩里"每步可回滚"✓）
#   ② **二次确认是真确认** ✗ —— 必须把确认词**原样打出来** ✓（一个按钮点两下不算 ✓）
#      + **有任务在跑就拒绝** ✗（否则正干活的 Agent 脚下的文件被抽走 ✓）
#   ③ **说不清就不删** ✓ —— 这份清单**逐条列出**"会清什么 / **不会**碰什么" ✓
#      （config.json 里的访问密码与 Key 名 ✗ 不删 ✓ 1.8GB 的本地 ASR 模型 ✗ 不删 ✓
#        授权文件 ✗ 不删 ✓ —— 删了这些，用户得重新配一遍、重下几个 G ✓）

#: 可清的"件"→（目录/文件名，界面上的名字 ✓）
_CLEAR_PARTS: dict[str, tuple[tuple[str, ...], str]] = {
    "tasks": (("tasks",), "任务与产物（对话记录、工作区文件）"),
    "memory": (("memory",), "长期记忆（越用越懂你的那份）"),
    "kb": (("kb",), "知识库（导入的文档切块与向量）"),
    "team": (("team", "groups"), "团队（员工卡、群聊、群共享工作区）"),
    "approvals": (("approvals",), "「这类以后都别问」的放行清单"),
    "tts": (("tts",), "朗读缓存音频（可再生成）"),
    "projects": (("projects.json",), "项目与项目指令"),
    "automations": (("automations.json",), "定时 / Webhook / 文件夹监听"),
    "wide": (("wide.json",), "Wide Research 记录"),
}
#: 清任务时要**一起**清掉的登记表（它们只是"哪个任务的工作区在哪"✓ 留着就是悬空引用 ✗）
_CLEAR_WITH_TASKS = ("task_workdir.json", "identities.json")
#: **明确不碰**的东西（要原样写在界面上 ✓ —— 用户得知道"清干净"清到哪一步 ✓）
_CLEAR_KEEPS = (
    "config.json：模型/Key **名字**/访问密码/单价/上限等全部设置",
    "本地 ASR 模型（约 1.8GB，删了要重下）",
    "已装的依赖（Python 包、语音依赖）",
    "授权文件 license.json 与体检记录 eval_runs/",
)
#: 确认词：必须原样打出来 ✓（不是点两下按钮 ✓）
_CLEAR_PHRASE = "清空我的数据"


@app.get("/api/v1/data/clear")
async def data_clear_preview() -> dict[str, Any]:
    """清空**之前**先看清楚：会清掉什么、占多大、**不会**碰什么 ✓（诚实清单 ✓）。"""
    items: list[dict[str, Any]] = []
    for key, (names, label) in _CLEAR_PARTS.items():
        size = 0
        exists = False
        for nm in names:
            p = _DATA_DIR / nm
            if not p.exists():
                continue
            exists = True
            if p.is_dir():
                size += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            else:
                size += p.stat().st_size
        items.append({"key": key, "label": label, "exists": exists, "bytes": size})
    running = [t for t, r in runs.items() if getattr(r, "aio_task", None) and not r.aio_task.done()]
    return {
        "ok": True,
        "items": items,
        "keeps": list(_CLEAR_KEEPS),
        "phrase": _CLEAR_PHRASE,          # 界面要把这个词显示给用户照打 ✓
        "running": running,               # 非空 ⇒ 现在**不许**清 ✗
        "note": ("⚠️ 破坏性操作：会把这些数据**先挪到备份目录**再清空 ✓ "
                 "（同一个盘上是瞬时的 ✓ 想彻底不留就把那个目录删掉 ✓）。"
                 "默认**不碰**你的设置、Key、已下模型与授权 ✓。"),
    }


class DataClearReq(BaseModel):
    """`confirm` 必须**原样等于**那句确认词 ✓（二次确认 ✓）；`parts` 空 = 全清 ✓。"""

    confirm: str = ""
    parts: list[str] = []


@app.post("/api/v1/data/clear")
async def data_clear(req: DataClearReq) -> dict[str, Any]:
    """★ 一键清干净（破坏性）：**先备份再清** ✓ 二次确认 ✓ 有任务在跑就拒绝 ✗。"""
    # ① 二次确认：确认词必须原样打对 ✓
    if str(req.confirm or "").strip() != _CLEAR_PHRASE:
        raise HTTPException(422, f"没有确认：请在确认框里原样输入「{_CLEAR_PHRASE}」")
    # ② 有任务在跑 ⇒ 拒绝（正干活的 Agent 脚下的文件不能被抽走 ✓）
    live = [t for t, r in runs.items() if getattr(r, "aio_task", None) and not r.aio_task.done()]
    if live:
        raise HTTPException(409, f"还有 {len(live)} 个任务在跑 —— 先等它们结束或取消，再清 ✗"
                                 f"（不然它们正在写的文件会被抽走）")
    want = [p for p in (req.parts or list(_CLEAR_PARTS)) if p in _CLEAR_PARTS]
    if not want:
        raise HTTPException(422, "没有选中任何要清的东西")
    if "tasks" in want:
        want = list(dict.fromkeys([*want, "tasks"]))     # 任务必选（登记表跟着它走 ✓）

    stamp = _now().replace("-", "").replace(":", "").replace("T", "-")[:15]
    backup = _DATA_DIR / f"_cleared_{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    for key in want:
        names, _label = _CLEAR_PARTS[key]
        targets = [*names, *_CLEAR_WITH_TASKS] if key == "tasks" else list(names)
        for nm in targets:
            src = _DATA_DIR / nm
            if not src.exists():
                continue
            dst = backup / nm
            if dst.exists():
                dst = backup / f"{nm}.{len(moved)}"
            try:
                src.rename(dst)          # ★ 同一个盘上**瞬时**完成 ✓ 而且可回滚 ✓
            except OSError:
                # 跨盘/被占用 ⇒ 退回复制删除（慢一点，但结果一样 ✓）
                import shutil as _sh
                if src.is_dir():
                    _sh.move(str(src), str(dst))
                else:
                    _sh.move(str(src), str(dst))
            moved.append(nm)

    # ★★ 清完必须把**目录骨架建回来** ✓ —— 否则 `_save_index()` 写 `tasks/index.json` 时
    #   目录已经不存在 ⇒ FileNotFoundError ⇒ 清完当场 500 ✗
    #   （本班在隔离目录里真跑这条链时抓到的 ✓ 光看代码看不出来 ✓）
    for _d in ("tasks", "memory", "kb", "team", "groups", "approvals", "tts"):
        (_DATA_DIR / _d).mkdir(parents=True, exist_ok=True)

    # ③ 内存里的那份也要跟着清 ✓（否则界面照旧显示、而且下一次保存会把文件**又写回来** ✗）
    cleared_mem: list[str] = []
    if "tasks" in want:
        tasks.clear()
        runs.clear()
        _save_index()
        cleared_mem.append("任务索引")
    if "memory" in want:
        try:
            _memory_store.clear()
        except Exception:                                # noqa: BLE001
            pass
        cleared_mem.append("长期记忆")
    if "kb" in want:
        global _kb_store
        _kb_store = KBStore(_DATA_DIR / "kb")
        cleared_mem.append("知识库")
    if "team" in want:
        global _team_store
        _team_store = TeamStore(_DATA_DIR / "team")
        cleared_mem.append("团队")
    if "approvals" in want:
        approval.revoke_forever("")                      # 全收回 ✓
        cleared_mem.append("放行清单")
    if "projects" in want:
        projects.clear()
        cleared_mem.append("项目")
    if "automations" in want:
        automations.clear()
        cleared_mem.append("自动化")

    # ★★ 2026-10-07（第 9 项补②）：**清空数据必须进审计账** ✓
    #   按这本账自己的定位（"只记**需要有人负责**的事"✓），清空数据正是最该留痕的一件 ✓
    #   ★ 用户当天亲手演示了为什么：他清完 202 → 4 个任务 ✓ 而账本里**一个字都没有** ✗
    #     ⇒ 事后谁也说不清"数据是什么时候没的、谁清的、清到哪去了" ✓
    #   ★ 记下**备份路径**是关键 ✗ —— 这句让"账本"直接变成"找回东西的线索" ✓
    audit.record("cleared", parts=",".join(want), moved=len(moved), backup=str(backup))
    return {
        "ok": True,
        "cleared": want,
        "moved": moved,
        "memory_reset": cleared_mem,
        "backup": str(backup),
        "note": (f"已清空 {len(want)} 类 ✓ —— 原件都**挪到**了 {backup} ✓"
                 "（要彻底不留就把这个目录删掉 ✓；要找回就把里面的东西挪回 data/ ✓）。"
                 "设置、Key、已下模型与授权**没动** ✓。"),
    }


class SettingsResetReq(BaseModel):
    """恢复某一节的默认值（Phase 3 ⑧ 设置页正规化）。"""

    section: str


# ★ 允许恢复默认的节：**不含 server / storage / skills / mcp** ——
#   把 server 恢复默认会把访问密码清空、把局域网关掉（用户会被锁在外面）；
#   storage/skills 恢复默认会让"任务/技能去哪了"变成事故。这几节要改就手工改配置文件。
_RESETTABLE = ("model", "executor", "video", "image", "asr", "tts", "kb", "ui", "notify", "memory")


@app.post("/api/v1/settings/reset")
async def reset_settings(req: SettingsResetReq) -> dict[str, Any]:
    """把**某一节**恢复成出厂默认（其余节一个字节都不动）。

    默认值取自配置模型自己的字段默认（`type(cfg.<节>)()`）—— 与"新装一台机器时
    config.example.json 生成的值"同源（example 就是照着这些默认写的）。
    """
    sec = req.section.strip()
    if sec not in _RESETTABLE:
        raise HTTPException(
            422,
            f"不支持恢复这一节：{sec!r}（可恢复：{list(_RESETTABLE)}；"
            "server/storage/skills/mcp 出于安全考虑只能手工改配置文件）",
        )
    before = getattr(cfg, sec)
    fresh = type(before)()          # pydantic 默认值
    setattr(cfg, sec, fresh)
    _save_config()
    # 把改后的这节回给界面（顺便证明"只动了这一节"）
    return {"ok": True, "section": sec, "value": fresh.model_dump(mode="json"),
            "note": f"「{sec}」已恢复默认；其它节没有改动。"}


@app.get("/api/v1/capabilities")
async def get_capabilities() -> dict[str, Any]:
    """★ Phase 2 ④：统一的「能力槽 + 提供者」——一处问清"每个能力能用谁、现在能不能用"。

    以前这些知识散在四个模块里（providers._REGISTRY / main._VIDEO_PROVIDERS /
    ImageCfg.provider / asr+tts 的实现分支），而且没有任何一处能回答
    "我现在这套到底能不能出声/出图/出视频"。现在由 `app/capabilities.py` 统一声明，
    这里只做"声明 + 当前配置 + 环境变量" → 状态的翻译。
    Key 只报设没设，**绝不回传值**。
    """
    return capabilities_status(cfg)


class SettingsAsrReq(BaseModel):
    """语音转文字档位（Phase 2 ⑤ 两档：mimo=云端 / local_qwen3=本地离线）。"""

    provider: str
    model: str = ""
    api_key_env: str = ""


class SettingsUiReq(BaseModel):
    """界面偏好（目前只有主题 ✓ 2026-10-07 用户要的深色 + 浅色）。"""

    theme: str = ""


@app.post("/api/v1/settings/ui")
async def set_ui(req: SettingsUiReq) -> dict[str, Any]:
    """存界面主题 ✓ —— 「深色 / 浅色」两档（用户："最起码有两个"✓）。

    ★ 校验过再写 ✓（表里没有的值一律拒 ✗ —— 写进去界面会变成没定义的样式 ✗）。
    """
    if req.theme:
        if req.theme not in ("dark", "light"):
            raise HTTPException(422, f"未知主题：{req.theme}（可用：dark / light）")
        cfg.ui.theme = req.theme
        _save_config()
    return {"ok": True, "theme": cfg.ui.theme,
            "note": f"主题已存为「{'浅色' if cfg.ui.theme == 'light' else '深色'}」✓ 下次打开也生效 ✓"}


class SettingsTtsReq(BaseModel):
    """语音合成后端（2026-10-06 新增：**界面上终于能切了** ✓）。

    edge=微软在线（免费免装）/ qwen3tts=千问3 本地（中文最准 ✓ 2.4GB）/
    melotts=本地（MIT 可商用，约 100MB）/ pyttsx3=用系统自带语音（零依赖兜底）。

    ★ 2026-10-08 加 `voice`：千问3-TTS 有 **9 个音色**（vivian/serena/uncle_fu/dylan/eric… ✓）
      留空 = 不动当前音色 ✓ 传了就必须是声明表里的名字（否则 422 ✓ 不许静默退回默认 ✗）。
    """

    backend: str
    voice: str = ""


@app.post("/api/v1/settings/asr/install")
async def install_local_asr() -> dict[str, Any]:
    """★ 2026-10-07「**一键装**」✓（用户原话："我想要的就是一键能安装，然后还能使用这种的"）。

    · 跑的是**写死的**一条：`<本项目的 python> -m pip install -U qwen-asr` ✓
      **不经过 shell** ✓ **不接受任何用户输入** ✓（所以它不是一个"任意命令执行口子" ✓）
    · 装完会**再探一次**（能不能 import）✓ 才报成功 ✓ —— pip 返回 0 不等于真能用 ✓
    · 输出实时进日志 ✓ 前端轮询 `/settings/asr/install/status` 就能看到 ✓
    """
    from . import local_install as asr_install
    r = asr_install.install_deps()
    if not r.get("ok"):
        raise HTTPException(409, str(r.get("detail") or "装不了"))
    return r


class AsrPullReq(BaseModel):
    tier: str = "0.6b"


@app.post("/api/v1/settings/asr/pull")
async def pull_local_asr_model(req: AsrPullReq) -> dict[str, Any]:
    """★「**一键下模型**」✓（0.6B 约 1–2GB / 1.7B 约 3–4GB ✓ 进度看 status ✓）。"""
    from . import local_install as asr_install
    r = asr_install.pull_model(req.tier)
    if not r.get("ok"):
        raise HTTPException(422, str(r.get("detail") or "下不了"))
    return r


@app.get("/api/v1/settings/asr/install/status")
async def local_asr_status() -> dict[str, Any]:
    """安装/下载的**实时状态**（含真实输出最后几行 ✓ 与已下字节数 ✓）。"""
    from . import local_install as asr_install
    return asr_install.status()


@app.post("/api/v1/settings/kb/install")
async def install_kb_embedder() -> dict[str, Any]:
    """★ 一键装**知识库本地向量**依赖 ✓（用户："我做这个知识库本地的" ✓）。

    跑的是写死的一条 `pip install -U fastembed` ✓ 不走 shell ✓ 不接受用户输入 ✓
    装完**再探一次**才报成功 ✓（pip 返回 0 ≠ 真能 import ✓）。
    """
    from . import local_install
    r = local_install.install_kb_deps()
    if not r.get("ok"):
        raise HTTPException(409, str(r.get("detail") or "装不了"))
    return r


# ═══ ★★ 2026-10-08：**本地朗读的「一键装」**（用户："就像上面这个千问3似的，点一下它就安装"）═══
#
# 两个引擎各一条路 ✓ 都跑**写死的**命令 ✓ 进度看同一个 status 接口 ✓
#   · melotts ⇒ 本仓自带的安装器（`scripts/install_melotts.py` ✓ 钉 commit + 哈希 ✓）
#     ★ 为什么不能写 `pip install melotts` ✗：实测在 Python 3.13 上**装不上**
#       （PyPI 只有源码包且缺 requirements.txt；还写死 torch<2.0、1.x 没有 3.13 的轮子 ✓）
#   · qwen3tts ⇒ pip 四条 + ModelScope 下模型（**用户点名那档** ✓ 中文词全对 ✓ 2.4GB ✓）
#     ★ Kokoro 已**下架** ✗（中文系统性错音 ✓ 实测 20+ 音色全一样 ✓）—— 不再提供安装 ✓
#     ★ 它才是用户问的"更好更小还能商用"那档：Apache-2.0 ✓ int8 模型 109MB ✓ CPU 就能跑 ✓


class TtsInstallReq(BaseModel):
    engine: str = "qwen3tts"


@app.post("/api/v1/settings/tts/install")
async def install_local_tts(req: TtsInstallReq) -> dict[str, Any]:
    """一键装本地朗读引擎 ✓（后台跑 ✓ 实时输出走 status ✓ 同一时刻只跑一个 ✓）。"""
    from . import local_install
    r = local_install.install_tts(req.engine)
    if not r.get("ok"):
        raise HTTPException(409, str(r.get("detail") or "装不了"))
    return r


@app.get("/api/v1/settings/tts/install/status")
async def local_tts_install_status() -> dict[str, Any]:
    """本地朗读安装/下载的**实时状态** ✓（与 ASR 共用同一份任务状态 ✓ 同一时刻只有一个在跑 ✓）。"""
    from . import local_install
    return local_install.status()


class SettingsKbReq(BaseModel):
    """知识库向量档位（local=本地离线 / dashscope=阿里云端）。"""

    embedder: str
    local_model: str = ""


@app.post("/api/v1/settings/kb")
async def set_kb_embedder(req: SettingsKbReq) -> dict[str, Any]:
    """切换知识库的**向量档位** ✓ —— 照 ASR/TTS 那两处的规矩来 ✓（三处口径必须一致 ✓）：

    · **校验过再写** ✓（表里没有的档位一律拒 ✗）
    · 允许先选一个**还没装**的本地档 ✓ 但**如实说明"现在还用不了、怎么装"** ✓
    · ★ **绝不静默回退云端** ✗ —— 选本地就只用本地 ✓（用户选它就是为了资料不出本机 ✓）
    """
    from .capabilities import CAPABILITIES, status as _cap_status
    allowed = CAPABILITIES["kb"]["providers"]
    if req.embedder not in allowed:
        raise HTTPException(422, f"未知的知识库向量档位：{req.embedder}（可用：{sorted(allowed)}）")
    cfg.kb.embedder = req.embedder
    if req.local_model:
        cfg.kb.local_model = req.local_model
    _save_config()
    st = _cap_status(cfg)["capabilities"]["kb"]
    state = st["providers"][req.embedder]["state"]
    detail = st["providers"][req.embedder]["detail"]
    ok = state == "ready"
    return {"ok": True, "embedder": req.embedder, "state": state, "detail": detail,
            "note": ("已切换到「" + allowed[req.embedder]["label"] + "」，现在就能用。"
                     if ok else
                     "已记下「" + allowed[req.embedder]["label"] + "」，但它现在还用不了：" + detail)}


@app.post("/api/v1/settings/tts")
async def set_tts(req: SettingsTtsReq) -> dict[str, Any]:
    """切换语音合成后端 ✓ —— **照 ASR 那个口子的规矩来** ✓（两边口径必须一致 ✓）：

    · **校验过再写** ✓：声明表里没有的后端一律拒 ✗（写进去用户会炸 ✓）
    · 允许先选一个**还没装**的后端 ✓ —— 但**如实告诉他"现在还发不出声，怎么装"** ✓
      （这是本项目的规矩：宁可说"暂时不行" ✓ 也不装作能跑 ✗）
    """
    from .capabilities import CAPABILITIES, status as _cap_status
    allowed = CAPABILITIES["tts"]["providers"]
    if req.backend not in allowed:
        raise HTTPException(422, f"未知的 TTS 后端：{req.backend}（可用：{sorted(allowed)}）")
    cfg.tts.backend = req.backend
    # ★ 2026-10-08：**音色**（用户要能自己挑 ✓）—— 校验过再写 ✓
    #   只有声明表里带 `voices` 的后端才认它 ✓ 名字不在表里一律拒 ✗
    #   （不校验的话会**静默退回默认音色** ✗ 而用户以为切好了 ✓ —— 正是本项目最恨的那种"骗人" ✓）
    if req.voice:
        voices = [v["id"] for v in allowed[req.backend].get("voices", [])]
        if not voices:
            raise HTTPException(422, f"「{req.backend}」不支持选音色（只有千问3-TTS 有 9 个音色）")
        if req.voice not in voices:
            raise HTTPException(422, f"未知音色：{req.voice}（可用：{voices}）")
        cfg.tts.voice = req.voice
    _save_config()
    st = _cap_status(cfg)["capabilities"]["tts"]
    state = st["providers"][req.backend]["state"]
    detail = st["providers"][req.backend]["detail"]
    ok = state == "ready"
    return {"ok": True, "backend": req.backend, "state": state, "detail": detail,
            "voice": str(getattr(cfg.tts, "voice", "") or ""),
            "note": ("已切换到「" + allowed[req.backend]["label"] + "」，现在就能用。"
                     if ok else
                     "已记下「" + allowed[req.backend]["label"] + "」，但它现在还用不了：" + detail)}


@app.post("/api/v1/settings/asr")
async def set_asr(req: SettingsAsrReq) -> dict[str, Any]:
    """切换 ASR 档位。**校验过再写**：不许把声明表里没有的档位写进配置（用户选了会炸）。"""
    from .capabilities import CAPABILITIES, status as _cap_status
    allowed = CAPABILITIES["asr"]["providers"]
    if req.provider not in allowed:
        raise HTTPException(422, f"未知的 ASR 档位：{req.provider}（可用：{sorted(allowed)}）")
    cfg.asr.provider = req.provider
    if req.model:
        cfg.asr.model = req.model
    if req.api_key_env:
        cfg.asr.api_key_env = req.api_key_env
    _save_config()
    st = _cap_status(cfg)["capabilities"]["asr"]
    ok = st["providers"][req.provider]["state"] == "ready"
    return {"ok": True, "provider": req.provider, "state": st["providers"][req.provider]["state"],
            "detail": st["providers"][req.provider]["detail"],
            "note": ("已切换到" + allowed[req.provider]["label"] + ("，现在就能用。")
                     if ok else
                     "已记下这个档位，但它现在还用不了：" + st["providers"][req.provider]["detail"])}


class SettingsImageReq(BaseModel):
    """出图引擎（★ 2026-10-07 补：此前**只能手改配置文件** ✗ 见下面端点的注释 ✓）。"""

    provider: str
    model: str = ""
    api_key_env: str = ""


@app.post("/api/v1/settings/image")
async def set_image(req: SettingsImageReq) -> dict[str, Any]:
    """★★ 2026-10-07（体检⑥ 引出来的一个真缺口 ✗）：**出图引擎在界面上切不了** ✓。

    现场（本班查证 ✓）：能力总览里明明写着出图有**两档** ✓
      · `dashscope` 通义万相（云端，要 Key ✓）
      · `comfyui`  本地 ComfyUI（离线 ✓ 不花钱 ✓）
    而**切换接口只有 ASR / TTS / 知识库有** ✗ —— **唯独出图没有** ✗
    ⇒ 用户想从云端换到本地，**只能手改 config.json** ✓（而本项目其它每一档都能在界面上点 ✓）
    ⇒ 结果就是：百炼额度用完之后（正是本机现在的状态 ✗），
      界面上**看不出为什么画不了、也没地方换** ✓ —— 与"能力总览说能用、实际不能用"同类 ✓。

    照 ASR/TTS/KB 的规矩：**校验过再写** ✓ 写了之后**如实回报它现在能不能用** ✓
    （本地那档还额外探一下 ComfyUI 在不在跑 ✓ —— 不在跑就说清楚"先把 ComfyUI 开起来"✓）。
    """
    from .capabilities import CAPABILITIES, status as _cap_status

    allowed = CAPABILITIES["image"]["providers"]
    if req.provider not in allowed:
        raise HTTPException(422, f"未知的出图引擎：{req.provider}（可用：{sorted(allowed)}）")
    cfg.image.provider = req.provider
    if req.model:
        cfg.image.model = req.model
    if req.api_key_env:
        cfg.image.api_key_env = req.api_key_env
    _save_config()
    st = _cap_status(cfg)["capabilities"]["image"]
    cur = st["providers"][req.provider]
    ok = cur["state"] == "ready"
    return {
        "ok": True, "provider": req.provider, "state": cur["state"], "detail": cur["detail"],
        "note": (f"已切换到「{allowed[req.provider]['label']}」，现在就能用。"
                 if ok else
                 f"已记下「{allowed[req.provider]['label']}」，但它现在还用不了：" + cur["detail"]),
    }


@app.get("/api/v1/version")
async def get_version() -> dict[str, Any]:
    """★ 2026-10-07（第 3 项）：**这个软件是哪个版本** ✓ —— 一处声明，处处读它 ✓。

    为什么要单开一个接口（而不是前端写死一行字 ✗）：
      加之前是**三处各说各的** ✓ —— `package.json` 一份 ✓ 关于页硬编码一份 ✗
      **后端压根没有** ✗（连"我是哪个版本"都答不出来 ✓ 那还谈什么检查更新 ✓）
      ⇒ 现在后端是唯一来源 ✓ 界面显示它报的 ✓ 打包/发版也读它 ✓
    """
    return {
        "ok": True,
        "name": version.APP_NAME,
        "vendor": version.VENDOR,
        "version": version.__version__,
        # 没配检查地址 ⇒ 界面就不该显示"检查更新"按钮（或者点了如实说"没配"✓）
        "check_url_set": bool(str(getattr(cfg.update, "check_url", "") or "").strip()),
        "note": "版本号只有一处：backend/app/version.py ✓（界面显示的就是它报的 ✓）",
    }


@app.get("/api/v1/author")
async def get_author_card() -> dict[str, Any]:
    """★ 2026-10-08：**作者卡**（只读 + 防伪签名 ✓ 用户定的内容 ✓）。

    为什么要签名（不只是"写个名字"✗）：
      别人拿这个程序**改个名、换张作者卡**就说是自己做的 ✗
      ⇒ 卡的内容用 Ed25519 私钥签 ✓ 程序内置公钥 ✓ 打开关于页就验 ✓
      ⇒ 对不上就当场说「⚠️ 这张卡不是原版」✓（而不是"反正显示正版"✗）

    ★ 工作室名**从 version.VENDOR 读** ✓（不在卡里另抄一份 ✗ 免得两处打架 ✓）
    ★ 只给**验签结论** ✓ 不给签名原文、更不给私钥 ✗
    """
    return {"ok": True, **author.card_for_ui()}


@app.post("/api/v1/version/check")
async def check_version() -> dict[str, Any]:
    """★ 2026-10-07：**查有没有新版本** ✓ —— 只查、不装 ✓ 查不到就说查不到 ✓。

    ★ 三条设计（每条都在防"最坏的那种提示" ✗）：
      ① **默认不查** ✓ —— 没配地址就一个字节都不往外发 ✓（本项目是本地优先 ✓）
      ② **只查不装** ✗ —— 查到新版只告诉你**去哪下** ✓
         绝不自动下载/替换程序 ✓（那等于"谁改了那个地址谁就能给你换程序"✗ 供应链口子 ✓）
      ③ **宁可保守** ✓ —— 断网/格式怪/版本号比不出来 ⇒ 一律判"没发现新版本" ✓
         绝不因为解析失败就弹一个假的"有新版本" ✗（让用户去下一个不存在的东西 = 最糟 ✓）
    """
    cur = version.__version__
    url = str(getattr(cfg.update, "check_url", "") or "").strip()
    if not url:
        return {"ok": False, "current": cur, "has_update": False, "latest": "", "notes": "", "url": "",
                "note": ("还没配「更新检查地址」—— 所以**没有联网查** ✓（本项目默认不往外发东西 ✓）。"
                         "等你的仓库/发布页建好，把地址填进设置里的「升级检查」即可 ✓")}
    if not url.lower().startswith(("http://", "https://")):
        return {"ok": False, "current": cur, "has_update": False, "latest": "", "notes": "", "url": "",
                "note": "更新检查地址必须是 http/https ✓（不接受 file:// 这类 ✗）"}
    # ★ 复用项目里那套 SSRF 防护 ✓（它本来就在防"被指到内网/本机"✓ 自己再写一套只会更弱 ✗）
    from .ssrf import SsrfBlocked, assert_public_url
    try:
        await assert_public_url(url)
    except SsrfBlocked as e:
        return {"ok": False, "current": cur, "has_update": False, "latest": "", "notes": "", "url": "",
                "note": f"这个地址不允许访问：{e}"}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            r = await client.get(url, headers={"Accept": "application/json"})
        if r.status_code >= 400:
            return {"ok": False, "current": cur, "has_update": False, "latest": "", "notes": "", "url": "",
                    "note": f"查不到（HTTP {r.status_code}）—— 地址对不对？"}
        d = r.json()
    except Exception as e:                                   # noqa: BLE001
        return {"ok": False, "current": cur, "has_update": False, "latest": "", "notes": "", "url": "",
                "note": f"查不到（{type(e).__name__}）—— 断网或地址不对，都如实说 ✓"}
    latest = str((d or {}).get("version") or "").strip()
    if not latest:
        return {"ok": False, "current": cur, "has_update": False, "latest": "", "notes": "", "url": "",
                "note": "对方返回的内容里没有 version 字段 —— 我认不出来，就不猜 ✓"}
    has = version.is_newer(latest, cur)
    return {
        "ok": True, "current": cur, "latest": latest, "has_update": has,
        "notes": str((d or {}).get("notes") or "")[:600],
        "url": str((d or {}).get("url") or ""),
        "note": (f"有新版本：{cur} → {latest} ✓ 去上面那个网址下载安装 ✓（**我不会自动替换你的程序** ✗）"
                 if has else f"已是最新（{cur}）✓"),
    }


@app.get("/api/v1/models")
async def get_models(provider: str = "", base_url: str = "", refresh: int = 0) -> dict[str, Any]:
    """★ Phase 2 ⑥：问服务商"此刻有哪些模型"（模型名下拉 + 一键刷新）。

    `provider` 缺省 = 当前配置里的提供者；`base_url` 可选（界面点预设芯片时还没保存配置，
    用它把该预设的地址先带过来）；`refresh=1` 绕过 5 分钟缓存。
    拉不到**不是错误**：返回 `source="preset"` + 原因，界面退回内置预设即可（绝不 500）。
    Key 只从环境变量读、只往该提供者的地址上带，**返回值里绝不出现**。
    """
    from .model_list import fetch_models
    from .providers import _REGISTRY      # 与其它端点同款：按需导入（模块级没有它）
    p = provider or cfg.model.provider
    if p not in _REGISTRY:
        raise HTTPException(422, f"未知提供者：{p}（可用：{sorted(_REGISTRY)}）")
    # 地址优先级：调用方显式给 > 当前配置（仅当问的就是当前提供者）
    base = base_url or (cfg.model.base_url or "" if p == cfg.model.provider else "")
    key_env = cfg.model.api_key_env if p == cfg.model.provider else None
    key = os.environ.get(key_env) if key_env else None
    if p == "ollama":
        key = None
    return await fetch_models(p, base, key, refresh=bool(refresh))


@app.get("/api/v1/settings")
async def get_settings() -> dict[str, Any]:
    """返回当前配置（Key 绝不回传，只回传环境变量名与是否已设置）。"""
    m = cfg.model
    key_env = m.api_key_env or ""
    return {
        "model": {
            "provider": m.provider,
            "model_name": m.model_name,
            "base_url": m.base_url,
            "api_key_env": key_env,
            "key_set": bool(os.environ.get(key_env)) if key_env else None,
            # ★★ 2026-10-07（功能体检⑤**真跑**抓到的**静默数据丢失** ✗✗，见下）：
            #   界面 `SettingsPanel.tsx:476/477` 本来就会读这两个字段来回填表单 ✓
            #   但**这里不返回它们** ✗ ⇒ 表单里永远是"默认 25 步 + 空单价表" ✗
            #   ⇒ 而"保存"是把**表单内容**整体写回（`POST /settings/limits` ✓）
            #   ⇒ **用户只要点一次保存，他设的步数上限和填好的单价就被重置/清空** ✗✗
            #
            #   实测复现（评审区 `_repro_limits_wipe.py` ✓ 真接口跑出来的）：
            #     ① 设 60 步 + 单价表 → 存进去 ✓
            #     ② 重开设置页 → 读到 None ✗（表单显示 25 + 空表）
            #     ③ 改个别的设置顺手保存 → 写回 25 + 空表 ✗✗
            #     ④ 结果：**60 → 25，单价表被清空** ✗ 而且**没有任何提示** ✓
            #
            #   这两件事恰恰是用户 2026-10-06 专门提的 ✓（"25 步常在快做完时被砍断"✓
            #   "花了多少钱看不见"✓）—— 结果**每保存一次就被悄悄清掉** ✗✗
            #   ⇒ 现在如实回传 ✓ 表单填得上、保存不再毁东西 ✓。
            "max_iterations": int(getattr(m, "max_iterations", 0) or 0),
        },
        # ★ 同上：单价表也要回传 ✓（不回传 = 界面永远空表 = 保存即清空 ✗）
        "pricing": {str(k): dict(v) for k, v in (getattr(cfg, "pricing", {}) or {}).items()
                    if isinstance(v, dict)},
        # ★★ 2026-10-07：**花费上限也要回传** ✓ —— 与上面 max_iterations / pricing
        #   **同一个形状的坑**（体检⑤ 抓到的那条 ✗）：不回传 ⇒ 界面回填成空 ⇒
        #   用户一按保存就把自己设的上限**静默清掉** ✗✗（而且他根本不会发现 ✓）。
        #   这次是**先想到再加**的 ✓（测试 `test_settings_readback_does_not_wipe_the_budget` 钉着 ✓）。
        "budget": {
            "enabled": bool(getattr(getattr(cfg, "budget", None), "enabled", True)),
            "caps": {str(k): dict(v) for k, v in
                     (getattr(getattr(cfg, "budget", None), "caps", {}) or {}).items()
                     if isinstance(v, dict)},
        },
        # ★★ 2026-10-06（**"多处口径打架"的第四处** ✗✗）：这里原来**硬编码 `"melotts"`** ✗ ——
        #   而真实生效的后端是 `edge` ✓（TTS 接口的默认 ✓）
        #   ⇒ 设置接口说 melotts ✓ 关于页说 Edge ✓ 能力总览硬写 melotts ✗ 实际跑 edge ✓
        #   **四处各说各的** ✓ 用户当然一头雾水 ✓ —— 现在统一从**配置**读 ✓（唯一真相 ✓）
        "tts": {"backend": str(getattr(cfg.tts, "backend", "") or "edge")},
        "executor": {
            "type": cfg.executor.type,
            "workspace_root": str(cfg.executor.workspace_root),
            "shell": getattr(cfg.executor, "shell", ""),
            "allowed_dirs": [str(p) for p in cfg.executor.allowed_dirs],
            "approval_required": list(cfg.executor.approval_required),
            "timeout_seconds": cfg.executor.timeout_seconds,
            "comfyui_url": getattr(cfg.executor, "comfyui_url", ""),
            "search_url": getattr(cfg.executor, "search_url", ""),
            "searxng_url": getattr(cfg.executor, "searxng_url", ""),
            # 沙箱（第 41 班）：配置 + 运行时可用性（模块级缓存 60s，探测 docker info）
            "sandbox": getattr(cfg.executor, "sandbox", "off"),
            "sandbox_image": getattr(cfg.executor, "sandbox_image", "python:3.12-slim"),
            "sandbox_available": await executor_docker_ok(),
        },
        # 设置面板此前只有"模型大脑"，执行环境/界面/数据都看不到（第 29 班补）
        "ui": {"language": cfg.ui.language, "theme": cfg.ui.theme},
        # 视频引擎（第 41 班：从"接口预留"变成正式设置分区）
        "video": {
            "provider": cfg.video.provider,
            "model": cfg.video.model,
            "resolution": cfg.video.resolution,
            "ratio": cfg.video.ratio,
            "audio": cfg.video.audio,
            "api_key_env": cfg.video.api_key_env,
            "key_set": bool(os.environ.get(cfg.video.api_key_env)) if cfg.video.api_key_env else None,
            "engines": {
                name: {
                    "api_key_env": ecfg.get("api_key_env", ""),
                    "model": ecfg.get("model", ""),
                    "resolution": ecfg.get("resolution", ""),
                    "key_set": bool(os.environ.get(ecfg.get("api_key_env") or "")),
                }
                for name, ecfg in cfg.video.engines.items()
            },
        },
        "stats": {"tasks": len(tasks), "data_dir": str(cfg.storage.data_dir)},
        "server": {"host": cfg.server.host, "lan": cfg.server.host in ("0.0.0.0", "::"), "ip": _local_ip()},
        "providers": sorted([
            "anthropic", "claude", "deepseek", "qwen", "glm", "kimi",
            "mimo", "ollama", "openai_compatible", "mock",
        ]),
    }


def _clean_api_key(raw: str | None, env_name: str) -> str:
    """规整并校验用户填的 API Key —— 二十六轮第 7 批（"发消息不回复"事故的直接产物）。

    做两件事：
      ① `strip()`：复制粘贴常带首尾空白/换行（Windows 剪贴板尤其常见）；
      ② **拒绝非 ASCII**：HTTP 请求头只能放 ASCII。含非 ASCII 的 Key 在**填的时候**
         一切正常（构造 provider 只检查"非空"），偏偏只在**真正发起调用**时才炸成
             UnicodeEncodeError: 'ascii' codec can't encode characters in position 7-25
         （7 正是 `"Bearer "` 的长度 ⇒ 报错位置就是 Key 自己的第 1~18 个字符）。
         用户看到的是"发消息不回复 + 一句完全无法据以行动的报错"。
         这里把校验提前到**入口**，换成"第几位、怎么办"的人话。
    """
    key = (raw or "").strip()
    bad = [i for i, ch in enumerate(key) if ord(ch) > 127]
    if bad:
        pos = ", ".join(str(i + 1) for i in bad[:5]) + ("…" if len(bad) > 5 else "")
        raise HTTPException(422, (
            f"API Key 含非 ASCII 字符（第 {pos} 位，共 {len(bad)} 个）——复制时很可能"
            "带进了全角空格、中文标点或换行。请重新复制，只保留英文字母/数字与 - _ . "
            f"等 ASCII 字符。（这个值若原样写进环境变量 {env_name}，每次模型调用都会失败。）"
        ))
    return key


def _key_fingerprint(key: str) -> str:
    """Key 的**指纹**：长度 + 头 4 + 尾 4 ✓ —— 记账只用它，**绝不记全值** ✗。

    为什么要它（★ 2026-10-10 用户实测那件事的产物）：
      他的 MiMo 那格被别家的 Key 覆盖了 ✗ 于是每次任务 401 ✗
      而**账本上一个字都没有** ✗ ⇒ 谁也说不清"什么时候、被写成了什么" ✓
      ⇒ 记指纹就够：拿长度 + 头尾一比对就知道"这是谁家的 Key"✓ 又不泄密 ✓
    """
    k = key or ""
    if len(k) <= 12:
        return f"len={len(k)}（太短，不打头尾）"
    return f"len={len(k)} {k[:4]}…{k[-4:]}"


async def _probe_key(provider: str, base_url: str, key: str) -> tuple[bool, str]:
    """拿这把 Key **真打一次**该服务商 —— 只拦"服务商明确拒收"（401/403）✓ 别的都不拦 ✗。

    为什么只拦 401/403（★ 2026-10-10，用户实测那件事的直接产物）：
      MiMo 那格被换成了别家的 Key ⇒ 每次任务 401 失败 ✗ 而**界面上看不出异常** ✗
      （能力表只查"变量有没有值"✓；模型列表接口把 401 吞了、还回 200 ✗）
      ⇒ 用户只能一个个任务去撞 ✓ ⇒ 保存前当场验一次，拒了就**不保存** ✓

    为什么网络错误**不拦** ✓：不能因为"网不好"就不让用户存 Key ✗
      （那种情况照常保存 ✓ 并在 note 里如实说"没验成"✓）
    """
    import httpx
    prov = (provider or "").strip().lower()
    if prov in ("mock", "ollama"):
        return True, "本地/离线档，不用验 Key"
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return True, "没配 base_url，跳过校验"
    if prov == "anthropic":
        base = base if base.endswith("/v1") else base + "/v1"
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    else:
        headers = {"Authorization": f"Bearer {key}"}
    try:
        async with httpx.AsyncClient(timeout=12) as c:
            r = await c.get(f"{base}/models", headers=headers)
    except Exception as e:                                   # noqa: BLE001
        return True, f"没验成（{type(e).__name__}）—— 网络问题不拦你，已照常保存"
    if r.status_code in (401, 403):
        return False, f"服务商拒收这把 Key（HTTP {r.status_code}）"
    return True, f"Key 校验通过（HTTP {r.status_code}）"


class SettingsLimitsReq(BaseModel):
    """★ 2026-10-06：单任务步数上限 + 单价表 —— 接进设置界面（此前只能改配置文件）。

    · max_iterations：新建任务用它（群任务另按角色分档，见 `_budget_for`）
    · pricing：`{"模型名": {"in": 输入价, "out": 输出价, "cached": 缓存价}}`，单位**元/百万 token**；
      不填就只报 token、不报钱（价格会变，编一个"看起来对"的单价会让人当账单）
    """
    max_iterations: int | None = None
    pricing: dict[str, dict[str, float]] | None = None


class PricingFetchReq(BaseModel):
    """★ 自动查单价：模型名默认取当前配置（可覆盖）；汇率可覆盖。"""
    model: str | None = None
    fx: float | None = None


class SettingsModelReq(BaseModel):
    provider: str
    model_name: str = ""
    base_url: str | None = None
    api_key: str | None = None  # 写入环境变量（进程内 + 可选写 User 级持久化）
    api_key_env: str | None = None
    # ★ 默认 False（保守）：只有调用方**显式要求**才动用户的注册表。
    #   设置页那个勾选框默认勾上 ⇒ 用户看得见、可取消。
    persist: bool = False
    # ★ 2026-10-05（Jev 判定建议 + 用户实测）：把"一次任务最多走多少步"做成可改的。
    #   现场依据：三个群任务把 25 步烧完就失败（"写一整个模块"这种活 25 步不够），
    #   而用户当时**没有任何地方能调**。范围夹 5..200，防手滑写出离谱值。
    max_iterations: int | None = None
    # ★ 2026-10-10（用户实测"切来切去把别家的 Key 存进来"那件事）：
    #   设置页显式要求时，保存前**先拿这把 Key 打一次该服务商** ✓
    #   服务商明确拒收（401/403）⇒ **拒绝保存**并说人话 ✓（网络不通不算 ✗ 照常保存 ✓）
    #   默认 False ⇒ 现有调用方与测试**行为完全不变** ✓
    verify_key: bool = False


@app.post("/api/v1/settings/pricing/fetch")
async def fetch_pricing(req: PricingFetchReq) -> dict[str, Any]:
    """★ 自动查单价（2026-10-06，用户提的："价格随时会变，不能让我手填吧？"）

    **先说实话**：服务商自己的 API **不提供价格** ✗ —— 实测 `{base_url}/models` 只返回
    `id / object / owned_by`，没有单价字段 ✓。所以"连上服务商 API 查价"这条路不存在 ✓。

    **能做的**：查 **OpenRouter 的公开模型表**（无需密钥 ✓，带 prompt/completion 单价 ✓，
    单位是**美元 / 每个 token**）。命中就换算成**元 / 百万 token**填回来，并把
    **来源 + 时间 + 汇率**一起返回 ✓；查不到就**如实说查不到** ✓（不许编一个价 ✗）。

    ★ 价格随时会变 ⇒ 设置页可以随时点一次"重新查询" ✓；返回里带 `fetched_at` ✓。
    """
    model = (req.model or cfg.model.model_name or "").strip()
    if not model:
        return {"found": False, "note": "没指定模型名（也没配当前模型）"}
    # ★ 2026-10-06（用户批评："中国是中国定价，美国是美国定价，不能拿美元换算"）：
    #   **官方价优先** ✓ —— 下面这张表是从官方定价页抄的（带来源链接与更新日期 ✓，可核对）。
    #   只有官方表里没有的模型，才去第三方渠道查参考价 ✓，并在文案里写明"仅供参考" ✓。
    off = pricing.official_for(model)
    if off:
        row = {"in": off["in"], "out": off["out"]}
        if off.get("cached") is not None:
            row["cached"] = off["cached"]
        return {
            "found": True, "model": model, "matched": off["model"], "official": True,
            "pricing": row, "region": off.get("region", "国内"),
            "source": off.get("source", ""), "fetched_at": off.get("updated", _now()),
            "note": (f"**官方价**（{off.get('region', '国内')}，元/百万 tokens）："
                     f"输入 {row['in']} / 输出 {row['out']}"
                     + (f" / 命中缓存 {row['cached']}" if "cached" in row else "")
                     + f"。来源：官方定价页（更新于 {off.get('updated', '?')}）"
                     + (f"。{off['note']}" if off.get("note") else "")),
        }
    fx = float(req.fx) if req.fx else 7.1        # 美元→人民币；只是换算口径，界面会写明
    try:
        import httpx
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get("https://openrouter.ai/api/v1/models",
                                 headers={"User-Agent": "agent-shell/1.0"})
            r.raise_for_status()
            items = (r.json() or {}).get("data") or []
    except Exception as e:
        return {"found": False, "note": f"查价失败（{type(e).__name__}）—— 可手动填官方价"}

    base = model.lower().split(":")[0]
    key = base.split("/")[-1]
    cands = [x for x in items if key and key in str(x.get("id", "")).lower()]
    if not cands:                                # 退化匹配：把 "mimo-v2.6-flash" 拆成关键片段
        parts = [p for p in re.split(r"[-_.]", key) if len(p) > 2]
        cands = [x for x in items
                 if parts and sum(1 for p in parts if p in str(x.get("id", "")).lower()) >= max(2, len(parts) - 1)]
    if not cands:
        # ★ 查不到就把你送到**官方定价页**（各家页面在 pricing.PRICING_PAGES 里有目录 ✓）
        page = pricing.page_for(str(cfg.model.provider or ""), model)
        where = f"官方定价页：{page['url']}（{page['name']}）" if page else "服务商的官方定价页"
        return {"found": False, "model": model,
                "note": (f"公开价格表里没有「{model}」，服务商自己的 API 也不给价 ✗。"
                         f"请照{where}填一次（价格会变，填完这里会记下时间）")}
    best = sorted(cands, key=lambda x: len(str(x.get("id"))))[0]
    price = best.get("pricing") or {}
    try:
        pin = float(price.get("prompt") or 0) * 1e6 * fx       # 美元/token → 元/百万
        pout = float(price.get("completion") or 0) * 1e6 * fx
        pcache = float(price.get("input_cache_read") or 0) * 1e6 * fx
    except (TypeError, ValueError):
        return {"found": False, "model": model, "note": "公开价格表里这个模型的单价格式看不懂 ✗"}
    if pin <= 0 and pout <= 0:
        return {"found": False, "model": model, "note": "公开价格表里这个模型没标价 ✗"}
    row: dict[str, float] = {"in": round(pin, 4), "out": round(pout, 4)}
    if pcache > 0:
        row["cached"] = round(pcache, 4)
    return {
        "found": True, "model": model, "matched": str(best.get("id") or ""),
        "pricing": row, "fx": fx, "usd": {"in": float(price.get("prompt") or 0) * 1e6,
                                          "out": float(price.get("completion") or 0) * 1e6},
        "source": "openrouter.ai/api/v1/models",
        "fetched_at": _now(),
        "official": False,
        "note": (f"⚠️ **第三方渠道参考价**（不是官方价 ✗）：给的是「{best.get('id')}」在 OpenRouter 的挂牌价"
                 f"（美元），按 1 美元={fx} 元换算 ⇒ 输入 {row['in']} / 输出 {row['out']}"
                 + (f" / 缓存 {row['cached']}" if "cached" in row else "")
                 + " 元每百万。**仅供估算，以你服务商的官方账单为准** ✓"),
    }


@app.get("/api/v1/budget")
async def get_budget() -> dict[str, Any]:
    """★ 2026-10-07「每个 Key 花费上限」：现在每把 Key 花了多少、上限多少、还能不能花 ✓。

    口径与限制**照实回话**（见 `app/budget.py` 文件头 ✓）：
      · 账 = 任务里的**用量事件**（与 `/usage` 同一份 ✓ 绝不另记一份 ✗）
      · 上限**只管语言模型**那一档 ✓ —— 出图/出视频/语音是按次或按秒计费、
        本程序拿不到单价 ⇒ **拦不住** ✓ 这里逐条标出来 ✓（默默不管 = 假安心 ✗）
    """
    return budget.status(cfg, list(tasks.keys()), store.read_events)


class BudgetReq(BaseModel):
    """设/清上限。

    · `key` 给哪把 Key（环境变量名 ✓ 如 XIAOMI_MIMO_API_KEY）
    · `limit` 上限（元）；给 0 或负数 = **撤掉这把 Key 的上限** ✓（回到"不拦"✓）
    · `reset_since=True` = **从现在重新计** ✓（老账不删 ✗ 只是不计入本上限 ✓）
    """

    key: str
    limit: float | None = None
    reset_since: bool = False


@app.post("/api/v1/budget")
async def set_budget(req: BudgetReq) -> dict[str, Any]:
    """给某把 Key 设上限 / 撤上限 / 清零重来 ✓（改完**立刻生效** —— 闸门每轮都重新问一次 ✓）。"""
    import datetime as _dt

    key = str(req.key or "").strip()
    if not key:
        raise HTTPException(422, "缺少 key（要设哪把 Key 的上限）")
    caps = dict(getattr(cfg.budget, "caps", {}) or {})
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()

    if req.limit is None and not req.reset_since:
        raise HTTPException(422, "要么给 limit（元），要么 reset_since=true 清零重来")
    if req.limit is not None and req.limit < 0:
        raise HTTPException(422, "上限不能是负数（撤掉上限请填 0）")

    if req.limit is not None and req.limit == 0:
        caps.pop(key, None)                       # 撤掉 = 回到"不拦" ✓
    else:
        old = dict(caps.get(key) or {})
        limit = float(req.limit) if req.limit is not None else float(old.get("limit") or 0)
        since = str(old.get("since") or "")
        if req.reset_since or not since:
            since = now                           # 第一次设 / 主动清零 ⇒ 从此刻开始计 ✓
        caps[key] = {"limit": limit, "since": since}
    cfg.budget.caps = caps
    _save_config()
    st = budget.status(cfg, list(tasks.keys()), store.read_events)
    return {**st, "note": ("已撤掉这把 Key 的上限（回到不拦）" if key not in caps
                           else f"上限已存：{key} ≤ ¥{float(caps[key]['limit']):.2f}"
                                "（从现在起算 ✓ 改了立刻生效 ✓）")}


@app.post("/api/v1/settings/limits")
async def set_limits(req: SettingsLimitsReq) -> dict[str, Any]:
    """★ 2026-10-06：把「单任务最大步数」与「单价表」接进设置界面（此前只能改配置文件）。

    两者都是"用户看得见、但改不了"的典型：
    · 步数上限 —— 代码活要"写-跑-改"迭代，25 步必然被砍在半路（群任务另有按角色分档）
    · 单价表 —— 不填就只报 token、不报钱；填一次之后群里每步都能看到约 ¥X

    生效范围：步数上限对**新建任务**生效（在跑的任务用它启动时的值）；
    单价表**立刻生效**（群里下一行账单就按新价算）。
    """
    if req.max_iterations is not None:
        if not (1 <= req.max_iterations <= 500):
            raise HTTPException(422, "单任务最大步数应在 1–500 之间")
        cfg.model.max_iterations = int(req.max_iterations)
    if req.pricing is not None:
        pricing.configure(req.pricing)          # 立刻生效（估算用）
        cfg.pricing = {str(k): dict(v) for k, v in req.pricing.items() if isinstance(v, dict)}
    _save_config()          # 与其它设置端点同一个写回落点（测试隔离的根也一致）
    priced = sorted(pricing.PRICES.keys())
    note = ("单价表已生效：" + "、".join(priced)) if priced else "单价表为空 —— 群里只报 token，不报钱"
    return {"ok": True, "max_iterations": cfg.model.max_iterations, "pricing": cfg.pricing, "note": note}


@app.post("/api/v1/settings/model")
async def set_model(req: SettingsModelReq):
    """切换模型提供者；若带 api_key 则写入进程环境变量。

    ★ Phase 1 ①（2026-10-04）：`persist=True` 时**同时写 User 级环境变量**
      （`HKCU\\Environment`，免管理员、不落盘）—— 这是"重启后还认得你"的唯一正确落点：
      此前只写进程内 `os.environ`，后端一重启 Key 就没了 ⇒ 界面能开、发消息永远不回复
      （当天血案：四个 PID 全是这个原因）。默认 False（保守），设置页的勾选框默认勾上。
    """
    from .providers import _REGISTRY
    if req.provider not in _REGISTRY:
        raise HTTPException(422, f"未知提供者：{req.provider}（可用：{sorted(_REGISTRY)}）")
    # ★ 2026-10-10：设置页要求"先验后存"时 —— 服务商明确拒收就**原地拒绝** ✓
    #   在改 cfg **之前**验 ✓ 拒了 ⇒ 配置一个字都没动 ✓ 干净 ✓
    verify_note = ""
    if req.verify_key and req.api_key:
        _ok, verify_note = await _probe_key(
            req.provider,
            (req.base_url if req.base_url is not None else cfg.model.base_url),
            _clean_api_key(req.api_key, req.api_key_env or cfg.model.api_key_env or ""))
        if not _ok:
            audit.record("key_write", provider=req.provider,
                         env=req.api_key_env or cfg.model.api_key_env, result="rejected",
                         fingerprint=_key_fingerprint(req.api_key), detail=verify_note)
            raise HTTPException(400, (
                f"没有保存：{verify_note}。\n"
                f"请确认这是【{req.provider}】自己的 Key —— 把别家的 Key 存进来，"
                "这家以后每次调用都会 401（而界面上看不出异常）。"
            ))
    m = cfg.model
    m.provider = req.provider
    if req.model_name:
        m.model_name = req.model_name
    if req.base_url is not None:
        m.base_url = req.base_url
    # ★ 2026-10-10（接本地 Bonsai 时发现的缺口 ✗）：
    #   此前只在 `req.api_key_env` **非空**时才写 ⇒ 换成"本地无需 Key"的服务时
    #   这一格**清不掉** ✗（会留着上一家的变量名 ✓ 语义错 ✓ 虽然本地服务多半忽略鉴权 ✓）
    #   ⇒ 现在：显式传了空串就**清空**（None 仍然表示"这次不动它" ✓ 向后兼容 ✓）
    if req.api_key_env is not None:
        m.api_key_env = req.api_key_env.strip()
    persisted = False
    note = "配置已保存。Key 已写入当前进程；要永久保存请在系统环境变量中设置。"
    # ★ 步数上限（用户实测"25 步烧完就失败"，Jev 判定也建议做成可配）：夹 5..200
    if req.max_iterations is not None:
        m.max_iterations = max(5, min(200, int(req.max_iterations)))
        note = f"已把一次任务最多走的步数设为 {m.max_iterations}。" + note
    if req.api_key and m.api_key_env:
        # 进程内生效。
        # ★ 第 7 批：先 strip + 拒非 ASCII（非 ASCII 的 Key 只在真调用时才炸，见 _clean_api_key）
        cleaned = _clean_api_key(req.api_key, m.api_key_env)
        os.environ[m.api_key_env] = cleaned
        if req.persist:
            ok, why = write_user_env(m.api_key_env, cleaned)      # ← 不回显 Key
            persisted = ok
            note = ("配置已保存。" + why +
                    ("；当前进程已立即生效。" if ok else "；当前进程仍可用，但重启后会丢。"))
        # ★ 2026-10-10（第二期，照 DSH 的做法）：**同时存进凭据库** ✓
        #   一个文件、按名字存、写前备份 ✓ ⇒ 下次"哪把 Key 被谁覆盖了"一眼可查 ✓
        #   ★ 只加不减 ✗：环境变量（进程内 + 可选注册表）那条路原样保留 ✓
        cred_ok, cred_why = credentials_store.set(m.api_key_env, cleaned)
        note = note + ("；" + cred_why if cred_ok else "；凭据库没写进去（" + cred_why + "）")
        # ★ 2026-10-10：**每次写 Key 都记账** ✓（只记指纹 ✗ 不记全值 ✓）——
        #   下次再出"哪把 Key 被谁覆盖了"这种事，翻账本一眼就看到 ✓
        audit.record("key_write", provider=m.provider, env=m.api_key_env, result="saved",
                     persisted=bool(req.persist), credentials=bool(cred_ok),
                     fingerprint=_key_fingerprint(cleaned),
                     detail=verify_note or "调用方没要求校验")
        if verify_note:
            note = verify_note + "。" + note
    _save_config()
    return {"ok": True, "persisted": persisted, "key_env": m.api_key_env, "note": note}


class SettingsVideoReq(BaseModel):
    """视频引擎配置（第 41 班）：provider/模型/档位 + 主 Key + 多引擎 Key（像模型设置一样在页面填）。"""
    provider: str | None = None
    model: str | None = None
    resolution: str | None = None
    ratio: str | None = None
    audio: bool | None = None
    api_key_env: str | None = None
    api_key: str | None = None  # 写入进程环境变量（不落盘；持久化靠系统环境变量）
    engines: dict[str, dict[str, Any]] | None = None  # 引擎名 → {api_key_env?, model?, resolution?, api_key?}


_VIDEO_PROVIDERS = {"", "minimax", "wan", "seedance", "kling"}


@app.post("/api/v1/settings/video")
async def set_video(req: SettingsVideoReq) -> dict[str, Any]:
    v = cfg.video
    if req.provider is not None:
        if req.provider not in _VIDEO_PROVIDERS:
            raise HTTPException(422, f"未知视频引擎：{req.provider}（可用：{sorted(p for p in _VIDEO_PROVIDERS if p)}）")
        v.provider = req.provider
    if req.model is not None:
        v.model = req.model.strip()
    if req.resolution is not None:
        v.resolution = req.resolution.strip() or "480P"
    if req.ratio is not None:
        v.ratio = req.ratio.strip() or "16:9"
    if req.audio is not None:
        v.audio = req.audio
    if req.api_key_env is not None:
        v.api_key_env = req.api_key_env.strip()
    if req.api_key and v.api_key_env:
        # ★ 第 7 批：同模型设置——strip + 拒非 ASCII（见 _clean_api_key）
        os.environ[v.api_key_env] = _clean_api_key(req.api_key, v.api_key_env)
    if req.engines is not None:
        for name, ecfg in req.engines.items():
            if name not in {"minimax", "wan", "seedance", "kling", "comfyui"}:
                raise HTTPException(422, f"未知引擎名：{name}")
            env_name = str(ecfg.get("api_key_env") or "").strip()
            entry: dict[str, Any] = {"api_key_env": env_name}
            if ecfg.get("model"):
                entry["model"] = str(ecfg["model"]).strip()
            if ecfg.get("resolution"):
                entry["resolution"] = str(ecfg["resolution"]).strip()
            key = str(ecfg.get("api_key") or "")
            if key and env_name:
                # ★ 第 7 批：同模型设置——strip + 拒非 ASCII（见 _clean_api_key）
                os.environ[env_name] = _clean_api_key(key, env_name)
            # 空 api_key_env 的条目 = 用户没配这个引擎 → 清掉
            if env_name:
                v.engines[name] = entry
            else:
                v.engines.pop(name, None)
    _save_config()
    return {
        "ok": True,
        "note": "配置已保存（config.json 只存环境变量名，Key 本身不落盘）。Key 已写入当前进程即刻生效；"
                "要永久保存请在系统环境变量中设置同名变量后重启后端。",
    }


_docker_cache: tuple[float, bool] | None = None  # (monotonic, ok) —— 设置页探测缓存 60s


async def executor_docker_ok() -> bool:
    """沙箱可用性探测（docker info），结果缓存 60 秒。"""
    global _docker_cache
    import time as _t

    now = _t.monotonic()
    if _docker_cache is not None and now - _docker_cache[0] < 60:
        return _docker_cache[1]
    try:
        from .executors.local import LocalExecutor

        probe = LocalExecutor.__new__(LocalExecutor)  # 只用探测方法，不初始化完整执行器
        probe._docker_ok = None
        probe._docker_at = 0.0
        ok = await probe._docker_available()
    except Exception:
        ok = False
    _docker_cache = (now, ok)
    return ok


class SettingsExecutorReq(BaseModel):
    allowed_dirs: list[str] | None = None
    approval_required: list[str] | None = None
    timeout_seconds: float | None = None
    searxng_url: str | None = None
    sandbox: str | None = None
    sandbox_image: str | None = None


@app.post("/api/v1/settings/executor")
async def set_executor(req: SettingsExecutorReq) -> dict[str, Any]:
    """改执行环境（授权目录 / 审批动作清单 / 命令超时）—— 第 30 班：设置面板从只读变可写。

    ⚠️ **这些值在后端启动时被 executor 实例读走**，所以保存后对**新起的进程**才完全生效。
    返回的 note 里写明这一点，界面也要提示用户重启。
    """
    ex = cfg.executor
    if req.allowed_dirs is not None:
        cleaned = [d.strip() for d in req.allowed_dirs if d and d.strip()]
        ex.allowed_dirs = [Path(d) for d in cleaned]
    if req.approval_required is not None:
        # 归一化：去空白 + 小写（审批判定本身就是小写比较，避免用户填 "RM" 不生效）
        ex.approval_required = [w.strip().lower() for w in req.approval_required if w and w.strip()]
    if req.timeout_seconds is not None:
        if not (1 <= req.timeout_seconds <= 3600):
            raise HTTPException(422, "timeout_seconds 必须在 1–3600 秒之间")
        ex.timeout_seconds = float(req.timeout_seconds)
    if req.searxng_url is not None:
        u = req.searxng_url.strip().rstrip("/")
        if u and not u.startswith(("http://", "https://")):
            raise HTTPException(422, "searxng_url 必须以 http(s):// 开头")
        ex.searxng_url = u
    if req.sandbox is not None:
        s = req.sandbox.strip().lower()
        if s not in ("off", "docker"):
            raise HTTPException(422, "sandbox 只能是 off 或 docker")
        ex.sandbox = s
    if req.sandbox_image is not None:
        img = req.sandbox_image.strip()
        if img:
            ex.sandbox_image = img
    _save_config()
    return {
        "ok": True,
        "note": "已写入 config.json。授权目录与审批清单在**后端重启**后对新的执行器实例生效。",
    }


def _save_config() -> None:
    # ★ 第 8f 处：路径统一用模块级 `_CONFIG_PATH`（原先这里自己算 `__file__` 的路径，
    #   于是测试里替换 `_CONFIG_PATH` **拦不住它** —— 本班实测：一条本意只碰 tmp 的
    #   测试把用户真实的 config.json 给写了。统一成一处，测试才能真正隔离。）
    _CONFIG_PATH.write_text(
        json.dumps(cfg.model_dump(), ensure_ascii=False, indent=2, default=str), "utf-8")

# ---------- ASR（语音转文字） ----------


async def _transcribe(audio_path: Path) -> str:
    from .asr import transcribe
    # 传 cfg：ASR 现在是两档（云端 MiMo / 本地千问3），档位从 config.asr 读
    return await transcribe(audio_path, cfg=cfg)


async def _to_wav(audio_path: Path) -> Path:
    """任意音频 → wav（16k 单声道）：ASR 网关只收 wav/mp3，而浏览器 MediaRecorder
    默认产 webm——统一 ffmpeg 转码（第 41 班）。

    ★★ 2026-10-07 修（用户报"会议录不进去"的真根因之一 ✗✗ 本班实测量出来的 ✓）：
      原来转不了就 `return audio_path` **静默退回原文件** ✗ ⇒ 一个 webm 被当成音频
      一路送到 ASR ⇒ 本地那档 soundfile 直接
        `LibsndfileError: Error opening '...webm': Format not recognised.`（实测原话 ✓）
      ⇒ 用户只看到"没反应" ✗ 而**真正的原因（本机没 ffmpeg）一个字都没露出来** ✗✓。
      ⇒ 现在：**转不出来就说清楚**（是什么格式 ✓ 缺什么 ✓ 两条怎么修 ✓），
        绝不把"注定读不了的文件"再往下传 ✓（那种失败最难查 ✓）。
      （前端也已改成浏览器侧直接录 WAV —— 这条路**不依赖 ffmpeg** ✓ 见 lib/recwav.ts ✓）
    """
    if audio_path.suffix.lower() in (".wav", ".mp3"):
        return audio_path
    out = audio_path.with_suffix(".conv.wav")
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", str(audio_path), "-ar", "16000", "-ac", "1", str(out),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=30)
        if proc.returncode == 0 and out.exists():
            return out
    except (asyncio.TimeoutError, FileNotFoundError):
        pass
    # 转码没成 —— 分清"内容本来就是 wav/mp3（只是后缀不叫这个）"与"真转不了" ✓
    from .asr import sniff_audio_format
    if sniff_audio_format(audio_path) in ("wav", "mp3"):
        return audio_path
    raise RuntimeError(
        f"这段音频是 {sniff_audio_format(audio_path)} 格式，而本机没有 ffmpeg 可供转码，"
        "语音识别读不了它。两条路：① 用界面里的录音按钮（浏览器直接录成 WAV，不需要 ffmpeg）；"
        "② 装上 ffmpeg 后重试。"
    )

@app.get("/api/v1/memory")
async def memory_all() -> dict[str, Any]:
    return {"enabled": cfg.memory.enabled, "count": len(_memory_store.all()), "entries": _memory_store.all()[-50:]}


class MemoryReq(BaseModel):
    enabled: bool | None = None
    clear: bool = False


@app.get("/api/v1/memory/export")
async def memory_export() -> Any:
    """★ 2026-10-07（用户："数据在你手里"✓）：**记忆库一键导出** ✓。

    为什么要有它（不是可有可无 ✓）：
      · 记忆库是这项目里**最像"你的东西"**的一份数据 ✓（记的是你的偏好/身份/纠错 ✓）
      · 它只存在本机 ✓ 但**用户得有办法把它拿走** ✓（换机器 ✓ 备份 ✓ 想看看里面记了啥 ✓）
      · 导出的是**原始 JSON** ✓ 不是截图/摘要 ✗ —— 拿到就能直接读 ✓
    ⚠️ 内容**已经过打码管线** ✓（写进来时就打了 ✓ 见 memory.py ✓）
    """
    import json as _json
    data = _memory_store.all()
    body = _json.dumps({"exported_at": _now(), "count": len(data), "entries": data},
                       ensure_ascii=False, indent=1)
    return Response(content=body, media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="memory-export.json"'})


@app.get("/api/v1/kb/{name}/export")
async def kb_export(name: str) -> Any:
    """★ 知识库一键导出 ✓ —— 打成 **zip** ✓（含 manifest + 切块 + 一份说明 ✓）。

    为什么是 zip（而不是把 chunks.json 直接吐出来 ✗）：
      · 一个库两个文件（`manifest.json` + `chunks.json`）✓ 少哪个都不完整 ✗
      · `chunks.json` 可能很大（5000 块能到 ~100MB ✓）✓ zip 能压一部分 ✓
      · 用户拿到的是**一个能直接存档的文件** ✓ 不是要自己去拼的目录 ✓
    """
    import io
    import zipfile
    kb = _kb_store.get(name)
    if kb is None:
        raise HTTPException(404, f"没有这个知识库：{name}")
    d = _kb_store.root / name
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for fname in ("manifest.json", "chunks.json"):
            f = d / fname
            if f.exists():
                z.write(f, fname)
        z.writestr("README.txt",
                   f"知识库：{name}\n导出时间：{_now()}\n块数：{kb.get('chunks')}\n"
                   f"文件数：{kb.get('files')}\n说明：{kb.get('description')}\n\n"
                   "这个包是【数据】不是代码：manifest.json 是元信息，chunks.json 是切块与向量。\n"
                   "（换了向量模型之后，旧向量要重新入库才可用。）\n")
    buf.seek(0)
    # ★★ 2026-10-07（**测试当场抓到的真 bug** ✗✗）：
    #   HTTP 头只能放 **latin-1** ✓ 而"知识库名"**多半是中文** ✗
    #   ⇒ 原来直接 `filename="{name}.zip"` ⇒ **UnicodeEncodeError ⇒ 导出直接崩** ✗✓
    #   （中文名是常态 ✓ 也就是说这个功能对绝大多数人**一用就报错** ✗）
    #   ⇒ 用 RFC 5987 的写法 ✓：ASCII 兜底名 + `filename*=UTF-8''<百分号编码>` ✓✓
    #      —— 浏览器优先用后者 ⇒ 用户拿到的还是**中文名** ✓
    import urllib.parse as _up
    safe = "".join(c for c in name if c.isascii() and c not in '\\/:*?"<>|').strip() or "kb"
    quoted = _up.quote(f"{name}.zip")
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition":
                             f"attachment; filename=\"{safe}.zip\"; filename*=UTF-8''{quoted}"})


@app.post("/api/v1/memory")
async def memory_update(req: MemoryReq) -> dict[str, Any]:
    if req.enabled is not None:
        cfg.memory.enabled = req.enabled
        _save_config()
    cleared = _memory_store.clear() if req.clear else 0
    return {"ok": True, "enabled": cfg.memory.enabled, "cleared": cleared}


# ---------- 知识库（第 41 班 v2） ----------


@app.get("/api/v1/kb")
async def kb_list_all() -> dict[str, Any]:
    return {"kbs": _kb_store.list()}


class KbIngestReq(BaseModel):
    name: str
    folder: str
    description: str = ""


@app.post("/api/v1/kb/ingest")
async def kb_ingest(req: KbIngestReq) -> dict[str, Any]:
    name = req.name.strip()[:40]
    if not name or "/" in name or "\\" in name:
        raise HTTPException(422, "知识库名不能为空且不含路径分隔符")
    try:
        man = await _kb_store.ingest_folder(name, Path(req.folder), req.description)
        return {"ok": True, "manifest": man}
    except ValueError as e:
        raise HTTPException(422, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))


class KbSearchReq(BaseModel):
    query: str
    name: str | None = None
    top_k: int = 5


@app.post("/api/v1/kb/search")
async def kb_search_api(req: KbSearchReq) -> dict[str, Any]:
    try:
        results = await _kb_store.search(req.query, req.name, req.top_k)
        return {"ok": True, "results": results}
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@app.delete("/api/v1/kb/{name}")
async def kb_delete(name: str) -> dict[str, Any]:
    # §2.2 路径穿越防护（第 41 班审计）：resolve 后必须在 root 内
    root = _kb_store.root.resolve()
    safe = (root / name).resolve()
    if not safe.is_relative_to(root):
        raise HTTPException(403, f"路径越界：{name}")
    return {"ok": _kb_store.delete(name)}


# ---------- 团队群聊（第 41 班） ----------


@app.get("/api/v1/team/roles")
async def team_roles() -> dict[str, Any]:
    """角色库（前端只拿角色/部门/一句话简介——完整专家人设不外发）。

    ★ 2026-10-07：每个角色**多带一份权限说明** ✓（`permissions.describe` ✓）——
      身份选择器上要**选之前就看得出**"这个角色不能改东西" ✓
      （选完才发现 = 用户会以为界面坏了 ✗ 本项目栽过多次这种"事后才知道"✓）。
    """
    from .roles import roster_for_frontend

    roles = [{**r, **permissions.describe(str(r.get("role") or ""))} for r in roster_for_frontend()]
    return {"roles": roles, "max": MAX_EMPLOYEES}


@app.get("/api/v1/roles/suggest")
async def roles_suggest(input: str = "") -> dict[str, Any]:
    """★ 2026-10-07（第 8 项）：**这句话该派谁** ✓ —— 24 个角色摆着，新用户不知道选哪个 ✓。

    ★ 两条口径（与 `roles.suggest_roles` 一致 ✓ 不另立一套 ✗）：
      · **只在命中关键词时才推** ✓ 匹配不上就返回空数组 ✓（乱推比不推更烦人 ✓）
      · 每条都带 `why` ✓（"因为你提到了「测试」"✓ —— 用户才知道该不该听 ✓）
    """
    from .roles import suggest_roles

    return {"ok": True, "input": str(input or "")[:200], "suggestions": suggest_roles(input)}


@app.get("/api/v1/audit")
async def get_audit(limit: int = 200, kind: str = "") -> dict[str, Any]:
    """★ 2026-10-07（第 9 项）：**跨任务审计流水** ✓ —— 回答"这一个月我批过什么"✓。

    · 只记**需要有人负责**的那些（审批决议 / 放权与收回 / 被闸门挡下 / 任务起止 ✓）
    · **不记**对话内容与工具输出 ✗（那是任务事件流的活 ✓）
    · 最新的在前 ✓ 可按类别过滤 ✓
    · 命令文本**已打码** ✓（可能带密钥 ✓ 与全项目同一套 ✓）
    """
    return {"ok": True, "entries": audit.tail(limit=limit, kind=kind), **audit.stats()}


@app.get("/api/v1/team/employees")
async def team_employees() -> dict[str, Any]:
    # ★ 第 7b 处：这里是【团队员工总数】的上限，用 MAX_EMPLOYEES（200），
    #   不是群聊的 MAX_MEMBERS（12）——此前两者共用同一个数，导致加第 13 个员工就被拒。
    return {"employees": _team_store.employees(), "max": MAX_EMPLOYEES}


class EmployeeReq(BaseModel):
    name: str
    dept: str = "综合部"
    role: str = "通用助理"
    mode: str = "expert"  # expert=按角色人设（persona 忽略）/ free=自由人设（persona 生效）
    persona: str = ""
    provider: str | None = None
    model_name: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    color: str = "#6ba3f5"


@app.post("/api/v1/team/employees", status_code=201)
async def team_add_employee(req: EmployeeReq) -> dict[str, Any]:
    try:
        return {"ok": True, "employee": _team_store.add_employee(req.model_dump())}
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/v1/team/employees/import-roles", status_code=201)
async def team_import_missing_roles() -> dict[str, Any]:
    """★ 2026-10-07：**把角色库里还没建卡的角色一键补成员工卡** ✓。

    背景（用户提的）：角色库有 **24 个专家角色** ✓ 但他的员工卡只建了 12 个 ✗
      ⇒ 剩下 12 个"有角色、没卡"✓ 想用还得一个个手建 ✓ 太麻烦 ✓
      ⇒ 做成**一个按钮** ✓ 点了就补齐 ✓

    ★ 三条规矩（与项目一贯口径一致 ✓）：
      ① **只补缺的** ✓ 已有同名卡**绝不动** ✗（不覆盖用户改过的人设 ✓）
      ② **只建卡、不建群** ✓（群是他自己拉的 ✓ 不替他做决定 ✓）
      ③ 建卡走**和手动建卡同一条路**（`add_employee` ✓）
         ⇒ 校验、上限、去重规则**完全一致** ✓ 不是另开一条后门 ✗
    """
    from .roles import ROLE_LIBRARY
    existing = {str(e.get("name") or "") for e in _team_store.employees()}
    added, skipped = [], []
    for role_name, spec in ROLE_LIBRARY.items():
        if role_name in existing:
            skipped.append(role_name)
            continue
        try:
            emp = _team_store.add_employee({
                "name": role_name,
                "dept": str(spec.get("dept") or "综合部"),
                "role": role_name,
                "mode": "expert",          # 按角色人设 ✓（角色库自带 persona ✓）
                "persona": "",
            })
            added.append(emp.get("name") or role_name)
        except ValueError as e:            # 撞上限/重名 ⇒ 如实说，不硬塞 ✓
            return {"ok": False, "added": added, "skipped": skipped,
                    "detail": f"加到「{role_name}」时停了：{e}",
                    "note": f"已补 {len(added)} 个 ✓ 剩下的下次再点 ✓（原因见 detail ✓）"}
    return {"ok": True, "added": added, "skipped": skipped, "total": len(ROLE_LIBRARY),
            "note": (f"补了 {len(added)} 个角色的员工卡 ✓"
                     + (f"；{len(skipped)} 个已经有卡、一个没动 ✓" if skipped else "")
                     + "　（只是建卡 ✓ 群还是你自己拉 ✓）")}


@app.put("/api/v1/team/employees/{emp_id}")
async def team_update_employee(emp_id: str, req: EmployeeReq) -> dict[str, Any]:
    try:
        return {"ok": True, "employee": _team_store.update_employee(emp_id, req.model_dump())}
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.delete("/api/v1/team/employees/{emp_id}")
async def team_del_employee(emp_id: str) -> dict[str, Any]:
    return {"ok": _team_store.delete_employee(emp_id)}


@app.get("/api/v1/team/groups")
async def team_groups() -> dict[str, Any]:
    # max_members = 【单个群聊】的成员上限（与"团队总人数"MAX_EMPLOYEES 是两回事，见第 7b 处）
    return {"groups": _team_store.groups(), "max_members": MAX_MEMBERS}


class GroupReq(BaseModel):
    name: str
    members: list[str]
    leader: str | None = None  # 组长员工 id（组长拆解模式）
    mode: str = "manual"  # manual=点名派 / broadcast=广播 / leader=组长拆解


@app.post("/api/v1/team/groups", status_code=201)
async def team_create_group(req: GroupReq) -> dict[str, Any]:
    try:
        return _team_store.create_group(req.name, req.members, req.leader, req.mode)
    except ValueError as e:
        raise HTTPException(422, str(e))


class GroupUpdateReq(BaseModel):
    """★ 第 7c 处：建群后改配置。只传想改的字段；都不传 = 只读回当前群。"""

    mode: str | None = None                                    # manual/broadcast/leader
    leader: str | None = None                                  # "" = 清掉组长


@app.put("/api/v1/team/groups/{gid}")
async def team_update_group(gid: str, req: GroupUpdateReq) -> dict[str, Any]:
    """建群后也能改派发模式 / 换组长（此前 mode 只能在建群时定死）。"""
    try:
        if req.leader is not None:
            _team_store.set_leader(gid, req.leader.strip() or None)
        if req.mode is not None:
            return _team_store.set_mode(gid, req.mode)
        g = _team_store.get_group(gid)
        if g is None:
            raise ValueError("群不存在")
        return g
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.delete("/api/v1/team/groups/{gid}")
async def team_delete_group(gid: str) -> dict[str, Any]:
    return {"ok": _team_store.delete_group(gid)}


@app.get("/api/v1/team/groups/{gid}/approvals")
async def group_pending_approvals(gid: str) -> dict[str, Any]:
    """★★ 2026-10-07（第 10 项 · 多审批汇总）：**这个群里所有正在等的审批** ✓

    为什么要它：一个群同时开几个任务时，审批卡片会**一条条刷屏** ✗
      · 用户得**一个个点** ✓
      · 而且**看不出还有几条在等** ✗（点完一条才发现下面还有 ✓）
      · 更糟的是：**任务各自卡着** ✓ 他以为"就这一个" ✓ 其实后面排着仨 ✓
    ⇒ 这个接口一次把"**还差几个决定**"给全 ✓ 界面据此汇总成一块 ✓

    口径与 `_gid_of_task` **同源、反着走** ✓：
      群里的任务 = 这个群 feed 里出现过的 `task_id` ✓（不另立一套 ✗）
    命令取自 `approval.command_of` ✓ —— 就是我这一轮刚补上的那份**服务端记的命令** ✓
      （不是界面传的 ✓ 所以它可信 ✓）
    """
    g = _team_store.get_group(gid)
    if not g:
        raise HTTPException(404, f"群不存在：{gid}")
    tids: list[str] = []
    for m in _team_store.feed(gid):
        t = str(m.get("task_id") or "")
        if t and t not in tids:
            tids.append(t)
    out: list[dict[str, Any]] = []
    for tid in tids:
        for cid in approval.pending_for(tid):
            t = tasks.get(tid)
            out.append({
                "task_id": tid,
                "call_id": cid,
                "title": str(getattr(t, "title", "") or "")[:80],
                "command": approval.command_of(tid, cid)[:300],
            })
    return {"ok": True, "group_id": gid, "count": len(out), "approvals": out}


@app.get("/api/v1/team/groups/{gid}/feed")
async def team_feed(gid: str, after_seq: int = 0) -> dict[str, Any]:
    if _team_store.get_group(gid) is None:
        raise HTTPException(404, "群不存在")
    return {"messages": _team_store.feed_after(gid, after_seq)}


class SoloChatReq(BaseModel):
    text: str


@app.post("/api/v1/team/employees/{emp_id}/say", status_code=201)
async def team_solo_say(emp_id: str, req: SoloChatReq) -> dict[str, Any]:
    """单独聊：直接和某位员工对话（不起群），返回任务 id 供前端打开。"""
    emp = _team_store.get_employee(emp_id)
    if emp is None:
        raise HTTPException(404, "员工不存在")
    text = req.text.strip()
    if not text:
        raise HTTPException(422, "内容不能为空")
    sys_extra = (
        f"你是「{emp['name']}」，团队{emp.get('dept', '')}的{emp.get('role', '成员')}。" + chr(10)
        + _team_store.persona_for(emp) + chr(10)
        + "回复要简洁、直接解决问题。"
    )
    try:
        mc = cfg.model
        if emp.get("provider"):
            mc = cfg.model.model_copy(update={
                "provider": emp["provider"],
                "model_name": emp.get("model_name") or mc.model_name,
                "base_url": emp.get("base_url") or mc.base_url,
                "api_key_env": emp.get("api_key_env") or mc.api_key_env,
            })
        provider = create_provider(mc)
    except Exception as e:
        raise HTTPException(503, f"该员工的大脑不可用：{type(e).__name__}: {str(e)[:120]}")
    task = _launch_task(f"【{emp['name']}】{text}", None, provider=provider, system_extra=sys_extra,
                        role=str(emp.get("role") or ""))   # ★ 2026-10-07：单独聊也按这个角色的权限 ✓
    return {"ok": True, "task_id": task.id}


class IdentityReq(BaseModel):
    role: str = ""  # 空 = 恢复默认助手


@app.post("/api/v1/tasks/{task_id}/identity")
async def set_task_identity(task_id: str, req: IdentityReq) -> dict[str, Any]:
    """切换本会话身份（第 41 班）：选身份立即注入人设，选默认立即恢复——无需用户再用文字说明。"""
    task = _get_task(task_id)
    role = req.role.strip()
    if role:
        from .roles import ROLE_LIBRARY
        lib = ROLE_LIBRARY.get(role)
        if lib is None:
            raise HTTPException(422, f"未知身份：{role}")
        msg_content = f"[身份切换] 你现在以「{role}」的身份执行后续任务。" + chr(10) + lib["persona"]
    else:
        msg_content = "[身份切换] 用户已取消专家身份，恢复为 LanternLogic Agent 默认助手，忽略之前任何身份设定。以正常方式回答。"
    run = runs.get(task_id)
    if run and run.aio_task and not run.aio_task.done():
        run.history.append({"role": "system", "content": msg_content})
        run._save_history()
        run.role = role                        # ★ 2026-10-07：**正在跑的这一次**立刻改 ✓
        run._role_readonly = permissions.is_readonly(role)
    else:
        hist = normalize_history(store.load_history(task_id))
        hist.append({"role": "system", "content": msg_content})
        store.save_history(task_id, hist)
    store.set_identity(task_id, role)          # ★ 落盘：下一次运行（含群里接力）也认这个身份 ✓
    prof = permissions.describe(role)
    _emit_standalone(task_id, "status", {
        "state": task.status,
        "detail": f"身份已切换：{role or '默认助手'}" + (f"（{prof['label']}）" if role else ""),
    })
    return {"ok": True, "identity": role or "默认助手", **prof}


class TeamSayReq(BaseModel):
    text: str



@app.post("/api/v1/team/groups/{gid}/meeting/resummarize")
async def team_meeting_resummarize(gid: str) -> dict[str, Any]:
    """**重新出纪要**（2026-10-05 用户实测后加的）。

    为什么要它：收口那一步是"最后一次模型调用"，最容易撞上瞬时空响应 ——
    旧实现直接把它写成"（收口失败：…）"，等于**整场会白开**（讨论记录还在，就是没纪要）。
    现在：① 收口自带重试与兜底整理；② 还不行/已经坏掉的存量会议，可以用这个接口**再收一次口**，
    讨论记录不用重来（也就不再烧一遍讨论的钱）。
    """
    g = _team_store.get_group(gid)
    if g is None:
        raise HTTPException(404, "群不存在")
    # ★ 先补认领（后端重启丢看门 ⇒ 卡住的链在用户下次说话时自动往前走）
    _reconcile_leader(gid)
    tr = list(g.get("meeting_transcript") or [])
    if not tr:
        raise HTTPException(422, "这个群还没有会议记录，先开会再收口")
    moderator = _team_store.get_employee(g.get("leader") or "") if g.get("leader") else None
    if moderator is None:
        for m in g.get("members") or []:
            moderator = _team_store.get_employee(m)
            if moderator is not None:
                break
    summary = ""
    if moderator is not None:
        try:
            summary = str(await _employee_say(moderator, _team_store.meeting_summary_prompt(g)) or "").strip()
        except Exception as e:
            short = dict(g, meeting_transcript=tr[-6:] if len(tr) > 6 else tr)
            try:
                summary = str(await _employee_say(moderator, _team_store.meeting_summary_prompt(short)) or "").strip()
            except Exception:
                summary = _meeting_fallback_digest(g, type(e).__name__)
    if not summary.strip():
        summary = _meeting_fallback_digest(g, "模型返回空纪要")
    if _summary_grounding(g, summary) < SUMMARY_GROUNDING_MIN:
        summary = _meeting_fallback_digest(g, "纪要内容与讨论记录对不上（疑似跑题）")
    _team_store.append(gid, **{"from": "system", "text": "📋 会议纪要（重新收口）" + chr(10) + summary})
    _team_store.meeting_finish(gid, summary, "手动重新收口")
    return {"ok": True, "summary": summary}
@app.post("/api/v1/team/groups/{gid}/say")
async def team_say(gid: str, req: TeamSayReq) -> dict[str, Any]:
    """群聊发言：@名字 点派任务（可多个 @，各自派发）。无 @ 则提示用法。"""
    g = _team_store.get_group(gid)
    if g is None:
        raise HTTPException(404, "群不存在")
    # ★ 先补认领（后端重启丢看门 ⇒ 卡住的链在用户下次说话时自动往前走）
    _reconcile_leader(gid)
    text = req.text.strip()
    if not text:
        raise HTTPException(422, "内容不能为空")

    # ★ P0-4 群命令：直接说「接着跑」⇒ 把最近一件没做完的活续上（不用去任务页点）
    if re.fullmatch(r"\s*(接着跑|继续跑|继续|接着干|resume)\s*[。.!！]?\s*", text or ""):
        item = _latest_resumable(gid)
        if item is None:
            _team_store.append(gid, **{"from": "system",
                                       "text": "这个群里没有需要接着跑的活（最近的交付都完成了）"})
            return {"ok": True, "mode": "resume", "task_id": None}
        _team_store.append(gid, **{"from": "user", "text": text})
        out = _do_resume(gid, str(item["task_id"]), str(item.get("name") or ""))
        return {"ok": True, "mode": "resume", **out}

    members = {e["name"]: e for e in _team_store.employees() if e["id"] in g["members"]}
    mentioned = []
    for nm in members:
        if f"@{nm}" in text:
            mentioned.append((nm, members[nm]))
    _team_store.append(gid, **{"from": "user", "text": text})

    # 组长模式：无 @ 点名 + 群设了组长 → 组长自动分解分派
    if not mentioned and g.get("leader") and g.get("mode") == "leader":
        leader_emp = _team_store.get_employee(g["leader"])
        if leader_emp is None:
            _team_store.append(gid, **{"from": "system", "text": "⚠️ 组长不存在，无法自动分工"})
            return {"ok": True, "dispatched": 0}
        _team_store.append(gid, **{"from": f"emp:{leader_emp['name']}", "text": "收到目标，正在拆解分工…", "status": "running"})
        try:
            prompt = _team_store.leader_dispatch_prompt(g, text)
            # 组长用自己的大脑做分解
            plan_raw = await _leader_plan(leader_emp, prompt)
            plan = _team_store.parse_leader_plan(plan_raw)
            if not plan:
                # ★ 2026-10-05 用户实测：组长常把计划写成**人话**（"已拆出13项…T1需求→T2原型…"），
                #   解析器认不出 ⇒ 一个人都没派出去，用户只看到一句"解析失败"、白等一场。
                #   现在把**原文贴进群里**：能自动派就派，派不了也让用户看得到内容、能手动 @。
                raw = (plan_raw or "").strip()
                if raw:
                    _team_store.append(gid, **{"from": f"emp:{leader_emp['name']}", "text": (
                        "（我的分工计划，格式没能被系统识别，先发出来给你看：）" + chr(10) + raw[:1500]
                    )})
                _team_store.append(gid, **{"from": "system", "text": (
                    "⚠️ 组长这次输出**不是可解析的分工单**（没派活）。上面是他的原话，"
                    "你可以：① 再发一次目标让他重拆；② 直接 `@某人 做什么` 手动派。"
                    "（提示：让组长按「每行一个 @名字 + 任务」写，系统就能认）"
                )})
                return {"ok": True, "dispatched": 0}
            # 按分工单**分批**派发（★ 2026-10-05 P0-1：有前置的活要等前置交付 —— 见 team.leader_*）
            name_to_id = {e["name"]: e["id"] for e in _team_store.employees() if e["id"] in g["members"]}
            try:
                begin = _team_store.leader_begin(gid, text, plan)
            except Exception as e:
                _team_store.append(gid, **{"from": "system", "text": f"⚠️ 分工单无法落库：{e}"})
                return {"ok": True, "mode": "leader", "dispatched": []}
            dispatched = _dispatch_leader_batch(gid, begin, name_to_id)
            st = _team_store.leader_state(gid)
            _team_store.append(gid, **{"from": f"emp:{leader_emp['name']}", "text": (
                f"分工完成，共 {st['total']} 项，这次先开工 {len(dispatched)} 项：" + chr(10)
                + chr(10).join(f"- @{d['name']}：{d['task_summary']}" for d in dispatched)
                + (chr(10) + "等前置交付后再派：" + "、".join(
                    f"@{w['name']}（等 {'、'.join('@' + a for a in w['after'])}）"
                    for w in begin.get("waiting") or []) if begin.get("waiting") else "")
            )})
            return {"ok": True, "mode": "leader", "dispatched": dispatched,
                    "waiting": begin.get("waiting") or []}
        except Exception as e:
            _team_store.append(gid, **{"from": "system", "text": f"⚠️ 组长分工失败：{type(e).__name__}: {str(e)[:150]}"})
            return {"ok": True, "dispatched": 0}

    # ★ 接力模式（2026-10-05）：无 @ → 起一轮接力，第一棒先上；之后由交付回流自动接棒。
    #   与组长模式的区别是**串行**：同一件事一个人做完交给下一个（写作→评审→定稿）。
    if not mentioned and g.get("mode") == "relay":
        try:
            begin = _team_store.relay_begin(gid, text)
        except Exception as e:
            _team_store.append(gid, **{"from": "system", "text": f"⚠️ 接力启动失败：{e}"})
            return {"ok": True, "dispatched": []}
        emp = _team_store.get_employee(begin["emp_id"])
        if emp is None:
            _team_store.relay_stop(gid, "第一棒员工不存在")
            _team_store.append(gid, **{"from": "system", "text": "⚠️ 接力第一棒的员工不存在，已停下"})
            return {"ok": True, "dispatched": []}
        nm = emp["name"]
        order = _team_store.relay_order(g)
        names = " → ".join((_team_store.get_employee(i) or {}).get("name", "?") for i in order)
        _team_store.append(gid, **{"from": "system", "text": (
            f"🔁 接力开始（共 {begin['total']} 棒）：{names}" + chr(10) + f"第 1 棒：@{nm}"
        )})
        task = _dispatch_to_employee(
            gid, g, nm, emp,
            _team_store.relay_prompt(text, nm, "", 1, begin["total"]),
            relay_pos=1,
        )
        return {"ok": True, "mode": "relay",
                "dispatched": [{"name": nm, "task_id": task.id, "pos": 1, "total": begin["total"]}]}

    # ★ 开会模式（2026-10-05）：不点名 → 起一场讨论（**后台跑**，因为要 N×R 次模型调用，
    #   阻塞 HTTP 请求必然超时）。过程实时进群聊，最后收口成「结论/分歧/待办」。
    #   轮次默认 2；消息里写「3轮」就按 3 轮（上限 5）。
    if not mentioned and g.get("mode") == "meeting":
        rounds = _meeting_rounds_from(text)
        try:
            begin = _team_store.meeting_begin(gid, text, rounds)
        except Exception as e:
            _team_store.append(gid, **{"from": "system", "text": f"⚠️ 开会失败：{e}"})
            return {"ok": True, "mode": "meeting", "rounds": 0}
        names = [_team_store.get_employee(i)["name"] for i in begin["order"] if _team_store.get_employee(i)]
        moderator = _team_store.get_employee(g["leader"]) if g.get("leader") else None
        _team_store.append(gid, **{"from": "system", "text": (
            f"🗣 开会：{text[:120]}" + chr(10)
            + f"参会 {len(names)} 人（{', '.join(names)}）· 最多 {begin['rounds']} 轮 · "
            + f"主持：{moderator['name'] if moderator else names[0] if names else '—'}"
            + chr(10) + "讨论中…（每轮结束会问主持人「够了没」，够就提前收口）"
        )})
        _spawn_bg(_run_meeting(gid, begin["order"], begin["rounds"]))
        return {"ok": True, "mode": "meeting", "rounds": begin["rounds"], "members": names}

    # 广播模式：无 @ → 全员派（每人都收到同一任务，各自交付）
    _broadcast_all = False
    if not mentioned and g.get("mode") == "broadcast":
        _broadcast_all = True          # ★ 记下"这是全员广播批次"（后面要开批次 ✓）
        for mid in g["members"]:
            emp = _team_store.get_employee(mid)
            if emp:
                # §6.4：群里署名用名字，不是裸 id（此前群任务标题显示 emp_xxxx）
                mentioned.append((emp["name"], emp))

    if not mentioned:
        _team_store.append(gid, **{"from": "system", "text": "请用 @名字 指派任务，例如：@" + (g["members"] and (_team_store.get_employee(g["members"][0]) or {}).get("name", "") or "员工名") + " 做什么…"})
        return {"ok": True, "dispatched": 0}

    dispatched = []
    for nm, emp in mentioned:
        task_text = f"【群任务 @{nm}】{text}"
        sys_extra = (
            f"你是「{emp['name']}」，团队{emp.get('dept', '')}的{emp.get('role', '成员')}。" + chr(10)
            + _team_store.persona_for(emp) + chr(10)
            + "在沙箱/工作区内完成任务后，用 task_done 交付，交付消息要简洁、可直接贴进群聊。"
        )
        # 员工自己的大脑（BYOK）：无专属配置则回落全局默认
        try:
            mc = cfg.model
            if emp.get("provider"):
                mc = cfg.model.model_copy(update={
                    "provider": emp["provider"],
                    "model_name": emp.get("model_name") or mc.model_name,
                    "base_url": emp.get("base_url") or mc.base_url,
                    "api_key_env": emp.get("api_key_env") or mc.api_key_env,
                })
            provider = create_provider(mc)
        except Exception as e:
            _team_store.append(gid, **{"from": f"sys:{emp['name']}", "text": f"⚠️ {nm} 的大脑不可用（{type(e).__name__}: {str(e)[:80]}）——检查该员工的模型配置。"})
            continue
        # ★★ 2026-10-06 真 bug（"四个模式各真跑一轮"当场抓到的 ✗）：
        #   **这条路（点名 / 广播）没把工作区指到群共享目录** ✓ ——
        #   `_dispatch_to_employee`（组长 / 接力走的那条）是**对的** ✓，这里是**另写的一份** ✗，
        #   漏了 `workdir` ⇒ 产物落在 `data/tasks/<任务id>/workspace` ✗
        #   ⇒ **同群的人互相看不到对方的文件** ✓（"群共享工作区"就名存实亡 ✗）。
        #   症状：冒烟跑完交付说"已写 hello.txt ✓"，而群工作区里**空空如也** ✓。
        gws = store.group_workspace(gid)
        task = _launch_task(task_text, None, provider=provider,
                            system_extra=sys_extra + chr(10) + chr(10) + envfacts.facts(str(gws)),
                            workdir=gws, max_iterations=_budget_for(str(emp.get("role") or ""), task_text))
        store.set_task_workdir(task.id, gws)      # 文件接口 / 低层验收靠这个映射解析相对路径 ✓
        dispatched.append({"name": nm, "task_id": task.id})
        _team_store.append(gid, **{"from": f"emp:{nm}", "text": f"收到，开始执行：{text}", "task_id": task.id, "status": "running"})
        # 完成监视：交付卡片回流群聊
        # ★ 第 7a 处：同上——合并到 _watch_group_delivery，不再内联抄一份
        _spawn_bg(_watch_group_delivery(task.id, nm, gid))

    # ★ 2026-10-06：**广播模式开一个批次** ✓ —— 全交齐时自动派「项目终验」✓
    #   （只对「没 @ ⇒ 全员派」那种批次开 ✓；有 @ 的点名派不算批次 ✓）
    if _broadcast_all and len(dispatched) > 1:
        _team_store.broadcast_begin_batch(gid, text, [d["task_id"] for d in dispatched])
        _team_store.append(gid, **{"from": "system", "text": (
            f"📣 广播批次开始：{len(dispatched)} 人各领同一件事。" + chr(10)
            + "**都交齐之后会自动加一道「项目验收」**（真跑一遍看能不能用 ✓）——"
              "免得「每个人都交了」被当成「项目能跑」✗"
        )})
    return {"ok": True, "dispatched": dispatched}


@app.post("/api/v1/voice/transcribe", status_code=200)
async def voice_transcribe_only(request: Request):
    """语音转写（不发送）：音频 → 文字，返回给前端填进输入框供用户检查后手动发送。
    与 /tasks/{id}/voice（直接当消息发）的区别：这里是"草稿模式"（第 41 班，用户要求）。"""
    form = await request.form()
    up = form.get("audio")
    if up is None:
        raise HTTPException(422, "缺少 audio 文件")
    suffix = Path(up.filename or "draft.webm").suffix or ".webm"
    tmp = store.tasks_dir / f"draft_{secrets.token_hex(3)}{suffix}"
    _up = await up.read()
    if len(_up) > cfg.executor.attachment_max_mb * 1024 * 1024:  # 复审 P2：语音上传此前无上限
        raise HTTPException(413, f"音频超过 {cfg.executor.attachment_max_mb}MB 上限")
    tmp.write_bytes(_up)
    try:
        wav = await _to_wav(tmp)
        text = await _transcribe(wav)
    except Exception as e:
        raise HTTPException(502, f"转写失败：{type(e).__name__}: {str(e)[:150]}")
    finally:
        tmp.unlink(missing_ok=True)
        tmp.with_suffix(".conv.wav").unlink(missing_ok=True)
    return {"ok": True, "text": (text or "").strip()}


# ---------- 会议纪要（第 41 班 v1：分片录音 → 转写 → 时间线落盘） ----------


@app.post("/api/v1/tasks/{task_id}/meeting", status_code=201)
async def meeting_chunk(task_id: str, request: Request):
    """会议模式：前端分片上传录音 → 每片转写 → 带时间戳追加进工作区 meeting_notes.md。"""
    _get_task(task_id)
    form = await request.form()
    up = form.get("audio")
    if up is None:
        raise HTTPException(422, "缺少 audio 文件")
    suffix = Path(up.filename or "chunk.webm").suffix or ".webm"
    tmp = store.tasks_dir / f"meeting_{secrets.token_hex(3)}{suffix}"
    _up = await up.read()
    if len(_up) > cfg.executor.attachment_max_mb * 1024 * 1024:  # 复审 P2：语音上传此前无上限
        raise HTTPException(413, f"音频超过 {cfg.executor.attachment_max_mb}MB 上限")
    tmp.write_bytes(_up)
    try:
        wav = await _to_wav(tmp)
        text = await _transcribe(wav)
    except Exception as e:
        return {"ok": False, "text": "", "error": f"转写失败：{type(e).__name__}: {str(e)[:150]}"}
    finally:
        tmp.unlink(missing_ok=True)
        tmp.with_suffix(".conv.wav").unlink(missing_ok=True)
    if not text.strip():
        return {"ok": True, "text": "", "note": "（本段无语音内容，未记录）"}
    notes = store.workspace_dir(task_id) / "meeting_notes.md"
    ts = _now()[11:19]
    with notes.open("a", encoding="utf-8") as f:
        if not notes.exists() or notes.stat().st_size == 0:
            f.write(f"# 会议记录（{_now()[:10]}）\n\n")
        f.write(f"- [{ts}] {text.strip()}\n")
    return {"ok": True, "text": text.strip(), "file": "meeting_notes.md"}


@app.get("/api/v1/tasks/{task_id}/meeting")
async def meeting_read(task_id: str):
    """读当前会议记录全文（无记录返回空串）。"""
    _get_task(task_id)
    notes = store.workspace_dir(task_id) / "meeting_notes.md"
    return {"ok": True, "text": notes.read_text("utf-8") if notes.exists() else ""}


# ---------- TTS（语音合成） ----------


@app.post("/api/v1/tasks/{task_id}/voice", status_code=201)
async def voice_input(task_id: str, request: Request):
    """语音输入：上传音频 → ASR 转文字 → 作为用户消息发给 Agent。"""
    _get_task(task_id)
    form = await request.form()
    up = form.get("audio")
    if up is None:
        raise HTTPException(422, "缺少 audio 文件")
    suffix = Path(up.filename or "audio.mp3").suffix or ".mp3"
    tmp = store.tasks_dir / f"upload_{secrets.token_hex(3)}{suffix}"
    _up = await up.read()
    if len(_up) > cfg.executor.attachment_max_mb * 1024 * 1024:  # 复审 P2：语音上传此前无上限
        raise HTTPException(413, f"音频超过 {cfg.executor.attachment_max_mb}MB 上限")
    tmp.write_bytes(_up)
    try:
        wav = await _to_wav(tmp)
        text = await _transcribe(wav)
    except Exception as e:
        # ★ 2026-10-07：`_to_wav` 现在会**为"转不了码"抛出可照做的说明** ✓
        #   此前这里没有 except ⇒ 直接 500「Internal Server Error」✗（用户什么也得不到 ✓）
        raise HTTPException(502, f"转写失败：{type(e).__name__}: {str(e)[:200]}") from e
    finally:
        tmp.unlink(missing_ok=True)
        tmp.with_suffix(".conv.wav").unlink(missing_ok=True)
    if not text:
        raise HTTPException(422, "语音识别结果为空")
    run = runs.get(task_id)
    if run and run.aio_task and not run.aio_task.done():
        run.inject_user(text)
        return {"ok": True, "text": text, "mode": "injected"}
    task = _get_task(task_id)
    _start_run(task, text)
    return {"ok": True, "text": text, "mode": "new_run"}

_TTS_DIR = _DATA_DIR / "tts"  # 同上：跟随 cfg.storage.data_dir（§8）


@app.get("/api/v1/tasks/{task_id}/tts-audio/{filename}")
async def tts_audio(task_id: str, filename: str):
    """朗读音频播放（第 41 班）：音频存 data/tts，不进工作区——产物区只留真正的成果。"""
    import re as _re

    if not _re.fullmatch(r"tts_[0-9a-f]{4}_\d{2}\.(mp3|wav)", filename):
        raise HTTPException(404, "文件不存在")
    f = _TTS_DIR / task_id / filename
    if not f.exists():
        raise HTTPException(404, "音频不存在")
    from fastapi.responses import FileResponse

    return FileResponse(f, media_type="audio/mpeg" if f.suffix == ".mp3" else "audio/wav")


@app.post("/api/v1/tasks/{task_id}/tts", status_code=200)
async def tts_speak(task_id: str, req: TTSReq):
    """朗读（第 41 班真流式）：按句切分逐句合成，**NDJSON 流式返回**——
    第一句合成完（约 1-2s）立即推 URL，前端马上开口；后续句边合成边推。不等全文。"""
    _get_task(task_id)
    import re as _re

    from fastapi.responses import StreamingResponse

    async def _gen(backend_name: str, text: str, i: int) -> str:
        backend = get_tts_backend(backend_name)
        # ★ 2026-10-08：音色（请求 > 配置 > 后端默认 ✓ 只有多音色后端认它 ✓）
        want_voice = str(req.voice or getattr(cfg.tts, "voice", "") or "").strip()
        if want_voice and hasattr(backend, "voice"):
            backend.voice = want_voice
        ext = "mp3" if backend_name == "edge" else "wav"
        fn = f"tts_{secrets.token_hex(2)}_{i:02d}.{ext}"
        out_dir = _TTS_DIR / task_id
        out_dir.mkdir(parents=True, exist_ok=True)
        await backend.generate(text, out_dir / fn)
        return f"/api/v1/tasks/{task_id}/tts-audio/{fn}"

    # 按句切分（保留标点）。**首段只放第一句**（最短，合成最快，开口速度优先）；
    # 后续句合并到 ≤80 字/段，控制段数。
    parts = [p for p in _re.split(r"(?<=[。！？!?；;\n])", req.text.strip()) if p.strip()]
    if not parts:
        raise HTTPException(422, "没有可朗读的文本")
    chunks: list[str] = [parts[0][:80]]
    buf = ""
    for p in parts[1:]:
        if len(buf) + len(p) <= 80:
            buf += p
        else:
            if buf:
                chunks.append(buf)
            buf = p[:160]
    if buf:
        chunks.append(buf)

    # ★★ 2026-10-06：**后端从配置来** ✓（`config.tts.backend` ✓ 设置页能切 ✓）
    #   此前这里写死 `or "edge"` ✗ ⇒ 用户在设置里选了别的后端也**不生效** ✓
    #   （而且"三处口径打架"的第三处就是它 ✓）。
    primary = req.backend or str(getattr(cfg.tts, "backend", "") or "edge")
    # ★ `asked` = **你要的那个** ✓ `effective` = **真跑的那个** ✓ —— 两者都必须如实体现在回执里 ✓
    #   （长文本策略换引擎时，用户仍然有权知道他点的是谁 ✓ 见下面 policy ✓）
    asked = primary
    effective = primary

    # ★★ 2026-10-08（用户："那个千问TTS 有点慢呢" ✓）：**长文本自动换快的引擎** ✓
    #   实测：千问3(0.6B) 一句 4~5 秒的话要合成 **5~12 秒** ✗
    #   而朗读是**边合成边播**（下面就是流式 ✓）—— 合成比播放慢 ⇒ 队列永远空 ⇒ 一句一顿 ✓
    #   ⇒ 长文本改用在线引擎（edge 几乎瞬时 ✓）✓ 短句仍用本地（离线、最准 ✓）
    #   ★ 换的时候**必须说清**（`policy` 字段 ✓ 与"用不了所以退回"是两码事 ✗ 别混成一条提示 ✓）
    #   ★ 配置里 `long_text_backend` 留空 ⇒ 这条策略**关掉** ✓（想全程本地就留空 ✓）
    policy = ""
    _slow = ("qwen3tts", "melotts")
    _fast = str(getattr(cfg.tts, "long_text_backend", "") or "").strip()
    _thr = int(getattr(cfg.tts, "long_text_threshold", 300) or 300)
    if _fast and _fast != primary and primary in _slow and len(req.text.strip()) >= _thr:
        try:
            get_tts_backend(_fast)          # 能用吗？（不能用会抛 ✓ 那就老实留在本地 ✓）
            policy = (f"这段有 {len(req.text.strip())} 字，本地引擎一句要 5~12 秒 ⇒ "
                      f"自动改用「{_fast}」了（要强制用本地：设置 → 语音合成 里把这档关掉 ✓）")
            effective = _fast
        except Exception:                   # noqa: BLE001 —— 换不了就用原来的 ✓ 绝不因此不念 ✓
            policy = ""

    async def _stream():
        # ★★ 2026-10-08（用户当场问出来的 ✗✗）：**退回了就必须说出来** ✓
        #   原来 `backend=melotts`（没装）会**静默**改用 pyttsx3 ✗ ——
        #   用户点名要 A、耳朵里听到的是 B，而回执里**一个字都没有** ✓。
        #   实测铁证：同一句话分别点 melotts 与 pyttsx3，两个 wav
        #   **字节数一模一样（104796）**、采样率同为 22050 ⇒ 就是同一个引擎念的 ✓。
        #   ★ 而设置页那句"没装的后端会**如实告诉你**发不出声"——它**没做到** ✗
        #     （界面承诺 × 实现不符 = 本仓最恨的那种"骗人" ✓）
        #   ⇒ 每一行带上 `used`（真用的谁）/ `asked`（你要的谁）/ `why`（为什么换了 ✓
        #     取自那句异常里的安装指引 ✓）；**没换就不带**（正常路径一个字节不多 ✓）。
        for i, s in enumerate(chunks):
            used, why = effective, ""
            try:
                url = await _gen(effective, s, i)
            except Exception as e:
                if effective == "pyttsx3":
                    raise
                why = str(e)[:300]          # 里面的"怎么装"就是用户要的那句话 ✓
                used = "pyttsx3"
                url = await _gen("pyttsx3", s, i)  # 网络合成失败 → 本地离线兜底
            line: dict[str, Any] = {"url": url}
            # ★ 口径：`asked` = 你要的 ✓ `used` = 真跑的 ✓（两者都如实 ✓ 一个字段都不许省 ✗）
            if used != asked:
                line.update({"used": used, "asked": asked, "why": why})
            # ★ 2026-10-08：策略性的换引擎（长文本 ⇒ 换快的 ✓）**单独一个字段** ✓
            #   别跟"你选的用不了所以退回"混在一起 ✗ —— 那句话说"用不了"会误导 ✓
            if policy and i == 0:
                line["policy"] = policy
            yield (json.dumps(line) + "\n").encode()

    return StreamingResponse(_stream(), media_type="application/x-ndjson")

# ---------- 工作区文件浏览 ----------



_READ_CAP = 100_000


def _safe_path(task_id: str, rel: str) -> Path:
    base = store.workspace_dir(task_id).resolve()
    p = (base / rel).resolve() if rel else base
    if not p.is_relative_to(base):
        raise PermissionError(rel)
    return p


@app.post("/api/v1/tasks/{task_id}/files", status_code=201)
async def upload_attachment(task_id: str, request: Request) -> dict[str, Any]:
    """上传附件到任务工作区（图片/文档），返回工作区内的相对路径。

    **用途**：前端把用户拖进来的图片/文件存进工作区，再把文件名写进消息，
    Agent 就能用 `image_read` / `file_read` 看到它们。
    （能拖文件进去属于标配。）

    **安全**：
      · 只取 `Path(filename).name` —— 丢掉任何目录部分，**防路径穿越**（`../../x` → `x`）；
      · 只写进任务自己的工作区，不碰别处；
      · 大小上限防塞满磁盘（可用 attachments.max_mb 覆盖，默认 50MB）。
    """
    task = _get_task(task_id)
    form = await request.form()
    up = form.get("file")
    if up is None:
        raise HTTPException(422, "缺少 file 字段")
    raw_name = Path(getattr(up, "filename", "") or "upload.bin").name or "upload.bin"
    data = await up.read()
    max_bytes = int(getattr(cfg.executor, "attachment_max_mb", 50)) * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(413, f"附件过大（{len(data)} 字节，上限 {max_bytes}）")

    ws = store.workspace_dir(task_id)
    ws.mkdir(parents=True, exist_ok=True)
    dest = ws / raw_name
    stem, suffix = Path(raw_name).stem, Path(raw_name).suffix
    i = 1
    while dest.exists():  # 同名不覆盖，加序号
        dest = ws / f"{stem}_{i}{suffix}"
        i += 1
    dest.write_bytes(data)
    task.updated_at = _now()
    _save_index()
    _emit_standalone(task_id, "knowledge", {
        "title": f"📎 已上传附件：{dest.name}",
        "content": f"{len(data)} 字节　工作区路径：{dest.name}",
    })
    return {"ok": True, "name": dest.name, "path": dest.name, "size": len(data)}


@app.get("/api/v1/tasks/{task_id}/files")  # 契约二：GET /tasks/:id/files?path=
async def browse_files(task_id: str, path: str = ".") -> dict[str, Any]:
    _get_task(task_id)
    try:
        p = _safe_path(task_id, path)
    except PermissionError:
        raise HTTPException(403, f"路径越界（沙箱外）：{path}")
    if p.is_dir():
        entries = [
            {"name": e.name, "is_dir": e.is_dir(), "size": 0 if e.is_dir() else e.stat().st_size}
            for e in sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
            if not e.name.startswith("tts_")  # 朗读缓存不是产物（第 41 班）
        ]
        return {"type": "dir", "entries": entries}
    if p.is_file():
        return {"type": "file", "content": p.read_text("utf-8", errors="replace")[:_READ_CAP]}
    raise HTTPException(404, f"路径不存在：{path}")


# ═══════════ 手机直连（局域网）· 一键开通 + 界面托管（第 8 批 问题4） ═══════════
#
# 用户原话：「手机直连局域网这个功能吧，感觉是有，但对小白来说他根本就不会设置——
#   你写一个什么 backend config.json，他们根本不懂、不知道搁哪找」。
#
# 实测下来问题比"难设置"更严重 —— **今天根本连不上**：
#   · 后端 API 能设 0.0.0.0 ✓，但**后端不托管界面**（此前只托管了一个 favicon）；
#   · 界面在 vite:5173，而 `vite.config.ts` **没设 `server.host`** ⇒ 只绑 localhost；
#   · 旧说明（SettingsPanel 里那段）还教人打开 `http://电脑IP:5173` —— 那个端口
#     外部本来就进不来。
# ⇒ 这批补上"界面那一半"（下面的 SPA 托管）+ 一键开通（本节的三个接口）。
#   注：**不要**改用"把 vite 暴露到局域网"这条捷径 —— vite 的 proxy 会把
#   请求 Host 改写成回环地址，后端会认为"来自本机"从而**整个 token 闸门失效**。

# ★ 2026-10-04 第二次血案修正：写回路径必须与 `load_config()` **同源**。
#   此前这里自己算 `__file__` 的路径 ⇒ pytest 里 conftest 把 AGENT_SHELL_CONFIG
#   指向临时配置、读没问题，**写**却落回用户真实的 config.json（本次实测：
#   server.host 被打回 127.0.0.1、storage.data_dir 指向 %TEMP% ⇒ 重启即"任务全消失"）。
_CONFIG_PATH = config_path()
_UI_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def _read_config_file() -> dict[str, Any]:
    try:
        data = json.loads(_CONFIG_PATH.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _patch_config_file(patch: dict[str, Any]) -> None:
    """只改 config.json 里指定字段并写回（保留文件其余内容）。

    ★ 刻意**不动内存里的 `cfg`**：监听地址是 uvicorn 启动时定下的，运行中改
      `cfg.server.host` 只会让 `_lan_mode()` 立刻变真 ⇒ **本机浏览器会突然被要
      访问密码**，而局域网其实还没开通（要重启才通）。所以口径是：
      **文件写新值、内存保持旧值**，界面明确写"重启后生效"。
    """
    data = _read_config_file() or cfg.model_dump()
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(data.get(k), dict):
            data[k].update(v)
        else:
            data[k] = v
    _CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), "utf-8")


def _lan_view() -> dict[str, Any]:
    """手机直连状态：分别给出【当前生效】与【配置里待生效】两套值，别混着说。"""
    f_server = _read_config_file().get("server") or {}
    pending_host = str(f_server.get("host") or cfg.server.host)
    pending_token = str(f_server.get("access_token") or cfg.server.access_token or "")
    pending_lan = pending_host in ("0.0.0.0", "::") and bool(pending_token)
    # ★ 第 8f 处：`active` 看【启动时真正绑定的地址】，不看配置 —— 否则会出现
    #   "配置说通了、其实没通"（二维码给出去却连不上）。
    active = _lan_mode()
    ip = _local_ip()
    port = cfg.server.port
    idx = _UI_DIST / "index.html"
    ui_built = idx.stat().st_mtime if idx.exists() else None
    return {
        "enabled": active,                       # 现在真正生效的
        "pending_lan": pending_lan,              # 配置里写的（重启后生效）
        "needs_restart": pending_lan != active,
        "host": cfg.server.host,
        "bound_host": _BOUND_HOST,               # 进程真正绑的（诊断用，别删）
        "port": port,
        "ip": ip,
        "token": pending_token if pending_lan else "",
        "token_set": bool(pending_token),
        "url": f"http://{ip}:{port}/?token={pending_token}" if pending_lan else f"http://{ip}:{port}/",
        "ui_ready": idx.exists(),
        "ui_built_at": (datetime.fromtimestamp(ui_built, timezone.utc).isoformat()
                        if ui_built else None),
    }


def _client_is_local(request: Request) -> bool:
    """请求是不是**从本机发出来**的。

    判据用 `request.client.host`（TCP 层拿到的对端地址）——**不是** Host / X-Forwarded-For
    这类请求头：那些是客户端自己写的，手机可以把 Host 伪造成 127.0.0.1。
    拿不到对端地址时**保守判否**（fail-closed）：宁可本机多输一次密码，也不要把密码发给外人。
    """
    host = (getattr(getattr(request, "client", None), "host", "") or "").strip().lower()
    return host in {"127.0.0.1", "::1", "localhost"} or host.startswith("127.")


def _current_token() -> str:
    """内存优先、回落到配置文件（与 lan/enable 的取法一致）。"""
    tok = str(getattr(cfg.server, "access_token", "") or "")
    return tok or str(_read_config_file().get("server", {}).get("access_token") or "")


@app.get("/api/v1/server/token")
async def get_access_token(request: Request) -> dict[str, Any]:
    """查看访问密码 —— **仅本机**（手机 / 别的电脑一律 403）。

    为什么要这个接口：密码是 `secrets.token_urlsafe(24)` 随机串，用户记不住、也没地方看
    （只能去翻 config.json）。允许在本机上"看一眼"是体验，**不给远端**是安全。
    """
    if not _client_is_local(request):
        raise HTTPException(403, "访问密码只能在本机上查看（手机或其它设备看不到）——请在电脑上打开设置页")
    tok = _current_token()
    return {"ok": True, "token": tok, "set": bool(tok),
            "note": "这是本机局域网访问密码；手机连的时候输入的就是它。" if tok else "当前没有设置访问密码。"}


class AccessTokenReq(BaseModel):
    """改访问密码。`token` 留空 = 让服务端随机生成一个强的。"""

    token: str = ""


@app.post("/api/v1/server/token")
async def set_access_token(req: AccessTokenReq, request: Request) -> dict[str, Any]:
    """改访问密码 —— **也仅本机**。

    为什么连改也要限本机：手机改密码会**把手机自己锁在外面**（下一次请求就得用新密码，
    而手机端并没有可靠的改密入口）。要改就回电脑上改。
    """
    if not _client_is_local(request):
        raise HTTPException(403, "改访问密码只能在本机操作——在手机上改会把手机自己锁在外面")
    new = req.token.strip()
    generated = False
    if not new:
        new = secrets.token_urlsafe(24)          # ≈192 位熵，32 字符
        generated = True
    else:
        if len(new) < 8:
            raise HTTPException(422, "密码太短（至少 8 个字符；也可以留空让系统随机生成）")
        if any(ch.isspace() for ch in new):
            raise HTTPException(422, "密码里不能有空格/换行（地址栏与命令行里会被截断）")
    # ★ 内存 + 文件一起改：只改文件的话，之后任何一次设置保存（_save_config 整体覆盖写）
    #   都会把密码打回旧值（8f 同型事故：用户以为改了，重启后又变回去）。
    cfg.server.access_token = new
    _patch_config_file({"server": {"access_token": new}})
    # ★ 2026-10-07（第 5 项）：**换了密码 ⇒ 把爆破失败记录清掉** ✓
    #   道理很简单：门锁换了 ✓ 上一把锁的错误记录没道理还算在新密码头上 ✓
    #   （不然用户改完密码还得站门口再等一分钟 ✓ 说不通 ✓）
    login_guard.reset_all()
    return {"ok": True, "token": new, "generated": generated,
            "note": "已修改：旧密码立即失效，手机/其它设备需要用新密码重新连接（设置页二维码也要重新扫）。"}


@app.get("/api/v1/settings/approval/forever")
async def list_forever_approvals() -> dict[str, Any]:
    """★ 2026-10-06「这类以后都别问」的清单（设置页用它展示与收回 ✓）。

    交付这个接口是因为**界面上那句提示答应过用户"随时能在设置里收回"** ✓ ——
    答应了就得真给得出这个口子 ✗ 不然就是骗人 ✓。
    """
    return {"ok": True, "rules": approval.forever_rules()}


class ForeverRevokeReq(BaseModel):
    key: str = ""          # 空 = 全收回 ✓


@app.post("/api/v1/settings/approval/forever/revoke")
async def revoke_forever_approval(req: ForeverRevokeReq) -> dict[str, Any]:
    """收回一条（或全部）「以后都别问」✓ —— 收回后那条命令马上恢复逐条询问 ✓。"""
    left = approval.revoke_forever(req.key.strip())
    return {"ok": True, "rules": {k: v for k, v in approval.forever_rules().items()},
            "left": left}


@app.get("/api/v1/settings/export")
async def export_settings() -> dict[str, Any]:
    """导出**可搬走**的设置（Phase 3 ⑧ 第二刀）—— 换机器/发给别人时一键搬。

    ★ **不含任何密钥**：只导出白名单里的节，而这几节里跟密钥有关的字段只有
      `api_key_env`（**变量名**，不是值）；`server`（含访问密码）根本不在白名单里。
      所以"导出的文件里没有秘密"是**结构上成立**的，不靠事后过滤。
    """
    return {
        "ok": True,
        "app": "agent-shell",
        "version": cfg.version,
        "exported_at": _now(),
        "sections": {k: getattr(cfg, k).model_dump(mode="json") for k in _RESETTABLE},
        "note": ("只导出可搬的设置；API Key 与访问密码**不在里面**（它们只存在本机环境变量与配置文件里）。"
                 "导入时会把这几节整体替换。"),
    }


class SettingsImportReq(BaseModel):
    """导入设置。`dry_run=True` 只回报"会改动什么"，不落盘。"""

    sections: dict[str, dict[str, Any]]
    dry_run: bool = False


@app.post("/api/v1/settings/import")
async def import_settings(req: SettingsImportReq) -> dict[str, Any]:
    """导入设置：**先校验、再（可选）预演、最后才应用**。

    三道闸：
      ① 节名白名单（与恢复默认同一套；`server`/`storage`/`skills`/`mcp` 一律拒绝 ——
         导入一个别人的 server 段会把你的访问密码顶掉）
      ② 每节都用**配置模型本身**校验（`extra="forbid"` ⇒ 多一个字段就报错，不会静默吃掉）
      ③ `dry_run=True` 只回报改动（界面先给用户看清楚，再让他确认）
    """
    bad = [k for k in req.sections if k not in _RESETTABLE]
    if bad:
        raise HTTPException(
            422,
            f"这些节不能导入：{bad}（可导入：{list(_RESETTABLE)}；"
            "server/storage/skills/mcp 出于安全考虑不能从文件覆盖）",
        )
    # ② 逐节用配置模型校验。**按字段合并**（给的字段覆盖，没给的原样保留）——
    #    这样"只想改超时"的手写片段不会把工作区路径一起重置；而导出的是完整节，
    #    合并与整体替换结果一致（round-trip 是 no-op，有锚点钉）。
    #    合并后再用配置模型校验（extra="forbid" ⇒ 多一个字段就报错，不会静默吃掉）。
    parsed: dict[str, Any] = {}
    for sec, payload in req.sections.items():
        merged = {**getattr(cfg, sec).model_dump(mode="json"), **(payload or {})}
        try:
            parsed[sec] = type(getattr(cfg, sec))(**merged)
        except ValidationError as e:
            raise HTTPException(422, f"「{sec}」的设置有 {e.error_count()} 处不合法：{e.errors()[:4]}") from e

    # 改动清单（预演与正式应用共用同一份计算）
    changed: dict[str, list[str]] = {}
    for sec, obj in parsed.items():
        cur = getattr(cfg, sec).model_dump(mode="json")
        new = obj.model_dump(mode="json")
        diff = [k for k in new if cur.get(k) != new[k]]
        if diff:
            changed[sec] = diff

    if req.dry_run:
        return {"ok": True, "dry_run": True, "changed": changed,
                "note": (f"预演：将改动 {len(changed)} 个节" + (f"（{', '.join(changed)}）" if changed else "（没有变化）"))}

    for sec, obj in parsed.items():
        setattr(cfg, sec, obj)
    _save_config()
    return {"ok": True, "dry_run": False, "changed": changed,
            "note": (f"已导入 {len(changed)} 个节：{', '.join(changed) if changed else '（没有实际变化）'}；"
                     "API Key 仍需你在本机设置（导入文件里没有密钥）。")}


@app.get("/api/v1/lan/status")
async def lan_status() -> dict[str, Any]:
    """设置页「手机直连」面板的数据源。"""
    return _lan_view()


@app.post("/api/v1/lan/enable")
async def lan_enable() -> dict[str, Any]:
    """一键开通：没有访问密码就生成一个强的，然后把绑定放开到 0.0.0.0。

    密码用 `secrets.token_urlsafe(24)`（≈192 位熵，32 字符）——比让用户自己想强得多。

    ★ 第 8f 处：**内存 cfg 也要一起改**。只改文件的话，之后任何一次设置保存
      （`_save_config()` 拿内存 cfg 整体覆盖写文件）都会把 host 静默打回 127.0.0.1
      —— 本班真实踩到：用户开通成功、手机也能用，但 config.json 已被改回去，
      **下次重启手机就连不上**。
      注意：改了内存 cfg 也**不会**让局域网提前生效 —— "是否生效"看 `_BOUND_HOST`，
      它只在进程启动时取一次（见 `_lan_mode()`）。
    """
    token = cfg.server.access_token or str(_read_config_file().get("server", {}).get("access_token") or "")
    token = token or secrets.token_urlsafe(24)
    cfg.server.host = "0.0.0.0"          # 防被后续 _save_config() 覆盖（8f）
    cfg.server.access_token = token
    _patch_config_file({"server": {"host": "0.0.0.0", "access_token": token}})
    return {"ok": True, **_lan_view()}


@app.post("/api/v1/lan/disable")
async def lan_disable() -> dict[str, Any]:
    """收回局域网绑定，**保留访问密码**（下次开通不用重新配对手机）。

    ★ 第 8f 处：内存 cfg 一起改（同 enable）—— 否则一次设置保存就会把它又打回 0.0.0.0。
    """
    cfg.server.host = "127.0.0.1"
    _patch_config_file({"server": {"host": "127.0.0.1"}})
    return {"ok": True, **_lan_view()}


@app.get("/{full_path:path}")
async def serve_ui(full_path: str) -> Any:
    """托管构建好的前端界面 —— 手机直连与将来打包分发都走这一条路。

    ★ 必须注册在**所有 API 路由之后**（本文件最末尾），否则会把 /api/* 也吞掉。
    ★ 路径穿越防护：把拼出来的路径 resolve 后校验仍在 dist 之内（`/../../etc/passwd` 之类）。
    """
    if full_path.startswith("api/"):
        raise HTTPException(404, "not found")
    index = _UI_DIST / "index.html"
    if not index.exists():
        raise HTTPException(
            503, "前端界面尚未构建：请在 frontend/ 目录执行 npm run build（手机直连需要它）")
    dist_root = _UI_DIST.resolve()
    candidate = (_UI_DIST / full_path).resolve()
    if full_path and dist_root in candidate.parents and candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(index)          # SPA 回退


if __name__ == "__main__":  # 端口/主机只来自 config.json（契约三零硬编码）
    import uvicorn

    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port)
