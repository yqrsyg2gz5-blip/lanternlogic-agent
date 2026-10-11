# backend（Phase 2 最小闭环 ✅）

一个模型 + 5 个工具 + 事件落盘 + 命令审批护栏，全部按契约实现、可替换。

## 启动（手动）

```powershell
cd D:\AI\agent-shell\backend
pip install -r requirements.txt
python -m app.main        # 主机/端口读 config.json（默认 127.0.0.1:8642）
```

前端联调：`frontend/.env` 写 `VITE_API_MODE=http` 后重启 `npm run dev`（vite 已代理 `/api` → 8642，无 CORS 问题）。前端一行代码不用改。

## 架构（七大预留接口 → 现役实现）

| 层 | 文件 | 现役 | 可换 |
|---|---|---|---|
| HTTP/SSE | `app/main.py` | FastAPI，`/api/v1` | — |
| Agent Loop | `app/loop.py` | 六步循环，一次迭代一个工具 | — |
| 模型提供者 | `app/providers/` | `mock`（无 Key 演示） | `openai_compatible`：deepseek/qwen/glm/ollama |
| 执行器 | `app/executors/` | `local`（工作区沙箱+子进程） | docker/wsl2/e2b |
| 工具 | `app/tools/` | list_dir/file_read/file_write/shell_exec/task_done | Phase 3 加 web 类 |
| 存储 | `app/store.py` | fs：`data/tasks/<id>/events.jsonl` + `index.json` | sqlite |
| 审批 | `app/approval.py` | waiting_approval 状态机（once/always/deny） | — |

## 关键设计

- **事件信封**（契约一）：`{id: evt_000001, seq, task_id, type, version:1, ts, payload}`；seq 任务内从 1 递增，**重启续聊从落盘恢复游标**（`store.last_seq`）
- **SSE**：事件名 `agent_event`；`Last-Event-ID`/`?after_seq=` 断线补发；先订阅后回放，无竞态窗口；15s 心跳注释帧
- **零硬编码**（契约三）：主机/端口/工具/审批清单全在 `config.json`；API Key 只存 `api_key_env` 环境变量名；配置缺失/字段错 → 启动即报错
- **沙箱**：工具路径参数限定在任务工作区内（resolve 后 `is_relative_to` 校验，越界 403/观察失败）
- **每任务一个 provider 实例**（mock 有游标状态，不能跨任务共享）

## 端点（契约二）

`POST /tasks` · `GET /tasks` · `GET /tasks/{id}` · `POST /tasks/{id}/messages` ·
`POST /tasks/{id}/cancel` · `POST /tasks/{id}/approve` ·
`GET /tasks/{id}/events?after_seq=` · `GET /tasks/{id}/events/stream`（SSE）· `GET /tasks/{id}/files?path=`

## 已知边界（Phase 3 处理）

- 续聊起新运行时不带上一轮历史（事件流连续，模型上下文未带）
- waiting_approval 中途重启 → 索引状态可能短暂不准（事件流为准）
- mock 工具调用无审批环节（`approval_required` 配置后对 shell_exec 生效）

