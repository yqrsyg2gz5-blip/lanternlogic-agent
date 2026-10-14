# -*- coding: utf-8 -*-
"""★★ 云端**不许关思考**（2026-10-14 用户明确要求）。

## 用户原话

> 「尤其那个思考你不能关，**你关思考他就是个笨蛋**，你知不知道？」

## 我上一版错在哪

我为了"提速/省钱"，对 DeepSeek / MiMo **默认关掉了思考**，理由写着
「想死自己、慢」。**那是看错了病**：

```
真问题：`max_tokens` 给小了（4096）⇒ 思考吃光预算 ⇒ 正文为空
正解：  修**预算**，不是把思考关掉 ✗
```

**实测同一份"写 Canvas 动画代码"任务：**

| 设置 | 输出 token | 正文行数 | 质量 |
|---|---|---|---|
| 开思考 | **3950** | **260 行** | 9/9 |
| 关思考 | 1106 | 102 行 | 9/9 |

⇒ 短任务上"都过"，但**输出量差 3 倍、正文差 2.5 倍** ——
agent 干长活靠的就是思考（每步多推演几步才不跑偏）✓

## 现行规矩（只有两条）

1. **本地端点** → 默认附 `chat_template_kwargs.enable_thinking=False`
   （2026-10-12 真机：本地模板默认开思考 ⇒ 绕圈吐空 **3/5** ✗ 这条要留）
2. **云端（含 DeepSeek / MiMo）** → **一个思考字段都不加** ⇒ 服务商默认 ✓
   只有调用方**显式**传 `enable_thinking=False` 才关 ✓

## 本文件钉什么

· 云端默认请求体里**不许出现** `thinking` / `chat_template_kwargs` ✗
· 本地默认请求体里**必须**有 `enable_thinking=False`（老规矩不许坏）✓
· 显式传 `enable_thinking=False` 时，DeepSeek / MiMo 都要**真关**（开关还能用）✓
"""
from __future__ import annotations

import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.providers.openai_compat import OpenAICompatProvider  # noqa: E402

HISTORY = [{"role": "user", "content": "你好"}]
TOOLS: list[dict] = []

CLOUD = {
    "DeepSeek": "https://api.deepseek.com/v1",
    "MiMo": "https://api.xiaomimimo.com/v1",
    "OpenAI": "https://api.openai.com/v1",
    "阿里": "https://dashscope.aliyuncs.com/compatible-mode/v1",
}


def _body(url: str, model: str = "m", **kw):
    class Cfg:
        pass

    c = Cfg()
    c.base_url = url
    c.model_name = model
    c.api_key_env = ""
    c.max_tokens = 16384
    c.temperature = 0.7
    return OpenAICompatProvider(c)._build_body(HISTORY, TOOLS, **kw)


def test_cloud_models_do_not_get_thinking_disabled():
    """★★ 核心：云端**默认不关思考** —— 「关思考他就是个笨蛋」（用户原话）。

    实测：关思考后输出 1106 tok / 正文 102 行；开思考 3950 tok / 260 行 ✗
    """
    for label, url in CLOUD.items():
        body = _body(url)
        assert "thinking" not in body, (
            f"{label} 被默认关了思考 ⇒ 长任务里它会变笨 ✗\n"
            f"  （用户明确要求：云端的思考不许关）\n  实际：{body.get('thinking')!r}"
        )
        assert "chat_template_kwargs" not in body, (
            f"{label} 被默认塞了模板思考开关 ⇒ 长任务里它会变笨 ✗\n"
            f"  实际：{body.get('chat_template_kwargs')!r}"
        )
        assert "reasoning_effort" not in body, f"{label} 被塞了 reasoning_effort ✗"


def test_local_endpoint_still_disables_thinking():
    """★ 本地那条老规矩不许坏（2026-10-12：本地模板默认开思考 ⇒ 绕圈吐空 3/5）。"""
    for url in ("http://127.0.0.1:8080/v1", "http://localhost:11434/v1"):
        body = _body(url)
        kw = body.get("chat_template_kwargs")
        assert isinstance(kw, dict) and kw.get("enable_thinking") is False, (
            f"{url} 的本地关思考丢了 ⇒ 本地小模型会绕圈吐空回复 ✗ {kw!r}"
        )
        assert "thinking" not in body, f"{url} 本地端点不该拿到 `thinking` 字段 ✗"


def test_explicit_disable_still_works_for_known_providers():
    """★ 开关还得能用：调用方**显式**要求关时，DeepSeek / MiMo 要真关。"""
    ds = _body(CLOUD["DeepSeek"], enable_thinking=False)
    assert (ds.get("thinking") or {}).get("type") == "disabled", ds.get("thinking")
    mi = _body(CLOUD["MiMo"], enable_thinking=False)
    assert (mi.get("chat_template_kwargs") or {}).get("enable_thinking") is False, \
        mi.get("chat_template_kwargs")


def test_unknown_cloud_never_gets_a_thinking_field():
    """★ 不认识的云端：显式传了也不许塞（严格网关见陌生字段会 400）。"""
    body = _body("https://api.some-other-vendor.example/v1", enable_thinking=False)
    assert "thinking" not in body, body.get("thinking")
    assert "chat_template_kwargs" not in body, body.get("chat_template_kwargs")


def test_max_tokens_is_the_right_lever_not_thinking():
    """★ 治"想死自己"的正确杠杆是**预算**，不是关思考。

    这条是给后来人看的锚点：`max_tokens` 必须够大（本地/云端都别给 4096 那种小值）。
    """
    src = (BACKEND / "app" / "providers" / "openai_compat.py").read_text("utf-8")
    assert "云端一律不许关思考" in src, (
        "代码注释里没留下「云端不许关思考」的结论 ⇒ 后来人会再关一次 ✗"
    )
    assert "该修的是**预算**" in src or "该修的是" in src, (
        "没写清「正确杠杆是 max_tokens 不是关思考」✗"
    )
