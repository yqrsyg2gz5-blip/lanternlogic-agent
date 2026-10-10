"""★ P0-5 成本可见：这一步/这一批花了多少，群里直接看得到。

## 依据

· **Anthropic 多智能体复盘**实测：多智能体约烧 **15×** 普通对话的 token，
  "任务价值必须高到付得起这个开销" ⇒ 用户有权知道钱花在哪，而不是月底看账单才发现。
· 我们已有真实用量（loop 每轮结束落盘 `usage`：模型/调用次数/输入/输出/缓存命中），
  缺的只是**把它换算成钱并贴到群里**。

## 诚实原则（本模块的核心）

**token 永远报**（上游真值，或明确标注"估算"）；**¥ 只在用户填了单价时才报**。
理由：各家价格会变、口径不一（缓存价/阶梯价/包月），**编一个"看起来对"的单价
比不报更糟** —— 用户会拿它当账单。所以单价从 `config.pricing` 读，默认空。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import main as m
from app import pricing
from app.config import AppConfig


@pytest.fixture(autouse=True)
def _clean_prices():
    pricing.PRICES.clear()
    yield
    pricing.PRICES.clear()


def test_config_has_an_empty_pricing_section_by_default():
    """默认**不带任何单价**（避免编数字）；配置结构向后兼容（add-only）。"""
    assert AppConfig(version=1).pricing == {}


def test_estimate_returns_none_until_prices_are_configured():
    assert pricing.estimate("mimo-v2.6-flash", 1000, 100) is None
    pricing.configure({"mimo-v2.6-flash": {"in": 2.0, "out": 8.0, "cached": 0.5}})
    # 手算：输入 (120k-20k)×2 + 缓存 20k×0.5 + 输出 30k×8 = 0.45 元
    assert pricing.estimate("mimo-v2.6-flash", 120_000, 30_000, 20_000) == 0.45


def test_estimate_matches_model_name_by_prefix():
    pricing.configure({"deepseek-chat": {"in": 1.0, "out": 2.0}})
    assert pricing.estimate("deepseek-chat-0324", 1_000_000, 0) == 1.0
    assert pricing.estimate("unrelated-model", 1_000_000, 0) is None


def test_configure_ignores_garbage():
    pricing.configure({"bad": "not-a-dict", "half": {"in": "x", "out": 3.0},
                       "neg": {"in": -1, "out": 2.0}})
    assert "bad" not in pricing.PRICES
    assert pricing.PRICES.get("half") == {"out": 3.0}      # 只认数值字段
    assert "in" not in pricing.PRICES["neg"]               # 负价字段被丢（条目本身还在，out 合法）


def test_money_keeps_small_amounts_visible():
    assert "0.0040" in pricing.money(0.004)      # 小于 1 分也要看得见，别显示成 ¥0.00
    assert pricing.money(0.456).endswith("0.456")
    assert pricing.money(12.345).endswith("12.35")
    assert pricing.money(None) == ""


def _patch_usage(monkeypatch, usage):
    monkeypatch.setattr(m.store, "read_events", lambda tid: [
        SimpleNamespace(type="knowledge", payload={"usage": usage}),
    ] if usage else [])


def test_usage_line_reports_tokens_and_admits_it_has_no_price(monkeypatch):
    _patch_usage(monkeypatch, {"model": "mimo-v2.6-flash", "calls": 6,
                               "input_tokens": 12000, "output_tokens": 3000,
                               "cached_tokens": 4000, "estimated": False})
    line = m._usage_line("t1", "@程序员 这一步")
    assert "6 次调用" in line and "12000" in line and "3000" in line and "15000 tok" in line
    assert "缓存命中 4000" in line
    assert "未填单价" in line and "¥" not in line, line        # 没配单价就绝不报钱


def test_usage_line_reports_money_when_prices_are_configured(monkeypatch):
    pricing.configure({"mimo-v2.6-flash": {"in": 2.0, "out": 8.0}})
    _patch_usage(monkeypatch, {"model": "mimo-v2.6-flash", "calls": 2,
                               "input_tokens": 100_000, "output_tokens": 25_000,
                               "cached_tokens": 0, "estimated": False})
    line = m._usage_line("t1")
    assert "¥" in line and "按你在设置里填的单价估算" in line, line


def test_usage_line_warns_when_a_step_burns_a_lot(monkeypatch):
    _patch_usage(monkeypatch, {"model": "m", "calls": 25, "input_tokens": 200_000,
                               "output_tokens": 10_000, "cached_tokens": 0, "estimated": True})
    line = m._usage_line("t1")
    assert "估算" in line, "上游没给用量时必须标注估算"
    assert "烧得比平常多" in line and "拆小" in line, line


def test_usage_line_is_empty_without_usage(monkeypatch):
    _patch_usage(monkeypatch, None)
    assert m._usage_line("t1") == ""


def test_invoice_sums_the_whole_batch(monkeypatch, tmp_path):
    from app.team import TeamStore

    st = TeamStore(tmp_path)
    e1 = st.add_employee({"name": "架构师", "dept": "技术部", "role": "架构师",
                          "persona": "定契约", "mode": "expert"})
    e2 = st.add_employee({"name": "程序员", "dept": "技术部", "role": "工程师",
                          "persona": "写代码", "mode": "expert"})
    g = st.create_group("开发群", [e1["id"], e2["id"]], mode="leader")
    st.leader_begin(g["id"], "做外卖小程序", [
        {"name": "架构师", "task": "定契约", "output": "", "depends_on": []},
        {"name": "程序员", "task": "写代码", "output": "", "depends_on": ["架构师"]},
    ])
    st.leader_attach_task(g["id"], "架构师", "task_a")
    st.leader_attach_task(g["id"], "程序员", "task_b")
    monkeypatch.setattr(m, "_team_store", st)
    monkeypatch.setattr(m.store, "read_events", lambda tid: [
        SimpleNamespace(type="knowledge", payload={"usage": {
            "model": "m", "calls": 3, "input_tokens": 10_000, "output_tokens": 2_000,
            "cached_tokens": 0, "estimated": False}}),
    ])
    line = m._invoice_line(g["id"])
    assert "6 次调用" in line and "24000 tok" in line, line      # 两个任务各 3 次、各 12000 tok


def test_watcher_and_batch_end_post_the_cost(monkeypatch):
    """接线锚点：交付后贴"这一步花了多少"；收口时贴整批合计。"""
    src = (__import__("pathlib").Path(__file__).resolve().parents[1] / "app" / "main.py").read_text("utf-8")
    assert "cost = _usage_line(task_id" in src, "看门任务没贴单步成本"
    assert "_invoice_line(gid)" in src, "收口没贴整批账单"
