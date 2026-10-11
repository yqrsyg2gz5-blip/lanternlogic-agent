"""MCP（Model Context Protocol）客户端 —— "万物皆可插"的落地（第 41 班）。

MCP 是 Anthropic 开源（MIT）、现由 Linux 基金会中立治理的工具接入标准，
OpenAI/Google 均已兼容。第三方按此标准发布"server"（每个 server 暴露若干工具），
我们做 client：配置里填启动命令，即可把外部工具**动态注入**给 Agent。

本实现走最通用的 **stdio 传输**：server 是个子进程，JSON-RPC 2.0 走 stdin/stdout。

⚠️ 实现说明（第 41 班踩坑）：最初用 asyncio 子进程 + 后台 reader task——普通脚本
正常、uvicorn 里稳定超时/EOF（探针矩阵证明子进程与协议均正常，纯属 asyncio 管道
在 uvicorn 事件循环下的怪癖）。改为**同步 Popen + 读线程 + 队列**，
async 方法用 to_thread 桥接——与事件循环彻底解耦，哪里都能跑。

⚠️ 合规：MCP 协议 MIT 可商用；但每个第三方 server **各自的许可证要逐个查**（写进配置文档）。
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from typing import Any

MCP_PROTOCOL_VERSION = "2024-11-05"
MCP_CLIENT_INFO = {"name": "agent-shell", "version": "0.1.0"}


# MCP 子进程环境白名单：只透传运行必需的系统变量，绝不整包继承
# （复审 P1：os.environ 里可能有用户运行时写入的全部 BYOK API Key）
_CHILD_ENV_KEYS = (
    # Windows 运行时
    "PATH", "COMSPEC", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR",
    "HOMEDRIVE", "HOMEPATH", "USERPROFILE", "TEMP", "TMP", "APPDATA",
    "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "COMMONPROGRAMFILES",
    "NUMBER_OF_PROCESSORS", "OS", "PROCESSOR_ARCHITECTURE", "USERNAME",
    "COMPUTERNAME", "DRIVERDATA", "POWERSHELL_DISTRIBUTION_CHANNEL",
    # POSIX 运行时
    "HOME", "SHELL", "LANG", "LC_ALL", "TERM", "TZ", "PWD",
    # 本地化
    "LANGUAGE",
)


def _loads_tolerant(raw: bytes) -> dict[str, Any] | None:
    """**宽容地**读子进程吐回来的一行 ✓ —— 读不出就返回 None（跳过这行 ✓）**绝不抛** ✗。

    ★ 2026-10-10（功能扫测真跑抓到的 ✗）：此前只有 `except json.JSONDecodeError` ✗ ——
      而"编码不对"抛的是 **UnicodeDecodeError** ✗ ⇒ 它会**穿过去**把整个 server 判死 ✓
      实测：一个 Python 写的 MCP server 在中文 Windows 上按 GBK 输出 ⇒ 一启动就被丢掉 ✗
    ⇒ 现在三级兜底：UTF-8 → GBK → UTF-8(replace) ✓ 都读不出才算"这行没用" ✓
      （配合上面的 PYTHONIOENCODING 显式下发 ✓ 双保险 ✓）
    """
    for enc, errs in (("utf-8", "strict"), ("gbk", "strict"), ("utf-8", "replace")):
        try:
            obj = json.loads(raw.decode(enc, errs))
        except (UnicodeDecodeError, ValueError):
            continue
        return obj if isinstance(obj, dict) else None
    return None


class McpServer:
    """一个 MCP stdio server 的连接（懒启动；进程随连接存亡）。线程安全（内部锁串行化请求）。"""
    def __init__(self, name: str, command: str, args: list[str] | None = None, env: dict[str, str] | None = None):
        # 环境白名单（__init__ 前的模块常量 _CHILD_ENV_KEYS）见下方定义
        self.name = name
        self.command = command
        self.args = args or []
        self.env_extra = env or {}
        self._initialized = False  # 复审：进程重启后需重放握手
        self.tools: list[dict[str, Any]] = []
        self._proc: subprocess.Popen | None = None
        self._q: queue.Queue[bytes] = queue.Queue()
        self._req_id = 0
        self._lock = threading.Lock()

    # ---------- 进程与请求-响应（全部同步方法，async 侧用 to_thread 调） ----------

    def _sync_ensure(self) -> subprocess.Popen:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        # 进程不在了（首次启动或崩溃后）——复审 P1：重启后的新进程从未收到
        # initialize 握手，协议合规的 server 会拒绝一切 tools/call。
        self._initialized = False
        command = self.command
        # Windows 坑：npx/uvx 等是 .cmd 脚本，Popen 直接找会 FileNotFoundError——
        # 用 shutil.which 解析出完整路径（含 .cmd）；解析不到按原样给（报错更真实）
        if os.name == "nt" and not os.path.isabs(command) and not command.lower().endswith((".exe", ".cmd", ".bat")):
            from shutil import which

            resolved = which(command)
            if resolved:
                command = resolved
        # 复审 P1：子进程环境改白名单——此前 `**os.environ` 把父进程全部环境
        #（含运行时写入的各服务 API Key）整包移交给 MCP server 进程。server 是
        # 第三方代码，配置即执行；用户可用 env 配置显式补传它需要的变量。
        env = {k: os.environ[k] for k in _CHILD_ENV_KEYS if k in os.environ}
        env.update(self.env_extra)
        # ★★ 2026-10-10（功能扫测真跑抓到的 ✗✗）：**别让子进程用 GBK 说话**
        #   现场：拿一个 Python 写的 MCP server 实测 ⇒ 启动就被丢掉 ✗
        #     `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xb0 in position 88`
        #   查实：白名单环境里没有 PYTHONIOENCODING ⇒ 子进程 stdout.encoding = **gbk** ✗
        #     ⇒ 它按 GBK 吐 JSON ⇒ 这边按 UTF-8 读 ⇒ 当场炸 ✓
        #     （MCP 协议规定走 UTF-8 ✓ —— **任何没自己设编码的 Python server 在中文
        #       Windows 上都会这样死** ✗，而这类 server 一大把 ✓）
        #   ⇒ 这里显式给足：PYTHONIOENCODING=utf-8 + PYTHONUTF8=1 ✓
        #     （用户可在 `env` 配置里覆盖 ✓ —— 用 setdefault 不硬压 ✓）
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        self._proc = subprocess.Popen(
            [command, *self.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # server 的日志别混进协议流
            env=env,
        )
        assert self._proc.stdout is not None

        def _pump() -> None:
            # 后台线程：把子进程 stdout 逐行塞进队列（阻塞读在独立线程，主调用方用超时取）
            assert self._proc is not None and self._proc.stdout is not None
            for raw in self._proc.stdout:
                self._q.put(raw)

        threading.Thread(target=_pump, daemon=True).start()
        return self._proc

    def _sync_rpc(self, method: str, params: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
        with self._lock:  # 串行化：请求-响应必须一一配对
            proc = self._sync_ensure()
            assert proc.stdin is not None
            self._req_id += 1
            rid = self._req_id
            line = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}, ensure_ascii=False)
            try:
                proc.stdin.write(line.encode("utf-8") + b"\n")
                proc.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                raise RuntimeError(f"MCP server「{self.name}」管道已断（{e}）——下次调用会重启进程") from e

            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(f"MCP server「{self.name}」响应 {method} 超时（{timeout:.0f}s）——确认命令可启动")
                try:
                    raw = self._q.get(timeout=remaining)
                except queue.Empty:
                    raise RuntimeError(f"MCP server「{self.name}」响应 {method} 超时（{timeout:.0f}s）——确认命令可启动")
                if not raw:
                    raise RuntimeError(f"MCP server「{self.name}」进程退出（exit={proc.poll()}）")
                msg = _loads_tolerant(raw)
                if msg is None:
                    continue  # 非 JSON 行（横幅/日志/编码不符）——跳过 ✓ 不许因此把 server 判死 ✗
                if msg.get("id") != rid:
                    continue  # server 主动通知/无关回复——跳过
                if "error" in msg:
                    raise RuntimeError(f"MCP 错误：{msg['error']}")
                return msg.get("result") or {}

    # ---------- 协议三步：initialize → tools/list → tools/call（async 门面） ----------

    async def initialize(self) -> None:
        import asyncio

        await asyncio.to_thread(self._sync_rpc, "initialize", {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": MCP_CLIENT_INFO,
        }, 90.0)  # npx 冷启动可到 30s+，放宽
        # 协议要求 client 随后发 initialized 通知（无 id、无响应）
        proc = self._sync_ensure()
        assert proc.stdin is not None
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode("utf-8") + b"\n")
        proc.stdin.flush()

    async def _ensure_handshake(self) -> None:
        """重启后自动重放 initialize/initialized（复审 P1-2）。幂等。"""
        if getattr(self, "_initialized", False) and self._proc and self._proc.poll() is None:
            return
        await self.initialize()
        self._initialized = True

    async def list_tools(self) -> list[dict[str, Any]]:
        import asyncio

        await self._ensure_handshake()
        res = await asyncio.to_thread(self._sync_rpc, "tools/list", {}, 60.0)
        self.tools = list(res.get("tools") or [])
        return self.tools

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> str:
        import asyncio

        await self._ensure_handshake()
        res = await asyncio.to_thread(self._sync_rpc, "tools/call", {"name": tool, "arguments": arguments}, 180.0)
        # 结果 content 是数组 [{type:text,text:...}, ...]——拼文本回给模型
        parts = res.get("content") or []
        texts = [str(p.get("text") or "") for p in parts if isinstance(p, dict) and p.get("type") == "text"]
        out = "\n".join(t for t in texts if t)
        if res.get("isError"):
            raise RuntimeError(out or f"MCP 工具 {tool} 执行失败（无详情）")
        return out or "（MCP 工具执行完成，无文本输出）"

    async def close(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass
        self._proc = None


class McpRegistry:
    """全部 MCP servers 的管理：启动/列举/调用/动态工具表。"""

    def __init__(self, servers_cfg: dict[str, dict[str, Any]] | None = None):
        self._servers: dict[str, McpServer] = {}
        self._cfg = servers_cfg or {}

    async def load_all(self) -> dict[str, list[dict[str, Any]]]:
        """启动全部配置的 server 并拉工具清单。返回 {server名: [工具def]}（失败的服务器跳过并记录）。"""
        out: dict[str, list[dict[str, Any]]] = {}
        for name, scfg in self._cfg.items():
            srv = McpServer(name, str(scfg.get("command") or ""), list(scfg.get("args") or []), scfg.get("env"))
            try:
                await srv.initialize()
                tools = await srv.list_tools()
                self._servers[name] = srv
                out[name] = tools
            except Exception as e:
                await srv.close()
                print(f"[MCP] server「{name}」启动失败，已跳过：{type(e).__name__}: {e}", flush=True)
        return out

    def get(self, name: str) -> McpServer | None:
        return self._servers.get(name)

    @property
    def server_names(self) -> list[str]:
        return sorted(self._servers)

    async def close_all(self) -> None:
        for srv in self._servers.values():
            await srv.close()
        self._servers.clear()


# ---------- 模块级单例（main.py lifespan 装载，loop 调用） ----------

_registry: McpRegistry | None = None


def get_mcp_registry() -> McpRegistry:
    global _registry
    if _registry is None:
        _registry = McpRegistry()
    return _registry


async def load_mcp_servers(servers_cfg: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """启动全部配置的 server 并把工具注册进内置工具表。返回 {server: 注册名列表}。"""
    from .tools.builtin import register_mcp_tool

    reg = get_mcp_registry()
    reg._cfg = servers_cfg or {}
    loaded = await reg.load_all()
    registered: dict[str, list[str]] = {}
    for server, tools in loaded.items():
        names = []
        for t in tools:
            r = register_mcp_tool(server, t)
            if r:
                names.append(r)
        registered[server] = names
        print(f"[MCP] server「{server}」已接入 {len(names)} 个工具：{[t.get('name') for t in tools]}", flush=True)
    return registered
