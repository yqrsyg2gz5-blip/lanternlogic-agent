# -*- coding: utf-8 -*-
"""★ 2026-10-06「模型分级」——把**只做判断、不动手**的那几处换成便宜快的模型。

## 它做什么

配置里填 `model.simple_model`（例：`glm-5.3-flash`）之后：
开会发言 / 收口 / 主持人"够了没"这三处**纯判断**的调用走便宜模型 ✓ ——
它们不需要强模型 ✓ 而量大 ✓。

## 三条边界（每条都是有意为之 ✓）

1. **默认关着** ✓ —— `simple_model` 空着就等于没这功能 ✓。
   这是**唯一一个可能拿质量换钱**的改动 ✗ —— 今天刚把"四种任务类型全过"调出来 ✓
   不能为了省一点钱把它换掉 ✓（所以做成"你填了才生效" ✓）。
2. **不碰验收人的判定** ✗ —— 它决定"能不能过" ✓ 用弱模型省下的钱
   会以"该打回没打回"的形式几倍还回去 ✓。
3. **不覆盖员工自己绑的模型** ✓ —— 员工卡上配了 provider/model_name 就尊重它 ✓
   （BYOK 的语义不能被"省钱"破坏 ✓）。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import main as m  # noqa: E402


def _capture(monkeypatch) -> list[dict]:
    """把 `_leader_plan` 换成"记下收到的员工卡"，好断言它用了哪个模型 ✓。"""
    seen: list[dict] = []

    async def _fake(emp, prompt):                           # noqa: ARG001
        seen.append(dict(emp))
        return "好"

    monkeypatch.setattr(m, "_leader_plan", _fake)
    return seen


def test_off_by_default(monkeypatch):
    """**默认关着** ✓：配置里不填 `simple_model` ⇒ 员工卡原样传下去 ✓（等于没这功能 ✓）。"""
    seen = _capture(monkeypatch)
    monkeypatch.setattr(m.cfg.model, "simple_model", "", raising=False)
    asyncio.run(m._employee_say({"name": "甲", "role": "产品经理"}, "说句话"))
    assert "model_name" not in seen[0], seen[0]


def test_uses_simple_model_when_configured(monkeypatch):
    """填了就生效 ✓：纯判断那几处改用便宜模型 ✓。"""
    seen = _capture(monkeypatch)
    monkeypatch.setattr(m.cfg.model, "simple_model", "glm-5.3-flash", raising=False)
    monkeypatch.setattr(m.cfg.model, "model_name", "mimo-v2.6-flash", raising=False)
    asyncio.run(m._employee_say({"name": "甲", "role": "产品经理"}, "说句话"))
    assert seen[0].get("model_name") == "glm-5.3-flash", seen[0]


def test_does_not_override_an_employee_bound_model(monkeypatch):
    """**员工自己绑了模型就不动它** ✓ —— BYOK 的语义不能被"省钱"破坏 ✓。"""
    seen = _capture(monkeypatch)
    monkeypatch.setattr(m.cfg.model, "simple_model", "glm-5.3-flash", raising=False)
    asyncio.run(m._employee_say(
        {"name": "甲", "provider": "openai", "model_name": "我自己的模型"}, "说句话"))
    assert seen[0].get("model_name") == "我自己的模型", seen[0]


def test_full_model_is_not_downgraded_to_itself(monkeypatch):
    """填的跟主模型一样 ⇒ 什么都不做 ✓（别把"分级"变成一次空转 ✓）。"""
    seen = _capture(monkeypatch)
    monkeypatch.setattr(m.cfg.model, "simple_model", "mimo-v2.6-flash", raising=False)
    monkeypatch.setattr(m.cfg.model, "model_name", "mimo-v2.6-flash", raising=False)
    asyncio.run(m._employee_say({"name": "甲"}, "说句话"))
    assert "model_name" not in seen[0], seen[0]


def test_verifier_is_deliberately_left_on_the_main_model():
    """★★ **验收人的判定必须留在主模型上** ✗（这条是"故意不做"的守卫 ✓）。

    理由：验收决定"能不能过" ✓ 用弱模型省的那点钱，
    会以"该打回没打回"的形式几倍还回去 ✓。
    ⇒ 源码里 `verification_prompt` 那条路**不该**出现 simple_model ✓。
    """
    src = pathlib.Path(m.__file__).read_text("utf-8")
    head, _, tail = src.partition("async def _verify_delivery(")
    body = tail.split("\nasync def ", 1)[0]
    assert "simple_model" not in body, "验收那条路被接上省钱模型了 ✗（决定能不能过的判定不能省 ✓）"
