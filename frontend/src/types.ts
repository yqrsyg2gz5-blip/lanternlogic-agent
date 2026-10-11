/**
 * 契约的 TypeScript 镜像 —— 对应 contracts/events.schema.json（v1）
 * 规则：契约变动 → 只改本文件 + schema，两端各自跟随。
 */

// ============ 事件信封 ============

export type EventType =
  | 'message'
  | 'message_delta'
  | 'action'
  | 'observation'
  | 'plan'
  | 'knowledge'
  | 'datasource'
  | 'status'
  | 'error';

export interface EventEnvelope<P = unknown> {
  id: string; // evt_000001
  seq: number; // 任务内从 1 严格递增
  task_id: string;
  type: EventType;
  version: 1;
  ts: string; // ISO8601 UTC
  payload: P;
}

// ============ 七类事件 payload ============

/** message —— "Messages input by actual users" */
export interface MessageSource {
  title: string;
  url: string;
}
export interface MessagePayload {
  role: 'user' | 'assistant';
  text: string;
  attachments?: string[];
  /** 交付来源清单（add-only）：只含本次运行确实访问过的链接 */
  sources?: MessageSource[];
}

/** message_delta —— 流式输出增量（我们新增）：随后的完整 message 事件才是权威文本 */
export interface MessageDeltaPayload {
  delta: string;
}

/** action —— "Tool use (function calling) actions" */
export interface ActionPayload {
  tool: string; // snake_case，如 file_write
  params: Record<string, unknown>;
  call_id: string;
}

/** observation —— "Results generated from corresponding action execution" */
export interface ObservationPayload {
  call_id: string;
  ok: boolean;
  result: string;
  duration_ms: number;
}

/** plan —— Planner 模块的编号伪代码 + 状态 + 反思 */
export type PlanStepStatus = 'pending' | 'in_progress' | 'done' | 'failed';
export interface PlanStep {
  no: number;
  text: string;
  status: PlanStepStatus;
}
export interface PlanPayload {
  steps: PlanStep[];
  current_step: number;
  reflection?: string;
}

/** knowledge —— Knowledge 模块提供的知识与最佳实践 */
export interface KnowledgePayload {
  title: string;
  content: string;
}

/** datasource —— Datasource 模块提供的数据 API 文档 */
export interface DatasourcePayload {
  name: string;
  description: string;
  endpoint?: string;
}

/** status —— 系统杂项事件 + 任务状态机 */
export type TaskState =
  | 'running'
  | 'waiting_approval'
  | 'done'
  | 'failed'
  | 'partial'
  | 'idle'
  | 'cancelled';
export interface StatusPayload {
  state: TaskState;
  detail?: string;
  /** 仅在 state === 'waiting_approval' 时出现：待审批那次工具调用的 call_id（契约 add-only 字段） */
  call_id?: string;
}

/** error —— 错误也是一等公民（走事件流，不另搞报错通道） */
export interface ErrorPayload {
  message: string;
  code?: string;
}

// ============ 任务（契约二）============

export type TaskStatus =
  | 'created'
  | 'running'
  | 'waiting_approval'
  | 'done'
  | 'failed'
  | 'partial'
  | 'cancelled';

export interface TaskSummary {
  id: string; // task_20260929_a1b2
  title: string;
  status: TaskStatus;
  created_at: string;
  updated_at: string;
  project_id?: string;
  pinned?: boolean; // 所属项目（配置注入来源）
}

// ============ 项目（配置注入）============

export interface Skill {
  name: string;
  description: string;
}

export interface Automation {
  id: string; // auto_xxxxxx
  name: string;
  kind: 'schedule' | 'hook';
  task_input: string;
  project_id?: string;
  schedule?: { kind: string; minutes?: number; time?: string };
  enabled: boolean;
  secret?: string;
  created_at: string;
  last_run?: string | null;
  last_task_id?: string | null;
}
export interface Project {
  id: string; // proj_xxxxxx
  name: string;
  master_prompt: string; // 自动注入该项目每个新任务
  created_at: string;
}

// ============ 按类型取 payload 的工具类型 ============

export interface EventPayloadMap {
  message: MessagePayload;
  message_delta: MessageDeltaPayload;
  action: ActionPayload;
  observation: ObservationPayload;
  plan: PlanPayload;
  knowledge: KnowledgePayload;
  datasource: DatasourcePayload;
  status: StatusPayload;
  error: ErrorPayload;
}

export type TypedEvent<K extends EventType> = EventEnvelope<EventPayloadMap[K]>;
