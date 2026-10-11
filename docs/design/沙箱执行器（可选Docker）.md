# 设计文档 · 可插拔执行器（含可选的 Docker 沙箱）

> **状态**：🟡 **已定设计，未实现**（2026-09-30 用户拍板，DSH 记录）
> **给谁看**：以后接手实现的人（人或 AI）。**读完这一份就能开工，不需要任何对话上下文。**
> **相关**：`contracts/03-config.md`（配置契约）、`backend/app/executors/`（执行器目录）、
> `docs/collab/shifts/2026-09-30-第12班.md`（本机 Docker 为何不可用）、
> `docs/collab/shifts/2026-09-30-第7班.md`（审批模型）

---

## 1. 为什么这样做（背景，30 秒）

用户提出：**"给想用本地 Docker 的人留个口子；不想用的人就用软件自带的。"**

这条思路解决了三个真实问题：
1. **安装门槛**：若把 Docker 做成硬依赖，每个用户都得装 Docker Desktop（1GB+、要 WSL2/Hyper-V）
2. **授权风险**：Docker Desktop 对个人/小企业免费，但**大企业需付费订阅** ——
   硬依赖等于把第三方授权问题绑到本产品上
3. **定位冲突**：本产品卖点是「本地、轻量、你的文件你的 Key」，硬塞容器平台自相矛盾

而项目架构**本来就是 `executor.type` 注册表模式**，口子早就留好了 —— 现在只是接上。

## 2. 目标与非目标

**目标**
- 默认执行器**永远是 `local`**，零依赖、开箱即用
- 提供**可选**的 `docker` 执行器：有 Docker 的用户改一行配置即可启用
- 环境不可用时**优雅降级**（明确报错 + 提示切回 local），绝不崩溃
- 为以后的 `wsl` / `windows-sandbox` 执行器留出同样的接口

**非目标（明确不做）**
- ❌ 不做容器编排、不做镜像管理界面
- ❌ 不强制任何人安装 Docker
- ❌ 不在本期实现 WSL / Windows 沙盒（但接口要留好）

## 3. 配置契约（add-only 扩展）

```jsonc
"executor": {
  "type": "local",                  // "local"（默认） | "docker"
  "workspace_root": "./workspace",
  "allowed_dirs": ["./workspace"],
  "approval_required": ["rm", "del", "rmdir", "rd", "erase", "format", "reg", "remove-item"],
  "timeout_seconds": 60,
  "shell": "",

  // ↓ 仅当 type == "docker" 时生效
  "docker": {
    "image": "alpine:latest",       // 建议提供包含常用工具的镜像
    "network": "none",              // 默认断网（最安全）；需要联网时显式改为 "bridge"
    "memory": "512m",
    "cpus": "1",
    "user": "1000:1000",            // 容器内非 root
    "extra_args": []                // 逃生口：附加 docker run 参数
  }
}
```

**契约三处同步**（本项目的铁律）：`contracts/03-config.md`、`contracts/config.example.json`、
`backend/app/config.py`。

## 4. 接口（实现时照抄）

执行器需实现 `Executor` 基类（见 `backend/app/executors/base.py`）：

```python
class DockerExecutor:
    def __init__(self, cfg: ExecutorCfg) -> None: ...

    def resolve_in_workspace(self, workdir: Path, rel: str) -> Path:
        """与 LocalExecutor 同语义：工作区 + allowed_dirs 内放行，否则 PermissionError。
        宿主侧仍要校验（容器内路径是映射过的）。"""

    async def run_tool(self, tool: str, args: dict, workdir: Path) -> ExecResult:
        """shell_exec → docker run；其余工具（file_read/file_write…）可继续走宿主实现，
        或一并进容器（见 §6 待定问题）。"""

    @staticmethod
    def available() -> tuple[bool, str]:
        """返回 (是否可用, 原因)。用于优雅降级与设置页展示。"""
```

**`docker run` 命令行构造（关键）**：
```
docker run --rm -i
  --network <network>            # 默认 none
  --memory <memory> --cpus <cpus>
  --user <user>
  -v "<宿主工作区>:/w"           # 只挂工作区，不挂整盘
  -w /w
  <image>
  sh -lc "<命令>"
```

**注意点**
- Windows 路径要转成 Docker 能接受的写法（`D:\x\y` → `//d/x/y` 或 `D:/x/y`；
  **实现时实测确认**，这是最容易出错的一处）
- 交互式命令不要用 `-t`（非 TTY 环境会报错）
- 输出编码：容器内多为 UTF-8；宿主侧仍走 `decode_output()`（见 P2-20 修复）
- **超时**：`timeout_seconds` 到点要 `docker kill`（否则容器会一直挂）

## 5. 优雅降级（必须有）

| 情况 | 行为 |
|---|---|
| 配置了 `docker` 但 `docker` 命令不存在 | 启动自检时打印警告；建任务时返回 **503 + 明确原因**（沿用 P1-15 的机制：**不落盘僵尸任务**） |
| 配置了 `docker` 但守护进程没起 | 同上，原因写"守护进程未运行" |
| 配置了 `docker` 但镜像不存在 | 提示"请先 `docker pull <image>`"，或按配置决定自动拉取 |
| 任务执行中途 Docker 挂了 | 单次工具调用失败 → 观察结果里返回错误，让模型自己决定（不整个任务崩） |

**设置页要显示**：`当前执行器：docker（不可用：守护进程未运行）` —— 让用户一眼看到问题。

## 6. 审批模型的变化

- **容器内的写/删不再需要审批**（伤不到真机）→ 审批量应大幅下降
- **审批只保留给"把文件搬出容器/工作区"** 这一个动作
- 实现上：`loop._maybe_execute()` 里判断当前执行器是否为沙箱；若是，跳过
  `approval_required` 的动作词判定，但**越界路径判定仍需保留**（挂载边界外的东西依旧要问）

## 7. 测试计划

**必须有的单测**（不需要真的 Docker）
1. `docker run` 命令行构造：网络/内存/CPU/用户/挂载/工作目录/镜像 全部正确
2. Windows 路径转换正确（含盘符、空格、中文路径）
3. `available()` 在三种失败场景下返回正确原因（无命令 / 守护进程没起 / 镜像不存在）——用 stub 或 monkeypatch
4. 不可用时的降级：建任务返回 503 且**任务数不变**（沿用 P1-15 的测试写法）
5. 超时：到点发 kill，不留下孤儿容器

**自检命令（重要：本机无法实测，让用户自己验）**
```powershell
python -m app.executors.docker --selftest
# 输出示例：
#   [1/5] docker 命令存在 ................ ✅ 29.8.0
#   [2/5] 守护进程可用 ................... ✅
#   [3/5] 镜像存在 ....................... ❌ 请先 docker pull alpine:latest
#   [4/5] 挂载工作区并执行命令 ........... ✅ 输出：中文测试
#   [5/5] 容器内写回工作区 ............... ✅ 宿主可见
```
这条命令是"没条件实测"的补偿：**实现者不必拥有可用 Docker，用户一条命令即可验收。**

## 8. ⚠️ 已知限制（实现者必读）

- **本机（`D:\AI\agent-shell` 的开发机）Docker Desktop 是坏的**：
  `com.docker.backend` 在 `initializing settings loading` 阶段崩溃，并吃掉 6.4GB 内存。
  详见 `shifts/2026-09-30-第12班.md`。
  ⇒ **实现完只能做单测，无法在开发机端到端实测。交付时必须写明"未在真机实测"。**
- 容器内镜像自带哪些工具取决于镜像；`alpine` 极简（无 bash、无 python）。
  **文档要建议用户选一个带常用工具的镜像**，否则模型会撞墙。
- Docker Desktop 的商用授权条款请以官方为准（大企业需订阅）。

## 9. 交付标准（做完算完成）

- [ ] `backend/app/executors/docker.py` 实现并通过全部单测
- [ ] `executor.type = "docker"` 在配置契约三处同步
- [ ] 不可用时优雅降级（503 + 明确原因，且不留僵尸任务）
- [ ] `python -m app.executors.docker --selftest` 可运行
- [ ] 文档更新（README / `docs/provider-setup.md` 或新建 `docs/sandbox.md`），
      明确写「Docker 是可选手段，不是依赖」
- [ ] 交接记录里如实注明**是否在真机实测过**
