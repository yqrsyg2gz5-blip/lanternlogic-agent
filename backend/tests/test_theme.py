# -*- coding: utf-8 -*-
"""界面主题（深色 / 浅色）—— 2026-10-07 用户要的："主题就一个，最起码有两个…一个是深色的，一个是白色的"。

★ 在此之前：`ui.theme` **只是设置页里显示的一行字** ✗ —— 从来没作用到界面上 ✗✓
  （设置页自己都写着"主题与语言目前为只读展示"✓ 也就是**根本没得选** ✗）。
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import main as m  # noqa: E402

_ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture()
def client():
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        yield c


def test_theme_can_be_switched_and_read_back(client):
    """两档都能存 ✓ 而且**读得回来** ✓（不然界面会以为没生效 ✓）。"""
    before = client.get("/api/v1/settings").json()["ui"]["theme"]
    try:
        for t in ("light", "dark"):
            r = client.post("/api/v1/settings/ui", json={"theme": t})
            assert r.status_code == 200, r.text
            assert r.json()["theme"] == t, r.text
            assert client.get("/api/v1/settings").json()["ui"]["theme"] == t, \
                f"存了 {t} 却读不回来 ✗（界面会以为没生效 ✓）"
    finally:
        client.post("/api/v1/settings/ui", json={"theme": before})   # 别改用户的设置 ✓


def test_bogus_theme_is_rejected(client):
    """★ **校验过再写** ✓ —— 写进一个没定义的主题 ⇒ 界面会变成没有样式的鬼样子 ✗。"""
    r = client.post("/api/v1/settings/ui", json={"theme": "彩虹"})
    assert r.status_code == 422, r.text
    assert "未知主题" in r.text, r.text


def test_css_defines_a_light_theme_covering_the_used_variables():
    """★ CSS 里**必须真有浅色那层** ✓ 而且要覆盖**所有在用的变量** ✓。

    这个文件的坑：有**两处 `:root`** ✗（`--panel` 与 `--bg-panel` 两个名字并存 ✓）
    ⇒ 只覆盖一个就会出现"改了一半、另一半还是黑的" ✗✓ —— 这条测试就盯这个 ✓。
    """
    css = (_ROOT / "frontend" / "src" / "styles.css").read_text("utf-8")
    assert '[data-theme="light"]' in css, "没有浅色主题那一段 ✗"
    light = css.split('[data-theme="light"]', 1)[1]
    # 在用的变量名（从 :root 里取 ✓）+ 两个并存的名字都要覆盖 ✓
    for var in ("--bg", "--panel", "--bg-panel", "--card", "--border", "--text",
                "--muted", "--accent"):
        assert f"{var}:" in light, f"浅色层没覆盖 {var} ✗（会出现半黑半白 ✓）"


def test_frontend_applies_the_theme_on_boot():
    """★ 启动就要应用 ✓ —— 光有 CSS 没人设 `data-theme` 等于没有 ✗✓。"""
    app = (_ROOT / "frontend" / "src" / "App.tsx").read_text("utf-8")
    assert "dataset.theme" in app, "启动时没设 data-theme ✗（主题不会生效 ✓）"
    assert "localStorage" in app and "theme" in app, "没记住用户的选择 ✗（每次打开都变回去 ✓）"
    panel = (_ROOT / "frontend" / "src" / "components" / "SettingsPanel.tsx").read_text("utf-8")
    assert "switchTheme" in panel, "设置页没有切换入口 ✗（还是只能看 ✗）"
    assert "主题与语言目前为只读展示" not in panel, "还写着「只读展示」✗（现在能切了 ✓）"


def test_light_theme_covers_the_floating_widgets():
    """★★ 2026-10-08（用户截图报的 ✗）：**右下角那两个浮动件**在浅色下也得跟着变 ✓

    他原话："你看没看见这个浅色这个模板啊……右下角一个是流量的一个是那个就是往下的，
    就是那个按键，颜色什么的改改匹配一下" ✓

    ★ 这两个类是**写死的深色底**（`rgba(24,28,36,…)` / `rgba(30,34,42,…)` ✗）
      ⇒ 不跟变量走 ⇒ 换主题时必然被漏掉 ✓ —— 所以必须**在浅色层里逐个点名** ✓
      （这正是这个文件开头记的那类坑 ✓：`.settings-nav` 当初也是这么漏的 ✓）

    回滚实验：把浅色层里这两条删掉 ⇒ 本条必红 ✓
    """
    css = (_ROOT / "frontend" / "src" / "styles.css").read_text("utf-8")
    light = css.split('[data-theme="light"]', 1)[1]
    for cls in (".usage-badge", ".usage-open", ".jump-bottom"):
        # ★ 判据要**钉到花括号** ✓ —— 第一版我写成"含 `[data-theme="light"] .usage-badge` 就行" ✗
        #   红绿一验：把它改名成 `.usage-badge-NOPE` **照样绿** ✗（前缀匹配 ✓）⇒ 判据是摆设 ✓
        #   （这是本仓反复吃过的那个亏 ✓ 顺手记在这儿 ✓）
        assert f'[data-theme="light"] {cls} {{' in light, \
            f"浅色层没点名补 {cls} ✗（它底色是写死的深色 ⇒ 浅色下就是灰底浅字 ✓）"
    # 「流量」那个徽章展开后的面板也得补 ✓（不然只修了收起态 ✓）
    i = light.index('[data-theme="light"] .usage-badge')
    seg = light[i : i + 700]
    assert "background: #ffffff" in seg, f"浅色下那两个件的底色必须是白 ✓：{seg[:120]}"
    assert "color: #1c2128" in seg, "浅色下字色必须是深色 ✗（不然白底浅字 ✓）"
    # ★ 用**等价 hex**（不用 var ✓）—— 与 `.btn-primary` 同一条理由：
    #   `test_round10_fixes.py` 会**计算实际颜色**做对比度断言 ⇒ 要可计算的 hex ✓
    assert "var(--" not in seg, f"浅色覆盖里别用 var() 做底色 ✗（对比度测试算不出来 ✓）：{seg[:120]}"
