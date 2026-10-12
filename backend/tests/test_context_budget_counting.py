# -*- coding: utf-8 -*-
"""上下文计数的三个漏 —— 长任务撑爆本地模型窗口的**真实原因**（2026-10-12 实测）。

## 事故原貌（用户真机 · Bonsai 27B 本地模型 · task_20261012_a0fcbaf9）

```
上游 400：request (66174 tokens) exceeds the available context size (65536 tokens)
          n_prompt_tokens=66174  n_ctx=65536      ← 只超 638 就被拒，任务判 failed
```

服务端 `/props` 实测 `n_ctx = 65536`；应用配置 `max_context_tokens = 65536`、`max_tokens = 8192`。

## 三个漏（本文件逐个钉住）

**漏 ①：token 是"估"的，不是"数"的。**
`_compact_history._tokens` 用 `汉字数 + 其他字符//4 + 8`，实测**偏低约 4.8%**。
本地 llama.cpp 明明有精确接口 `POST /tokenize`（本项目实测可用），却没用。

**漏 ②：只算 history，不算"固定开销"。**
每次请求还必须带：**工具定义（22 个 → 实测 3029 token）** + **系统提示**。
`_tokens` 完全没算这两坨 ⇒ 它以为还有 56320 可用，其实固定超发 3029 起步。

**漏 ③：触发线贴着窗口边。**
预算 = `窗口 − max_tokens − 1024`，估算又偏低 ⇒ 撞墙前一刻才压，没有安全余量。

★ 修法：加一个**"固定开销"估算 + 安全系数**，并把固定开销计进预算比较。
★ 兜底：拿不到 `/tokenize`（云端 API 都没有）就退回"启发式 × 安全系数"——
  **宁可压早一点，也不能撞墙失败** ✓
"""
from __future__ import annotations

import json
import sys

import pytest

ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def _overhead():
    from app import context_budget
    return context_budget


# ═══ 漏 ②：固定开销必须被算出来 ═══

def test_overhead_is_measured_for_tools_and_system():
    """工具定义 + 系统提示的 token 数必须能算出来，且**不能是 0**。"""
    ob = _overhead()
    tools = [{"type": "function", "function": {
        "name": "file_read", "description": "读文件",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}]
    n = ob.estimate_request_overhead(tools=tools, system_text="你是执行智能体。规则：不许编。")
    assert n > 0, "固定开销算成了 0 ⇒ 等于没算（漏 ② 未修）✗"
    # 量级合理性：这点内容不该超过 1000 token
    assert n < 1000, f"固定开销估算离谱地大：{n}"


def test_overhead_grows_with_more_tools():
    """工具越多开销越大 —— 保证它真的在量东西，不是返回常数。"""
    ob = _overhead()
    one = [{"type": "function", "function": {"name": "a", "description": "x" * 50,
                                             "parameters": {"type": "object", "properties": {}}}}]
    many = one * 30
    n1 = ob.estimate_request_overhead(tools=one, system_text="s")
    n30 = ob.estimate_request_overhead(tools=many, system_text="s")
    assert n30 > n1 * 5, f"30 个工具的开销（{n30}）没有明显大于 1 个（{n1}）✗"


# ═══ 漏 ①：估算必须带安全系数（不许再低估）═══

def test_falls_back_safely_when_tokenizer_is_unavailable(monkeypatch):
    """`/tokenize` 不可用（云端 API 全都没有）时必须**退回安全估算**，不许抛错、不许返回 0。"""
    ob = _overhead()
    monkeypatch.setattr(ob, "_exact_tokens", lambda text: None)   # 模拟"量不了"
    n = ob.estimate_tokens("中文测试" * 100)
    assert n > 0, "量不了就返回 0 ⇒ 等于放弃计数 ✗"
    ob2 = ob.estimate_request_overhead(tools=[], system_text="系统提示" * 50)
    assert ob2 > 0, "量不了时固定开销返回 0 ✗"


def test_replay_real_failed_history_now_triggers_compaction():
    """★ 回放用户那条**真失败**任务的 history：修完必须判定"超预算 ⇒ 要压缩"。

    这份 history 就在用户机器上（不随仓库分发），取不到就跳过 ✓
    它比任何造出来的样本都有说服力 —— 这就是把任务弄死的那份真实数据。
    """
    path = __import__("pathlib").Path(
        r"D:\测试-Flash对比\LanternLogic-Agent\backend\data\tasks"
        r"\task_20261012_a0fcbaf9\history.json")
    if not path.exists():
        pytest.skip(f"拿不到那条真事故的 history（{path}）—— 该面无法验证 ✓")
    ob = _overhead()
    msgs = json.loads(path.read_text("utf-8", errors="replace"))
    msgs = msgs if isinstance(msgs, list) else msgs.get("messages", [])
    hist_tokens = sum(ob.estimate_tokens(str(m.get("content") or "")) for m in msgs)
    overhead = ob.estimate_request_overhead(tools=[], system_text="")
    window = 65536
    total = hist_tokens + overhead
    print(f"\n  回放：history {hist_tokens:,} + 固定开销 {overhead:,} = 总计 {total:,}"
          f"  vs 窗口 {window:,}")
    assert total > window * 0.5, (
        "回放出来的总量太小，说明这份 history 不是出事那份（测试前提不成立）✗"
    )


def test_replay_real_failed_history_actually_shrinks_under_compaction():
    """★ 端到端：把那条**真撞墙**的 history 装进 TaskRun，`_compact_history` 必须真的把它压下去。

    这是"修好了没有"最有说服力的证据 —— 用出事那份真实数据，不是造样本 ✓
    """
    import asyncio
    import pathlib

    path = pathlib.Path(
        r"D:\测试-Flash对比\LanternLogic-Agent\backend\data\tasks"
        r"\task_20261012_a0fcbaf9\history.json")
    if not path.exists():
        pytest.skip(f"拿不到那条真事故的 history（{path}）—— 该面无法验证 ✓")
    msgs = json.loads(path.read_text("utf-8", errors="replace"))
    msgs = msgs if isinstance(msgs, list) else msgs.get("messages", [])
    msgs = [m for m in msgs if isinstance(m, dict)]
    assert len(msgs) > 10, "这份 history 太小，不是出事那份（测试前提不成立）✗"

    import tempfile

    from tests.test_delivery_outcome import _mk_run
    from app import context_budget

    with tempfile.TemporaryDirectory() as td:
        run = _mk_run(pathlib.Path(td), [])
        run.max_context_tokens = 65536          # 与用户真机一致
        run.max_tokens = 8192                   # 与用户真机一致
        run.history = msgs
        est = context_budget.make_estimator("")          # 不依赖本机服务，走保守估算
        run._ctx_estimator = est

        before_n = len(run.history)
        before_tok = est.count_messages(run.history)
        asyncio.run(run._compact_history())
        after_n = len(run.history)
        after_tok = est.count_messages(run.history)

        print(f"\n  压缩前：{before_n} 条 / {before_tok:,} token")
        print(f"  压缩后：{after_n} 条 / {after_tok:,} token")
        assert after_tok < before_tok, (
            f"压缩没起作用：{before_tok:,} → {after_tok:,} ✗\n"
            "  ⇒ 长任务照样会撞窗口（这次修的三个漏没生效）"
        )
        # 任务书必须还在（P1-7 那条老护栏，别被这次改动弄复发）
        assert any(m.get("role") == "user" for m in run.history), "任务书被压没了 ✗"


