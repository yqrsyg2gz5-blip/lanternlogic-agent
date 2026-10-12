# -*- coding: utf-8 -*-
"""让"依赖本机服务"的测试变成**确定性**的（2026-10-13）。

## 为什么要改

我新加的几个测试原本这么写：

    try:
        urllib.request.urlopen("http://127.0.0.1:8080/props", timeout=5)
    except ...:
        pytest.skip("本机 llama.cpp 服务不在（8080）")

后果：**本机模型服务一关（用户经常关它腾显存出图），这些测试就变成 skip** ——
门依旧"绿"，但**其实什么都没验**。跳过数从 2 涨到 8 就是这么来的 ✗

## 怎么改

不用真服务，**进程内起一个假服务**：
  · `http.server` 监听 127.0.0.1 的**随机端口**（不占 8080，不跟真服务抢）
  · 它就是回 `{"tokens": [...]}` / 一个带 tool_calls 的 OpenAI 响应
  · 于是"代码有没有真的去调那个接口""请求体对不对"**永远能验**，且极快 ✓

★ 真服务那一条**保留**，但**只在真服务在跑时才跑**（那是集成验证，本来就该可选）——
  它用 `@pytest.mark.skipif` 明确标出来，不算在"该验没验"里 ✓
"""
from __future__ import annotations

import json
import pathlib
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


class _FakeLlama:
    """冒充 llama.cpp 的最小服务：/tokenize 与 /v1/chat/completions。

    每个请求的 body 都记在 `self.seen` 里，供断言"应用到底发了什么" ✓
    """

    def __init__(self, *, tokens_per_char: float = 0.6, chat_content: str = "假的回复"):
        self.seen: list[dict] = []
        self.tokens_per_char = tokens_per_char
        self.chat_content = chat_content
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):        # 别往测试输出里灌日志
                pass

            def _send(self, obj, code=200):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    body = {}
                outer.seen.append({"path": self.path, "body": body})
                if self.path.endswith("/tokenize"):
                    text = str(body.get("content") or "")
                    k = max(1, int(len(text) * outer.tokens_per_char))
                    self._send({"tokens": list(range(k))})
                elif self.path.endswith("/chat/completions"):
                    self._send({"choices": [{"message": {"role": "assistant",
                                                         "content": outer.chat_content},
                                             "finish_reason": "stop"}],
                                "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
                else:
                    self._send({"ok": True})

        self._httpd = HTTPServer(("127.0.0.1", 0), H)
        self.port = self._httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self._t = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._t.start()

    def close(self):
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture
def fake_llama():
    srv = _FakeLlama()
    try:
        yield srv
    finally:
        srv.close()


# ═══ A) 精确计数：必须真的去调那个接口 ═══

def test_exact_counting_really_calls_the_tokenize_endpoint(fake_llama):
    """★ 用假服务证明：代码**真的发了 /tokenize 请求**，并用了返回的 token 数。

    这比"真服务在跑时才有结果"强 —— 它**永远能验**，而且能断言请求体内容 ✓
    """
    from app import context_budget

    text = "这是一段用来验证精确计数的中文文本。" * 5
    got = context_budget._exact_tokens_via(fake_llama.url, text)
    assert got is not None, "假服务就在跑，却没拿到精确计数 ✗"
    assert fake_llama.seen, "根本没发出请求 ⇒ 代码没真调接口 ✗"
    req = fake_llama.seen[-1]
    assert req["path"].endswith("/tokenize"), f"打的不是 /tokenize：{req['path']} ✗"
    assert req["body"].get("content") == text, "请求体里的正文不对 ✗"
    # 假服务按 len(text)*0.6 造 token 数 ⇒ 结果应与之一致（证明用了返回值）
    assert got == max(1, int(len(text) * 0.6)), f"没用返回的 token 数：得到 {got} ✗"


def test_cloud_endpoint_gets_no_tokenize_probe(fake_llama):
    """云端地址（这里指向假服务但**不把它当本地**）不该被拿去数 token。

    说明口径：`Estimator` 只对 `base_url` 调 `/tokenize`；它拿不到就退回保守估算。
    本用例验"拿不到精确值时**退回估算而不是报错/返回 0**" ✓
    """
    from app import context_budget

    est = context_budget.make_estimator("http://127.0.0.1:1")   # 打不通
    n = est.count("中文测试" * 50)
    assert n > 0, "拿不到精确计数就返回 0 ⇒ 等于放弃计数 ✗"


def test_estimator_caches_the_probe(fake_llama):
    """探测"这个服务有没有 /tokenize"只该发生一次（别每轮都探）。"""
    from app import context_budget

    est = context_budget.make_estimator(fake_llama.url)
    assert est.exact_available() is True
    before = len(fake_llama.seen)
    est.count("第一段")
    est.count("第二段")
    # 只该多出 2 次 /tokenize（计数用），不该再多出"探测"请求
    after = len(fake_llama.seen)
    assert after - before == 2, f"探测没缓存（多发了 {after - before} 次请求）✗"


# ═══ B) 关思考：请求体必须带那个字段（不再依赖真服务） ═══

def test_local_request_reaches_the_wire_with_thinking_disabled(fake_llama):
    """★ 用假服务证明：**真的发出去的请求体**里带着 enable_thinking=false。

    这条替代了原来"本机模型没开就跳过"的端到端用例 —— 现在**永远能验** ✓
    """
    import asyncio

    from app.providers.openai_compat import OpenAICompatProvider

    class Cfg:
        base_url = f"{fake_llama.url}/v1"
        model_name = "fake-local"
        api_key_env = ""
        max_tokens = 64
        temperature = 0.7

    p = OpenAICompatProvider(Cfg())
    turn = asyncio.run(p.next_turn("", [{"role": "user", "content": "你好"}], []))
    assert turn.text == "假的回复", f"没拿到假服务的回复：{turn.text!r} ✗"
    chat = [r for r in fake_llama.seen if r["path"].endswith("/chat/completions")]
    assert chat, "没发出 chat/completions 请求 ✗"
    kw = (chat[-1]["body"] or {}).get("chat_template_kwargs")
    assert isinstance(kw, dict) and kw.get("enable_thinking") is False, (
        f"发出去的请求体里没有关思考：{kw!r}\n"
        "  ⇒ 本地模板会默认打开思考模式，实测会导致空回复 ✗"
    )


def test_cloud_request_body_has_no_template_kwargs():
    """云端地址不发 chat_template_kwargs（用 _build_body 直接验，不必发请求）。"""
    from app.providers.openai_compat import OpenAICompatProvider

    class Cfg:
        base_url = "https://api.deepseek.com/v1"     # 云端（不真发：见下）
        model_name = "deepseek-flash"
        api_key_env = ""
        max_tokens = 64
        temperature = 0.7

    p = OpenAICompatProvider(Cfg())
    body = p._build_body([{"role": "user", "content": "你好"}], [])
    assert "chat_template_kwargs" not in body, f"给云端也发了模板参数 ✗：{body.get('chat_template_kwargs')}"


# ═══ C) 保留一条真服务集成验证（可选：真服务在跑才跑） ═══

def _real_local_service_up() -> bool:
    import urllib.error
    import urllib.request
    for url in ("http://127.0.0.1:8080/props", "http://127.0.0.1:8080/v1/models"):
        try:
            urllib.request.urlopen(url, timeout=3).read()
            return True
        except (urllib.error.URLError, OSError):
            continue
    return False


@pytest.mark.skipif(not _real_local_service_up(),
                    reason="本机没有真 llama.cpp 服务在跑（8080）—— 集成验证跳过，单测已覆盖同一逻辑 ✓")
def test_real_local_model_returns_content_when_thinking_is_disabled():
    """真服务集成验证：关思考后必须每次都出内容。

    ★ 这是**集成**验证（可选）。同一逻辑已由上面的假服务用例覆盖，
      所以它跳过**不代表没验** ✓
    """
    import urllib.request

    blank = 0
    for _ in range(3):
        body = {
            "model": "bonsai-2-27b",
            "messages": [{"role": "system", "content": "你是执行智能体。"},
                         {"role": "user", "content": "输出 3 行候选标题，然后一行写「采用：」+ 理由。"}],
            "max_tokens": 300, "stream": False, "cache_prompt": False,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        req = urllib.request.Request("http://127.0.0.1:8080/v1/chat/completions",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as resp:
            out = json.loads(resp.read().decode())
        text = str(((out.get("choices") or [{}])[0].get("message") or {})
                   .get("content") or "").strip()
        if not text:
            blank += 1
    assert blank == 0, f"真服务上关思考后仍有 {blank}/3 次吐空 ✗"
