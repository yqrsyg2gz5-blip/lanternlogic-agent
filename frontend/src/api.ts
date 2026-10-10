/**
 * API 层 —— 前后端唯一交互面（契约二：contracts/02-task-api.md）
 *
 * 两个实现，同一个接口：
 *   MockApi —— Phase 1 用：剧本式假事件流，localStorage 持久化
 *   HttpApi —— Phase 2 用：真后端（fetch + SSE）
 *
 * 切换方式：环境变量 VITE_API_MODE=http，默认 mock。
 */
import { startScriptedRun } from './mockRun';
import type { Automation, EventEnvelope, EventType, Project, Skill, TaskSummary } from './types';

/** ★ 第 8 批 问题4：手机直连状态。
 *  刻意分开【当前生效 enabled】与【配置里待生效 pending_lan】——
 *  监听地址要重启后端才变，混成一个字段会让用户以为"点了就通了"。 */
export interface LanStatus {
  enabled: boolean;        // 现在真正生效的（局域网可达）
  pending_lan: boolean;    // 配置文件里写的（重启后生效）
  needs_restart: boolean;  // 两者不一致 ⇒ 需要重启
  host: string;
  port: number;
  ip: string;
  token: string;           // 局域网模式下回显，用于拼给手机的完整链接
  token_set: boolean;
  url: string;             // 手机直接打开这个（带访问密码）
  ui_ready: boolean;       // 界面是否已构建（没构建手机打不开）
  ui_built_at: string | null;
}

/** ★ 2026-10-07「每个 Key 花费上限」：界面要显示的那几样 ✓（后端 `app/budget.py` 是唯一口径 ✓）。
 *
 *  ★ 注意 `measurable`：**上限对这把 Key 管不管用** ✓
 *    —— 只有语言模型那档能按 token 算出钱 ✓；出图/出视频/语音是按次或按秒计费，
 *    后端拿不到单价 ⇒ 拦不住 ✓ 界面上必须把这件事**写出来** ✓（默默不管 = 给假安心 ✗）。
 */
export interface BudgetKeyRow {
  key: string;
  roles: string[];          // 这把 Key 现在被哪些能力用着
  measurable: boolean;      // 上限管不管用（只有语言模型那档为 true）
  is_llm: boolean;          // 是不是当前语言模型用的那把
  used: boolean;
  cny: number;              // 本上限周期内已花（元）
  all_time_cny: number;     // 全部时间已花（元）—— 只为让用户心里有数
  tokens: number;
  calls: number;
  unpriced_calls: number;   // 没填单价、算不出钱的调用次数（如实报，不编 ✗）
  limit: number;            // 0 = 没设上限（不拦）
  since: string;
  left?: number;
  over?: boolean;
}

export interface BudgetStatus {
  ok: boolean;
  keys: BudgetKeyRow[];
  llm_key: string;
  enabled: boolean;
  blocked: string | null;   // 非空 = 现在正被上限拦着（里面是一句能照做的话）
  note: string;
}

/** ★ 2026-10-07「一键清干净」：**清之前先把话说清楚** ✓
 *  （会清什么 / 占多大 / **不会**碰什么 / 确认词是什么 / 现在能不能清 ✓）。 */
export interface DataClearPreview {
  ok: boolean;
  items: { key: string; label: string; exists: boolean; bytes: number }[];
  keeps: string[];          // 明确**不碰**的东西（原样显示给用户 ✓）
  phrase: string;           // 要用户原样打出来的确认词 ✓
  running: string[];        // 非空 = 有任务在跑，现在不许清 ✗
  note: string;
}

export interface DataClearResult {
  ok: boolean;
  cleared: string[];
  moved: string[];
  memory_reset: string[];
  backup: string;           // 原件挪到哪了（可回滚 ✓）
  note: string;
}

/** ★ 2026-10-10：浏览器实况缓存 `_edgeprof*` 的体检结果（**只读** ✓） */
export interface BrowserCacheStatus {
  dirs: number;
  files: number;
  bytes: number;
  mb: number;
  sample: string[];
  note: string;
}

/** ★ 2026-10-10：清理结果 —— `failed_count>0` 表示有几个正被占用没删掉 ✓（如实报 ✓） */
export interface BrowserCacheCleanResult {
  ok: boolean;
  removed: number;
  freed_bytes: number;
  freed_mb: number;
  failed: string[];
  failed_count: number;
  reason: string;
}

export interface AgentApi {
  listTasks(): Promise<TaskSummary[]>;
  createTask(input: string, projectId?: string): Promise<TaskSummary>;
  getTask(id: string): Promise<TaskSummary>;
  cancelTask(id: string): Promise<void>;
  /** 审批待确认命令：once=仅此一次 / always=总是允许（本任务内记住）/ deny=拒绝 */
  approveTask(id: string, callId: string, decision: 'once' | 'always' | 'deny'): Promise<{ ok: boolean }>;
  /** ★ editOfSeq：改一句重发 —— 把那条用户消息及其之后作废，用 text 从那里重跑 */
  sendMessage(taskId: string, text: string, role?: string, editOfSeq?: number): Promise<void>;
  /** 切换本会话身份（空 = 恢复默认助手）——立即注入对话历史 */
  setTaskIdentity(taskId: string, role: string): Promise<{ ok: boolean; identity: string }>;
  /** 重命名任务 */
  renameTask(taskId: string, title: string): Promise<{ ok: boolean }>;
  /** 置顶/取消置顶 */
  pinTask(taskId: string, pinned: boolean): Promise<{ ok: boolean; pinned: boolean }>;
  getEvents(taskId: string, afterSeq?: number): Promise<EventEnvelope[]>;
  /** 订阅实时事件（SSE 的 Mock 等价物）。返回退订函数。 */
  subscribeEvents(taskId: string, onEvent: (e: EventEnvelope) => void): () => void;
  listProjects(): Promise<Project[]>;
  createProject(name: string, masterPrompt: string): Promise<Project>;
  deleteProject(id: string): Promise<void>;
  listSkills(): Promise<Skill[]>;
  listAutomations(): Promise<Automation[]>;
  /** ★ 2026-10-06：kind 多一种 `watch`（**文件夹有变化就跑** ✓）+ 两个 watch 参数 ✓ */
  createAutomation(
    name: string, kind: 'schedule' | 'hook' | 'watch', taskInput: string,
    // ★ 2026-10-07（第 7 项）：频率扩成**五种** ✓（原来只有 interval/daily ⇒
    //   "每小时整点""每周一""每月 1 号"**表达不出来** ✗）
    //   · hourly ：用 time 里的**分**（第几分跑 ✓）
    //   · weekly ：weekday 1-7（1=周一 ✓）+ time
    //   · monthly：day 1-31（短月算月末 ✓）+ time
    schedule?: { kind: string; minutes?: number; time?: string; weekday?: number; day?: number } | null,
    watch?: { watchPath: string; watchSeconds?: number },
    // ★ 2026-10-07（第 7 项）：**单次花费上限** + **失败自动重试**（都可不填 ✓）
    extras?: { maxCostCny?: number; retries?: number },
  ): Promise<Automation>;
  toggleAutomation(id: string): Promise<void>;
  rotateWebhookSecret(id: string): Promise<{ ok: boolean; secret: string }>;
  /** ★ 2026-10-09（AGPL 批）：补 `enforced`/`mode`/`source_url`/`license` ——
   *  AGPL 版（`enforced=false`）界面上**不许出现「试用期 / 未经授权不得商用」** ✗
   *  那是 AGPL 第 10 条禁止的「附加限制」的暗示 ✓ 两档必须分得清 ✓ */
  getLicense(): Promise<{ trial_days_left: number; trial_expired: boolean; activated: boolean; kind: string; name: string; exp: string;
                          enforced?: boolean; mode?: string; note?: string; source_url?: string; license?: string }>;
  activateLicense(key: string): Promise<{ ok: boolean; message: string }>;
  deleteAutomation(id: string): Promise<void>;
  getFiles(taskId: string): Promise<{ name: string; is_dir: boolean; size: number }[]>;
  deleteTask(taskId: string): Promise<{ ok: boolean }>;
  getUsage(): Promise<{ input_tokens: number; output_tokens: number; total_tokens: number; calls: number; tasks_with_usage: number; task_count: number; by_model: Record<string, { input: number; output: number; calls: number }> }>;
  setAccessMode(mode: 'full' | 'auto_edit' | 'confirm'): Promise<{ ok: boolean; mode: string }>;
  getAccessMode(): Promise<{ mode: string }>;
  takeoverTask(taskId: string): Promise<{ ok: boolean; note?: string }>;
  resumeTask(taskId: string, text: string): Promise<{ ok: boolean; note?: string }>;
  ttsSpeak(taskId: string, text: string): Promise<{ ok: boolean; file: string; raw_url: string; playlist?: string[] }>;
  /** ★★ 2026-10-07 **真流式朗读**：后端 NDJSON 逐行回音频地址 ⇒ 第一句合成完就开口 ✓
   *  `onUrl` 拿到的地址**已经带好访问密码** ✓（调用方直接 `player.src = url` 即可 ✓）。
   *  ★★ 2026-10-08：**退回要说出来** ✓ —— 点名的后端没装 / 挂了，后端会改用系统语音兜底 ✓
   *    这时它会在同一行带上 `used`/`asked`/`why` ✓ ⇒ 走 `onNote` 告诉调用方 ✓
   *    （以前是**静默**换引擎 ✓ 用户点名 A、听到 B，界面上一个字都没有 ✗）。 */
  ttsSpeakStream(taskId: string, text: string, signal: AbortSignal,
                 onUrl: (url: string) => void,
                 onNote?: (n: { used: string; asked: string; why: string; policy?: string }) => void): Promise<void>;
  /** ★ 2026-10-07：记忆库（读 / 开关 / 清空） */
  getMemory(): Promise<{ enabled: boolean; count: number; entries: unknown[] }>;
  setMemory(patch: { enabled?: boolean; clear?: boolean }):
    Promise<{ ok: boolean; enabled: boolean; cleared: number }>;
  /** ★ 2026-10-07：知识库（列表 / 入库 / 删除） */
  listKbs(): Promise<{ name: string; description: string; chunks: number }[]>;
  ingestKb(name: string, folder: string): Promise<{ ok: boolean }>;
  deleteKb(name: string): Promise<{ ok: boolean }>;
  /** ★ 2026-10-07「每个 Key 花费上限」（用户点名要的 ✓）：
   *  看每把 Key 花了多少 / 上限多少 / 还能不能花 ✓；设上限 / 撤上限 / 清零重来 ✓ */
  /** ★ 2026-10-07（第 8 项）：**这句话该派谁** ✓ 返回空数组 = 没把握（不推 ✓） */
  suggestRoles(input: string): Promise<{ ok: boolean; suggestions: { role: string; dept: string; summary: string; why: string }[] }>;
  /** ★ 2026-10-07（第 9 项）：**跨任务审计流水** ✓（最新在前 ✓ 命令已打码 ✓） */
  getAudit(limit?: number, kind?: string): Promise<{
    ok: boolean; entries: { ts: string; kind: string;[k: string]: unknown }[];
    count: number; by_kind: Record<string, number>; max_entries: number; path: string;
  }>;
  getBudget(): Promise<BudgetStatus>;  setBudget(p: { key: string; limit?: number; reset_since?: boolean }): Promise<BudgetStatus>;
  /** ★ 2026-10-07「一键清干净」（**破坏性** ⇒ 后端要二次确认 ✓）：
   *  看会清掉什么 / 不会碰什么 ✓；真清时 `confirm` 必须**原样**是那句确认词 ✓ */
  getDataClear(): Promise<DataClearPreview>;
  clearData(confirm: string, parts: string[]): Promise<DataClearResult>;
  /** ★ 2026-10-10：浏览器实况缓存 `_edgeprof*`（每开一次浏览器留一个 profile ✗ 实测攒了约 2GB） */
  browserCacheStatus(): Promise<BrowserCacheStatus>;
  cleanBrowserCache(): Promise<BrowserCacheCleanResult>;
  /** ★ 2026-10-07：切换**出图引擎**（云端通义万相 / 本地 ComfyUI）——
   *  此前界面上**根本没有这个开关** ✗（其余每一档都能切 ✓ 唯独出图要手改配置文件 ✓） */
  setImage(provider: string): Promise<{ ok: boolean; provider: string; state: string; detail: string; note: string }>;
  /** ★ 2026-10-07：版本号（**后端是唯一来源** ✓ —— 关于页此前硬编码一行 ✗ 三处各说各的 ✓） */
  getVersion(): Promise<{ ok: boolean; name: string; vendor: string; version: string;
                          check_url_set: boolean; note: string }>;
  /** ★★ 2026-10-08：作者卡（只读 + **防伪签名** ✓ 用户定：主名只写工作室 ✓ 不写个人名字 ✓）
   *  · `verified` = 后端**真验签**的结论 ✓ 界面必须显示它 ✓（不许写死"正版"✗）
   *  · 邮箱/微信默认**不显示** ✓ 点一下才展开 ✓（界面清爽 ✓ 顺带少被爬 ✓） */
  getAuthorCard(): Promise<{ ok: boolean; org: string; line: string; email: string; wechat: string;
                              alias: string; signed_at: string; verified: boolean;
                              reason: string; fingerprint: string }>;
  /** ★ 2026-10-07：检查更新（**只查不装** ✓ 没配地址就如实说"没查"✓） */
  checkUpdate(): Promise<{ ok: boolean; current: string; latest: string; has_update: boolean;
                            notes: string; url: string; note: string }>;
  /** 会议模式：上传一段录音分片，转写并追加进任务工作区 meeting_notes.md
   *  ★ 2026-10-07：**转写失败时后端也回 2xx**（body 里 `ok:false` + `error` ✓ 见 meeting_chunk ✓）
   *    ⇒ 类型里必须有 `error` ✓ 调用方也必须**看 ok** ✓（丢掉返回值 = 静默失败 ✗）。 */
  meetingChunk(taskId: string, blob: Blob): Promise<{ ok: boolean; text: string; note?: string; file?: string; error?: string }>;
  meetingRead(taskId: string): Promise<{ ok: boolean; text: string }>;
  /** 团队：员工卡/群/群聊 */
  teamRoles(): Promise<{ roles: { role: string; dept: string; summary: string }[]; max: number }>;
  teamSoloSay(empId: string, text: string): Promise<{ ok: boolean; task_id: string }>;
  teamEmployees(): Promise<{ employees: any[]; max: number }>;
  teamAddEmployee(card: { name: string; dept?: string; role?: string; persona?: string }): Promise<{ ok: boolean; employee: any }>;
  teamDelEmployee(id: string): Promise<{ ok: boolean }>;
  /** ★ 2026-10-07：一键补齐角色卡（只补缺的 ✓ 已有卡不动 ✓ 只建卡不建群 ✓） */
  importRoleCards(): Promise<{ ok: boolean; added: string[]; skipped: string[]; note: string }>;
  teamUpdateEmployee(id: string, card: { name: string; dept?: string; role?: string; persona?: string; provider?: string | null; model_name?: string | null; base_url?: string | null; api_key_env?: string | null }): Promise<{ ok: boolean; employee: any }>;
  teamGroups(): Promise<{ groups: any[] }>;
  teamCreateGroup(name: string, members: string[], leader?: string, mode?: string): Promise<any>;
  /** ★ 第 7c 处：建群后改派发模式 / 换组长 */
  teamUpdateGroup(gid: string, patch: { mode?: string; leader?: string }): Promise<any>;
  teamDeleteGroup(gid: string): Promise<{ ok: boolean }>;
  teamFeed(gid: string, afterSeq: number): Promise<{ messages: any[] }>;
  teamSay(gid: string, text: string): Promise<{ ok: boolean; dispatched: any[] }>;
  /** ★ 第 8 批 问题4：手机直连（局域网）一键开通 */
  lanStatus(): Promise<LanStatus>;
  lanEnable(): Promise<{ ok: boolean } & LanStatus>;
  lanDisable(): Promise<{ ok: boolean } & LanStatus>;
  /** 语音输入（草稿模式）：转写但不发送，文字回填输入框供检查 */
  voiceTranscribe(blob: Blob): Promise<{ ok: boolean; text: string }>;
  /** 上传附件到任务工作区（图片/文档），返回工作区内的文件名 */
  uploadFile(taskId: string, file: File): Promise<{ ok: boolean; name: string; path: string; size: number }>;
  /** ★ Phase 2 ⑥：问服务商"此刻有哪些模型"（拉不到不是错误：source=preset + note 说明原因） */
  listModels(provider?: string, baseUrl?: string, refresh?: boolean): Promise<{
    provider: string; models: string[]; source: 'live' | 'preset'; note: string; cached?: boolean;
  }>;
  /** ★ Phase 2 ④⑤：能力槽状态（每个能力现在用谁、能不能用、坏了退到哪、本地档位推荐） */
  getCapabilities(): Promise<{
    capabilities: Record<string, {
      label: string; current: string; current_state: string;
      providers: Record<string, { label: string; kind: string; state: string; detail: string; models?: string[] }>;
      fallbacks: string[]; next_available: string | null;
    }>;
    local_tiers: Array<{ id: string; label: string }>;
    local_recommendation: { tier: string; label: string; reason: string; method: string;
                            detected: boolean; vram_gb: number; ram_gb: number };
  }>;
  /** ★ Phase 2 ⑤：切换 ASR 档位（mimo=云端 / local_qwen3=本地离线） */
  setAsrTier(provider: string, model?: string): Promise<{
    ok: boolean; provider: string; state: string; detail: string; note: string;
  }>;
  /** ★ 设置页正规化：把某一节恢复出厂默认（后端有白名单，危险节会被拒） */
  resetSettings(section: string): Promise<{ ok: boolean; section: string; note: string }>;
  /** ★ A-4：查看访问密码（后端**仅本机**放行，手机拿不到） */
  getAccessToken(): Promise<{ ok: boolean; token: string; set: boolean; note: string }>;
  /** ★ A-4：改访问密码（同样仅本机）。token 传空 = 服务端随机生成一个强的 */
  setAccessToken(token: string): Promise<{ ok: boolean; token: string; generated: boolean; note: string }>;
  /** ★ 群里「接着跑」（P0-4）：失败/被中断的活带着已有产物继续，不从头烧钱 */
  teamResume(gid: string, taskId: string, name?: string): Promise<{ ok: boolean; task_id: string }>;
  /** ★ 群里直接批（用户要求：他们的活、要点的允许，都该在群里，不用跑任务页）
   *  ★ 2026-10-06 多一档 `forever`（这类以后都别问 ✓ 跨任务、后端落盘 ✓）+ 命令原文 */
  teamApprove(gid: string, taskId: string, callId: string,
              decision: 'once' | 'always' | 'all' | 'forever' | 'deny', command?: string):
    Promise<{ ok: boolean; decision: string }>;
  /** ★★ A-1 收尾（2026-10-07）：群里**所有**正在等的审批 —— 后端权威口径 ✓
   *  为什么要它：界面上那块「还差 N 个决定」原来只数**这一屏 feed 里**的 ✗ ⇒
   *  藏在**还没加载到的更早消息**里的审批就数不到 ✓（用户看到的 N 会偏小 ✓）。
   *  口径与群 feed 同源（群里的任务 = feed 里出现过的 task_id ✓）不另立一套 ✗。 */
  groupApprovals(gid: string): Promise<{
    ok: boolean; group_id: string; count: number;
    approvals: { task_id: string; call_id: string; title: string; command: string }[];
  }>;
  /** ★ 开会：重新出纪要（收口那步撞上空响应时用它再收一次，讨论记录不用重来） */
  resummarizeMeeting(gid: string): Promise<{ ok: boolean; summary: string }>;
  /** ★ 2026-10-06「这类以后都别问」的清单与收回（在「执行环境」那节展示 ✓） */
  listForeverApprovals(): Promise<{ ok: boolean; rules: Record<string, string> }>;
  revokeForeverApproval(key: string): Promise<{ ok: boolean; rules: Record<string, string>; left: string[] }>;
  /** ★ 2026-10-06：切换语音合成后端（edge / melotts / pyttsx3 ✓ 没装的也能先选，会如实提示 ✓） */
  setTts(backend: string, voice?: string): Promise<{ ok: boolean; backend: string; state: string;
                                                       detail: string; note: string; voice?: string }>;
  /** ★ 2026-10-07「一键装 + 一键下模型」（用户要的：点了就能用 ✓） */
  installLocalAsr(): Promise<{ ok: boolean; kind: string; target: string }>;
  /** ★★ 2026-10-08：**本地朗读「一键装」**（用户："就像上面这个千问3似的，点一下它就安装"）
   *  `engine`：`melotts`（本仓安装器 ✓）| `kokoro`（pip + 下模型 ✓ 更小、可商用 ✓）
   *  ★ 与 ASR **共用同一把锁**（同一时刻只能跑一个安装 ✓ 两条 pip 撞一起会互相毁掉 ✗）
   *    进度看 `ttsInstallStatus`（与 `localAsrInstallStatus` 同一个任务状态 ✓）。 */
  installTtsEngine(engine: string): Promise<{ ok: boolean; kind: string; target: string }>;
  ttsInstallStatus(): Promise<{
    kind: string; state: string; lines: string[]; bytes: number; elapsed: number;
    error: string; target: string;
  }>;
  pullLocalAsrModel(tier: string): Promise<{ ok: boolean; kind: string; target: string }>;
  localAsrInstallStatus(): Promise<{
    kind: string; state: 'idle' | 'running' | 'done' | 'failed';
    lines: string[]; bytes: number; elapsed: number; error: string; target: string;
  }>;
  /** ★ 2026-10-07：知识库向量档位（本地优先 ✓ 用户拍板） */
  setKbEmbedder(embedder: string): Promise<{
    ok: boolean; embedder: string; state: string; detail: string; note: string;
  }>;
  /** ★ 2026-10-07：界面主题（深色 / 浅色 ✓ 用户要的两档 ✓） */
  setUiTheme(theme: 'dark' | 'light'): Promise<{ ok: boolean; theme: string; note: string }>;
  installKbEmbedder(): Promise<{ ok: boolean; kind: string; target: string }>;
  /** ★ 设置导出：可搬走的节（**不含密钥**） */
  exportSettings(): Promise<Record<string, unknown>>;
  /** ★ 设置导入：dryRun=true 只回报会改动什么（后端不落盘） */
  importSettings(sections: Record<string, unknown>, dryRun: boolean): Promise<{ ok: boolean; dry_run: boolean; changed: Record<string, string[]>; note: string }>;
  getSettings(): Promise<Record<string, unknown>>;
  /** ★ persist=true 时后端**同时写 User 级环境变量**（重启后仍有效；默认不写）
   *  ★ verifyKey=true（2026-10-10）：保存前**先拿这把 Key 打一次该服务商** ——
   *    服务商明确拒收（401/403）就**拒绝保存**并回人话（网络不通不算，照常保存） */
  setModel(provider: string, modelName: string, baseUrl: string | null, apiKey: string | null, apiKeyEnv: string | null, persist?: boolean, verifyKey?: boolean): Promise<{ ok: boolean; persisted?: boolean; key_env?: string; note?: string }>;
  /** ★ 2026-10-06：自动查单价（查 OpenRouter 公开表；服务商 API 本身不给价） */
  fetchPricing(p: { model?: string; fx?: number }): Promise<{
    found: boolean; model?: string; matched?: string; fx?: number;
    pricing?: { in?: number; out?: number; cached?: number };
    source?: string; fetched_at?: string; note: string;
  }>;
  /** 改执行环境（授权目录/审批清单/超时/SearXNG/沙箱）—— 改动需重启后端才完全生效 */
  setExecutor(p: {
    allowed_dirs?: string[];
    approval_required?: string[];
    timeout_seconds?: number;
    searxng_url?: string | null;
    sandbox?: string;
    sandbox_image?: string;
  }): Promise<{ ok: boolean; note?: string }>;
  /** ★ 2026-10-06：单任务步数上限 + 单价表（元/百万 token）—— 设置页那两项 */
  setLimits(p: {
    max_iterations?: number;
    pricing?: Record<string, { in?: number; out?: number; cached?: number }>;
  }): Promise<{ ok: boolean; max_iterations: number; pricing: Record<string, unknown>; note: string }>;
  /** 视频引擎配置（provider/模型/档位/Key/多引擎）——Key 写进程环境即刻生效 */
  setVideo(p: {
    provider?: string;
    model?: string;
    resolution?: string;
    ratio?: string;
    audio?: boolean | null;
    api_key_env?: string;
    api_key?: string;
    engines?: Record<string, { api_key_env?: string; model?: string; resolution?: string; api_key?: string }>;
  }): Promise<{ ok: boolean; note?: string }>;
}

// API 基址：dev/vite 与 手机直连用相对路径（同源或代理）；桌面壳（Tauri）里
// webview 的源是 http://tauri.localhost，相对路径会打到自己身上——必须指向后端回环地址。
export const API_BASE = ((): string => {
  const h = window.location;
  if (h.protocol === 'tauri:' || h.hostname === 'tauri.localhost') {
    return 'http://127.0.0.1:8642/api/v1';
  }
  return '/api/v1';
})();

/** ★ 给"浏览器自己发起的资源请求"用的地址（`<img src>` / `<video src>` / iframe）。

 *  为什么需要：工作区取文件（`/files/raw`）要鉴权，而 `<img>` **发不了自定义请求头** ——
 *  只写 `src=...` 会 401，图片看着有元素其实**一个字节都没下来**（本班实测：产物面板的图
 *  一直加载失败，直到验证脚本报出 401）。后端支持 `?token=` 查询参数（SSE 就是这么连的），
 *  这里统一补上；**一处实现、四处调用**（对话附件 / 正文图片 / 产物面板 / 灯箱）。
 *
 *  安全权衡：token 进了 URL。所以调用方只在本机/局域网自用场景用它取**自己工作区里的文件**；
 *  页面里没有任何第三方资源，也不会把这些地址发到外站。
 */
export function authedUrl(url: string): string {
  try {
    const tok = localStorage.getItem('authToken') ?? '';
    if (!tok || !url) return url;
    if (!url.startsWith('/') && !url.startsWith(API_BASE)) return url;   // 外站地址不动
    if (/[?&]token=/.test(url)) return url;
    return `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(tok)}`;
  } catch {
    return url;
  }
}

/** ★ 第 8c 处：把链接里的 `?token=` 收进 localStorage。
 *
 *  为什么必须有它：手机直连生成的链接是 `http://<ip>:8642/?token=<32 位密码>`，
 *  而**本前端此前只认 localStorage、根本不读 URL 里的 token** ⇒ 手机扫码进来后
 *  页面能打开（HTML 那次请求带着 token，后端放行），但**页面自己的 API 请求不带密码
 *  ⇒ 401 ⇒ 弹出密码框，让用户在手机上打那 32 位随机密码**。二维码等于白做。
 *  （本班实测发现的：只验了"二维码编码正确"，没验"扫了能不能进去"——补上。）
 *
 *  收下后立刻把 token 从地址栏抹掉：不要留在浏览器历史/截图/Referer 里。
 */
export function absorbTokenFromUrl(): void {
  try {
    if (typeof window === 'undefined') return;
    const u = new URL(window.location.href);
    const t = u.searchParams.get('token');
    if (!t) return;
    localStorage.setItem('authToken', t);
    u.searchParams.delete('token');
    window.history.replaceState({}, '', u.pathname + (u.search || '') + u.hash);
  } catch {
    /* 隐私模式 / 老浏览器：收不下就退回"手输密码"，不影响可用性 */
  }
}

// 模块加载即执行 —— 早于任何 API 调用与组件 effect（request() 是调用时才读 localStorage）。
absorbTokenFromUrl();

/** ★ 第 8e 处（C12 根治）：把访问密码注入做成**唯一入口**，不靠每个调用点自觉。
 *
 *  背景：`request()` 一直会带 token，但仓库里另有 7 处**直接 fetch / EventSource**：
 *  附件/TTS/记忆库/知识库/产物预览/设置开关/事件流。局域网模式下这些**静默 401** ——
 *  在"只服务本机"时代没人发现，开了手机直连才暴露（本班真局域网实测：控制台一条
 *  `Failed to load resource: 401`，手机上看不到 agent 的实时输出）。
 *
 *  做法：包一层 window.fetch —— 凡是同源 `/api/` 且还没带 token 的请求，自动补上。
 *  · 只补 token，不改方法/请求体/头，也不吞异常（出错就按原样发出去）；
 *  · 已经带 token 的 URL 原样放行（幂等）；
 *  · 非 /api/ 的请求一律不碰（不干扰页面去取静态资源）。
 */
if (typeof window !== 'undefined' && !(window as unknown as { __asTokenFetch?: boolean }).__asTokenFetch) {
  const _origFetch = window.fetch.bind(window);
  (window as unknown as { __asTokenFetch?: boolean }).__asTokenFetch = true;
  window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    try {
      const tok = localStorage.getItem('authToken');
      if (tok) {
        const url = typeof input === 'string' ? input
          : input instanceof URL ? input.href
            : (input as Request).url;
        if (url.includes('/api/') && !url.includes('token=')) {
          const fixed = `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(tok)}`;
          return _origFetch(fixed, init as RequestInit);
        }
      }
    } catch {
      /* 任何意外都退回原样请求 —— 认证不该让页面崩掉 */
    }
    return _origFetch(input as RequestInfo, init as RequestInit);
  }) as typeof window.fetch;
}

/**
 * 统一请求入口：**非 2xx 一律抛出**（带上后端的 detail）。
 *
 * 为什么必须统一（P1-8）：此前每个方法都写 `(await fetch(...)).json()`，
 * 后端返回 4xx/5xx 时错误体会被当成正常数据 ——
 *   · `listTasks` 在后端 500 时拿到 `{detail:...}`，前端把它当任务数组渲染；
 *   · 设置页在 422（未知 provider）时拿不到 `note`，于是显示"已保存"（实际没生效）；
 *   · `sendMessage` 失败时消息凭空消失，用户毫无察觉。
 * 2026-09-30 起所有 HttpApi 方法都走这里。
 */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // 手机直连（局域网模式）：自动附带访问密码（localStorage 存的 token）
  const _tok = localStorage.getItem('authToken') ?? '';
  const _sep = path.includes('?') ? '&' : '?';
  const _path = _tok ? `${path}${_sep}token=${encodeURIComponent(_tok)}` : path;
  const r = await fetch(`${API_BASE}${_path}`, init);
  if (!r.ok) {
    let detail = '';
    try {
      detail = ((await r.json()) as { detail?: string }).detail ?? '';
    } catch {
      /* 非 JSON 错误体 */
    }
    throw new Error(detail || `请求失败（HTTP ${r.status}）`);
  }
  if (r.status === 204) return undefined as T;
  return (await r.json()) as T;
}

/** 截断字符串（任务标题取前 30 字；mockRun 里另有一份同款实现） */
function clip(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + '…' : s;
}


/* ================= HttpApi（Phase 2 对接真后端）================= */

export class HttpApi implements AgentApi {
  async listTasks(): Promise<TaskSummary[]> {
    return request<TaskSummary[]>('/tasks');
  }

  async createTask(input: string, projectId?: string): Promise<TaskSummary> {
    // P1-15 起 provider 不可用会返回 503 + 原因；request() 会把它抛出，
    // 由 App 的错误 toast 显示——绝不把错误体当成任务对象塞进列表。
    return request<TaskSummary>('/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ input, project_id: projectId ?? null }),
    });
  }

  async getTask(id: string): Promise<TaskSummary> {
    return request<TaskSummary>(`/tasks/${id}`);
  }

  async cancelTask(id: string): Promise<void> {
    await request<unknown>(`/tasks/${id}/cancel`, { method: 'POST' });
  }

  async approveTask(
    id: string,
    callId: string,
    decision: 'once' | 'always' | 'deny',
  ): Promise<{ ok: boolean }> {
    // 409 = 没有等待中的审批（可能已被处理或任务已取消）——request() 会把 detail 带出来
    return request<{ ok: boolean }>(`/tasks/${id}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ call_id: callId, decision }),
    });
  }

  async sendMessage(taskId: string, text: string, role?: string, editOfSeq?: number): Promise<void> {
    const body: Record<string, unknown> = { text };
    if (role) body.role = role;
    if (editOfSeq != null) body.edit_of_seq = editOfSeq;
    await request<unknown>(`/tasks/${taskId}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  }

  async getEvents(taskId: string, afterSeq?: number): Promise<EventEnvelope[]> {
    const q = afterSeq != null ? `?after_seq=${afterSeq}` : '';
    return request<EventEnvelope[]>(`/tasks/${taskId}/events${q}`);
  }

  subscribeEvents(taskId: string, onEvent: (e: EventEnvelope) => void): () => void {
    // ★ C12 根治（第 8e 处）：EventSource 没法走 fetch 包装，必须自己带密码。
    //   否则局域网模式下**手机的实时输出流直接 401** —— 界面能打开，但看不到 agent
    //   在说什么（本班真局域网实测暴露：控制台里一条 401，任务列表之外的推送全哑）。
    const _t = localStorage.getItem('authToken');
    const _q = _t ? `?token=${encodeURIComponent(_t)}` : '';
    const es = new EventSource(`${API_BASE}/tasks/${taskId}/events/stream${_q}`);
    es.addEventListener('agent_event', (m) => {
      try {
        onEvent(JSON.parse((m as MessageEvent).data));
      } catch {
        /* 坏行忽略（契约：seq 防重复，丢一行不致命） */
      }
    });
    return () => es.close();
  }

  async listProjects(): Promise<Project[]> {
    return request<Project[]>('/projects');
  }

  async createProject(name: string, masterPrompt: string): Promise<Project> {
    return request<Project>('/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, master_prompt: masterPrompt }),
    });
  }

  async deleteProject(id: string): Promise<void> {
    await request<unknown>(`/projects/${id}`, { method: 'DELETE' });
  }

  async getFiles(taskId: string): Promise<{ name: string; is_dir: boolean; size: number }[]> {
    const r = await request<{ entries?: { name: string; is_dir: boolean; size: number }[] }>(
      `/tasks/${taskId}/files?path=.`,
    );
    return r?.entries ?? [];
  }

  async deleteTask(taskId: string): Promise<{ ok: boolean }> {
    return request<{ ok: boolean }>(`/tasks/${taskId}`, { method: 'DELETE' });
  }

  async getUsage(): Promise<{ input_tokens: number; output_tokens: number; total_tokens: number; calls: number; tasks_with_usage: number; task_count: number; by_model: Record<string, { input: number; output: number; calls: number }> }> {
    return request(`/usage`);
  }

  async setAccessMode(mode: 'full' | 'auto_edit' | 'confirm'): Promise<{ ok: boolean; mode: string }> {
    return request(`/access-mode`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode }) });
  }

  async getAccessMode(): Promise<{ mode: string }> {
    return request(`/access-mode`);
  }

  async takeoverTask(taskId: string): Promise<{ ok: boolean; note?: string }> {
    return request<{ ok: boolean; note?: string }>(`/tasks/${taskId}/takeover`, { method: 'POST' });
  }

  async resumeTask(taskId: string, text: string): Promise<{ ok: boolean; note?: string }> {
    return request<{ ok: boolean; note?: string }>(`/tasks/${taskId}/resume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
  }

  async setTaskIdentity(taskId: string, role: string): Promise<{ ok: boolean; identity: string }> {
    return request<{ ok: boolean; identity: string }>(`/tasks/${taskId}/identity`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role }),
    });
  }

  async renameTask(taskId: string, title: string): Promise<{ ok: boolean }> {
    return request<{ ok: boolean }>(`/tasks/${taskId}/update`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    });
  }

  async pinTask(taskId: string, pinned: boolean): Promise<{ ok: boolean; pinned: boolean }> {
    return request<{ ok: boolean; pinned: boolean }>(`/tasks/${taskId}/update`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pinned }),
    });
  }

  async ttsSpeak(taskId: string, text: string): Promise<{ ok: boolean; file: string; raw_url: string; playlist?: string[] }> {
    return request<{ ok: boolean; file: string; raw_url: string }>(`/tasks/${taskId}/tts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
  }

  /** ★★ 2026-10-07（**"朗读不出声"的根因就在这个函数里被堵死** ✓）：
   *
   *  现场（本班真点按钮 + 真抓包量出来的 ✓ 不是猜 ✗）：
   *    `POST /tasks/<id>/tts` → **200** ✓（后端合成没问题 ✓）
   *    后端回的地址是 `/api/v1/tasks/<id>/tts-audio/tts_xxxx_00.mp3` ✓
   *    浏览器 `<audio>` 去取它 → **401** ✗（**没带访问密码** ✓）
   *    ⇒ 控制台一条 `Failed to load resource: 401` ⇒ **一点声音都没有** ✗
   *
   *  为什么只有朗读中招：`<audio>`/`<img>` 这类**浏览器自己发起的请求带不了自定义头** ✗
   *    （`absorbTokenFromUrl` + `window.fetch` 包装器都够不着它们 ✓）
   *    ⇒ 必须把 `?token=` **拼进地址** ✓（项目里早有 `authedUrl` ✓ 图片/产物一直在用 ✓
   *       只有朗读这一处漏了 ✓）。
   *
   *  所以：**地址在 api 层就补好密码** ✓ 调用点拿到的永远是"能直接播的地址" ✓
   *  （这样下一个写朗读的人也**没法忘** ✓ —— 与 C12「不让调用点记着带密码」同一口径 ✓）。
   */
  async ttsSpeakStream(
    taskId: string, text: string, signal: AbortSignal, onUrl: (url: string) => void,
    // ★ 2026-10-08：后端**退回**别的引擎时会带 used/asked/why ✓ 这里原样交给调用方 ✓
    onNote?: (n: { used: string; asked: string; why: string; policy?: string }) => void,
  ): Promise<void> {
    const r = await fetch(authedUrl(`${API_BASE}/tasks/${taskId}/tts`), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
      signal,
    });
    if (!r.ok || !r.body) throw new Error(`TTS ${r.status}`);
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop() ?? '';
      for (const ln of lines) {
        if (!ln.trim()) continue;
        try {
          const j = JSON.parse(ln) as { url?: string; used?: string; asked?: string;
                                         why?: string; policy?: string };
          if (j.url) onUrl(authedUrl(j.url));   // ★ 补密码（就是这一步在治"朗读不出声"✓）
          // ★ 2026-10-08：**换过引擎就得说** ✓（used≠asked 才报 ✓ 正常路径一个字不多 ✓）
          if (onNote && j.policy) {
            // ★ 2026-10-08：**策略性换引擎**（长文本 ⇒ 换快的 ✓）—— 与下面那条**不是一回事** ✓
            //   那句说的是「你选的用不了」✗ 这句说的是「这段太长，我替你换了快的」✓ 别混 ✓
            onNote({ used: j.used ?? '', asked: j.asked ?? '', why: j.why ?? '', policy: j.policy });
          } else if (onNote && j.used && j.asked && j.used !== j.asked) {
            onNote({ used: j.used, asked: j.asked, why: j.why ?? '' });
          }
        } catch {
          /* 非 JSON 行跳过（坏行不该打断整段朗读 ✓） */
        }
      }
    }
  }

  async getMemory(): Promise<{ enabled: boolean; count: number; entries: unknown[] }> {
    return request<{ enabled: boolean; count: number; entries: unknown[] }>('/memory');
  }

  async setMemory(patch: { enabled?: boolean; clear?: boolean }) {
    return request<{ ok: boolean; enabled: boolean; cleared: number }>('/memory', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
  }

  async listKbs(): Promise<{ name: string; description: string; chunks: number }[]> {
    const d = await request<{ kbs?: { name: string; description: string; chunks: number }[] }>('/kb');
    return d?.kbs ?? [];
  }

  async ingestKb(name: string, folder: string): Promise<{ ok: boolean }> {
    return request<{ ok: boolean }>('/kb/ingest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, folder }),
    });
  }

  async deleteKb(name: string): Promise<{ ok: boolean }> {
    return request<{ ok: boolean }>(`/kb/${encodeURIComponent(name)}`, { method: 'DELETE' });
  }

  /** ★ 2026-10-07：一键清干净（破坏性）——先看清单，再原样打出确认词 ✓ */
  async getDataClear(): Promise<DataClearPreview> {
    return request<DataClearPreview>('/data/clear');
  }
  async clearData(confirm: string, parts: string[]): Promise<DataClearResult> {
    return request<DataClearResult>('/data/clear', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm, parts }),
    });
  }

  /** ★ 2026-10-10：浏览器实况缓存（`_edgeprof*`）—— 看看攒了多少（**只读** ✓） */
  async browserCacheStatus(): Promise<BrowserCacheStatus> {
    return request<BrowserCacheStatus>('/data/browser-cache');
  }
  /** ★ 2026-10-10：清掉它（**只删这一类目录** ✗ 别的字节都不碰 ✓） */
  async cleanBrowserCache(): Promise<BrowserCacheCleanResult> {
    return request<BrowserCacheCleanResult>('/data/browser-cache/clean', { method: 'POST' });
  }

  /** ★ 2026-10-07：切出图引擎（云端 / 本地）—— 照 ASR/TTS/KB 的规矩：校验过再写 ✓ */
  async setImage(provider: string): Promise<{ ok: boolean; provider: string; state: string; detail: string; note: string }> {
    return request<{ ok: boolean; provider: string; state: string; detail: string; note: string }>(
      '/settings/image', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider }),
      });
  }

  /** ★ 2026-10-07：版本号 —— 后端 `app/version.py` 是**唯一来源** ✓（前端不硬编码 ✗） */
  async getVersion() {
    return request<{ ok: boolean; name: string; vendor: string; version: string;
                     check_url_set: boolean; note: string }>('/version');
  }

  /** ★★ 2026-10-08：作者卡 —— 验签结论由**后端**给 ✓ 前端只负责显示 ✓ */
  async getAuthorCard() {
    return request<{ ok: boolean; org: string; line: string; email: string; wechat: string;
                     alias: string; signed_at: string; verified: boolean;
                     reason: string; fingerprint: string }>('/author');
  }

  /** ★ 2026-10-07：检查更新（**只查不装** ✓ 查到新版本只告诉你去哪下 ✓） */
  async checkUpdate() {
    return request<{ ok: boolean; current: string; latest: string; has_update: boolean;
                     notes: string; url: string; note: string }>('/version/check', { method: 'POST' });
  }

  /** ★ 2026-10-07：每把 Key 花了多少 / 上限多少 / 现在能不能花 ✓ */
  async getBudget(): Promise<BudgetStatus> {
    return request<BudgetStatus>('/budget');
  }

  /** ★ 2026-10-07：设上限 / 撤上限（limit=0）/ 清零重来（reset_since=true）✓ */
  async setBudget(p: { key: string; limit?: number; reset_since?: boolean }): Promise<BudgetStatus> {
    return request<BudgetStatus>('/budget', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(p),
    });
  }

  async meetingChunk(taskId: string, blob: Blob): Promise<{ ok: boolean; text: string; note?: string; file?: string; error?: string }> {
    const fd = new FormData();
    // ★ 2026-10-07：文件名要跟**真实类型**一致 ✓ —— 与 `voiceTranscribe` 同一条规矩 ✓
    //   写死 `chunk.webm` 而内容其实是 WAV 的话：后端 `_to_wav()` 会照后缀当成 webm ✗
    //   ⇒ 去叫 ffmpeg（没装）⇒ 白跑一趟 ✓（现在会议改录 WAV 了 ✓ 这个后缀必须跟着改 ✓）
    const name = blob.type === 'audio/wav' ? 'chunk.wav'
      : blob.type === 'audio/mpeg' ? 'chunk.mp3'
        : blob.type === 'audio/ogg' ? 'chunk.ogg' : 'chunk.webm';
    fd.append('audio', blob, name);
    return request<{ ok: boolean; text: string; note?: string; file?: string; error?: string }>(`/tasks/${taskId}/meeting`, {
      method: 'POST',
      body: fd,
    });
  }

  async teamRoles(): Promise<{ roles: { role: string; dept: string; summary: string }[]; max: number }> {
    return request('/team/roles');
  }
  /** ★ 2026-10-07（第 8 项）：**这句话该派谁** ✓（空数组 = 没把握 ⇒ 界面就别显示 ✓） */
  async suggestRoles(input: string) {
    return request<{ ok: boolean; suggestions: { role: string; dept: string; summary: string; why: string }[] }>(
      `/roles/suggest?input=${encodeURIComponent(String(input || '').slice(0, 200))}`);
  }
  /** ★ 2026-10-07（第 9 项）：**跨任务审计流水** ✓ */
  async getAudit(limit = 200, kind = '') {
    return request<{
      ok: boolean; entries: { ts: string; kind: string;[k: string]: unknown }[];
      count: number; by_kind: Record<string, number>; max_entries: number; path: string;
    }>(`/audit?limit=${limit}${kind ? `&kind=${encodeURIComponent(kind)}` : ''}`);
  }
  async teamSoloSay(empId: string, text: string): Promise<{ ok: boolean; task_id: string }> {
    return request(`/team/employees/${empId}/say`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) });
  }
  async teamEmployees(): Promise<{ employees: any[]; max: number }> {
    return request('/team/employees');
  }
  async teamAddEmployee(card: { name: string; dept?: string; role?: string; persona?: string }): Promise<{ ok: boolean; employee: any }> {
    return request('/team/employees', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(card) });
  }
  async teamUpdateEmployee(id: string, card: { name: string; dept?: string; role?: string; persona?: string; provider?: string | null; model_name?: string | null; base_url?: string | null; api_key_env?: string | null }): Promise<{ ok: boolean; employee: any }> {
    return request<{ ok: boolean; employee: any }>(`/team/employees/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(card) });
  }
  async teamDelEmployee(id: string): Promise<{ ok: boolean }> {
    return request(`/team/employees/${id}`, { method: 'DELETE' });
  }

  /** ★ 2026-10-07 一键补齐角色卡（后端只补缺的 ✓ 已有卡不动 ✓ 只建卡不建群 ✓） */
  async importRoleCards(): Promise<{ ok: boolean; added: string[]; skipped: string[]; note: string }> {
    return request('/team/employees/import-roles', { method: 'POST' });
  }
  async teamGroups(): Promise<{ groups: any[] }> {
    return request('/team/groups');
  }
  async teamCreateGroup(name: string, members: string[], leader?: string, mode?: string): Promise<any> {
    return request('/team/groups', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, members, ...(leader ? { leader } : {}), ...(mode ? { mode } : {}) }) });
  }
  /** ★ 二十六轮第 7 批第 7c 处：建群后改派发模式 / 换组长（此前 mode 只能建群时定死）。 */
  async teamUpdateGroup(gid: string, patch: { mode?: string; leader?: string }): Promise<any> {
    return request(`/team/groups/${gid}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch) });
  }
  async teamDeleteGroup(gid: string): Promise<{ ok: boolean }> {
    return request(`/team/groups/${gid}`, { method: 'DELETE' });
  }
  async teamFeed(gid: string, afterSeq: number): Promise<{ messages: any[] }> {
    return request(`/team/groups/${gid}/feed?after_seq=${afterSeq}`);
  }
  async teamSay(gid: string, text: string): Promise<{ ok: boolean; dispatched: any[] }> {
    return request(`/team/groups/${gid}/say`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) });
  }
  /** ★ 第 8 批 问题4：手机直连。开通/收回都返回最新状态（含给手机用的完整链接）。 */
  async lanStatus(): Promise<LanStatus> {
    return request('/lan/status');
  }
  async lanEnable(): Promise<{ ok: boolean } & LanStatus> {
    return request('/lan/enable', { method: 'POST' });
  }
  async lanDisable(): Promise<{ ok: boolean } & LanStatus> {
    return request('/lan/disable', { method: 'POST' });
  }

  async voiceTranscribe(blob: Blob): Promise<{ ok: boolean; text: string }> {
    const fd = new FormData();
    // ★ 文件名要跟**真实类型**一致：写死 draft.webm 的话，后端会照后缀把 wav 当成 webm，
    //   网关直接回 `input_audio.format must be one of: wav, mp3. Got: webm`
    //   （2026-10-05 用户报"语音输入根本不好使"的直接原因之一）
    const name = blob.type === 'audio/wav' ? 'draft.wav'
      : blob.type === 'audio/mpeg' ? 'draft.mp3'
        : blob.type === 'audio/ogg' ? 'draft.ogg' : 'draft.webm';
    fd.append('audio', blob, name);
    return request<{ ok: boolean; text: string }>('/voice/transcribe', {
      method: 'POST',
      body: fd,
    });
  }

  async meetingRead(taskId: string): Promise<{ ok: boolean; text: string }> {
    return request<{ ok: boolean; text: string }>(`/tasks/${taskId}/meeting`);
  }

  /** 附件上传：用 FormData，**不设 Content-Type**（让浏览器带 boundary） */
  async uploadFile(taskId: string, file: File): Promise<{ ok: boolean; name: string; path: string; size: number }> {
    const fd = new FormData();
    fd.append('file', file);
    return request<{ ok: boolean; name: string; path: string; size: number }>(`/tasks/${taskId}/files`, {
      method: 'POST',
      body: fd,
    });
  }

  async fetchPricing(p: { model?: string; fx?: number }) {
    return request<{ found: boolean; model?: string; matched?: string; fx?: number;
      pricing?: { in?: number; out?: number; cached?: number };
      source?: string; fetched_at?: string; note: string }>(
      '/settings/pricing/fetch',
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(p) },
    );
  }

  async setLimits(p: {
    max_iterations?: number;
    pricing?: Record<string, { in?: number; out?: number; cached?: number }>;
  }): Promise<{ ok: boolean; max_iterations: number; pricing: Record<string, unknown>; note: string }> {
    return request<{ ok: boolean; max_iterations: number; pricing: Record<string, unknown>; note: string }>(
      '/settings/limits',
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(p) },
    );
  }

  async setExecutor(p: {
    allowed_dirs?: string[];
    approval_required?: string[];
    timeout_seconds?: number;
    searxng_url?: string | null;
    sandbox?: string;
    sandbox_image?: string;
  }): Promise<{ ok: boolean; note?: string }> {
    return request<{ ok: boolean; note?: string }>('/settings/executor', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(p),
    });
  }

  async setVideo(p: {
    provider?: string;
    model?: string;
    resolution?: string;
    ratio?: string;
    audio?: boolean | null;
    api_key_env?: string;
    api_key?: string;
    engines?: Record<string, { api_key_env?: string; model?: string; resolution?: string; api_key?: string }>;
  }): Promise<{ ok: boolean; note?: string }> {
    return request<{ ok: boolean; note?: string }>('/settings/video', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(p),
    });
  }

  async resetSettings(section: string): Promise<{ ok: boolean; section: string; note: string }> {
    return request('/settings/reset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ section }),
    });
  }

  async teamResume(gid: string, taskId: string, name?: string) {
    return request<{ ok: boolean; task_id: string }>(`/team/groups/${gid}/resume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: taskId, name }),
    });
  }

  // ★ 2026-10-06：多一档 `forever`（**这类以后都别问** ✓ 跨任务、后端落盘 ✓）。
  //   把命令原文一并带上 —— 后端要用它算"这类"是哪个程序（python/pytest/git… ✓）。
  async teamApprove(
    gid: string, taskId: string, callId: string,
    decision: 'once' | 'always' | 'all' | 'forever' | 'deny', command = '',
  ) {
    return request<{ ok: boolean; decision: string }>(`/team/groups/${gid}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: taskId, call_id: callId, decision, command }),
    });
  }

  // ★★ A-1 收尾（2026-10-07）：**这个群里所有在等的审批** ✓（只读 GET ✓ 不改任何状态 ✓）
  //   —— 「还差 N 个决定」的权威来源 ✓（含还没加载进 feed 的更早消息里的审批 ✓）
  async groupApprovals(gid: string) {
    return request<{
      ok: boolean; group_id: string; count: number;
      approvals: { task_id: string; call_id: string; title: string; command: string }[];
    }>(`/team/groups/${gid}/approvals`);
  }

  async resummarizeMeeting(gid: string) {
    return request<{ ok: boolean; summary: string }>(`/team/groups/${gid}/meeting/resummarize`, { method: 'POST' });
  }

  /** ★ 2026-10-07 一键装本地 ASR 依赖 / 一键下模型 / 看实时状态 ✓ */
  async installLocalAsr() {
    return request<{ ok: boolean; kind: string; target: string }>('/settings/asr/install', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    });
  }

  async pullLocalAsrModel(tier: string) {
    return request<{ ok: boolean; kind: string; target: string }>('/settings/asr/pull', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tier }),
    });
  }

  // ★★ 2026-10-08：**本地朗读「一键装」** ✓（melotts = 本仓安装器 / kokoro = pip + 下模型）
  async installTtsEngine(engine: string) {
    return request<{ ok: boolean; kind: string; target: string }>('/settings/tts/install', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ engine }),
    });
  }

  async ttsInstallStatus() {
    return request<{
      kind: string; state: string; lines: string[]; bytes: number; elapsed: number;
      error: string; target: string;
    }>('/settings/tts/install/status');
  }

  async localAsrInstallStatus() {
    return request<{
      kind: string; state: 'idle' | 'running' | 'done' | 'failed';
      lines: string[]; bytes: number; elapsed: number; error: string; target: string;
    }>('/settings/asr/install/status');
  }

  /** ★ 2026-10-07 知识库向量档位（本地优先 ✓ 一键装 ✓） */
  async setKbEmbedder(embedder: string) {
    return request<{ ok: boolean; embedder: string; state: string; detail: string; note: string }>(
      '/settings/kb', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ embedder }),
      });
  }

  /** ★ 2026-10-07 界面主题（深色 / 浅色） */
  async setUiTheme(theme: 'dark' | 'light') {
    return request<{ ok: boolean; theme: string; note: string }>('/settings/ui', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ theme }),
    });
  }

  async installKbEmbedder() {
    return request<{ ok: boolean; kind: string; target: string }>('/settings/kb/install', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    });
  }

  async exportSettings() {
    return request<Record<string, unknown>>('/settings/export');
  }

  /** ★ 2026-10-06 切换语音合成后端（照 ASR 那个口子的规矩：校验过再写 ✓ 没装的如实提示 ✓）
   *  ★ 2026-10-08：带**音色**（千问3 有 9 个 ✓ 别的后端不认它 ✓ 传了不合法后端会 422 ✓）*/
  async setTts(backend: string, voice?: string) {
    return request<{ ok: boolean; backend: string; state: string; detail: string; note: string;
                     voice?: string }>(
      '/settings/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(voice ? { backend, voice } : { backend }),
      });
  }

  /** ★ 2026-10-06「这类以后都别问」：清单 + 收回（key 为空 = 全收回 ✓） */
  async listForeverApprovals() {
    return request<{ ok: boolean; rules: Record<string, string> }>('/settings/approval/forever');
  }

  async revokeForeverApproval(key: string) {
    return request<{ ok: boolean; rules: Record<string, string>; left: string[] }>(
      '/settings/approval/forever/revoke', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key }),
      });
  }

  async importSettings(sections: Record<string, unknown>, dryRun: boolean) {
    return request<{ ok: boolean; dry_run: boolean; changed: Record<string, string[]>; note: string }>('/settings/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sections, dry_run: dryRun }),
    });
  }

  async getAccessToken() {
    return request<{ ok: boolean; token: string; set: boolean; note: string }>('/server/token');
  }

  async setAccessToken(token: string) {
    return request<{ ok: boolean; token: string; generated: boolean; note: string }>('/server/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    });
  }

  async getSettings(): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>('/settings');
  }

  async getCapabilities() {
    return request<Awaited<ReturnType<AgentApi['getCapabilities']>>>('/capabilities');
  }

  async setAsrTier(provider: string, model?: string) {
    return request<Awaited<ReturnType<AgentApi['setAsrTier']>>>('/settings/asr', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider, model: model ?? '' }),
    });
  }

  /** ★ Phase 2 ⑥：问服务商"此刻有哪些模型"（拉不到不是错误：source=preset + note 说明原因） */
  async listModels(provider?: string, baseUrl?: string, refresh?: boolean): Promise<{
    provider: string; models: string[]; source: 'live' | 'preset'; note: string; cached?: boolean;
  }> {
    const q = new URLSearchParams();
    if (provider) q.set('provider', provider);
    if (baseUrl) q.set('base_url', baseUrl);
    if (refresh) q.set('refresh', '1');
    return request(`/models?${q.toString()}`);
  }

  async setModel(
    provider: string,
    modelName: string,
    baseUrl: string | null,
    apiKey: string | null,
    apiKeyEnv: string | null,
    persist = false,
    verifyKey = false,
  ): Promise<{ ok: boolean; persisted?: boolean; key_env?: string; note?: string }> {
    return request<{ ok: boolean; persisted?: boolean; key_env?: string; note?: string }>('/settings/model', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider, model_name: modelName, base_url: baseUrl, api_key: apiKey, api_key_env: apiKeyEnv, persist, verify_key: verifyKey }),
    });
  }


  async listSkills(): Promise<Skill[]> {
    return request<Skill[]>('/skills');
  }

  async listAutomations(): Promise<Automation[]> {
    return request<Automation[]>('/automations');
  }

  // ★ 2026-10-06：多一种 kind = `watch`（**文件夹有变化就跑** ✓）+ 两个 watch 参数 ✓
  async createAutomation(
    name: string,
    kind: 'schedule' | 'hook' | 'watch',
    taskInput: string,
    schedule?: { kind: string; minutes?: number; time?: string; weekday?: number; day?: number } | null,
    watch?: { watchPath: string; watchSeconds?: number },
    extras?: { maxCostCny?: number; retries?: number },
  ): Promise<Automation> {
    return request<Automation>('/automations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name, kind, task_input: taskInput, schedule: schedule ?? null,
        // ★ 2026-10-07（第 7 项）：单次上限 + 重试次数（不填就不带 ⇒ 后端按"不拦/不重试"✓）
        max_cost_cny: extras?.maxCostCny ?? null,
        retries: extras?.retries ?? null,
        watch_path: watch?.watchPath ?? null,
        watch_seconds: watch?.watchSeconds ?? null,
      }),
    });
  }

  async toggleAutomation(id: string): Promise<void> {
    await request<unknown>(`/automations/${id}/toggle`, { method: 'POST' });
  }

  async rotateWebhookSecret(id: string): Promise<{ ok: boolean; secret: string }> {
    return request<{ ok: boolean; secret: string }>(`/automations/${id}/rotate`, { method: 'POST' });
  }

  async getLicense() {
    return request<{ trial_days_left: number; trial_expired: boolean; activated: boolean; kind: string; name: string; exp: string }>('/license');
  }

  async activateLicense(key: string) {
    return request<{ ok: boolean; message: string }>('/license', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key }) });
  }

  async deleteAutomation(id: string): Promise<void> {
    await request<unknown>(`/automations/${id}`, { method: 'DELETE' });
  }
}

/* ================= MockApi（Phase 1 当前使用）================= */

const TASKS_KEY = 'agentshell.tasks';
const PROJECTS_KEY = 'agentshell.projects';
const EVENTS_KEY = (id: string) => `agentshell.events.${id}`;

type Listener = (e: EventEnvelope) => void;

export class MockApi implements AgentApi {
  private tasks = new Map<string, TaskSummary>();
  private autos = new Map<string, Automation>();
  private projects = new Map<string, Project>();
  private events = new Map<string, EventEnvelope[]>();
  private listeners = new Map<string, Set<Listener>>();
  private cancelled = new Set<string>();
  private seqCursor = new Map<string, number>();

  constructor() {
    try {
      const raw = localStorage.getItem(TASKS_KEY);
      if (raw) for (const t of JSON.parse(raw) as TaskSummary[]) this.tasks.set(t.id, t);
    } catch {
      /* 首次运行无数据 */
    }
  }

  private persistTasks() {
    localStorage.setItem(TASKS_KEY, JSON.stringify([...this.tasks.values()]));
  }

  private appendEvent(e: EventEnvelope) {
    const list = this.events.get(e.task_id) ?? [];
    list.push(e);
    this.events.set(e.task_id, list);
    try {
      localStorage.setItem(EVENTS_KEY(e.task_id), JSON.stringify(list));
    } catch {
      /* 存储满了就不持久化，内存里还能跑 */
    }
    this.listeners.get(e.task_id)?.forEach((fn) => fn(e));
  }

  private makeEvent<T>(taskId: string, type: EventType, payload: T): EventEnvelope<T> {
    const seq = (this.seqCursor.get(taskId) ?? 0) + 1;
    this.seqCursor.set(taskId, seq);
    return {
      id: `evt_${String(seq).padStart(6, '0')}`,
      seq,
      task_id: taskId,
      type,
      version: 1,
      ts: new Date().toISOString(),
      payload,
    };
  }

  private updateTask(id: string, patch: Partial<TaskSummary>) {
    const t = this.tasks.get(id);
    if (!t) return;
    this.tasks.set(id, { ...t, ...patch, updated_at: new Date().toISOString() });
    this.persistTasks();
  }

  async listTasks(): Promise<TaskSummary[]> {
    return [...this.tasks.values()].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  }

  async createTask(input: string, projectId?: string): Promise<TaskSummary> {
    const id = `task_${new Date().toISOString().slice(0, 10).replace(/-/g, '')}_${Math.random()
      .toString(36)
      .slice(2, 6)}`;
    const now = new Date().toISOString();
    const task: TaskSummary = {
      id,
      title: clip(input, 30) || '新任务',
      status: 'running',
      created_at: now,
      updated_at: now,
      project_id: projectId,
    };
    this.tasks.set(id, task);
    this.seqCursor.set(id, 0);
    this.persistTasks();
    this.startRun(id, input, false);
    return task;
  }

  async getTask(id: string): Promise<TaskSummary> {
    const t = this.tasks.get(id);
    if (!t) throw new Error(`task not found: ${id}`);
    return t;
  }

  async cancelTask(id: string): Promise<void> {
    this.cancelled.add(id);
  }

  async approveTask(
    taskId: string,
    callId: string,
    _decision: 'once' | 'always' | 'deny',
  ): Promise<{ ok: boolean }> {
    // Mock 剧本不触发审批（mockRun.ts 不调 shell_exec）；这里只做形态对齐，
    // 并把状态推回 running，保证 UI 的按钮流程在 mock 模式下也能走通。
    this.appendEvent(
      this.makeEvent(taskId, 'status', {
        state: 'running',
        detail: '审批完成，继续执行',
        call_id: callId,
      }),
    );
    this.updateTask(taskId, { status: 'running' });
    return { ok: true };
  }

  async sendMessage(taskId: string, text: string, role?: string): Promise<void> {
    void role; // Mock 不消费身份
    this.appendEvent(this.makeEvent(taskId, 'message', { role: 'user', text }));
    this.updateTask(taskId, { status: 'running' });
    this.startRun(taskId, text, true);
  }

  async getEvents(taskId: string, afterSeq?: number): Promise<EventEnvelope[]> {
    if (!this.events.has(taskId)) {
      try {
        const raw = localStorage.getItem(EVENTS_KEY(taskId));
        const list = raw ? (JSON.parse(raw) as EventEnvelope[]) : [];
        this.events.set(taskId, list);
        // 回填 seq 游标：页面刷新后追加消息时，seq 不能从 1 重新开始
        //（否则新事件会被前端的 seq 去重逻辑吞掉，事件 id 还会撞 React key）
        const maxSeq = list.reduce((m, e) => Math.max(m, e.seq), 0);
        this.seqCursor.set(taskId, Math.max(this.seqCursor.get(taskId) ?? 0, maxSeq));
      } catch {
        this.events.set(taskId, []);
      }
    }
    const list = this.events.get(taskId) ?? [];
    return afterSeq != null ? list.filter((e) => e.seq > afterSeq) : [...list];
  }

  subscribeEvents(taskId: string, onEvent: Listener): () => void {
    let set = this.listeners.get(taskId);
    if (!set) {
      set = new Set();
      this.listeners.set(taskId, set);
    }
    set.add(onEvent);
    return () => set!.delete(onEvent);
  }

  async listProjects(): Promise<Project[]> {
    if (this.projects.size === 0) {
      try {
        const raw = localStorage.getItem(PROJECTS_KEY);
        if (raw) for (const p of JSON.parse(raw) as Project[]) this.projects.set(p.id, p);
      } catch {
        /* 首次运行无数据 */
      }
    }
    return [...this.projects.values()].sort((a, b) => b.created_at.localeCompare(a.created_at));
  }

  async createProject(name: string, masterPrompt: string): Promise<Project> {
    const p: Project = {
      id: `proj_${Math.random().toString(36).slice(2, 8)}`,
      name,
      master_prompt: masterPrompt,
      created_at: new Date().toISOString(),
    };
    this.projects.set(p.id, p);
    localStorage.setItem(PROJECTS_KEY, JSON.stringify([...this.projects.values()]));
    return p;
  }

  async deleteProject(id: string): Promise<void> {
    this.projects.delete(id);
    localStorage.setItem(PROJECTS_KEY, JSON.stringify([...this.projects.values()]));
  }

  async listSkills(): Promise<Skill[]> {
    // Mock 技能库：与后端示例技能同名的静态清单
    return [
      { name: '写作助手', description: '写诗、文案、文章、报告等创作型文字任务时使用——风格规范与流程' },
      { name: '桌面整理', description: '整理、归类、清理桌面或某目录下的文件时使用——安全操作规范' },
    ];
  }

  async listAutomations(): Promise<Automation[]> {
    return [...this.autos.values()].sort((a, b) => b.created_at.localeCompare(a.created_at));
  }

  async createAutomation(
    name: string,
    kind: 'schedule' | 'hook',
    taskInput: string,
    schedule?: { kind: string; minutes?: number; time?: string; weekday?: number; day?: number },
  ): Promise<Automation> {
    const a: Automation = {
      id: `auto_${Math.random().toString(36).slice(2, 8)}`,
      name,
      kind,
      task_input: taskInput,
      schedule,
      enabled: true,
      created_at: new Date().toISOString(),
    };
    this.autos.set(a.id, a);
    return a;
  }

  async toggleAutomation(id: string): Promise<void> {
    const a = this.autos.get(id);
    if (a) a.enabled = !a.enabled;
  }

  async rotateWebhookSecret(): Promise<{ ok: boolean; secret: string }> {
    return Promise.reject(new Error('演示模式不支持 Webhook'));
  }

  async getLicense() {
    return { trial_days_left: 30, trial_expired: false, activated: false, kind: '试用期（禁止商用）', name: '', exp: '' };
  }

  async activateLicense(): Promise<{ ok: boolean; message: string }> {
    return { ok: true, message: '演示模式：授权录入仅作展示' };
  }

  async deleteAutomation(id: string): Promise<void> {
    this.autos.delete(id);
  }

  async getFiles(): Promise<{ name: string; is_dir: boolean; size: number }[]> {
    return []; // Mock 无真实工作区文件
  }

  async deleteTask(taskId: string): Promise<{ ok: boolean }> {
    this.tasks.delete(taskId);
    this.persistTasks();
    return { ok: true };
  }

  async getUsage() {
    return { input_tokens: 0, output_tokens: 0, total_tokens: 0, calls: 0, tasks_with_usage: 0, task_count: this.tasks.size, by_model: {} };
  }

  async setAccessMode(mode: string) { return { ok: true, mode }; }

  async getAccessMode() { return { mode: 'auto_edit' }; }

  async getSettings(): Promise<Record<string, unknown>> {
    return {};
  }

  async exportSettings() {
    return { ok: true, sections: {}, note: '演示模式' };
  }
  async setTts(_backend: string) {
    return { ok: true, backend: _backend, state: 'ready', detail: '演示模式',
             note: '演示模式：没有真的切换' };
  }
  async installLocalAsr() {
    return { ok: true, kind: 'install', target: 'qwen-asr（演示模式：没有真的装 ✓）' };
  }
  async pullLocalAsrModel(tier: string) {
    return { ok: true, kind: 'pull', target: `${tier}（演示模式：没有真的下 ✓）` };
  }
  async localAsrInstallStatus() {
    return { kind: '', state: 'idle' as const, lines: [], bytes: 0, elapsed: 0,
             error: '', target: '' };
  }
  // ★ 2026-10-08：本地朗读一键装（演示模式：如实说没装 ✓ 不假装 ✓）
  async installTtsEngine(engine: string) {
    return { ok: true, kind: 'install-tts', target: `${engine}（演示模式：没有真的装 ✓）` };
  }
  async ttsInstallStatus() {
    return { kind: '', state: 'idle' as const, lines: [] as string[], bytes: 0, elapsed: 0,
             error: '', target: '' };
  }
  async setKbEmbedder(embedder: string) {
    return { ok: true, embedder, state: 'ready', detail: '演示模式',
             note: '演示模式：没有真的切换' };
  }
  async installKbEmbedder() {
    return { ok: true, kind: 'install-kb', target: 'fastembed（演示模式：没有真的装 ✓）' };
  }
  async setUiTheme(theme: 'dark' | 'light') {
    return { ok: true, theme, note: '演示模式：没有真的存' };
  }
  async listForeverApprovals() {
    return { ok: true, rules: {} as Record<string, string> };
  }
  async revokeForeverApproval(_key: string) {
    return { ok: true, rules: {} as Record<string, string>, left: [] as string[] };
  }
  async importSettings() {
    return { ok: true, dry_run: true, changed: {}, note: '演示模式：没有真的导入' };
  }
  async teamApprove(gid: string, _taskId: string, _callId: string, decision: string) {
    void gid; return { ok: true, decision };
  }
  // ★ A-1 收尾：演示模式没有真群也没有真审批 ⇒ 如实回 0 条 ✓（不假装有 ✓）
  async groupApprovals(gid: string): Promise<{
    ok: boolean; group_id: string; count: number;
    approvals: { task_id: string; call_id: string; title: string; command: string }[];
  }> {
    return { ok: true, group_id: gid, count: 0, approvals: [] };
  }
  async teamResume(_gid: string, taskId: string, _name?: string) {
    return { ok: true, task_id: taskId };     // 演示模式：不去真跑
  }
  async resummarizeMeeting(gid: string) {
    return { ok: true, summary: '演示模式：没有真的重新出纪要（' + gid + '）' };
  }
  async getAccessToken() {
    return { ok: true, token: 'demo-token-not-real', set: true, note: '演示模式' };
  }
  async setAccessToken(token: string) {
    return { ok: true, token: token || 'demo-generated', generated: !token, note: '演示模式：没有真的改' };
  }
  async resetSettings(section: string): Promise<{ ok: boolean; section: string; note: string }> {
    return { ok: true, section, note: '演示模式：没有真的恢复' };
  }

  async fetchPricing(): Promise<{ found: boolean; note: string }> {
    return { found: false, note: '演示模式：没有真的联网查价' };
  }

  async setLimits(): Promise<{ ok: boolean; max_iterations: number; pricing: Record<string, unknown>; note: string }> {
    return { ok: true, max_iterations: 25, pricing: {}, note: '演示模式：没有真的保存' };
  }

  async setModel(): Promise<{ ok: boolean }> {
    return { ok: true };
  }
  /** 离线/演示模式：没有后端，直接告诉界面"用内置预设" */
  async listModels(): Promise<{ provider: string; models: string[]; source: 'live' | 'preset'; note: string }> {
    return { provider: '', models: [], source: 'preset', note: '离线演示模式：用内置预设' };
  }
  /** 离线演示：给一份静态能力表，界面照常渲染 */
  async getCapabilities(): Promise<Awaited<ReturnType<AgentApi['getCapabilities']>>> {
    return {
      capabilities: {
        asr: {
          label: '语音转文字（听）', current: 'mimo', current_state: 'ready',
          providers: {
            mimo: { label: '云端 MiMo（有 Key 就能用，零下载）', kind: 'cloud', state: 'ready', detail: '演示模式' },
            local_qwen3: { label: '本地 千问3-ASR（离线）', kind: 'local', state: 'not_installed', detail: '演示模式' },
          },
          fallbacks: ['local_qwen3'], next_available: null,
        },
        tts: {
          label: '语音合成（说）', current: 'melotts', current_state: 'ready',
          providers: { melotts: { label: 'MeloTTS（本地）', kind: 'local', state: 'ready', detail: '演示模式' } },
          fallbacks: ['edge'], next_available: null,
        },
      },
      local_tiers: [{ id: '0.6b', label: '0.6B' }, { id: '1.7b', label: '1.7B' }],
      local_recommendation: {
        tier: '0.6b', label: '0.6B（最快）', reason: '演示模式', method: '演示',
        detected: false, vram_gb: 0, ram_gb: 0,
      },
    };
  }
  async setAsrTier(provider: string): Promise<Awaited<ReturnType<AgentApi['setAsrTier']>>> {
    return { ok: true, provider, state: 'ready', detail: '演示模式', note: '演示模式：没有真的切换' };
  }
  async takeoverTask(): Promise<{ ok: boolean }> {
    return { ok: true };
  }

  async resumeTask(): Promise<{ ok: boolean }> {
    return { ok: true };
  }
  async ttsSpeak(): Promise<{ ok: boolean; file: string; raw_url: string; playlist?: string[] }> {
    throw new Error('Mock 模式不支持 TTS——请切换到 http 模式');
  }

  async ttsSpeakStream(): Promise<void> {
    throw new Error('Mock 模式不支持 TTS——请切换到 http 模式');
  }

  async getMemory() {
    return { enabled: true, count: 0, entries: [] as unknown[] };
  }

  async setMemory(patch: { enabled?: boolean; clear?: boolean }) {
    return { ok: true, enabled: patch.enabled ?? true, cleared: 0 };
  }

  async listKbs() {
    return [] as { name: string; description: string; chunks: number }[];
  }

  async ingestKb(): Promise<{ ok: boolean }> {
    return { ok: true };
  }

  async deleteKb(): Promise<{ ok: boolean }> {
    return { ok: true };
  }

  async getBudget(): Promise<BudgetStatus> {
    return { ok: true, keys: [], llm_key: '', enabled: true, blocked: null, note: '演示模式' };
  }

  async setBudget(): Promise<BudgetStatus> {
    return { ok: true, keys: [], llm_key: '', enabled: true, blocked: null, note: '演示模式：没有真的存' };
  }

  async getDataClear(): Promise<DataClearPreview> {
    return { ok: true, items: [], keeps: [], phrase: '清空我的数据', running: [], note: '演示模式' };
  }

  async clearData(): Promise<DataClearResult> {
    return { ok: true, cleared: [], moved: [], memory_reset: [], backup: '',
             note: '演示模式：没有真的清（真跑请切到 http 模式）' };
  }

  async browserCacheStatus(): Promise<BrowserCacheStatus> {
    return { dirs: 0, files: 0, bytes: 0, mb: 0, sample: [], note: '演示模式：没有真的扫' };
  }
  async cleanBrowserCache(): Promise<BrowserCacheCleanResult> {
    return { ok: true, removed: 0, freed_bytes: 0, freed_mb: 0, failed: [], failed_count: 0,
             reason: '演示模式：没有真的清' };
  }

  async setImage(provider: string) {
    return { ok: true, provider, state: 'ready', detail: '演示模式', note: '演示模式：没有真的切' };
  }

  async getVersion() {
    return { ok: true, name: 'LanternLogic Agent', vendor: '演示模式', version: '0.0.0-demo',
             check_url_set: false, note: '演示模式' };
  }

  /** ★ 演示模式：如实说"演示模式"✓ —— 不许假装验过签名 ✗（`verified: false` ✓） */
  async getAuthorCard() {
    return { ok: true, org: '演示模式', line: '演示模式没有作者卡', email: '', wechat: '',
             alias: '', signed_at: '', verified: false,
             reason: '演示模式：没有真验签', fingerprint: '' };
  }

  async checkUpdate() {
    return { ok: false, current: '0.0.0-demo', latest: '', has_update: false,
             notes: '', url: '', note: '演示模式：没有真的查' };
  }

  async setTaskIdentity(): Promise<{ ok: boolean; identity: string }> { return { ok: true, identity: '' }; }
  async renameTask(): Promise<{ ok: boolean }> { return { ok: true }; }
  async pinTask(): Promise<{ ok: boolean; pinned: boolean }> { return { ok: true, pinned: true }; }

  async teamRoles(): Promise<{ roles: { role: string; dept: string; summary: string }[]; max: number }> { return { roles: [], max: 12 }; }
  async suggestRoles(): Promise<{ ok: boolean; suggestions: { role: string; dept: string; summary: string; why: string }[] }> {
    return { ok: true, suggestions: [] };
  }
  async getAudit(): Promise<{
    ok: boolean; entries: { ts: string; kind: string;[k: string]: unknown }[];
    count: number; by_kind: Record<string, number>; max_entries: number; path: string;
  }> {
    return { ok: true, entries: [], count: 0, by_kind: {}, max_entries: 2000, path: '' };
  }
  async teamSoloSay(): Promise<{ ok: boolean; task_id: string }> { return { ok: true, task_id: 'mock' }; }
  async teamEmployees(): Promise<{ employees: any[]; max: number }> { return { employees: [], max: 12 }; }
  async teamAddEmployee(): Promise<{ ok: boolean; employee: any }> { return { ok: true, employee: {} }; }
  async teamUpdateEmployee(): Promise<{ ok: boolean; employee: any }> { return { ok: true, employee: {} }; }
  async teamDelEmployee(): Promise<{ ok: boolean }> { return { ok: true }; }
  async importRoleCards() {
    return { ok: true, added: [], skipped: [], note: '演示模式：没有真的建卡' };
  }
  async teamGroups(): Promise<{ groups: any[] }> { return { groups: [] }; }
  async teamCreateGroup(): Promise<any> { return { id: 'mock' }; }
  async teamUpdateGroup(): Promise<any> { return {}; }   // ★ 第 7c 处（mock 实现，与真实实现同签名）
  async lanStatus(): Promise<LanStatus> {
    return { enabled: false, pending_lan: false, needs_restart: false, host: '127.0.0.1', port: 8642,
             ip: '127.0.0.1', token: '', token_set: false, url: 'http://127.0.0.1:8642/',
             ui_ready: true, ui_built_at: null };
  }
  async lanEnable(): Promise<{ ok: boolean } & LanStatus> { return { ok: true, ...(await this.lanStatus()) }; }
  async lanDisable(): Promise<{ ok: boolean } & LanStatus> { return { ok: true, ...(await this.lanStatus()) }; }
  async teamDeleteGroup(): Promise<{ ok: boolean }> { return { ok: true }; }
  async teamFeed(): Promise<{ messages: any[] }> { return { messages: [] }; }
  async teamSay(): Promise<{ ok: boolean; dispatched: any[] }> { return { ok: true, dispatched: [] }; }

  async meetingChunk(): Promise<{ ok: boolean; text: string; note?: string; file?: string }> {
    throw new Error('Mock 模式不支持会议纪要——请切换到 http 模式');
  }

  async meetingRead(): Promise<{ ok: boolean; text: string }> {
    return { ok: true, text: '' };
  }

  async voiceTranscribe(): Promise<{ ok: boolean; text: string }> {
    throw new Error('Mock 模式不支持语音输入——请切换到 http 模式');
  }

  async uploadFile(): Promise<{ ok: boolean; name: string; path: string; size: number }> {
    return { ok: true, name: 'mock-attachment.png', path: 'mock-attachment.png', size: 0 };
  }

  async setExecutor(): Promise<{ ok: boolean; note?: string }> {
    return { ok: true, note: '（Mock 模式未真正保存）' };
  }

  async setVideo(): Promise<{ ok: boolean; note?: string }> {
    return { ok: true, note: '（Mock 模式未真正保存）' };
  }

  /** 启动剧本式假 Agent（真正的"事件流生产者"） */
  private startRun(taskId: string, input: string, followUp: boolean) {
    startScriptedRun({
      taskId,
      input,
      followUp,
      emit: (type, payload) => this.appendEvent(this.makeEvent(taskId, type, payload)),
      isCancelled: () => this.cancelled.has(taskId),
      onDone: (status) => {
        this.cancelled.delete(taskId);
        this.updateTask(taskId, { status });
        // 终态补发一条 status 事件：TaskView 收到后才会刷新任务+侧边栏
        //（cancelled 不发事件的话，侧边栏的状态点会一直卡在"运行中"）
        if (status === 'done') {
          this.appendEvent(
            this.makeEvent(taskId, 'status', { state: 'idle', detail: '任务完成，回到待命' }),
          );
        } else if (status === 'cancelled') {
          this.appendEvent(
            this.makeEvent(taskId, 'status', { state: 'cancelled', detail: '用户取消' }),
          );
        }
      },
    });
  }
}

/* ================= 出口：一行切换 mock / http ================= */

export const api: AgentApi =
  import.meta.env.VITE_API_MODE === 'http' ? new HttpApi() : new MockApi();

