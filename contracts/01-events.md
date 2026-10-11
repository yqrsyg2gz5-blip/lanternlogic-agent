# 契约一：事件流协议（Event Schema）

> 事件规范 v1（七类事件）。
> **前端只消费事件流，后端是事件流的唯一生产者。** 本契约是全项目第一契约，改动必须向后兼容。

---

## 1. 事件信封（Envelope）

```json
{
  "id": "evt_000001",
  "seq": 1,
  "task_id": "task_20260929_a1b2",
  "type": "message",
  "version": 1,
  "ts": "2026-09-29T12:00:00.000Z",
  "payload": { }
}
```

| 字段 | 规则 |
|------|------|
| `id` | 全局唯一，格式 `evt_<6位序号>` |
| `seq` | **任务内序号**，从 1 严格递增。前端增量拉取用 `?after_seq=`，防重复也靠它 |
| `task_id` | 格式 `task_<日期>_<4位随机>` |
| `type` | `message / message_delta / action / observation / plan / knowledge / datasource / status / error` |
| `version` | schema 版本，当前恒为 `1`。**只许加字段，删改必须写迁移** |
| `ts` | ISO8601 UTC |

## 2. 七类事件 payload

| type | 说明 | payload 结构 |
|------|-----------|-------------|
| `message` | Messages input by actual users | `{ role: "user"\|"assistant", text: string, attachments?: string[], sources?: [{title, url}] }` |
| `action` | Tool use (function calling) actions | `{ tool: string, params: object, call_id: string }` |
| `observation` | Results generated from corresponding action execution | `{ call_id: string, ok: boolean, result: string, duration_ms: number }` |
| `plan` | Task step planning & status by Planner module | `{ steps: [{ no, text, status: "pending"\|"in_progress"\|"done"\|"failed" }], current_step: number, reflection?: string }` |
| `knowledge` | Knowledge & best practices by Knowledge module | `{ title: string, content: string }` |
| `datasource` | Data API documentation by Datasource module | `{ name: string, description: string, endpoint?: string }` |
| `status` | 系统杂项事件 | `{ state: "running"\|"waiting_approval"\|"done"\|"failed"\|"partial"\|"idle"\|"cancelled", detail?: string, call_id?: string }` |

> **`partial` 状态与 `sources` 字段（add-only，2026-09-30）**
> - `status.state = "partial"` / 任务 `partial`：交付只完成了一部分。由模型在 `task_done.outcome` 里如实声明，
>   或由系统在"联网检索过却没给来源"时自动降级。**它不再是绿点**——零产出或半成品不能显示成成功。
> - `message.sources`：交付的来源清单，只保留**本次运行确实访问过**的链接（凭空写出的会被丢弃）。
> 两者都向后兼容：旧前端忽略未知状态/字段即可照常工作。

> **`status.call_id`（add-only，2026-09-30 新增）**：仅当 `state: "waiting_approval"` 时出现，值是**待审批那次工具调用的 `call_id`**。
> 用途：前端据此把"允许一次 / 总是允许 / 拒绝"按钮与对应的 `action` 精确关联，而不是靠"最近一条 action"去猜。
> 向后兼容：旧前端忽略该字段照常工作（只是关联不如新前端精确）。

**我们新增（规范外但必须有）：**

| type | 为什么需要 | payload |
|------|-----------|---------|
| `error` | 错误是一等公民，走事件流，不另搞报错通道 | `{ message: string, code?: string }` |
| `message_delta` | 流式输出：assistant 生成中的文本增量，前端聚成"打字机"气泡；**随后必有完整 `message` 事件收尾**（以它为准，delta 仅渲染辅助） | `{ delta: string }` |

## 3. 关键规则

1. **一次迭代只调一个工具**（"Choose only one tool call per iteration"）——事件流里 `action` 与 `observation` 通过 `call_id` 配对，永远交替出现
2. **计划用编号伪代码**，每次更新带当前步骤号、状态、反思（Planner 模块规则）
3. **任务完成 = 发最终 message（带附件）→ 进入 idle**（agent loop 第 5、6 步）
4. 事件流**全量落盘**：`data/tasks/<task_id>/events.jsonl` 每行一个 JSON
5. 回放 = 按 `ts` 播放 JSONL，前端零特判

（完）
