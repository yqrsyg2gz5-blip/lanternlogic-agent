# -*- coding: utf-8 -*-
"""上游限流/瞬时故障的**统一退避**（★ 2026-10-07 第 4 项）。

## 为什么要有这个文件（查证结果：不是没做，是**只做了一半** ✗）

交接书第 6 节写着"429 限流退避 ✗" ✓ —— 我**先去量了一遍**（不猜 ✓）：
  · 语言模型那两档（openai_compat 的流式与非流式、anthropic）**早就有退避** ✓
    还听上游的 `Retry-After` ✓ 有测试钉着 ✓
  · 出视频也有 ✓
  · **出图 / 语音识别 / 知识库向量** 这三档云端调用 —— **没有** ✗
    撞上 429/5xx 直接抛 ⇒ 任务失败 ✓（而这三档**都是要花钱的** ✓
    出图那张图**已经提交上去了**、钱可能已经花了 ✗ 却因为一次瞬时 429 白扔 ✓）
  · 而且**重试期间用户什么都看不到** ✗ —— 最多等 60 秒，界面一片安静 ⇒ 像卡死 ✓

⇒ 所以这一项真正要补的是两件事：
  **① 把退避补到那三档** ✓（一处实现，三处调用 ✓ 不各写一套 ✗）
  **② 重试要让用户看得见** ✓（"上游忙，第 2 次重试，等 4 秒" ✓ —— 本项目最忌讳静默 ✓）

## 三条纪律（与 openai_compat 那套保持一致 ✓ 不另立口径 ✗）

1. **只重试"等一等可能就好了"的** ✓：408/409/425/429/5xx + 连接层异常（超时/断连）✓
   **不重试** 400/401/403/404/422 ✗ —— 那是"请求本身/钥匙不对"，重试纯属浪费你时间 ✓
   （而且会把真正的问题藏起来 ✓：钥匙错了却重试 4 次，用户等了半天只看到"超时"✗）
2. **听上游的 `Retry-After`** ✓（它说等多久就等多久，上限 60s ✓）；没有就指数退避 ✓
3. **一旦已经开始有产出，就不再重试** ✗ —— 重试会造成"同一段话出现两遍"✓
   （流式那条路早就是这么定的 ✓ 这里同一条纪律 ✓）
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

import httpx

#: 与 `providers/openai_compat._RETRYABLE_STATUS` **同一张表** ✓（一处口径 ✓）
#: 加新码之前先想清楚：它是不是"等一等可能就好了"✓
RETRYABLE_STATUS: frozenset[int] = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 522, 524})
MAX_ATTEMPTS = 4


def is_retryable(status: int) -> bool:
    return status in RETRYABLE_STATUS or status >= 500


def retry_delay(resp: Any | None, attempt: int) -> float:
    """等多久（秒）：优先听 `Retry-After` ✓ 否则 2/3/5/9 秒 ✓ 上限 60 秒 ✓。

    与 `providers/openai_compat._retry_delay` 同策略 ✓（不合并是因为那边在 provider 层、
    这里要给"不走 provider 的云端调用"用 ✓ 两边靠测试对齐 ✓）。
    """
    if resp is not None:
        try:
            raw = resp.headers.get("retry-after")
        except Exception:                                   # noqa: BLE001
            raw = None
        if raw:
            try:
                return min(60.0, max(1.0, float(raw)))
            except (TypeError, ValueError):
                pass
    return float(min(60, 2 ** attempt + 1))


class UpstreamBusy(RuntimeError):
    """重试到上限还是不通 ✓ —— 消息必须**能照做**（等多久 / 换什么 / 去哪看额度 ✓）。"""


async def request_with_backoff(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    what: str,
    on_wait: Callable[[str], None] | None = None,
    attempts: int = MAX_ATTEMPTS,
    **kw: Any,
) -> httpx.Response:
    """发一个请求，撞上限流/瞬时故障就**退避重试** ✓ 并把"在等"**说出来** ✓。

    · `what`：这一步是干嘛的（进提示语 ✓ 例如"提交出图任务"✓）
    · `on_wait`：把"正在重试"告诉用户 ✓（界面/事件流 ✓ 不传就只写日志 ✓）
    · 重试到上限仍不通 ⇒ 抛 `UpstreamBusy`，里面是一句**能照做**的话 ✓
      （不会让上层拿到一个裸的 httpx 异常 ✓ 那种报错用户没法据以行动 ✓）
    """
    last: str = ""
    for attempt in range(attempts):
        try:
            resp = await client.request(method, url, **kw)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            # 连接层抖动：等一等通常就好 ✓（与"上游回了个 4xx"是两回事 ✓）
            last = f"{type(e).__name__}: {str(e)[:120]}"
            if attempt == attempts - 1:
                raise UpstreamBusy(
                    f"{what}：连不上上游（{last}）—— 已重试 {attempts} 次 ✓"
                    "检查网络，或过一会儿再试 ✓") from e
            wait = retry_delay(None, attempt)
            _tell(on_wait, f"连不上上游，{wait:.0f} 秒后重试（第 {attempt + 2}/{attempts} 次）")
            await asyncio.sleep(wait)
            continue
        if not is_retryable(resp.status_code):
            return resp                                     # 好或"重试也没用" ⇒ 交回调用方 ✓
        last = f"HTTP {resp.status_code}"
        if attempt == attempts - 1:
            hint = ("（限流：稍等再试 ✓ 或到服务商控制台看看额度/并发上限 ✓）"
                    if resp.status_code == 429 else "（上游故障：等几分钟再试 ✓）")
            raise UpstreamBusy(f"{what}：上游一直忙（{last}）—— 已重试 {attempts} 次 ✓{hint}")
        wait = retry_delay(resp, attempt)
        _tell(on_wait, f"上游忙（{last}），{wait:.0f} 秒后重试（第 {attempt + 2}/{attempts} 次）")
        await asyncio.sleep(wait)
    return resp                                             # pragma: no cover —— 循环里必返回或抛 ✓


def _tell(on_wait: Callable[[str], None] | None, msg: str) -> None:
    """把"正在等"说出去 ✓ —— 说了没人听也不能因为报错把任务弄挂 ✗。"""
    try:
        if on_wait is not None:
            on_wait(msg)
    except Exception:                                       # noqa: BLE001
        pass


def now() -> float:                                         # pragma: no cover
    return time.monotonic()
