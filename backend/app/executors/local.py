"""本地执行器 —— 任务工作区沙箱 + 子进程，Windows 兼容。

护栏：
  1. 路径越界防护（resolve 后必须仍在任务工作区内）
  2. 输出截断（防止超长输出撑爆事件流）
  3. 命令超时（executor.timeout_seconds）
命令审批不在执行器里做——那是 loop + approval.py 的职责（状态机属于任务层）。
"""
from __future__ import annotations

import asyncio
import html as html_lib
import os
import secrets
import subprocess
import httpx
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from .base import ExecResult, Executor
from ..mcp import _CHILD_ENV_KEYS
from ..ssrf import SsrfBlocked, assert_public_url, browser_request_allowed, recheck_still_public

# 二十六轮第 7 批第 3 处：_web_fetch 的响应体硬上限（按【已读字节数】判，
# 不依赖 content-length——无 CL 的 chunked 响应此前完全不受限）。
_WEB_FETCH_MAX_BYTES = 5_000_000

#: ★ 2026-10-10（用户实测引出来的）：拼接视频**不能再写死 libx264** ✗
#:   现场：本机那份 ffmpeg（GPT-SoVITS 自带 n4.3.2）编译时带了
#:     `--disable-libx264 --disable-libx265` ⇒ `Unknown encoder 'libx264'`
#:     ⇒ **整个 video_join 用不了** ✗ —— 而同一个 ffmpeg 的 `mpeg4` 是好用的 ✓
#:   又实测：`h264_nvenc` 在这台机器上也起不来（ffmpeg 4.3 的 nvenc 头太老，认不了 5070 Ti ✓
#:     原话：`Cannot get the preset configuration: unsupported param` / `Error initializing output stream`）
#:   ⇒ 顺序：能用的优先，**任何一个失败就换下一个** ✓ 最后一个 mpeg4 兜底（几乎哪份 ffmpeg 都有 ✓）
#:   ★ 硬件编码器**一律不带参数** ✗ —— 新老版本的 preset 名不一样（`-preset p5` 老版本直接报错 ✓ 实测）
CONCAT_VIDEO_ENCODERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("libx264", ("-preset", "veryfast", "-crf", "20")),   # 通用软件编码，有就最好
    ("h264_nvenc", ()),                                   # NVIDIA 显卡硬编
    ("h264_qsv", ()),                                     # Intel 核显
    ("h264_amf", ()),                                     # AMD 显卡
    ("h264_mf", ()),                                      # Windows MediaFoundation
    ("mpeg4", ("-q:v", "3")),                             # 兜底：老，但哪份 ffmpeg 都带
)


def _child_env() -> dict[str, str]:
    """K3：shell 子进程环境白名单（与 MCP 子进程同源 _CHILD_ENV_KEYS）——
    不整包继承 os.environ：运行时会往其中写入全部 BYOK API Key，
    被批准命令里内嵌的恶意代码可静默读 Key 再经联网命令外发。"""
    return {k: os.environ[k] for k in _CHILD_ENV_KEYS if k in os.environ}

_READ_LIMIT = 20_000  # file_read 返回上限（字符）
_OUTPUT_LIMIT = 4_000  # 子进程输出上限（字符）

# A3（审计台账，P1 上线阻塞）：sandbox="off" = 宿主模式，命令**真实作用于用户电脑**。
# 此前宿主分支没有任何告知（沙箱分支有 sandbox_note，宿主分支原样返回输出）——
# 用户看到的和"模拟执行"长得一模一样。这行随每条本机命令回执一起进入会话时间线
# （events.jsonl / history.json）与模型上下文，做到"每条命令都告知一次"。
_HOST_EXEC_NOTE = ("（本机执行：沙箱已关闭——命令直接在你的真实电脑上运行，"
                   "对文件与系统的改动已真实生效、不可撤销）")


async def _docker_kill(cname: str) -> None:
    """超时/取消时终止沙箱容器本体（proc.kill 杀不到 dockerd 管理的容器）。"""
    try:
        kp = await asyncio.create_subprocess_exec(
            "docker", "kill", cname,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(kp.wait(), timeout=10)
        except asyncio.TimeoutError:
            pass
    except Exception:
        pass


def decode_output(raw: bytes) -> str:
    """解码子进程输出：UTF-8 优先，坏字节明显更多时才退回 GBK（cmd.exe 代码页 936）。

    为什么不沿用"UTF-8 严格解码失败就**整体**退回 GBK"：
    Git Bash 的输出里可能同时含合法 UTF-8 与非法字节（bash 会用 `$'\\212'` 形式转义
    路径中的原始字节），一旦严格解码失败就整体改用 GBK，会把本来正确的中文一起毁掉，
    于是**模型的自我验证证据被污染**——它读不到自己命令的输出。

    实测（2026-09-30，task_20260930_0449）：`rm -rf` 删除桌面中文目录后，
    观察结果里的"已删除"变成 `宸插垹闄`，模型只能靠 `ls` 报错去反推"是否删掉了"。

    本函数改为"两种解码各解一遍，谁的替换字符少用谁；打平则优先 UTF-8"。
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    utf8 = raw.decode("utf-8", errors="replace")
    gbk = raw.decode("gbk", errors="replace")
    return gbk if gbk.count("\ufffd") < utf8.count("\ufffd") else utf8


class LocalExecutor(Executor):
    type_name = "local"

    def __init__(self, cfg: Any) -> None:  # ExecutorCfg
        self.root = Path(cfg.workspace_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.timeout = cfg.timeout_seconds
        self.shell = (getattr(cfg, "shell", "") or "").strip() or None  # 显式 shell（Git Bash）：模型可写 Unix 语法
        self.cfg_shell = (getattr(cfg, "shell", "") or "").strip()  # 原始配置值（未解析时也留给 _resolve_bash）
        self.search_url = getattr(cfg, "search_url", "") or "https://www.bing.com/search"
        self.searxng_url = (getattr(cfg, "searxng_url", "") or "").rstrip("/")
        self.browser_channel = getattr(cfg, "browser_channel", "") or "msedge"
        self.comfyui_url = getattr(cfg, "comfyui_url", "") or "http://127.0.0.1:8189"
        self.image_checkpoint = getattr(cfg, "image_checkpoint", "") or "Juggernaut-XL_v9.safetensors"
        self._pw = None  # playwright 运行时（懒启动）
        self._page = None  # 当前实况页面（单例共享；用户可随时人工接管）
        # 授权目录：文件工具可写工作区之外这些目录
        self.allowed = [Path(p).expanduser().resolve() for p in (getattr(cfg, "allowed_dirs", None) or [])]
        # 沙箱（第 41 班实装）：shell 进 Docker 容器，伤不到真机；免审批（状态卡已定设计）
        self.sandbox = (getattr(cfg, "sandbox", "off") or "off").strip().lower()
        self.sandbox_image = getattr(cfg, "sandbox_image", "python:3.12-slim") or "python:3.12-slim"
        self.sandbox_network = getattr(cfg, "sandbox_network", "none") or "none"
        self.sandbox_memory = getattr(cfg, "sandbox_memory", "512m") or "512m"
        self._docker_ok: bool | None = None
        self._docker_at: float = 0.0

    def resolve_in_workspace(self, workdir: Path, rel: str) -> Path:
        base = Path(workdir).resolve()
        p = Path(rel) if rel else Path(".")
        resolved = (base / p).resolve() if p.is_absolute() is False else p.resolve()
        # 工作区永远可用；allowed_dirs 里的授权目录同样放行
        for scope in (base, *self.allowed):
            if resolved.is_relative_to(scope):
                return resolved
        raise PermissionError(f"路径越界（仅允许工作区与 allowed_dirs 授权目录）：{rel}")

    async def run_tool(self, tool: str, args: dict[str, Any], workdir: Path) -> ExecResult:
        t0 = time.monotonic()
        try:
            if tool == "list_dir":
                out = self._list_dir(workdir, str(args.get("path", ".")))
            elif tool == "file_read":
                out = self._file_read(workdir, str(args.get("path", "")))
            elif tool == "file_write":
                out = self._file_write(workdir, str(args.get("path", "")), str(args.get("content", "")))
            elif tool == "shell_exec":
                out = await self._shell(workdir, str(args.get("command", "")), cfg_shell=str(getattr(self, "cfg_shell", "") or ""))
            elif tool == "web_search":
                out = await self._web_search(str(args.get("query", "")))
            elif tool == "web_fetch":
                out = await self._web_fetch(str(args.get("url", "")))
            elif tool == "browser_navigate":
                out = await self._browser_navigate(str(args.get("url", "")))
            elif tool == "browser_snapshot":
                out = await self._browser_snapshot()
            elif tool == "browser_click":
                out = await self._browser_click(str(args.get("text", "")))
            elif tool == "browser_type":
                out = await self._browser_type(str(args.get("text", "")))
            elif tool == "browser_key":
                out = await self._browser_key(str(args.get("key", "Enter")))
            elif tool == "code_check":
                out = await self._code_check(workdir, str(args.get("path", "")))
            elif tool == "image_gen":
                out = await self._image_gen(workdir, str(args.get("prompt", "")), int(args.get("width", 512)), int(args.get("height", 512)))
            elif tool == "video_join":
                out = await self._video_join(workdir, list(args.get("files") or []), str(args.get("output") or ""))
            else:
                out = f"未知工具：{tool}"
                return ExecResult(False, out, self._ms(t0))
            return ExecResult(True, out, self._ms(t0))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # 工具失败也是合法观察结果，不炸 loop
            return ExecResult(False, f"{type(e).__name__}: {e}", self._ms(t0))

    # ---------- 各工具实现 ----------

    def _list_dir(self, workdir: Path, rel: str) -> str:
        d = self.resolve_in_workspace(workdir, rel)
        if not d.exists():
            return f"（不存在）{rel}"
        if d.is_file():
            return f"（文件）{rel}"
        entries = sorted(d.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        if not entries:
            return "（空目录）"
        lines = [
            f"{'[dir] ' if e.is_dir() else f'{e.stat().st_size:>8}B '} {e.name}"
            for e in entries[:100]
        ]
        return "\n".join(lines)

    def _file_read(self, workdir: Path, rel: str) -> str:
        f = self.resolve_in_workspace(workdir, rel)
        text = f.read_text("utf-8", errors="replace")
        if len(text) > _READ_LIMIT:
            text = text[:_READ_LIMIT] + f"\n…（截断，共 {len(text)} 字符）"
        return text or "（空文件）"

    def _file_write(self, workdir: Path, rel: str, content: str) -> str:
        f = self.resolve_in_workspace(workdir, rel)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, "utf-8")
        return f"已写入 {rel}（{len(content)} 字符）"

    _UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AgentShell/0.1"}

    async def _web_search(self, query: str) -> str:
        """联网搜索——降级链：SearXNG（配置了才走）→ Bing 结构化 → Bing RSS → 整页纯文本。"""
        if not query.strip():
            raise ValueError("query 不能为空")
        headers = self._UA
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=headers) as client:
            # 策略 0：SearXNG 本地实例（结构化 JSON，稳定不脆——第 40 班标记的搜索源升级）
            if self.searxng_url:
                try:
                    resp = await client.get(
                        f"{self.searxng_url}/search",
                        params={"q": query, "format": "json", "language": "zh-CN"},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    out: list[str] = []
                    for i, r in enumerate(data.get("results", [])[:10], 1):
                        out.append(f"{i}. {r.get('title', '')}")
                        out.append(f"   {r.get('url', '')}")
                        snip = str(r.get("content") or "")[:200]
                        if snip:
                            out.append(f"   {snip}")
                        if len(out) >= 30:
                            break
                    if out:
                        return chr(10).join(out)
                except (httpx.HTTPError, ValueError):
                    pass  # SearXNG 不可用 → 静默走 Bing 链（部署了才享受，没部署不受影响）

            # 策略 1：Bing 标准版（结构化解析）
            try:
                resp = await client.get(self.search_url, params={"q": query, "setlang": "zh-hans"})
                resp.raise_for_status()
                results = self._parse_bing(resp.text)
                if results:
                    return chr(10).join(results)
            except httpx.HTTPError:
                pass

            # 策略 2：Bing 简版（跳过结构化解析，直接抓整页文本让模型自行阅读）
            try:
                resp2 = await client.get("https://www.bing.com/search", params={"q": query, "format": "rss"})
                resp2.raise_for_status()
                if "<item>" in resp2.text:
                    items = re.findall(r"<title>(.*?)</title>", resp2.text)
                    links = re.findall(r"<link>(.*?)</link>", resp2.text)
                    out = []
                    for i, (t, u) in enumerate(zip(items[1:], links)):
                        out.append(f"{i + 1}. {t}")
                        out.append(f"   {u}")
                        if len(out) >= 16:
                            break
                    if out:
                        return chr(10).join(out)
            except httpx.HTTPError:
                pass

            # 策略 3：整页纯文本兜底（把搜索结果页直接剥成文本，模型自行阅读）
            try:
                resp3 = await client.get(self.search_url, params={"q": query})
                resp3.raise_for_status()
                raw = resp3.text
                raw = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=re.I | re.S)
                raw = re.sub(r"<[^>]+>", " ", raw)
                raw = html_lib.unescape(raw)
                raw = re.sub(r"\s{3,}", chr(10), raw).strip()
                if len(raw) > 200:
                    return "（搜索页纯文本——请自行提取相关信息）" + chr(10) + raw[:4000]
            except httpx.HTTPError:
                pass

        return "（所有搜索引擎均未返回结果——建议直接用 web_fetch 抓取已知网站页面）"

    def _parse_bing(self, page: str) -> list[str]:
        """Bing 结构化解析（多模式匹配）。"""
        out: list[str] = []
        # 模式 1：标准 b_algo
        for m in re.finditer(r'<li class="b_algo".*?<h2><a[^>]+href="([^"]+)"[^>]*>(.*?)</a></h2>(.*?)</li>', page, re.S):
            url = m.group(1)
            title = html_lib.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
            snip_m = re.search(r"<p[^>]*>(.*?)</p>", m.group(3), re.S)
            snip = html_lib.unescape(re.sub(r"<[^>]+>", "", snip_m.group(1))).strip() if snip_m else ""
            out.append(f"{len(out) + 1}. {title}")
            out.append(f"   {url}")
            if snip:
                out.append(f"   {snip[:200]}")
            if len(out) >= 24:
                break
        if out:
            return out
        # 模式 2：宽松匹配（某些 Bing 变体不用 b_algo class）
        for m in re.finditer(r'<h2><a[^>]+href="(http[^"]+)"[^>]*>(.*?)</a></h2>', page, re.S):
            url = m.group(1)
            title = html_lib.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
            out.append(f"{len(out) + 1}. {title}")
            out.append(f"   {url}")
            if len(out) >= 16:
                break
        return out

    async def _web_fetch(self, url: str, *, _transport=None) -> str:
        """抓取网页正文：HTML 剥标签取文本，截断 6000 字符。

        K2 SSRF 防线（fail-closed）：不再自动 follow 重定向——手动逐跳处理，
        每一跳都先 assert_public_url（解析全量 IP 过内网/回环/保留段黑名单），
        响应到手后 recheck_still_public（二次解析取交集，防 DNS 重绑定）。
        _transport 仅供测试注入 httpx.MockTransport（不影响生产默认）。

        二十六轮第 7 批第 3 处（K2 的 >5MB 拒抓名不副实）：改为 `client.send(...,
        stream=True)` + 边读边计数，超过 _WEB_FETCH_MAX_BYTES 立即 aclose()。
        旧实现是 `resp = await client.get(...)`（缓冲整体）再读 content-length：
        真实本地服务实测 D6 6MB(有 CL) → 抛错时服务端【已成功写出 6,000,000 字节】
        （全读完才判）；D7 6MB(无 CL/chunked) → 【根本不拒绝】且同样消费 6,000,000
        字节。即"拒绝采用"而非"拒绝下载"，带宽/内存无上限且可被反复触发。
        新判据只看【已读字节数】，不依赖 content-length。
        """
        if not url.strip().startswith(("http://", "https://")):
            raise ValueError("url 必须以 http(s):// 开头")
        current = url.strip()
        async with httpx.AsyncClient(timeout=20, follow_redirects=False,
                                     headers=self._UA, transport=_transport) as client:
            for _hop in range(6):
                ips = await assert_public_url(current)          # 每一跳都检查
                req = client.build_request("GET", current)
                resp = await client.send(req, stream=True)      # ★ 流式：不缓冲整体
                try:
                    await recheck_still_public(current, ips)    # 防 DNS 重绑定（不过关则丢弃响应）
                    if resp.status_code in (301, 302, 303, 307, 308):
                        loc = resp.headers.get("location") or ""
                        nxt = urljoin(current, loc)
                        if not nxt.startswith(("http://", "https://")):
                            raise SsrfBlocked(f"重定向到非 http(s) 协议（{loc[:60]}），已拦截")
                        current = nxt
                        continue
                    resp.raise_for_status()
                    body = bytearray()
                    async for chunk in resp.aiter_bytes():
                        body += chunk
                        if len(body) > _WEB_FETCH_MAX_BYTES:
                            raise ValueError(
                                f"响应体过大（>5MB，已读 {len(body)} 字节），已中断下载"
                            )
                    ctype = resp.headers.get("content-type", "")
                    text = bytes(body).decode(resp.encoding or "utf-8", errors="replace")
                    break
                finally:
                    await resp.aclose()   # ★ 超限/重定向/异常都必须立刻断连
            else:
                raise SsrfBlocked("重定向跳数超限（>6），已拦截")
        if "html" in ctype or text.lstrip().startswith("<"):
            text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.I | re.S)
            text = re.sub(r"<[^>]+>", " ", text)
            text = html_lib.unescape(text)
            text = text.replace(chr(9), " ").replace(chr(13), "")
            text = chr(10).join(line.strip() for line in text.splitlines() if line.strip())
        text = text.strip()
        return text[:6000] if text else "（无文本内容）"

    # ---------- 浏览器实况（Playwright 驱动真实窗口；用户可随时人工接管） ----------

    async def _ensure_page(self):
        if self._page is not None and not self._page.is_closed():
            return self._page
        from playwright.async_api import async_playwright

        if self._pw is None:
            self._pw = await async_playwright().start()
        try:
            browser = await self._pw.chromium.launch(channel=self.browser_channel, headless=False)
        except Exception:
            browser = await self._pw.chromium.launch(headless=False)  # 回退内置 chromium
        self._page = await (await browser.new_context()).new_page()
        # K2：浏览器路由级 SSRF 拦截——页面主文档/子资源/重定向的每个请求都过
        # 内网黑名单（LLM 被注入时点恶意链接、页面 302 跳内网，都在此被 abort）。
        async def _ssrf_route(route):
            if await browser_request_allowed(route.request.url):
                await route.continue_()
            else:
                await route.abort("blockedbyclient")
        await self._page.route("**/*", _ssrf_route)
        return self._page

    async def _browser_navigate(self, url: str) -> str:
        if not url.strip().startswith(("http://", "https://")):
            url = "https://" + url
        await assert_public_url(url)  # K2：导航前先过内网黑名单（fail-closed）
        page = await self._ensure_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await assert_public_url(page.url)  # 落地地址再验一次（重定向后）
        title = await page.title()
        return f"已打开 {page.url}（标题：{title}）"

    async def _browser_snapshot(self) -> str:
        page = await self._ensure_page()
        text = (await page.inner_text("body"))[:2000]
        elems = await page.eval_on_selector_all(
            "a, button, input, textarea, select",
            "els => els.slice(0, 40).map(e => ({t: e.tagName, s: (e.innerText || e.placeholder || e.value || e.alt || e.getAttribute('aria-label') || '').slice(0, 40), ty: e.type || ''}))",
        )
        lines = []
        for e in elems:
            label = (e.get("s") or "").strip()
            if e.get("ty"):
                label += f"（{e['ty']}）"
            if label or e["t"].lower() in ("input", "textarea"):
                lines.append(f"[{e['t'].lower()}] {label}")
        return (
            "页面文本：" + chr(10) + text
            + chr(10) * 2 + "可交互元素：" + chr(10)
            + chr(10).join(lines or ["（无）"])
        )

    async def _browser_click(self, text: str) -> str:
        """点一个元素（按可见文字/无障碍名模糊匹配 ✓）。

        ★★ 2026-10-07（功能体检**真跑**时抓到的可用性问题 ✗✗）：
          拿 example.com 试 ✓ 传 "More information" ✗ 而页面上是 "More information..." ✓
          ⇒ 三种匹配全失败 ✓ 于是返回一句"未找到可点击元素：xxx" ✗
          —— **模型看到这句话只能瞎猜** ✗（它不知道页面上到底有什么 ✓ 只能再 snapshot 一次 ✓
          白烧一轮 token ✓）。实测就是这样卡住的 ✓。
          ⇒ 两处改进：
            ① **成功时回报落地地址与标题** ✓ —— 让模型知道"点了到底跳没跳"✓
               （原来只说"已点击"✗ 跳没跳它不知道 ✓）
            ② **失败时把页面上能点的东西列出来** ✓ —— 模型下一轮就能照着改 ✓
               （而不是"未找到"三个字 ✓ 那是把难题原样丢回去 ✗）
        """
        page = await self._ensure_page()
        try:
            await page.get_by_text(text, exact=False).first.click(timeout=5000)
            return await self._click_done(page, text)
        except Exception:
            pass
        for role in ("button", "link", "textbox"):
            try:
                await page.get_by_role(role, name=text).first.click(timeout=3000)
                return await self._click_done(page, f"{text}（{role}）")
            except Exception:
                continue
        return (f"未找到可点击元素：{text}\n"
                f"这一页现在能点的是：\n{await self._clickable_list(page)}\n"
                "（挑一个和你要点的最接近的，用它的原文再点一次）")

    async def _click_done(self, page: object, what: str) -> str:
        """点完回报**落地地址 + 标题** ✓（让模型知道跳没跳 ✓ 不用再 snapshot 一次 ✓）。"""
        try:
            await page.wait_for_timeout(600)                       # noqa: SLF001
            title = await page.title()                             # type: ignore[attr-defined]
            return f"已点击：{what}；现在在 {page.url}（标题：{title}）"   # type: ignore[attr-defined]
        except Exception:                                          # noqa: BLE001
            return f"已点击：{what}"

    async def _clickable_list(self, page: object, limit: int = 15) -> str:
        """页面上**真正能点**的东西（按钮/链接 ✓ 去重 ✓ 截断 ✓）。"""
        try:
            items = await page.eval_on_selector_all(          # type: ignore[attr-defined]
                "a, button, input, select, textarea",
                "els => els.map(e => (e.innerText || e.placeholder || e.value ||"
                " e.getAttribute('aria-label') || '').trim()).filter(Boolean).slice(0, 60)")
        except Exception:                                          # noqa: BLE001
            return "（读不出来）"
        seen: list[str] = []
        for it in items:
            one = " ".join(str(it).split())[:40]
            if one and one not in seen:
                seen.append(one)
        return "\n".join(f"  · {s}" for s in seen[:limit]) or "（这一页没有可点的元素）"

    async def _browser_type(self, text: str) -> str:
        page = await self._ensure_page()
        await page.keyboard.type(text, delay=30)
        return f"已输入：{text}"

    async def _browser_key(self, key: str) -> str:
        page = await self._ensure_page()
        await page.keyboard.press(key)
        await page.wait_for_timeout(800)
        return f"已按键：{key}"


    async def _code_check(self, workdir: Path, rel: str) -> str:
        """代码语法/结构校验（确定性，不依赖模型自检）。

        支持：.py（py_compile）｜ .js/.mjs/.cjs（node --check）｜ .json（json 解析）
             ｜ .html（基础标签平衡粗检）｜ 其它类型明确报告「无可用校验器」。
        """
        import json as _json
        import sys as _sys

        f = self.resolve_in_workspace(workdir, rel)
        if not f.exists() or f.is_dir():
            raise FileNotFoundError(f"文件不存在：{rel}")
        suffix = f.suffix.lower()
        text = f.read_text('utf-8', errors='replace')

        if suffix == '.py':
            proc = await asyncio.create_subprocess_exec(
                _sys.executable, '-m', 'py_compile', str(f),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            out_bytes, _ = await proc.communicate()
            if proc.returncode == 0:
                return f'✓ {rel} 语法正确（py_compile 通过）'
            return f'✗ {rel} 语法错误：{out_bytes.decode("utf-8", "replace")[:1500]}'

        if suffix in ('.js', '.mjs', '.cjs'):
            try:
                proc = await asyncio.create_subprocess_exec(
                    'node', '--check', str(f),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                )
                out_bytes, _ = await proc.communicate()
                if proc.returncode == 0:
                    return f'✓ {rel} 语法正确（node --check 通过）'
                return f'✗ {rel} 语法错误：{out_bytes.decode("utf-8", "replace")[:1500]}'
            except FileNotFoundError:
                return f'（未找到 node，跳过 JS 校验）{rel}'

        if suffix == '.json':
            try:
                _json.loads(text)
                return f'✓ {rel} JSON 合法'
            except _json.JSONDecodeError as e:
                return f'✗ {rel} JSON 非法：{e}'

        if suffix in ('.html', '.htm'):
            import re as _re
            opens = len(_re.findall(r'<(div|section|main|aside|header|footer|ul|ol|table|form)\b', text, _re.I))
            closes = len(_re.findall(r'</(div|section|main|aside|header|footer|ul|ol|table|form)>', text, _re.I))
            if opens == closes:
                return f'✓ {rel} HTML 结构粗检通过（块级标签 {opens} 开 / {closes} 闭）'
            return f'✗ {rel} HTML 标签不平衡：{opens} 个开标签 vs {closes} 个闭标签（块级元素）'

        return f'（{suffix or "无扩展名"} 无内置校验器——可用 shell_exec 运行该语言的检查命令，如 npx tsc --noEmit）'

    async def _image_gen(self, workdir: Path, prompt: str, width: int = 512, height: int = 512) -> str:
        """调用 ComfyUI txt2img 生成图像，保存到任务工作区。"""
        import random
        wf = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": self.image_checkpoint}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry, low quality, watermark, ugly, deformed", "clip": ["1", 1]}},
            "4": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
            "5": {"class_type": "KSampler", "inputs": {"seed": random.randint(0, 2 ** 32), "steps": 12, "cfg": 6.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0, "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0]}},
            "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
            "7": {"class_type": "SaveImage", "inputs": {"filename_prefix": "agentshell", "images": ["6", 0]}},
        }
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{self.comfyui_url}/prompt", json={"prompt": wf})
            r.raise_for_status()
            pid = r.json()["prompt_id"]
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                await asyncio.sleep(3)
                h = (await client.get(f"{self.comfyui_url}/history/{pid}")).json()
                if pid in h and h[pid].get("status", {}).get("completed"):
                    outs = list(h[pid].get("outputs", {}).values())
                    if not outs or not outs[0].get("images"):
                        raise RuntimeError("ComfyUI 任务完成但没有产出图片（检查模型/显存错误日志）")
                    img_info = outs[0]["images"][0]
                    raw = await client.get(f"{self.comfyui_url}/view", params=img_info)
                    filename = f"gen_{random.randint(1000, 9999)}.png"
                    (workdir / filename).write_bytes(raw.content)
                    return filename
            raise TimeoutError("ComfyUI 图像生成超时（600s）")

    async def _shell_sandbox(self, workdir: Path, command: str) -> str:
        """Docker 沙箱执行：命令进一次性容器（--rm），只挂载任务工作区。

        与状态卡定稿设计一致：默认断网（network=none）、限内存/CPU/进程数、非 root。
        Docker 不可用时**明确报错**（安全相关绝不静默降级到真机执行）。
        限制（如实）：只挂载了工作区 → 沙箱下写不了桌面等授权目录，需要写盘外路径时
        用户可临时把沙箱切回"关"。
        """
        if not await self._docker_available():
            raise RuntimeError(
                "沙箱（Docker）不可用——请启动 Docker Desktop 后重试；"
                "或在设置页把「沙箱隔离」切回关（命令将直接在本机执行）。"
                "本次命令未执行（安全相关，不做静默降级）。"
            )
        await self._ensure_sandbox_image()
        # Docker 卷挂载必须是绝对路径（历史坑：Agent 任务工作区是相对路径 data\tasks\...，
        # 直接传会被当成"卷名"拒绝——第 41 班沙箱真机首跑踩到）
        # 复审：容器命名 + 超时 docker kill。proc.kill() 只杀 docker CLI 客户端，
        # 容器本体由 dockerd 管理会继续跑（残留容器叠加 + "已终止"是假消息）。
        cname = f"agentshell_{secrets.token_hex(4)}"
        docker_cmd = [
            "docker", "run", "--rm", "--name", cname,
            "-v", f"{workdir.resolve()}:/w", "-w", "/w",
            "--network", self.sandbox_network,
            "--memory", self.sandbox_memory,
            "--cpus", "1",
            "--pids-limit", "128",
            "--user", "1000:1000",
            self.sandbox_image, "sh", "-c", command,
        ]
        proc = await asyncio.create_subprocess_exec(
            *docker_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            raw, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await _docker_kill(cname)
            return f"命令超时（>{self.timeout:.0f}s），已终止容器（退出码 124）"
        except asyncio.CancelledError:
            proc.kill()
            await _docker_kill(cname)
            raise
        text = self._clip_output(decode_output(raw), proc.returncode)
        sandbox_note = ("（沙箱内执行：命令在一次性容器中运行——宿主机的 C盘/D盘/桌面不可见；"
                        "但 /w 是本机任务工作区的挂载，其中的删除/改写会真实作用于本机文件。"
                        "需要访问宿主机磁盘时，请告知用户关闭沙箱后重试）")
        suffix = sandbox_note if proc.returncode == 0 else f"（exit {proc.returncode}）"
        # 复审 P0-D：镜像不含 bash 时给出可操作的提示（exit 127 = command not found）
        if proc.returncode == 127 and "bash" in command:
            suffix += "；提示：当前沙箱镜像可能不含 bash——请改用 sh 语法，或在 executor.sandbox_image 配置 python:3.12-slim"

        return text + suffix

    @staticmethod
    def _clip_output(text: str, returncode: int) -> str:
        """输出截断（复审：沙箱路径此前无截断——容器内 yes/cat 大文件会把
        巨量输出抽进宿主内存与 events.jsonl）。语义与 _shell 的截断一致。"""
        text = text.strip()
        if not text:
            return "（无输出，退出码 %d）" % returncode
        if len(text) > _OUTPUT_LIMIT:
            return text[:_OUTPUT_LIMIT] + f"\n…（截断，退出码 {returncode}）"
        if returncode not in (0, None):
            return f"{text}\n（退出码 {returncode}）"
        return text

    async def _docker_available(self) -> bool:
        """docker info 探测，结果缓存 60s（避免每次设置页/命令都探一遍）。"""
        now = time.monotonic()
        if self._docker_ok is not None and now - self._docker_at < 60:
            return self._docker_ok
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "info", "--format", "ok",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=15)
            self._docker_ok = proc.returncode == 0
        except Exception:
            self._docker_ok = False
        self._docker_at = now
        return self._docker_ok

    async def _ensure_sandbox_image(self) -> None:
        """镜像缺失时自动拉取（只查本地，不重复 pull）。"""
        proc = await asyncio.create_subprocess_exec(
            "docker", "image", "inspect", self.sandbox_image,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=15)
        if proc.returncode == 0:
            return
        yield_msg = f"首次使用沙箱：正在拉取镜像 {self.sandbox_image}（约 3–8 MB，走已配镜像加速）"
        print(yield_msg, flush=True)
        pull = await asyncio.create_subprocess_exec(
            "docker", "pull", self.sandbox_image,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(pull.wait(), timeout=300)
        except asyncio.TimeoutError:
            pull.kill()
            raise RuntimeError(f"拉取沙箱镜像超时（{self.sandbox_image}）——检查网络/镜像加速后重试")
        if pull.returncode != 0:
            raise RuntimeError(f"拉取沙箱镜像失败（{self.sandbox_image}）——手动 docker pull 看具体报错")

    async def _video_join(self, workdir: Path, files: list[str], output: str) -> str:
        """ffmpeg 拼接多段视频 → 一条长视频（长视频/AI 漫剧的最后一步）。

        各家 API 单次只出 5–30s，长内容 = 分镜生成 + 此处合成。
        用 concat demuxer + **重编码**（不同分辨率/帧率/有无声的输入也能接；纯 -c copy 要求逐参数一致，太脆）。
        """
        if not files:
            raise ValueError("files 不能为空——按播放顺序列出工作区里的视频文件名")
        paths = [self.resolve_in_workspace(workdir, f) for f in files]
        missing = [str(p.name) for p in paths if not p.is_file()]
        if missing:
            raise ValueError(f"这些视频不存在于工作区：{missing}")
        out_rel = output.strip() or f"joined_{int(time.time())}.mp4"
        out_path = self.resolve_in_workspace(workdir, out_rel)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # concat 清单（相对路径 + 单引号转义，ffmpeg concat demuxer 格式）
        list_file = workdir / f"_concat_{time.monotonic_ns()}.txt"
        list_file.write_text(
            "".join(f"file '{p.relative_to(workdir).as_posix().replace(chr(39), chr(39) * 2)}'\n" for p in paths),
            "utf-8",
        )
        used, tried = "", []
        try:
            for enc, qargs in CONCAT_VIDEO_ENCODERS:
                proc = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
                    "-c:v", enc, *qargs,
                    "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                    str(out_path),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                )
                try:
                    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
                except asyncio.TimeoutError:
                    proc.kill()
                    raise RuntimeError("ffmpeg 拼接超时（>600s）")
                err = stdout.decode("utf-8", "replace").strip()
                if proc.returncode == 0:
                    used = enc
                    break
                # 这个编码器不行就换下一个 ✓（本机实测：libx264 没编进去 / nvenc 起不来 / mpeg4 好用 ✓）
                tried.append(f"{enc}: {((err.splitlines() or ['无输出'])[-1])[:140]}")
            if not used:
                raise RuntimeError("ffmpeg 拼接失败——试过的编码器都不行：\n  " + "\n  ".join(tried))
        finally:
            list_file.unlink(missing_ok=True)

        # 回报成片时长（ffprobe 探测，失败不阻塞交付）
        dur = ""
        try:
            probe = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(out_path),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await probe.communicate()
            secs = float(stdout.decode().strip())
            dur = f"，总时长 {secs:.1f} 秒"
        except Exception:
            pass
        return f"已拼接 {len(paths)} 段 → {out_path.name}（编码器 {used}）{dur}。交付时用 attachments 带上该文件名。"

    @staticmethod
    def _resolve_bash(cfg_shell: str = "", env=None) -> str | None:
        """解析可用的 Git Bash（验证报告 20 复验抓出的真 bug）：

        Windows 自带的 WSL 启动器 System32\bash.exe 会在 PATH 里排在 Git Bash
        前面——shell 未显式配置时 `bash -c …` 撞上 WSL（其环境里没有
        /bin/bash，直接 execvpe 失败），用户批准了命令还是跑不成。
        顺序：显式配置 > 常见安装路径 > where bash（排除 WSL/Store 劫持）。"""
        if cfg_shell and Path(cfg_shell).is_file():
            return cfg_shell
        if os.name != "nt":
            return None  # POSIX 平台系统 bash 本来就对
        for cand in (
            "C:/Program Files/Git/bin/bash.exe",
            "C:/Program Files (x86)/Git/bin/bash.exe",
            str(Path.home() / "AppData/Local/Programs/Git/bin/bash.exe"),
        ):
            if Path(cand).is_file():
                return cand
        try:
            out = subprocess.run(["where", "bash"], capture_output=True, text=True, timeout=10,
                                 env=env).stdout.splitlines()
        except Exception:
            return None
        for line in out:
            p = line.strip()
            if not p:
                continue
            low = p.lower()
            if "system32" in low or "windowsapps" in low:
                continue  # WSL 启动器 / Store 劫持——语义完全不同，绝不能用
            if Path(p).is_file():
                return p
        return None

    async def _shell(self, workdir: Path, command: str, cfg_shell: str = "") -> str:
        if not command.strip():
            raise ValueError("command 不能为空")
        if self.sandbox == "docker":  # 沙箱模式：命令进容器（第 41 班实装，状态卡已定设计）
            return await self._shell_sandbox(workdir, command)
        env = _child_env()  # K3：白名单环境（剔除 *_API_KEY/*_TOKEN/*SECRET 等）
        shell = self.shell or self._resolve_bash(cfg_shell, env=env)
        if shell:  # Git Bash（显式配置或自动解析）：走 exec，模型可写 Unix 语法，输出为 UTF-8
            proc = await asyncio.create_subprocess_exec(
                shell, "-c", command,
                cwd=str(workdir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
        else:  # 平台默认 shell（Windows = cmd.exe）
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(workdir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
        try:
            raw, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return f"命令超时（>{self.timeout:.0f}s），已终止（退出码 124）"
        except asyncio.CancelledError:
            proc.kill()  # 任务取消时别留下孤儿进程
            raise
        text = decode_output(raw)
        text = text.strip() or "（无输出，退出码 %d）" % proc.returncode
        if len(text) > _OUTPUT_LIMIT:
            text = text[:_OUTPUT_LIMIT] + f"\n…（截断，退出码 {proc.returncode}）"
        elif proc.returncode not in (0, None):
            text = f"{text}\n（退出码 {proc.returncode}）"
        # A3（审计台账）：宿主模式此前**没有任何风险告知**。沙箱分支有 sandbox_note，
        # 本机分支却把输出原样返回 ⇒ 用户（和模型）看到的只是一段普通命令回显，
        # 意识不到这条命令已经**真实作用在这台电脑上**（删除/改写/安装/联网都真的发生了、
        # 不可撤销）。与沙箱分支对称，补一行等价告知。逐条对照见
        # tests/test_host_mode_notice.py。
        return text + _HOST_EXEC_NOTE

    @staticmethod
    def _ms(t0: float) -> int:
        return int((time.monotonic() - t0) * 1000)
