# 契约三：配置文件（Config）

> 原则：**零硬编码**。模型名、路径、端口、审批清单全部外置。
>
> **2026-09-30 移除 `executor.command_whitelist`**：该字段从 v1 起就写在配置与文档里，
> 但**全项目没有任何一处强制执行** —— 它给人"命令受白名单限制"的错觉，实际毫无作用。
> 与其留着误导，不如删掉。真正的命令策略（allow-list 默认拒绝 / 沙箱）是后续单独的设计课题。
> 当前实际生效的审批规则见 `backend/app/approval.py` 文件头：按任务隔离、拆壳后全文匹配危险动作、越界路径必审批。
> 文件位置：`agent-shell/config.json`（真实配置，进 .gitignore）；`config.example.json` 是模板。

---

## 格式：JSON（version 字段兼容演进）

```json
{
  "version": 1,
  "server": {
    "host": "127.0.0.1",
    "port": 8642
  },
  "model": {
    "provider": "mock",
    "model_name": "deepseek-chat",
    "base_url": "https://api.deepseek.com/v1",
    "api_key_env": "DEEPSEEK_API_KEY",
    "temperature": 0.7,
    "max_tokens": 8192,
    "max_iterations": 60,
    "max_context_tokens": 65536
  },
  "executor": {
    "type": "local",
    "workspace_root": "./workspace",
    "allowed_dirs": ["./workspace"],
    "approval_required": ["rm", "del", "rmdir", "rd", "erase", "format", "reg", "remove-item"],
    "timeout_seconds": 60,
    "shell": ""
  },
  "storage": {
    "type": "fs",
    "data_dir": "./data"
  },
  "ui": {
    "language": "zh-CN",
    "theme": "dark"
  }
}
```

## 字段说明与"预留点"对应

| 段 | 预留接口 | 当前值 | 将来可换 |
|----|---------|--------|---------|
| `model.provider` | 模型提供者 | `mock` | `deepseek / qwen / glm / ollama / openai_compatible` |
| `executor.type` | 执行器（沙箱） | `local` | `docker / wsl2 / e2b`（E2B 自托管） |
| `storage.type` | 存储 | `fs`（JSONL/JSON/MD） | `sqlite` 及以上 |
| `executor.allowed_dirs` | 权限护栏 | workspace + 可授权目录（如桌面） | 已落地多目录授权 |
| `executor.approval_required` | 命令审批 | 危险**动作词**列表（不区分大小写） | Allow Once / Always Allow 记忆 |
| `model.max_iterations` | 迭代上限 | 60（原 25，真机任务实测不够用） | 按任务复杂度调整 |
| `model.max_context_tokens` | 上下文预算 | 65536 | history 超限自动裁剪（更精细的模型自压缩摘要待做） |
| `executor.shell` | shell 可执行文件 | 空 = 平台默认（Windows 为 cmd） | Git Bash / WSL bash（Unix 语法 + UTF-8 输出，免乱码） |
| `skills.skills_dir` | 技能库目录 | ./skills（每子目录 = 一技能，含 SKILL.md + 可选资源） | 三级渐进加载：L1 元数据常驻 / L2 全文按需 / L3 资源按需 |
| `executor.search_url` | 联网搜索数据源 | Bing 网页版（免 Key，国内可达） | SearXNG / Tavily（需 API Key 存环境变量） |
| `executor.searxng_url` | SearXNG 实例地址 | 空 = 不用（走 Bing 降级链） | 设置后搜索优先走它（`GET {url}/search?q=…&format=json`），失败静默回退 Bing；实例可用 Docker 或源码部署，PyPI 的 `searxng` 包是同名第三方封装**不是**官方引擎，勿装 |
| `executor.browser_channel` | 浏览器实况通道 | msedge（系统 Edge，免下载） | chrome / 内置 chromium |
| `video`（独立段） | 云端视频引擎 | `provider: ""` = 不启用（video_gen 给配置指引） | `minimax` / `wan` / `seedance` / `kling`，字段：api_key_env（Key 不落盘）/ model（留空用各家默认）/ resolution（**默认锁 480P 最便宜档**——wan 不锁会默认 1080P，实测一条 5s 好几元）/ ratio / api_base / poll_seconds / timeout_seconds。详见 docs/video-providers.md |

| `executor.sandbox` | shell 执行隔离 | `off`（本机直跑） | `docker`：命令进一次性容器（断网/限资源/非 root/免审批；工作区挂载为 /w）。Docker 不可用时命令不执行并明确报错（**不静默降级**） |
| `mcp`（独立段） | MCP 外部工具（万物皆可插） | `servers: {}` 空 | `enabled: true` + `servers: {名字: {command, args, env}}`——stdio JSON-RPC 子进程；启动时自动接入并注入工具表（`mcp__<server>__<tool>`），单 server 失败跳过不阻塞。**协议 MIT 可商用；第三方 server 各自许可证逐个查** |

## 2026-10-02 补齐的配置段（审计 §7.2：此前四处同步漏掉）

| 字段 | 默认 | 说明 |
|---|---|---|
| `server.host` | `127.0.0.1` | 监听地址；**设 `0.0.0.0`（手机直连/局域网）时 `access_token` 必填，启动时强制校验** |
| `server.access_token` | 无 | LAN 模式访问密码：请求带 `Authorization: Bearer` 或 `?token=`；SSE/webhook 同样校验 |
| `executor.sandbox` | `off` | `docker`：shell 命令进一次性容器（断网/限资源/非 root/fail-closed）。**example 配置已默认 docker**（§4：新装机审批不再是唯一闸门） |
| `executor.attachment_max_mb` | `50` | 附件上传大小上限（此前是死配置，`extra="forbid"` 写进去会启动失败，§1.7 已补真实字段） |
| `executor.wide_max_concurrent` | `8` | Wide Research 并行子任务上限（同上） |
| `image` | — | 作图引擎：`{provider: "dashscope"\|"comfyui", api_key_env?, model?}` |
| `video` | — | 视频引擎：`{provider, model?, resolution?, ratio?, audio?, engines?}`；engines=多引擎 Key 映射（Key 写进程环境不落盘） |
| `memory` | `enabled: true` | 长期记忆：`{enabled, max_entries}`——交付后提取、注入 system；**写入前过打码管线** |
| `notify` | — | 任务完成通知：`{slack_webhook?, email?}`，失败不影响任务 |
| `mcp` | `enabled: false` | 外部工具：`{enabled, servers: {名: {command, args, env}}}`；**写入类工具已纳入审批**（§8.2），confirm 模式下全部要问 |

## 规则

1. **API Key 不进配置文件**：只存环境变量名（`api_key_env`），值从环境读——防止截图/提交泄露
2. 改配置不需要改代码；加 provider / executor 类型 = 加一个实现 + 注册，不动核心
3. 配置解析失败 → 启动即报错并指出字段（不静默回退默认值）

（完）
