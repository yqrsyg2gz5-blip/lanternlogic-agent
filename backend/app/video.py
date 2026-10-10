"""视频生成引擎接口 —— 可插拔架构（多引擎编排）。

设计原则（与 providers 注册表同构）：
  - video_gen 工具调用本模块 → 按配置路由到具体引擎（本地 ComfyUI / 云端 API）
  - 每个引擎实现 generate(prompt, seconds, output_path) -> Path
  - 未配置时返回结构化错误（提示用户去设置页配置），绝不糊弄

各引擎的提示词规范差异较大（这是"专用提示词"的由来），由引擎内部转换：
  - ComfyUI（MiniMax H3 / Wan2.1）：正向/负向 prompt + 分辨率 + 帧率 + 采样参数
  - 可灵 Kling（云端）：prompt + mode(std/pro) + duration + aspect_ratio
  - 字节 Seedance（云端）：prompt + resolution + duration
  - Veo 类（Google）：prompt + aspect_ratio + person_generation
新引擎 = 加一个实现 + 一行注册，核心零改动。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable


class VideoEngine:
    name = "base"

    async def generate(
        self,
        prompt: str,
        seconds: int,
        output_path: Path,
        first_frame: Path | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> Path:
        """first_frame：可选首帧图（本地路径）——构图锁定的"零抽卡"打法（第 41 班）：
        先用便宜手段（image_gen/用户图）把画面定死，视频只负责"动"，命中率高一个量级。
        on_progress：进度回调（第 41 班补）——视频生成要 1-5 分钟，没有任何中间提示
        用户会以为卡死了（真实事故：用户连发两条催促把任务打断）。"""
        raise NotImplementedError

    @staticmethod
    def available(cfg: Any) -> bool:
        return False


def _image_to_data_uri(path: Path) -> str:
    """本地图 → base64 Data URL（万相/海螺官方都支持，免公网图床）。"""
    import base64

    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(
        path.suffix.lower(), "image/png"
    )
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


class ConfiguredError(RuntimeError):
    """配置了但引擎不可用（服务未启动/Key 无效）。"""


class NotConfiguredError(RuntimeError):
    """未配置——引导用户去设置页。"""


class ComfyUIVideoEngine(VideoEngine):
    """本地 ComfyUI 视频引擎——**模板驱动**（第 41 班落地）。

    工作流千机千面（每台机器装的节点不同），硬编码节点图必碎；所以引擎只做三件事：
      1. 发现：GET /object_info 列出视频相关节点（告诉用户这台机器能干什么）
      2. 模板：从 backend/comfyui_templates/ 读 API 格式工作流 JSON（ComfyUI 设置里开
         "开发者模式 → Save (API Format)" 导出），把 {{prompt}} 占位符替换成提示词
         （分辨率/帧数等参数在模板里直接设好——API 格式的数值字段放不了占位符）
      3. 执行：POST /prompt 提交 → 轮询 /history → /view 拉回成片
    想要什么工作流，就建什么模板——对应"帮用户接入 ComfyUI / 参照好模板"的诉求。
    """

    name = "comfyui"

    def __init__(self, cfg: Any, video_cfg: dict[str, Any] | None = None) -> None:
        self.base_url = (getattr(cfg, "comfyui_url", "") or "http://127.0.0.1:8189").rstrip("/")
        self.templates_dir = Path(__file__).resolve().parents[1] / "comfyui_templates"
        # ★ 2026-10-10（实测引出来的）：本地出片比云端**慢得多** —— 实测量到：
        #   · 480×272×33 帧（≈2 秒）        → 90 秒 ✓
        #   · 832×480×81 帧（5 秒）         → 跑了 30 分钟还没完 ✗（旧值写死 900 s ⇒ **必然误杀**）
        #   ⇒ 跟随配置 `video.timeout_seconds`（默认 30 分钟，与云端那档同一个口径 ✓）
        self.timeout_hint = float((video_cfg or {}).get("timeout_seconds") or 1800.0)

    @staticmethod
    def available(cfg: Any) -> bool:
        return bool(getattr(cfg, "comfyui_url", None))

    def _templates(self) -> list[Path]:
        if not self.templates_dir.exists():
            return []
        return sorted(self.templates_dir.glob("*.json"))

    async def generate(self, prompt: str, seconds: int, output_path: Path, first_frame: Path | None = None, on_progress: Callable[[str], None] | None = None) -> Path:
        # 模板引擎：首帧图由模板里的 LoadImage 节点预先绑定（API 格式的图片字段放不了运行时占位符）
        import asyncio as _aio

        import httpx

        templates = self._templates()
        if not templates:
            raise NotConfiguredError(
                "ComfyUI 视频模板为空——本引擎按模板工作流出片。"
                "做法：ComfyUI 里搭好文生视频工作流 → 设置开启开发者模式 → "
                "「Save (API Format)」导出 JSON → 放进 backend/comfyui_templates/ 目录"
                "（把工作流里正向提示词输入框的内容改成 {{prompt}}，分辨率等参数直接在模板里设好）。"
            )
        # 优先名里带 video 的模板；否则用唯一模板；多个不分视频的 → 让用户挑
        video_templates = [t for t in templates if "video" in t.name.lower()] or templates
        if len(video_templates) > 1:
            names = "、".join(t.name for t in video_templates)
            raise ConfiguredError(f"有多个视频模板（{names}），请保留一个或告诉我要用哪个。")
        template = video_templates[0]

        graph = json.loads(template.read_text(encoding="utf-8"))
        if "{{prompt}}" not in json.dumps(graph):
            raise ConfiguredError(
                f"模板 {template.name} 里没有 {{{{prompt}}}} 占位符——"
                "请把工作流中提示词输入框（正向提示词）的内容改成 {{prompt}} 再导出；"
                "分辨率等参数直接在模板里设好。"
            )
        # 占位符只做字符串级替换（{{prompt}} 都出现在文本字段里），避免破坏数值结构
        graph = json.loads(json.dumps(graph).replace("{{prompt}}", json.dumps(prompt)[1:-1]))

        async with httpx.AsyncClient(timeout=30) as client:
            # 提交
            try:
                info = (await client.get(f"{self.base_url}/object_info")).json()
            except httpx.HTTPError:
                raise ConfiguredError(f"ComfyUI 不在 {self.base_url}——确认已启动（显存与文字大模型互斥时先停掉对方）。")
            vid_nodes = [k for k in info if any(w in k.lower() for w in ("video", "h3", "wan", "ltx", "mochi", "cogvideo"))]
            q = await client.post(f"{self.base_url}/prompt", json={"prompt": graph})
            if q.status_code >= 400:
                raise ConfiguredError(
                    f"ComfyUI 拒绝了工作流（HTTP {q.status_code}）：{q.text[:300]}"
                    + (f"｜本机视频节点：{vid_nodes[:5]}" if vid_nodes else "")
                )
            prompt_id = q.json()["prompt_id"]
            # 轮询 /history
            deadline = _aio.get_event_loop().time() + max(self.timeout_hint, seconds * 120)
            while True:
                await _aio.sleep(4)
                if _aio.get_event_loop().time() > deadline:
                    raise ConfiguredError(f"ComfyUI 生成超时（prompt_id={prompt_id}），去 ComfyUI 界面看队列状态。")
                h = (await client.get(f"{self.base_url}/history/{prompt_id}")).json()
                if prompt_id not in h:
                    continue  # 还在队列里
                outputs = h[prompt_id].get("outputs") or {}
                # 找视频/动画输出（video/gif/mp4），退而求其次任何文件
                files = []
                for node_out in outputs.values():
                    for key in ("gifs", "videos", "images", "files"):
                        files.extend(node_out.get(key) or [])
                if not files:
                    continue
                pick = next((f for f in files if str(f.get("filename", "")).endswith((".mp4", ".webm", ".gif"))), files[0])
                params = {"filename": pick["filename"], "subfolder": pick.get("subfolder", ""), "type": pick.get("type", "output")}
                v = await client.get(f"{self.base_url}/view", params=params)
                v.raise_for_status()
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(v.content)
                return output_path

    # ★ 2026-10-10：900 → 1800 —— 实测（14B int8 / 12GB 笔记本卡）本地出 5 秒要十几分钟起 ✓
    timeout_hint: float = 1800.0


class ApiVideoEngine(VideoEngine):
    """云端视频 API 通用引擎——统一「提交任务 → 轮询 → 下载 mp4」异步模式。

    第 41 班按五家调研落地（详见 docs/video-providers.md）：
      minimax   MiniMax 海螺 H3        官方文档已核对（platform.minimax.io/cn）
      wan       阿里通义万相（百炼）    DashScope 异步端点（官方文档模式）
      seedance  字节 Seedance（火山方舟）Ark content_generation/tasks（官方文档模式）
      kling     快手可灵               官方开发者平台，ak/sk 签 JWT
    各家鉴权/字段差异封装在 _PROVIDERS 适配器里；未真机验证的适配器已标注，
    用户注册拿 Key 后第一次调用即可验证（错误信息会指出具体失败点）。

    配置（config.json → 顶层 `video` 段，VideoCfg）：
      {"video": {"provider": "minimax", "api_key_env": "MINIMAX_API_KEY",
                 "model": "MiniMax-H3", "resolution": "768P", "ratio": "16:9"}}
    """

    name = "api"

    def __init__(self, cfg: Any, video_cfg: dict[str, Any]) -> None:
        self.provider = (video_cfg.get("provider") or "").strip().lower()
        self.api_base = (video_cfg.get("api_base") or "").strip().rstrip("/")
        key_env = video_cfg.get("api_key_env", "")
        self.api_key = os.environ.get(key_env, "") if key_env else ""
        self.model = video_cfg.get("model") or ""
        self.resolution = video_cfg.get("resolution") or "480P"
        self.ratio = video_cfg.get("ratio") or "16:9"
        self.audio: bool | None = video_cfg.get("audio")  # wan3.0 音频开关（有声更贵）；None=不传用服务端默认
        self.poll_seconds = float(video_cfg.get("poll_seconds") or 10)
        # 实测：i2v 排队高峰可超 15 分钟，900s 会误杀（云端任务其实还在跑还扣费）——默认 30 分钟
        self.timeout_seconds = float(video_cfg.get("timeout_seconds") or 1800)
        self._video_cfg = video_cfg  # 留底：报错时列出其他可用引擎

    def available_engines(self) -> list[str]:
        """当前配置里 Key 已就绪的引擎（报错时告诉 Agent/用户还有哪些能换）。"""
        out = []
        cfg = self._video_cfg or {}
        # 根配置默认引擎（被 engine 覆盖合并后由 get_video_engine 存底，避免"隐形"）
        root_provider = cfg.get("_default_provider") or cfg.get("provider")
        root_key_env = cfg.get("_default_key_env") or cfg.get("api_key_env")
        if root_provider and os.environ.get(root_key_env or ""):
            out.append(root_provider)
        for name, ecfg in (cfg.get("engines") or {}).items():
            if os.environ.get(ecfg.get("api_key_env") or ""):
                out.append(name)
        return sorted(set(out))

    @staticmethod
    def available(cfg: Any) -> bool:
        return True

    # ---------- 适配器：每家返回 (提交请求, 解析轮询) 所需的全部差异 ----------

    def _provider_adapter(self) -> dict[str, Any]:
        """返回该 provider 的请求构造与响应解析。端点均为 2026-10 调研所得。"""
        if self.provider == "minimax":
            def _mm_submit(prompt: str, seconds: int, first_frame: Path | None = None) -> tuple[str, str, dict[str, Any]]:
                content: list[dict[str, Any]] = []
                if first_frame is not None:
                    # 官方 I2V：首帧图支持 base64 Data URL（免图床）——零抽卡打法的引擎级支撑
                    content.append({"type": "image_url", "image_url": {"url": _image_to_data_uri(first_frame)}, "role": "first_frame"})
                content.append({"type": "text", "text": prompt})
                return ("POST", "/v2/video_generation", {
                    "model": self.model or "MiniMax-H3",
                    "content": content,
                    "duration": seconds,
                    "resolution": self.resolution,
                    "ratio": self.ratio,
                })

            return {
                "default_base": "https://api.minimax.cn",  # 国内站；国际站填 https://api.minimax.io
                "headers": {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
                "submit": _mm_submit,
                "task_id": lambda r: r["task_id"],
                "poll": lambda tid: ("GET", f"/v2/query/video_generation/{tid}", None),
                "status": lambda r: str((r.get("task") or {}).get("status", "")).lower(),  # succeeded/failed/cancelled
                "url": lambda r: (
                    ((r.get("task") or {}).get("file") or {}).get("download_url")
                    or (((r.get("task") or {}).get("content") or {}).get("url"))
                ),
            }
        if self.provider == "wan":
            # 第 41 班实测（真 Key）：
            #   wan3.0-video：档位用 `resolution` 参数（480P/720P/1080P）+ `audio` 开关——
            #     不传 resolution 会默认 1080P 超分（SR:1080），一条 5s 好几元；传 480P 则 SR:480 便宜好几倍
            #   wan2.x：档位用 `size`（宽*高），不显式传同样默认 1080P
            size_map = {"480P": "832*480", "720P": "1280*720", "1080P": "1920*1080"}

            def _wan_submit(prompt: str, seconds: int, first_frame: Path | None = None) -> tuple[str, str, dict[str, Any]]:
                model = self.model or "wan3.0-video"
                inp: dict[str, Any] = {"prompt": prompt}
                if first_frame is not None:
                    # 官方 I2V：input.first_frame_image 支持 base64 Data URI（免图床）
                    inp["first_frame_image"] = _image_to_data_uri(first_frame)
                if model.startswith(("wan2", "wanx")):
                    params: dict[str, Any] = {"size": size_map.get(self.resolution.upper(), "832*480"), "duration": seconds}
                else:
                    params = {"resolution": self.resolution or "480P", "duration": seconds}
                    if self.audio is not None:
                        params["audio"] = self.audio
                return ("POST", "/api/v1/services/aigc/video-generation/video-synthesis",
                        {"model": model, "input": inp, "parameters": params})

            return {
                "default_base": "https://dashscope.aliyuncs.com",  # 阿里云百炼
                "headers": {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                    "X-DashScope-Async": "enable",  # DashScope 异步任务的开关头
                },
                "submit": _wan_submit,
                "task_id": lambda r: r["output"]["task_id"],
                "poll": lambda tid: ("GET", f"/api/v1/tasks/{tid}", None),
                "status": lambda r: str((r.get("output") or {}).get("task_status", "")).upper(),  # SUCCEEDED/FAILED
                "url": lambda r: (r.get("output") or {}).get("video_url"),
            }
        if self.provider == "seedance":
            def _sd_submit(prompt: str, seconds: int, first_frame: Path | None = None) -> tuple[str, str, dict[str, Any]]:
                content: list[dict[str, Any]] = []
                if first_frame is not None:
                    # Ark 内容数组图项（与 minimax 同形）；base64 Data URL 支持度未真机验证，报错会带原文
                    content.append({"type": "image_url", "image_url": {"url": _image_to_data_uri(first_frame)}, "role": "first_frame"})
                content.append({"type": "text", "text": prompt})
                return ("POST", "/api/v3/contents/generations/tasks",
                        {"model": self.model or "doubao-seedance-2-5", "content": content})

            return {
                "default_base": "https://ark.cn-beijing.volces.com",  # 火山方舟
                "headers": {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
                "submit": _sd_submit,
                "task_id": lambda r: r["id"],
                "poll": lambda tid: ("GET", f"/api/v3/contents/generations/tasks/{tid}", None),
                "status": lambda r: str(r.get("status", "")).lower(),  # succeeded/failed/cancelled
                "url": lambda r: ((r.get("content") or {}).get("video_url")),
            }
        if self.provider == "kling":
            self._kl_endpoint = "text2video"

            def _kl_submit(prompt: str, seconds: int, first_frame: Path | None = None) -> tuple[str, str, dict[str, Any]]:
                if first_frame is not None:
                    # 官方 I2V 走独立端点 image2video；image 字段为裸 base64（JWT 鉴权同 text2video）——未真机验证
                    self._kl_endpoint = "image2video"
                    b64 = _image_to_data_uri(first_frame).split(",", 1)[-1]
                    return ("POST", f"/v1/videos/{self._kl_endpoint}",
                            {"model_name": self.model or "kling-v3", "image": b64, "prompt": prompt,
                             "duration": str(seconds), "aspect_ratio": self.ratio})
                self._kl_endpoint = "text2video"
                return ("POST", "/v1/videos/text2video",
                        {"model_name": self.model or "kling-v3", "prompt": prompt,
                         "duration": str(seconds), "aspect_ratio": self.ratio})

            poll_path = lambda tid: ("GET", f"/v1/videos/{self._kl_endpoint}/{tid}", None)  # noqa: E731
            return {
                "default_base": "https://api.klingai.com",
                "headers": {"Content-Type": "application/json", "Authorization": f"Bearer {self._kling_jwt()}"},
                "submit": _kl_submit,
                "task_id": lambda r: r["data"]["task_id"],
                "poll": poll_path,
                "status": lambda r: str((r.get("data") or {}).get("task_status", "")).lower(),  # succeed/failed
                "url": lambda r: (((r.get("data") or {}).get("task_result") or {}).get("videos") or [{}])[0].get("url"),
            }
        return {}

    def _kling_jwt(self) -> str:
        """可灵鉴权：api_key_env 的值形如 "ak:sk"，用 HS256 签 JWT（官方鉴权模式）。
        ⚠️ 未真机验证——首次调用若 401，核对 kling.ai/dev 文档的 JWT 字段。"""
        import base64
        import hashlib
        import hmac
        import json
        import time

        try:
            ak, sk = self.api_key.split(":", 1)
        except ValueError:
            raise ConfiguredError(
                "可灵 API Key 格式应为「ak:sk」（开发者平台的应用 AccessKey:SecretKey）"
            )
        now = int(time.time())
        seg = lambda o: base64.urlsafe_b64encode(
            json.dumps(o, separators=(",", ":")).encode()
        ).rstrip(b"=")
        header, payload = seg({"alg": "HS256", "typ": "JWT"}), seg({"iss": ak, "exp": now + 1800, "nbf": now - 5})
        signing_input = header + b"." + payload
        sig = base64.urlsafe_b64encode(
            hmac.new(sk.encode(), signing_input, hashlib.sha256).digest()
        ).rstrip(b"=")
        return (signing_input + b"." + sig).decode()

    # ---------- 生成主流程：提交 → 轮询 → 下载 ----------

    async def generate(
        self,
        prompt: str,
        seconds: int,
        output_path: Path,
        first_frame: Path | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> Path:
        import asyncio as _aio

        import httpx

        def _prog(msg: str) -> None:
            if on_progress is not None:
                try:
                    on_progress(msg)
                except Exception:
                    pass  # 进度提示失败绝不影响生成本身

        if first_frame is not None and not first_frame.exists():
            raise ConfiguredError(f"首帧图不存在：{first_frame}——先用 image_gen 生成或让用户上传。")
        if not self.provider:
            raise NotConfiguredError(
                "视频引擎未配置——到 config.json 顶层 `video` 段填 provider（minimax / wan / "
                "seedance / kling）与 api_key_env，详见 docs/video-providers.md。"
            )
        adapter = self._provider_adapter()
        if not adapter:
            raise NotConfiguredError(
                f"未知视频 provider：{self.provider}（支持：minimax / wan / seedance / kling）"
            )
        if not self.api_key:
            avail = self.available_engines()
            hint = f"当前可用引擎：{'、'.join(avail)}（video_gen 传 engine 参数切换）。" if avail else ""
            raise NotConfiguredError(
                f"引擎 {self.provider} 的 API Key 未设置——先在服务商平台注册创建 Key，"
                f"设到环境变量后重启。{hint}详见 docs/video-providers.md。"
            )
        base = self.api_base or adapter["default_base"]
        headers = adapter["headers"]

        async with httpx.AsyncClient(timeout=60) as client:
            # 1. 提交
            method, path, body = adapter["submit"](prompt, max(3, min(30, int(seconds))), first_frame)
            resp = await client.request(method, f"{base}{path}", json=body, headers=headers)
            if resp.status_code in (401, 403):
                raise ConfiguredError(
                    f"{self.provider} 鉴权失败（HTTP {resp.status_code}）——检查 Key 是否有效、"
                    f"账号是否已开通该模型。响应：{resp.text[:200]}"
                )
            # 欠费/余额不足是真实使用中最常见的拦路虎（第 41 班实测：wan 欠费返回 400 Arrearage、
            # minimax 没钱返回 402 insufficient_balance）——给出行动指引，别让 Agent 自己去翻配置文件
            body_text = resp.text[:300]
            if resp.status_code == 402 or "rrearage" in body_text or "nsufficient balance" in body_text or "verdue" in body_text:
                raise ConfiguredError(
                    f"{self.provider} 账户余额不足或已欠费（HTTP {resp.status_code}）——"
                    f"视频生成是付费服务，请到服务商控制台充值后重试"
                    f"（wan=阿里云百炼控制台 / minimax=platform.minimax.cn / "
                    f"kling=kling.ai/dev / seedance=火山方舟控制台）。"
                )
            if resp.status_code >= 400:
                raise ConfiguredError(
                    f"{self.provider} 提交任务失败（HTTP {resp.status_code}）：{body_text}"
                )
            task_id = adapter["task_id"](resp.json())
            _prog(f"已提交 {self.provider} 云端（单号…{task_id[-6:]}），生成中预计 1–5 分钟，可先聊别的不用等")

            # 2. 轮询（官方建议 10s 间隔；视频生成一般 1–5 分钟，i2v 高峰可到 15 分钟+）
            deadline = _aio.get_event_loop().time() + self.timeout_seconds
            video_url = ""
            last_report = 0.0  # 进度节流：状态变化或每 30s 报一次，别刷屏
            t_start = _aio.get_event_loop().time()
            poll_errs = 0  # 复审 P1：轮询瞬时错误计数（连续 5 次才判死）
            while True:
                await _aio.sleep(self.poll_seconds)
                if _aio.get_event_loop().time() > deadline:
                    raise ConfiguredError(
                        f"视频生成超时（>{int(self.timeout_seconds)}s，task_id={task_id}）——"
                        f"可到服务商控制台查看任务状态，或调大 video.timeout_seconds。"
                    )
                pm, pp, pbody = adapter["poll"](task_id)
                pr = await client.request(pm, f"{base}{pp}", json=pbody, headers=headers)
                if pr.status_code in (429, 500, 502, 503, 504):
                    # 复审 P1：瞬时错误小步重试——直接判死会让 Agent 重新提交任务 = 同一镜头双份计费
                    poll_errs += 1
                    if poll_errs >= 5:
                        raise ConfiguredError(f"{self.provider} 查询任务连续失败（HTTP {pr.status_code}）：{pr.text[:200]}（task_id={task_id}，云端任务可能仍在运行——凭 task_id 到控制台查询，不要重复提交）")
                    await _aio.sleep(10)
                    continue
                poll_errs = 0
                if pr.status_code >= 400:
                    raise ConfiguredError(f"{self.provider} 查询任务失败（HTTP {pr.status_code}）：{pr.text[:200]}")
                data = pr.json()
                st = adapter["status"](data)
                if st in ("succeeded", "succeed"):
                    _prog(f"云端生成完成（等待 {int(_aio.get_event_loop().time() - t_start)}s），正在下载视频…")
                    video_url = adapter["url"](data) or ""
                    if not video_url:
                        raise ConfiguredError(f"{self.provider} 任务成功但未返回视频地址：{str(data)[:300]}")
                    break
                if st in ("failed", "cancelled", "canceled"):
                    raise ConfiguredError(
                        f"{self.provider} 任务{ '失败' if st == 'failed' else '已取消' }（task_id={task_id}）：{str(data)[:300]}"
                    )
                now = _aio.get_event_loop().time()
                if st != "queued" and now - last_report >= 30:
                    last_report = now
                    wait = int(now - t_start)
                    tip = "，云端排队中属正常" if wait >= 180 else ""
                    _prog(f"生成中：{st or '处理中'}，已等待 {wait}s{tip}")

        # 3. 下载 mp4 到任务工作区
        output_path.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
            async with client.stream("GET", video_url) as vr:
                vr.raise_for_status()
                with open(output_path, "wb") as f:
                    async for chunk in vr.aiter_bytes(65536):
                        f.write(chunk)
        return output_path


def get_video_engine(
    executor_cfg: Any,
    video_cfg: dict[str, Any] | None = None,
    engine_name: str | None = None,
) -> VideoEngine:
    """按配置返回视频引擎。

    路由优先级（第 41 班：多引擎并存）：
      1. engine_name 指定 comfyui → 本地引擎
      2. engine_name 在 video.engines 配置里 → 该引擎配置（覆盖合并到默认段）路由云端
      3. 未指定：配置了默认 provider → ApiVideoEngine；否则回 ComfyUI 本地
    """
    if not video_cfg:
        return ComfyUIVideoEngine(executor_cfg, video_cfg)
    name = (engine_name or "").strip().lower()
    if name == "comfyui":
        return ComfyUIVideoEngine(executor_cfg, video_cfg)
    engines = video_cfg.get("engines") or {}
    if name and name in engines:
        merged = {**video_cfg, **engines[name], "provider": engines[name].get("provider") or name}
        merged["_default_provider"] = video_cfg.get("provider")  # 存底：available_engines 别把根默认漏了
        merged["_default_key_env"] = video_cfg.get("api_key_env")
        return ApiVideoEngine(executor_cfg, merged)
    if video_cfg.get("provider") or video_cfg.get("api_url"):
        return ApiVideoEngine(executor_cfg, video_cfg)
    return ComfyUIVideoEngine(executor_cfg, video_cfg)  # 未配置时也返回，generate 内给友好报错
