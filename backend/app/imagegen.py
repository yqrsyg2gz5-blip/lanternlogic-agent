"""云端图像生成引擎（第 41 班）——做图与视频同架构：云端 API 优先，本地 ComfyUI 可选。

为什么：本地 ComfyUI 需要用户自装模型（分发场景不现实）；云端 API 用用户自己的
Key（阿里百炼，与视频/通义同账号），装完软件就能画。

引擎：DashScope 通义万相文生图（wanx2.1-t2i-turbo，异步任务：提交→轮询→下载）。
本地 ComfyUI 引擎保留（executors/local.py _image_gen），按配置路由。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

from . import retry        # ★ 2026-10-07 第 4 项：统一的限流/瞬时故障退避 ✓

BASE = "https://dashscope.aliyuncs.com"


class DashscopeImageEngine:
    def __init__(self, cfg: dict[str, Any], on_wait: Any = None):
        import os

        self.model = cfg.get("model") or "wanx2.1-t2i-turbo"
        self.size = cfg.get("size") or "1024*1024"
        self.api_key = os.environ.get(cfg.get("api_key_env") or "DASHSCOPE_API_KEY", "")
        self.timeout = float(cfg.get("timeout_seconds") or 180)
        # ★ 2026-10-07（第 4 项）：**重试要让用户看得见** ✓
        #   （原来是默默等/默默失败 ✓ 最多 60 秒界面一片安静 ⇒ 像卡死 ✓）
        self.on_wait = on_wait

    async def generate(self, prompt: str, output_path: Path, width: int | None = None, height: int | None = None) -> Path:
        if not self.api_key:
            raise RuntimeError(
                "云端作图需要 DASHSCOPE_API_KEY（阿里云百炼 Key，与通义/知识库同账号）——"
                "设置环境变量后重启；或在设置里把作图引擎切回本地 ComfyUI。"
            )
        size = self.size
        if width and height:
            size = f"{width}*{height}"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "X-DashScope-Async": "enable"}
        async with httpx.AsyncClient(timeout=60) as client:
            # ★ 2026-10-07（第 4 项）：提交这一步也要退避 ✓ ——
            #   原来撞上 429 直接抛 ⇒ 一次瞬时限流就把整个出图任务判死 ✗
            #   （而且**钱可能已经花了**：图已经提交上去了 ✓ 白扔 ✓）
            r = await retry.request_with_backoff(
                client, "POST", f"{BASE}/api/v1/services/aigc/text2image/image-synthesis",
                what="提交出图任务", on_wait=self.on_wait, headers=headers,
                json={"model": self.model, "input": {"prompt": prompt[:2000]}, "parameters": {"size": size, "n": 1}},
            )
            if r.status_code in (401, 403):
                raise RuntimeError(f"作图鉴权失败（HTTP {r.status_code}）——检查 Key/账号开通")
            if r.status_code >= 400:
                raise RuntimeError(f"作图提交失败（HTTP {r.status_code}）：{r.text[:200]}")
            task_id = r.json()["output"]["task_id"]

            loop = asyncio.get_event_loop()
            deadline = loop.time() + self.timeout
            url = ""
            while True:
                await asyncio.sleep(4)
                if loop.time() > deadline:
                    raise RuntimeError(f"作图超时（>{self.timeout:.0f}s，task={task_id}）")
                # 轮询也要退避 ✓（轮询撞 429 很常见：一边轮一边被限流 ✓
                #   原来一撞就直接判死 ✗ —— 而**图可能已经画好了** ✓ 白等一场 ✓）
                pr = await retry.request_with_backoff(
                    client, "GET", f"{BASE}/api/v1/tasks/{task_id}",
                    what="看出图结果", on_wait=self.on_wait,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                d = pr.json()
                st = str((d.get("output") or {}).get("task_status", "")).upper()
                if st == "SUCCEEDED":
                    results = (d.get("output") or {}).get("results") or [{}]
                    url = results[0].get("url") or ""
                    break
                if st in ("FAILED", "CANCELED"):
                    raise RuntimeError(f"作图任务失败：{str(d)[:250]}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            # 下载图也要退避 ✓（图已经画好了、钱已经花了 ✓ 因为一次下载超时就白扔 = 最亏 ✓）
            vr = await retry.request_with_backoff(
                client, "GET", url, what="下载生成的图", on_wait=self.on_wait,
            )
            vr.raise_for_status()
            output_path.write_bytes(vr.content)
        return output_path
