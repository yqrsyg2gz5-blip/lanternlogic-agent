# -*- coding: utf-8 -*-
"""★ 回归测试：**设置存进去，必须读得回来** —— 2026-10-07 功能体检⑤ 真跑抓到的静默数据丢失 ✗✗。

## 这个 bug 长什么样（真接口复现过 ✓ 见评审区 `_repro_limits_wipe.py`）

```
① 用户设好：步数上限 60 + 单价表         → POST /settings/limits 存进去了 ✓
② 他重开设置页：GET /settings            → 后端**不返回** max_iterations 与 pricing ✗
                                            ⇒ 表单只能显示"默认 25 + 空单价表" ✗
③ 他改了个别的设置、顺手点保存            → 界面把**表单内容**整体写回 ✓
④ 结果：60 → 25 ✗   单价表被清空 ✗       —— 而且**没有任何提示** ✓
```

## 为什么这条最严重

· **静默**：用户看不到任何异常 ✓ 只觉得"我明明设过啊" ✗
· **每次都发生**：只要保存一次就重置一次 ✓
· **毁的正是他专门提的两件事**（2026-10-06）：
    "代码活要'写-跑-改'反复迭代，25 步常在快做完时被砍断" ✓
    "花了多少钱看不见" ✓
  ⇒ 结果**每保存一次就被悄悄清掉** ✗✓。

## 这条测试的写法（要说清 ✓）

它不是"调一下接口看返回"✗ —— 而是**照着用户真实的操作顺序走一遍** ✓：
存 → 读回（模拟界面回填表单）→ 用**读回的值**再存一次 → **值必须没变** ✓✓
（修复前第 3 步会把 60 写成 25 ✓ 所以这条必红 ✓；现在必须一直绿 ✓）
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import main as m  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        yield c


def test_limits_and_pricing_survive_a_save_round_trip(client):
    """★ 存 → 读回 → 再存 → **值必须没变** ✓（这就是界面"回填表单再保存"的真实路径 ✓）。"""
    # ① 用户设好
    r1 = client.post("/api/v1/settings/limits",
                     json={"max_iterations": 60,
                           "pricing": {"test-model-x": {"in": 1.0, "out": 2.0, "cached": 0.02}}})
    assert r1.status_code == 200, r1.text
    assert r1.json()["max_iterations"] == 60

    # ② 重开设置页：**后端必须把他设的值回传** ✓（不回传 = 界面表单是空的 ✗）
    s = client.get("/api/v1/settings").json()
    got_iter = (s.get("model") or {}).get("max_iterations")
    got_price = s.get("pricing")
    assert got_iter == 60, f"步数上限没回传（界面会显示默认 25 ⇒ 一保存就重置 ✗）：{got_iter}"
    assert got_price and "test-model-x" in got_price, \
        f"单价表没回传（界面是空表 ⇒ 一保存就清空 ✗）：{got_price}"
    assert got_price["test-model-x"]["in"] == 1.0, got_price

    # ③ 界面拿**读回的值**原样再存一次（用户只是改了个别的设置 ✓）
    r2 = client.post("/api/v1/settings/limits",
                     json={"max_iterations": got_iter, "pricing": got_price})
    assert r2.status_code == 200, r2.text
    body = r2.json()

    # ④ **值必须没变** ✓
    assert body["max_iterations"] == 60, f"保存把步数上限改了 ✗：{body['max_iterations']}"
    assert body["pricing"]["test-model-x"]["in"] == 1.0, f"保存把单价清掉了 ✗：{body['pricing']}"


def test_settings_returns_everything_the_ui_form_needs(client):
    """★ 更一般的守卫：**界面表单要用的字段，接口都得给** ✓。

    这个 bug 的根子是"界面读的字段"与"接口给的字段"**对不上** ✗ ——
    单测某一个字段容易漏 ✓ 所以这里把"表单要用的那几个"整体钉一遍 ✓。
    （前端 `SettingsPanel.tsx` 读：model.* ✓ pricing ✓ tts.backend ✓ executor.* ✓）
    """
    s = client.get("/api/v1/settings").json()
    assert "model" in s and "pricing" in s and "tts" in s and "executor" in s, sorted(s)
    for key in ("provider", "model_name", "base_url", "api_key_env", "max_iterations"):
        assert key in s["model"], f"model 段缺 {key} ✗（界面读不到就会用默认值把它覆盖掉 ✗）"
    assert "backend" in s["tts"], "tts 段缺 backend ✗"
    assert "max_iterations" in s["model"], "步数上限没回传 ✗"


def test_pricing_relay_shape_matches_the_save_shape(client):
    """★ **读回来的形状必须能原样存回去** ✓ —— 否则"回填再保存"这一步仍会毁数据 ✗。

    （这条防的是"回传时改了结构"✓：例如把 `{in,out}` 拍平成数字 ✓
      那样界面回填后存回去就不认了 ✗。）
    """
    client.post("/api/v1/settings/limits",
                json={"max_iterations": 60, "pricing": {"m-y": {"in": 3.0, "out": 9.0}}})
    got = client.get("/api/v1/settings").json()["pricing"]["m-y"]
    assert isinstance(got, dict) and "in" in got and "out" in got, got
    r = client.post("/api/v1/settings/limits", json={"max_iterations": 60, "pricing": {"m-y": got}})
    assert r.status_code == 200 and r.json()["pricing"]["m-y"]["in"] == 3.0, r.text
