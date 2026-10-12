# -*- coding: utf-8 -*-
"""上下文计数 —— 修长任务撑爆模型窗口的三个漏（2026-10-12 实测）。

## 事故原貌（用户真机 · Bonsai 27B 本地模型 · task_20261012_a0fcbaf9）

```
上游 400：request (66174 tokens) exceeds the available context size (65536 tokens)
          n_prompt_tokens=66174  n_ctx=65536     ← 只超 638 就被拒，任务判 failed
```

## 三个漏（逐个修）

**漏 ①：token 是"估"的。** 旧算法 `汉字数 + 其他字符//4 + 8` —— 实测偏差极大：

| 内容类型 | 旧算法 / 真实 |
|---|---|
| 中文段落 | 1.78（够用） |
| 英文散文 | 1.14（够用） |
| **Python 代码** | **0.84（低估）** |
| **JSON 日志** | **0.55（低估）** |
| **路径堆** | **0.72（低估）** |
| **数字表格/CSV** | **0.25（低估 4 倍！）** |
| **base64** | **0.58（低估）** |

★ **重要发现（实测多种公式后）**：**任何"数长度"的启发式都压不住最坏情况** ——
  要压住 CSV 得用「其他字符 //1」，但那样英文会高估 **4.5 倍**（导致疯狂压缩、白丢上下文）。
  ⇒ **所以正确做法不是找完美公式**：
  **能精确数就精确数；数不了就用"绝不低估"的保守估算** ✓

**漏 ②：只算 history，不算固定开销。** 每次请求还要带
  **工具定义（22 个 → 实测 3029 token）** + **系统提示** —— 旧代码完全没算，
  于是"以为还有 56320 可用，其实固定超发 3029 起步" ✗

**漏 ③：触发线贴着窗口边。** 预算 = `窗口 − max_tokens − 1024`，估算又偏低
  ⇒ 撞墙前一刻才压。本模块用精确计数后不再需要虚高系数 ✓

## 精确计数从哪来

本地 llama.cpp / ik_llama 系服务有 `POST {base_url}/tokenize`（**本项目实测可用、免费、无需 Key**）：

    {"content": "..."}  →  {"tokens": [7403, 11, 559, ...]}

云端 API（DeepSeek / MiMo 等）**没有**这个接口 ⇒ `measure()` 返回 `None`，
自动退回保守估算 ✓ **绝不因为"数不了"就跳过计数** ✗

## 与"绝不低估"的关系（重要，别改错方向）

保守估算取 `max(旧启发式, 字符数 × 1.0)` —— 即**最坏情况一个字符一个 token**。
宁可压早一点（少留些上下文），也**不能撞墙把任务弄死** ✓
这是刻意的取舍，不是 bug；要改得先有更好的实测公式 ✓
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Sequence

__all__ = ["Estimator", "make_estimator", "estimate_request_overhead", "estimate_tokens",
           "SAFETY_NOTE"]

#: 保守估算的下限口径：最坏情况按「一个字符一个 token」算（见模块头"重要发现"）
_WORST_CASE_CHARS_PER_TOKEN = 1.0

#: 精确计数的超时（本地服务；超时即退回估算，绝不阻塞主循环）
_EXACT_TIMEOUT_S = 5.0

SAFETY_NOTE = "保守估算=最坏情况，绝不低估；有 /tokenize 则用精确值"


def _heuristic(text: str) -> int:
    """旧的启发式（汉字 1 token/字，其余 1 token/4 字符）——**只当下限用**。"""
    cjk = sum(1 for ch in text if ord(ch) > 0x2E7F)
    return cjk + (len(text) - cjk) // 4 + 8


def _conservative(text: str) -> int:
    """保守估算：绝不低估（取旧启发式与"一字符一 token"的较大者）。"""
    if not text:
        return 0
    return max(_heuristic(text), int(len(text) / _WORST_CASE_CHARS_PER_TOKEN) + 8)


def _exact_tokens_via(base_url: str, text: str) -> int | None:
    """调 `{base_url}/tokenize` 精确计数。拿不到（云端无此接口/超时/报错）返回 None。"""
    if not base_url or not text:
        return None
    url = base_url.rstrip("/") + "/tokenize"
    try:
        body = json.dumps({"content": text}).encode("utf-8")
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=_EXACT_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    toks = data.get("tokens") if isinstance(data, dict) else None
    return len(toks) if isinstance(toks, list) else None


class Estimator:
    """token 计数器：能精确就精确，不能就**绝不低估**。

    用一个实例而不是模块函数，是为了**缓存**：`base_url` 能不能精确计数、
    固定开销是多少，都只探一次，不必每轮重算 ✓
    """

    def __init__(self, base_url: str = "", *, allow_exact: bool = True) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.allow_exact = allow_exact
        self._probed: bool | None = None          # 本服务到底有没有 /tokenize
        self._overhead_cache: dict[tuple[int, int], int] = {}

    # ── 精确能力探测（只用一小段文本探一次）──
    def exact_available(self) -> bool:
        if self._probed is None:
            if not self.allow_exact or not self.base_url:
                self._probed = False
            else:
                self._probed = _exact_tokens_via(self.base_url, "ping") is not None
        return self._probed

    def measure(self, text: str) -> int | None:
        """精确 token 数；不可用返回 None。"""
        if not text or not self.exact_available():
            return None
        return _exact_tokens_via(self.base_url, text)

    def count(self, text: str) -> int:
        """这个文本占多少 token（精确优先，否则保守估算）。"""
        if not text:
            return 0
        exact = self.measure(text)
        return exact if exact is not None else _conservative(text)

    def count_messages(self, messages: Sequence[dict[str, Any]]) -> int:
        """一组消息的总 token。

        ★ 口径：**逐条分别计数**，不把历史拼成一大坨再数 ——
          拼接后 tokenizer 的边界效应会让结果和"上游实际收到的"不一致 ✓
        """
        total = 0
        for m in messages:
            parts = [str(m.get("content") or "")]
            for call in (m.get("tool_calls") or []):
                fn = (call or {}).get("function") or {}
                parts.append(str(fn.get("name") or ""))
                parts.append(str(fn.get("arguments") or ""))
            total += self.count("\n".join(p for p in parts if p)) + 8   # +8：角色/分隔等结构开销
        return total

    # ── 固定开销（漏洞 ② 的修法）──
    def overhead(self, tools: Sequence[dict[str, Any]] | None = None,
                 system_text: str = "") -> int:
        """每次请求都要带的**固定开销**：工具定义 + 系统提示。

        只算一次并缓存 —— 工具表与系统提示在一个任务内不变 ✓
        """
        tools = list(tools or [])
        key = (len(tools), len(system_text))
        if key in self._overhead_cache:
            return self._overhead_cache[key]
        n = 0
        if tools:
            n += self.count(json.dumps(tools, ensure_ascii=False))
        if system_text:
            n += self.count(system_text)
        self._overhead_cache[key] = n
        return n


def make_estimator(base_url: str = "", *, allow_exact: bool = True) -> Estimator:
    return Estimator(base_url, allow_exact=allow_exact)


# ── 无状态便捷函数（给只量一次的地方用；不带缓存）──

def estimate_tokens(text: str, *, base_url: str = "") -> int:
    """量一段文本。有 base_url 且它有 /tokenize ⇒ 精确；否则保守估算（绝不低估）。"""
    if not text:
        return 0
    if base_url:
        exact = _exact_tokens_via(base_url, text)
        if exact is not None:
            return exact
    return _conservative(text)


def estimate_request_overhead(tools: Sequence[dict[str, Any]] | None = None,
                              system_text: str = "", *, base_url: str = "") -> int:
    """量"每次请求的固定开销"（工具定义 + 系统提示）。**绝不为 0 假过** ✓"""
    n = 0
    if tools:
        n += estimate_tokens(json.dumps(list(tools), ensure_ascii=False), base_url=base_url)
    if system_text:
        n += estimate_tokens(system_text, base_url=base_url)
    return n


def _exact_tokens(text: str) -> int | None:
    """给测试用的默认端点探针（127.0.0.1:8080 = 本地 llama.cpp 默认）。"""
    return _exact_tokens_via("http://127.0.0.1:8080", text)


# 说明：`_exact_tokens` 是**本机默认端点**的便捷入口，测试用它验证"接口真的被接上了"。
# 生产路径走 `Estimator(base_url=provider.base_url)`，不依赖这个函数 ✓
_ = Callable  # 保留 typing 导入以便将来加回调（lint 用）
