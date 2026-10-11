# 契约二：任务 API（Task API）

> 前后端唯一交互面。Phase 1 前端用 `MockApi`（同接口假数据）；Phase 2 后端照本文档实现 `HttpApi`，前端改一行配置即可切换——这就是"预留"。

---

## 1. 通用约定

- Base URL：`http://127.0.0.1:8642/api/v1`（端口来自配置文件，不硬编码）
- 全部 JSON；时间一律 ISO8601 UTC
- 鉴权：纯本地（127.0.0.1）默认无 token，但有 **Origin/Host 护栏**（非回环 Origin 拒绝，防 DNS rebinding/CSRF）；
  **LAN 模式**（`server.host=0.0.0.0`）**强制要求** `server.access_token`——启动时没有直接拒启（安全拦截）。
  带 token 的请求方式：`Authorization: Bearer <token>` 头或 `?token=` 查询参数（SSE/webhook 用）

## 2. 端点清单

| Method | Path | 说明 |
|--------|------|------|
| GET | `/tasks` | 任务列表：`[{id, title, status, created_at, updated_at, project_id?}]` |
| POST | `/tasks` | 创建任务：`{input: string, project_id?: string}` → `{id, title, status: "created"}`；挂载项目时其 master 指令自动注入 |
| GET | `/tasks/:id` | 任务详情 |
| GET | `/tasks/:id/events?after_seq=N` | 增量拉事件（刷新/续读/回放都用它） |
| GET | `/tasks/:id/events/stream` | **SSE** 实时事件流，`data:` 为完整事件信封 JSON |
| POST | `/tasks/:id/cancel` | 取消任务 |
| POST | `/tasks/:id/messages` | 向运行中/已完成任务追加用户消息（追问/补充指令），Agent 继续 |
| POST | `/tasks/:id/approve` | 审批待确认命令：`{call_id, decision: "once"\|"always"\|"deny"}` |
| GET | `/tasks/:id/files?path=` | 文件树 / 文件内容（沙箱工作区视图） |
| GET | `/tasks/:id/files/raw?path=` | 产物原始文件（Studio 预览：html/css/图直接可打开） |
| GET | `/projects` | 项目列表：`[{id, name, master_prompt, created_at}]` |
| POST | `/projects` | 创建项目：`{name, master_prompt}` → 完整项目对象 |
| DELETE | `/projects/:id` | 删除项目 |
| GET | `/skills` | 技能清单（L1 元数据：name + description；SKILL.md 由模型按需 load_skill 加载） |
| GET | `/automations` | 自动化列表（id/name/kind/task_input/schedule/enabled/last_run/last_task_id） |
| POST | `/automations` | 创建：`{name, kind: "schedule"\|"hook", task_input, project_id?, schedule?}`；schedule 形如 `{kind:"interval",minutes:N}` 或 `{kind:"daily",time:"HH:MM"}` |
| POST | `/automations/:id/toggle` | 启用 / 暂停 |
| DELETE | `/automations/:id` | 删除 |
| POST | `/hooks/:id/:secret` | Webhook 触发（外部系统调用，密钥不符 404、已暂停 409）→ 自动创建任务 |
| GET | `/wide` | Wide Research 编排记录（input/items/task_ids/agg_task_id/status） |
| GET | `/wide/:id` | 单条编排详情 |
| POST | `/wide` | 发起并行调研：`{input, items: [2-8 个要点], project_id?}` → 每要点派一个并行子任务，全部终局后自动生成汇总报告任务 |
| GET | `/usage` | 全局用量统计：`{input_tokens, output_tokens, total_tokens, calls, tasks_with_usage, task_count, by_model, by_task}`；`by_task` 为 Top 20 消耗任务明细（第 41 班）。数据优先读事件 payload 结构化 `usage` 字段，旧任务回退解析文本 |
| GET | `/settings` | 当前配置视图（**Key 绝不回传**，只回传环境变量名 + `key_set` 布尔） |
| POST | `/settings/model` | 切换模型：`{provider, model_name?, base_url?, api_key?, api_key_env?}` |
| POST | `/settings/executor` | 改执行环境：`{allowed_dirs?, approval_required?, timeout_seconds?, searxng_url?}`（重启后端对新实例生效） |
| GET | `/access-mode` | 当前会话权限模式：`{mode: "full"\|"auto_edit"\|"confirm"}`（持久化，重启恢复） |
| POST | `/access-mode` | 切换权限模式：`{mode}`；confirm=所有 shell 命令先批（沙箱也不例外） |
| GET | `/auth/check` | 检查当前请求是否通过鉴权/Origin 护栏（前端启动探测用） |
| POST | `/tasks/:id/update` | 改任务标题：`{title}` |
| POST | `/tasks/:id/takeover` | 人工接管：暂停 Agent 运行，状态转 takeover（UI 出"交还 Agent"） |
| POST | `/tasks/:id/resume` | 交还 Agent：`{text}` 注入接管期间做了什么并继续；**running 状态拒绝**（防双 run，§2.4） |
| POST | `/tasks/:id/identity` | 切换专家身份：`{role}`（24 角色库；空串回默认助手），立即注入 system |
| POST | `/tasks/:id/voice` | 语音输入**直发**：音频 multipart → 转写 → 直接当用户消息进任务 |
| POST | `/voice/transcribe` | 语音输入**草稿**：音频 multipart → 只转写返回文本，用户确认后手动发 |
| GET | `/tasks/:id/tts-audio/:filename` | 取 TTS 生成的音频文件（inline 播放） |
| POST | `/tasks/:id/tts` | 把一段文字合成语音：`{text}` → 附件 .wav（melotts→edge→pyttsx3 回退链） |
| POST | `/tasks/:id/meeting` | 会议模式：录音分片 multipart → 转写 → 带时间戳追加进工作区 meeting_notes.md |
| GET | `/tasks/:id/meeting` | 读当前会议纪要内容 |
| POST | `/tasks/:id/files` | 上传附件到任务工作区：multipart（大小受 `executor.attachment_max_mb` 限制） |
| GET | `/kb` | 已装知识库列表：`[{name, chunks, files, created}]` |
| POST | `/kb/ingest` | 导入文件夹建库：`{name, path}`（embedding 走 DashScope text-embedding-v3；路径越界 422） |
| POST | `/kb/search` | 语义检索：`{query, name?, top_k?}` → `[{score, kb, source, text}]`（chunks 按 mtime 缓存） |
| DELETE | `/kb/:name` | 删除知识库（路径穿越已防，§2.2） |
| GET | `/memory` | 长期记忆列表（写入前过打码管线——Key 不进记忆，§2.1） |
| POST | `/memory` | 手工追加记忆：`{text}` |
| GET | `/team/employees` | 员工卡列表（含 mode/persona/provider 配置） |
| POST | `/team/employees` | 创建员工卡：`{name, dept?, role?, mode?: "expert"\|"free", persona?, provider?…}`（§6.2） |
| PUT | `/team/employees/:id` | 编辑员工卡 |
| DELETE | `/team/employees/:id` | 删除员工（同步从所有群移除并**落盘** groups.json，§6.5） |
| POST | `/team/employees/:id/say` | 单聊某员工：`{text}` → 起独立任务（员工 BYOK 大脑） |
| GET | `/team/roles` | 24 专家角色库：`{roles: [{role, dept, summary}], max}` |
| GET | `/team/groups` | 群列表：`[{id, name, members, leader?, mode: manual\|broadcast\|leader}]` |
| POST | `/team/groups` | 建群：`{name, members, leader?, mode?}`（leader/mode 此前被静默丢弃，§6.1 已修） |
| DELETE | `/team/groups/:gid` | 删群（feed 一并删） |
| GET | `/team/groups/:gid/feed?after_seq=N` | 群消息增量拉取（含 status/task_id/attachments） |
| POST | `/team/groups/:gid/say` | 群聊发言：`{text}` → 按 mode 派发（点名/广播/组长拆解）；交付自动回流群 |
| GET | `/automations`（响应变更） | 列表**不再回传 webhook secret**（返回 `"***"`，§2.3）；创建响应仍返回一次供保存 |`{provider?, model?, resolution?, ratio?, audio?, api_key?, api_key_env?, engines?}`；`engines` 为多引擎 Key（名 → {api_key_env, api_key?, model?, resolution?}，清空 api_key_env = 取消该引擎）；Key 写进程环境即刻生效、**不落盘**（第 41 班）；GET /settings 的 `video` 块返回当前值 + 各引擎 `key_set` 状态 |

## 3. 任务状态机

```
created → running ⇄ waiting_approval → done
              │  ↑                        → failed
              ↓  └────(approve/cancel 后恢复)
           cancelled
```

- `waiting_approval`：执行器遇到需审批命令时进入（Allow Once / Always Allow 模式）
- 前端顶栏和侧边栏状态点只消费这个字段

## 4. SSE 细节

- 事件名统一 `event: agent_event`
- 断线重连：前端带 `Last-Event-ID`（= 最后收到的 `seq`），服务端从 `after_seq` 补发
- 心跳：每 15s 发 `: ping\n\n` 注释行

## 5. Mock 契约

`MockApi` 必须实现与上表**完全相同的方法签名**，数据落在内存 + localStorage；
剧本式执行（plan → action → observation → … → 最终 message），节奏模拟真实 agent loop。

（完）
