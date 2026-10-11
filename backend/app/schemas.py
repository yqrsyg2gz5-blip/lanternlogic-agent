"""契约一/二的 pydantic 镜像 —— 与 contracts/events.schema.json（draft-07）对齐。
规则：契约变动 → 只改本文件 + schema（前端跟随 types.ts）。
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

EventType = Literal[
    "message", "message_delta", "action", "observation", "plan",
    "knowledge", "datasource", "status", "error",
]
TaskState = Literal["running", "waiting_approval", "done", "failed", "partial", "idle", "cancelled"]
TaskStatus = Literal["created", "running", "waiting_approval", "done", "failed", "partial", "cancelled"]

_TASK_ID_RE = re.compile(r"^task_\d{8}_[a-z0-9]{4,32}$")
_EVT_ID_RE = re.compile(r"^evt_\d{6}$")


class EventEnvelope(BaseModel):
    """事件信封（契约一）—— id/seq/task_id/type/version/ts/payload"""

    id: str
    seq: int = Field(ge=1)  # 任务内从 1 严格递增
    task_id: str
    type: EventType
    version: Literal[1] = 1  # 只许加字段，删改必须写迁移
    ts: str  # ISO8601 UTC
    payload: dict[str, Any]

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not _EVT_ID_RE.match(v):
            raise ValueError(f"事件 id 格式应为 evt_<6位序号>，得到：{v}")
        return v

    @field_validator("task_id")
    @classmethod
    def _check_task_id(cls, v: str) -> str:
        # ★ 只能是【下限】4：C7（880f887）把随机位从 4 位 hex 提到 8 位 hex 防碰撞，
        #   但这条校验当时没跟着放开 ⇒ **新任务第一条事件就 ValidationError**
        #   ⇒ 任务瞬间 failed、0 事件（本班实测）。旧任务仍是 4 位，必须照样收。
        #   ⇒ 判据 = "task_<8位日期>_<≥4 位小写字母数字>"（4=历史、8=现行、留余量）。
        if not _TASK_ID_RE.match(v):
            raise ValueError(f"task_id 格式应为 task_<日期>_<随机位>（≥4 位小写字母数字），得到：{v}")
        return v


class TaskSummary(BaseModel):
    id: str
    title: str
    status: TaskStatus = "created"
    created_at: str
    updated_at: str
    project_id: str | None = None  # 所属项目（配置注入来源）
    pinned: bool = False  # 置顶（第 41 班：侧栏排序置顶）


# ============ 请求体 ============


class CreateTaskReq(BaseModel):
    input: str = Field(min_length=1, description="初始任务描述")
    project_id: str | None = Field(default=None, description="可选：挂载的项目（其 master 指令自动注入）")
    role: str | None = Field(default=None, description="可选：专家身份（角色名，人设注入系统提示）")


class ProjectReq(BaseModel):
    name: str = Field(min_length=1, max_length=40, description="项目名")
    master_prompt: str = Field(default="", description="master 指令：自动注入该项目每个新任务")

class AutomationReq(BaseModel):
    name: str = Field(min_length=1, max_length=40, description="自动化名称")
    # ★ 2026-10-06 加第三种：watch（**某个文件夹有变化就开跑** ✓）
    #   为什么加它：Manus 2.0 的 Automations 主打"事件触发"✓ 而我们只有定时 ✓；
    #   而用户真正每天会用的那件事（"下载目录来新文件就整理"✓）本质是**文件变动** ✓，
    #   既不需要邮箱配置 ✗ 也不需要外部服务 ✗ —— 本地看一眼就够了 ✓。
    kind: str = Field(description="schedule（定时）/ hook（Webhook）/ watch（文件夹变动）")
    task_input: str = Field(min_length=1, description="每次触发创建的任务内容")
    project_id: str | None = Field(default=None, description="可选：触发任务挂载的项目")
    schedule: dict[str, Any] | None = Field(
        default=None,
        description=('kind=schedule 时必填，四种之一：'
                     '{"kind":"interval","minutes":N} ｜ {"kind":"daily","time":"HH:MM"} ｜ '
                     '{"kind":"hourly","time":"HH:MM"}（MM 有效=第几分 ✓）｜ '
                     '{"kind":"weekly","weekday":1-7,"time":"HH:MM"}（1=周一 ✓）｜ '
                     '{"kind":"monthly","day":1-28,"time":"HH:MM"}'),
    )
    # ★ 2026-10-07（第 7 项）：**单次花费上限**（元 ✓ 0/不填 = 不拦 ✓）
    #   与"每个 Key 上限"是**两把锁**：那把管总量 ✓ 这把管**某一次跑飞了** ✓
    max_cost_cny: float | None = Field(default=None, description="kind=schedule：这一次最多花多少钱（元）")
    # ★ 2026-10-07（第 7 项）：**失败自动重试几次**（0/不填 = 不重试，只如实记账 ✓）
    retries: int | None = Field(default=None, description="跑失败后自动重试几次（有上限，防烧钱）")
    # kind=watch 时必填 ✓（绝对路径 ✓ 支持 ~ 展开 ✓）；watch_seconds = 防抖间隔（默认 60 秒 ✓）
    watch_path: str | None = Field(default=None, description="kind=watch 时必填：要盯着哪个文件夹")
    watch_seconds: int | None = Field(default=None, description="kind=watch：两次触发之间至少隔多少秒")


class TTSReq(BaseModel):
    # ★ 2026-10-06：**留空 = 用设置里选的那个** ✓（`config.tts.backend` ✓ 默认 edge ✓）
    #   以前这里写死默认 "edge" ✗ ⇒ 用户在设置里切了也不生效 ✓
    text: str = Field(min_length=1, description="要转成语音的文字")
    backend: str = Field(default="", description="TTS 后端；**留空 = 用设置里选的那个**（edge/melotts/pyttsx3）")
    # ★ 2026-10-08：音色（留空 = 用设置里选的 ✓ 只有多音色后端认它 ✓ 见 config.TtsCfg.voice ✓）
    voice: str = Field(default="", description="音色；**留空 = 用设置里选的那个**（千问3: vivian/serena/uncle_fu…）")


class WideReq(BaseModel):
    input: str = Field(min_length=1, description="总体调研主题")
    items: list[str] = Field(min_length=2, max_length=8, description="2-8 个相互独立的研究要点")
    project_id: str | None = Field(default=None, description="可选：挂载的项目")


class MessageReq(BaseModel):
    text: str = Field(min_length=1, description="追加的用户消息")
    role: str | None = Field(default=None, description="可选：本次续聊以哪个专家身份执行（角色名，见 /team/roles）——身份人设将注入后续对话")
    edit_of_seq: int | None = Field(
        default=None, ge=1,
        description=("★ 改一句重发：被改写的**用户消息事件 seq**。给了它就把它之前的历史保留、"
                     "那一条及其之后全部作废，然后用 text 重跑。事件流是只追加的（契约），"
                     "所以会先补一条说明事件，界面上能看到「从第 N 条重跑」。"),
    )


class ApproveReq(BaseModel):
    call_id: str
    # ★ 2026-10-05「本任务全部允许（含新命令）」：all = 这个任务后续命令不再逐条询问。
    #   （实测踩过：只改端点不改模型 ⇒ 请求 422，前端点了没反应 ✗）
    # ★ 2026-10-06 新增 `forever`：**这类以后都别问**（跨任务、落盘 ✓）——
    #   记住这条命令的**程序名**（如 python/pytest/git ✓），新任务也不再问 ✓。
    #   破坏性动词与结构性命令永不记忆（见 ApprovalManager.allow_forever ✓）。
    decision: Literal["once", "always", "all", "forever", "deny"]
    # 命令原文（记"这类"要用它 ✓ 前端点了按钮就带上；老客户端不带也能用 ✓ = 退化成本次放行 ✓）
    command: str = ""


class GroupApproveReq(BaseModel):
    """★ 群里的审批卡片（2026-10-05）：带着 task_id —— 一个群里可能同时挂多个任务的审批，
    必须指名道姓批哪一个（与任务页共用 `_do_approve`，语义一致）。"""
    task_id: str
    call_id: str
    decision: Literal["once", "always", "all", "forever", "deny"] = "once"
    command: str = ""
