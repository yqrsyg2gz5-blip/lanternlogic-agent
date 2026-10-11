# -*- coding: utf-8 -*-
"""★ 第 4 项 · 限流退避 —— 2026-10-07。**先量后改** ✓（交接书那条"✗"只对了一半 ✓）

## 量出来的现状（不猜 ✓）

| 哪一档 | 撞上 429 会退避吗 |
|---|---|
| 语言模型（openai_compat 流式 + 非流式、anthropic） | ✅ **早就有**（还听 `Retry-After` ✓ 有测试钉着 ✓）|
| 出视频 | ✅ 有 |
| **出图 / 语音识别 / 知识库向量** | **✗ 没有** ✗ 直接抛 ⇒ 任务失败 |
| **重试期间用户看得见吗** | **✗ 看不见** ✗ 最多等 60 秒、界面一片安静 ⇒ 像卡死 |

⇒ 交接书第 6 节那条"429 限流退避 ✗"**是过时的** ✓ —— 但它指出的方向上有**两个真缺口** ✓

## 补的两件事

**① 把退避补到那三档** ✓（`app/retry.py` **一处实现** ✓ 三处调用 ✓ 不各写一套 ✗）
   为什么值得：这三档**都是要花钱/要用户出力的** ✗
   · 出图：图**已经提交上去了**、钱可能已经花了 ⇒ 因为一次瞬时 429 白扔 ✓
   · 语音：用户得**把那段话重说一遍** ✓
   · 知识库：大库要发几十上百批 ⇒ 最后一批撞 429 ⇒ **整个库不入库** ✓（前面全白算 ✓）

**② 重试要看得见** ✓（"上游忙（HTTP 429），4 秒后重试（第 2/4 次）"✓）
   本项目最忌讳静默 ✓ —— 而"等了 60 秒什么都没发生"正是用户以为卡死的来源 ✓

## 三条纪律（与 openai_compat 那套**同一口径** ✓ 靠测试对齐 ✓）

1. **只重试"等一等可能就好了"的** ✓（408/409/425/429/5xx + 连接层异常 ✓）
   **不重试** 400/401/403/404 ✗ —— 钥匙错了却重试 4 次，用户等半天只看到"超时"✓ 把真问题藏了 ✓
2. **听上游的 `Retry-After`** ✓（上限 60s ✓）
3. **已经有产出了就不再重试** ✗（重试会让同一段话出现两遍 ✓ —— 流式那条路早就是这么定的 ✓）
"""
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from app import retry


# ═══ ① 该重试谁、不该重试谁 ═══

@pytest.mark.parametrize("code,want", [
    (429, True), (500, True), (502, True), (503, True), (504, True), (408, True),
    (400, False), (401, False), (403, False), (404, False), (422, False),
])
def test_only_transient_failures_are_retried(code, want):
    """★ **钥匙错了不许重试** ✗ —— 重试 4 次只会让用户白等 ✓ 还把真问题藏起来 ✓。"""
    assert retry.is_retryable(code) is want, code


def test_delay_honors_retry_after_and_caps_it():
    """★ 听上游的 `Retry-After` ✓ 但要**封顶** ✓（它说等 1 小时也不能真等 ✓）。"""
    r = httpx.Response(429, headers={"retry-after": "7"})
    assert retry.retry_delay(r, 0) == 7.0
    big = httpx.Response(429, headers={"retry-after": "99999"})
    assert retry.retry_delay(big, 0) == 60.0, "没封顶 ⇒ 可能一觉睡到天亮 ✗"
    assert retry.retry_delay(None, 0) == 2.0 and retry.retry_delay(None, 2) == 5.0


def test_retry_table_matches_the_chat_provider():
    """★ 两处退避必须**同一张表** ✓ —— 不然"同一个 429，聊天会退避、出图直接死" ✗
    （本项目栽过六次的"多处口径打架"✓ 这条测试就是防第七次 ✓）。"""
    from app.providers import openai_compat as oc
    assert retry.RETRYABLE_STATUS == frozenset(oc._RETRYABLE_STATUS), \
        f"两张表不一致 ✗：retry={sorted(retry.RETRYABLE_STATUS)} vs provider={sorted(oc._RETRYABLE_STATUS)}"


# ═══ ② 真起一个会 429 的服务器（**真跑** ✓ 不是看代码觉得会退避 ✓）═══

class _Handler(BaseHTTPRequestHandler):
    """头 N 次回 429（可选带 Retry-After），之后回 200 ✓。"""
    fails = 2
    retry_after: str | None = None
    seen = 0

    def do_GET(self):                                       # noqa: N802
        type(self).seen += 1
        if type(self).seen <= type(self).fails:
            self.send_response(429)
            if type(self).retry_after:
                self.send_header("retry-after", type(self).retry_after)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"rate limited"}')
            return
        body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):                              # noqa: ARG002
        pass


@pytest.fixture()
def flaky_server():
    """起一个本地小服务器 ✓ 用完就关 ✓（单测**不碰真外网** ✓ 但**代码路径全真** ✓）。"""
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}"
    finally:
        srv.shutdown()
        srv.server_close()


def _run(coro):
    return asyncio.run(coro)


def test_it_really_retries_and_then_succeeds(flaky_server, monkeypatch):
    """★★ **真撞两次 429、第三次拿到 200** ✓ —— 这就是"退避"的全部意义 ✓。

    回滚实验：把 `request_with_backoff` 换成裸 `client.request` ⇒ 本组必红 ✓
    """
    _Handler.seen = 0
    _Handler.fails = 2
    _Handler.retry_after = None
    monkeypatch.setattr(retry, "retry_delay", lambda resp, attempt: 0.01)   # 测试里别真等 ✓
    waits: list[str] = []

    async def go():
        async with httpx.AsyncClient(timeout=5) as c:
            return await retry.request_with_backoff(
                c, "GET", f"{flaky_server}/x", what="试试", on_wait=waits.append)

    r = _run(go())
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert _Handler.seen == 3, f"应该请求 3 次（2 次 429 + 1 次成功），实际 {_Handler.seen} ✗"
    assert len(waits) == 2, f"'在等'应该说两次 ✓ 实际 {waits}"


def test_it_tells_the_user_what_it_is_waiting_for(flaky_server, monkeypatch):
    """★ **重试要看得见** ✓ —— 而且话里要有**在等多久**、**第几次** ✓（用户据此判断是不是卡死 ✓）。"""
    _Handler.seen = 0
    _Handler.fails = 1
    _Handler.retry_after = None
    monkeypatch.setattr(retry, "retry_delay", lambda resp, attempt: 3.0)

    # ★ 别真等 3 秒 ✓ 但**不能**去 patch `asyncio.sleep` 本身 ✗ ——
    #   `retry.asyncio` 就是全局那个 asyncio 模块 ✓ patch 它等于把 asyncio.sleep 换成
    #   "调用 asyncio.sleep"的 lambda ⇒ **无限递归** ✗（本班第一版就这么挂的 ✓）
    #   ⇒ 换掉 `retry` 眼里的那个模块对象 ✓（只影响它一家 ✓）
    class _NoSleep:
        async def sleep(self, _s):                          # noqa: ARG002
            return None

    monkeypatch.setattr(retry, "asyncio", _NoSleep())
    waits: list[str] = []

    async def go():
        async with httpx.AsyncClient(timeout=5) as c:
            return await retry.request_with_backoff(
                c, "GET", f"{flaky_server}/x", what="提交出图任务", on_wait=waits.append)

    _run(go())
    assert waits, "重试期间**一声不吭** ✗（用户会以为卡死 ✓）"
    msg = waits[0]
    assert "429" in msg and "重试" in msg and "秒" in msg, msg
    assert "2/4" in msg, f"没说是第几次 ⇒ 用户不知道还剩几次 ✓：{msg}"


def test_giving_up_says_what_to_do(flaky_server, monkeypatch):
    """★ 重试到上限仍不通 ⇒ 抛的那句话必须**能照着做** ✓（不是一句"失败了"✓）。"""
    _Handler.seen = 0
    _Handler.fails = 99
    _Handler.retry_after = None
    monkeypatch.setattr(retry, "retry_delay", lambda resp, attempt: 0.01)

    async def go():
        async with httpx.AsyncClient(timeout=5) as c:
            return await retry.request_with_backoff(
                c, "GET", f"{flaky_server}/x", what="提交出图任务", attempts=3)

    with pytest.raises(retry.UpstreamBusy) as ei:
        _run(go())
    msg = str(ei.value)
    assert "提交出图任务" in msg and "429" in msg and "3 次" in msg, msg
    assert "限流" in msg and "额度" in msg, f"没说清怎么办 ✗：{msg}"


def test_non_retryable_is_returned_immediately(flaky_server, monkeypatch):
    """★ 401/400 这类**立刻交回** ✓（重试纯属浪费你时间 ✓ 还会把真问题藏起来 ✓）。"""
    seen = {"n": 0}

    class _H(_Handler):
        def do_GET(self):                                   # noqa: N802
            seen["n"] += 1
            self.send_response(401)
            self.send_header("content-length", "0")
            self.end_headers()

    srv = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        async def go():
            async with httpx.AsyncClient(timeout=5) as c:
                return await retry.request_with_backoff(
                    c, "GET", f"http://127.0.0.1:{srv.server_port}/x", what="试试")
        r = _run(go())
        assert r.status_code == 401 and seen["n"] == 1, f"401 竟然重试了 {seen['n']} 次 ✗"
    finally:
        srv.shutdown()
        srv.server_close()


def test_connection_errors_are_retried_then_reported(monkeypatch):
    """★ 连不上（超时/断连）也要退避 ✓ —— 那是"等一等通常就好"的典型 ✓。"""
    monkeypatch.setattr(retry, "retry_delay", lambda resp, attempt: 0.0)
    calls = {"n": 0}

    class _Boom:
        async def request(self, *a, **k):                   # noqa: ARG002
            calls["n"] += 1
            raise httpx.ConnectError("连不上")

    async def go():
        return await retry.request_with_backoff(
            _Boom(), "GET", "http://x/y", what="试试", attempts=3)   # type: ignore[arg-type]

    with pytest.raises(retry.UpstreamBusy) as ei:
        _run(go())
    assert calls["n"] == 3, f"连接层错误没退避 ✗（只试了 {calls['n']} 次）"
    assert "连不上上游" in str(ei.value) and "网络" in str(ei.value), str(ei.value)


# ═══ ③ 三档**真的接上了**（"写了不等于接上了" ✓ 本仓的老教训 ✓）═══

def test_image_generation_is_wired_to_backoff():
    """★ 出图那档必须**真调用**退避 ✓ —— 图都提交上去了、钱可能花了 ✗ 不能因为一次 429 白扔 ✓。"""
    import pathlib
    from app import imagegen
    src = pathlib.Path(imagegen.__file__).read_text("utf-8")
    assert "retry.request_with_backoff" in src, "出图没接退避 ✗"
    # 提交、轮询、下载 三处都要 ✓（哪一处漏了都能把已经花掉的钱白扔 ✓）
    assert src.count("retry.request_with_backoff") >= 3, \
        f"出图只在 {src.count('retry.request_with_backoff')} 处接了退避 ✗（提交/轮询/下载三处都要 ✓）"
    assert "on_wait" in src, "出图退避时没有把'在等'说出来 ✗"


def test_loop_shows_the_image_retry_to_the_user():
    """★ 光引擎里有 on_wait 不算 ✓ —— **loop 得把它接到事件流上** ✓（否则用户还是看不见 ✓）。"""
    import pathlib
    from app import loop as loop_mod
    src = pathlib.Path(loop_mod.__file__).read_text("utf-8")
    assert "on_wait=lambda m: self.emit(\"status\"" in src, \
        "loop 没把出图的'正在重试'接到事件流 ✗（那用户还是干等 ✓）"


def test_asr_and_kb_are_wired_to_backoff():
    """★ 语音与知识库也要 ✓（一个要用户重说一遍 ✗ 一个让整个库不入库 ✗）。"""
    import pathlib
    from app import asr, kb
    for mod, what in ((asr, "语音识别"), (kb, "向量化")):
        src = pathlib.Path(mod.__file__).read_text("utf-8")
        assert "retry.request_with_backoff" in src, f"{what}那一档没接退避 ✗"
        assert "UpstreamBusy" in src, f"{what}没处理「退避到上限」那种情况 ⇒ 会抛一个用户看不懂的异常 ✗"


def test_giving_up_messages_point_at_a_way_out():
    """★ 三档"放弃了"的话都要给出路 ✓（换本地 / 稍后再试 ✓）—— 不是干说失败 ✓。"""
    import pathlib
    from app import asr, kb
    a = pathlib.Path(asr.__file__).read_text("utf-8")
    k = pathlib.Path(kb.__file__).read_text("utf-8")
    assert "本地 ASR" in a, "语音那档没告诉用户可以换本地（离线、不怕限流）✗"
    assert "本地向量" in k, "知识库那档没告诉用户可以换本地向量 ✗"
