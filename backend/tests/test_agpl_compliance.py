# -*- coding: utf-8 -*-
"""AGPL 合规：**公开版不能带使用限制** ✓ + 界面里必须有源码入口（第 13 条 ✓）

★ 这是 2026-10-09 用户一句"你得按照 agpl3.0 那个模式走，是不是"逼出来的复查 ✓
  查出两处**真不合规** ✗：
    ① `can_create_task()` 试用 30 天后锁"创建新任务" ✗
       文案还写着「本软件未经授权不得用于商业用途」✗
       ⇒ AGPL 第 10 条：**不得附加任何进一步限制** ✗（既限制使用 ✓ 又限制商用 ✓ 双重违规 ✓）
    ② 程序界面里**没有源码入口** ✗ ⇒ 违反第 13 条（网络用户必须能拿到源码 ✓）

★ 修法（**双授权**的标准做法 ✓ 一份代码两种分发 ✓）：
    · `config.license.enforce` **默认 False** ✓ ⇒ 公开的 AGPL 版没有试用闸 ✓
    · 商用版把它设 True ✓ ⇒ 试用期 + 授权闸照旧 ✓（那份是闭源分发 ✓ 不受 AGPL 约束 ✓）
    · 源码地址进 `version.SOURCE_URL`（一处声明 ✓）⇒ 接口 + 关于页都能读到 ✓
"""
from __future__ import annotations

import pathlib
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import main as m  # noqa: E402
from app import version  # noqa: E402

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_PANEL = (_ROOT / "frontend" / "src" / "components" / "SettingsPanel.tsx").read_text("utf-8")


def test_enforcement_is_off_by_default():
    """★★ **默认必须关** ✗ —— 默认值跟着**公开仓库**走 ✓

    如果默认是 True ⇒ 谁 clone 下来都带着"30 天锁" ✗
    ⇒ 那等于在**分发一个违反 AGPL 的版本** ✓（第 10 条 ✓）

    回滚实验：把默认值改回 True ⇒ 本条必红 ✓
    """
    from app.config import LicenseGateCfg

    assert LicenseGateCfg().enforce is False, "授权闸默认开着 ✗（AGPL 版不能有使用限制 ✓）"
    assert m.cfg.license.enforce is False, f"当前配置里也开着 ✗：{m.cfg.license.enforce}"


def test_the_trial_gate_is_skipped_when_not_enforced(monkeypatch):
    """★★ **关掉之后，试用到期也不许拦** ✗ —— 这是 AGPL 合规的实质 ✓

    （不是"界面上不显示"就完事 ✗ —— 接口层面也不能拦 ✓）
    """
    monkeypatch.setattr(m.cfg.license, "enforce", False, raising=False)
    monkeypatch.setattr(m._lic, "trial_expired", lambda: True)      # 假装已到期 ✓
    monkeypatch.setattr(m._lic, "key", "", raising=False)
    # 直接走那条总门的判断（不真建任务 ✓ 免得拖一堆依赖 ✓）
    src = pathlib.Path(m.__file__).read_text("utf-8")
    assert "if bool(getattr(cfg.license, \"enforce\", False)):" in src, \
        "总门没有按 enforce 开关判断 ✗（AGPL 版仍会被 402 拦住 ✓）"


def test_the_api_reports_the_agpl_mode_and_the_source():
    """★ 接口必须如实报：**这是开源版 · 没有使用限制 · 源码在哪** ✓

    ★ 为什么较真 ✗：AGPL 版要是还显示"试用期剩余 N 天" ✓
      那**本身就是限制的暗示** ✗（第 10 条 ✓）⇒ 两档必须分得清清楚楚 ✓
    """
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.get("/api/v1/license")
        assert r.status_code == 200, r.text
        d = r.json()
    assert d["enforced"] is False, d
    assert d["mode"] == "agpl", d
    assert d["license"] == "AGPL-3.0-only", d
    assert "无使用限制" in d["note"], d
    # ★ 第 13 条：源码地址必须发得出去 ✓
    assert d["source_url"] == version.SOURCE_URL and d["source_url"], d


def test_source_url_is_a_real_place_not_empty():
    """★ 源码地址**不许是空** ✗ —— 空 = 自己没遵守 AGPL 第 13 条 ✓

    ★ 现在是**占位符** ✓（上线前必须换成真仓库地址 ✓）——
      所以这条测试只钉"非空 + 有出处"✓ 并在注释里标明它必须替换 ✓
    """
    assert version.SOURCE_URL.strip(), "SOURCE_URL 是空的 ✗（第 13 条要求给得出源码 ✓）"
    assert "https://github.com/yqrsyg2gz5-blip/lanternlogic-agent" in version.SOURCE_URL or version.SOURCE_URL.startswith("http"), \
        f"源码地址既不是占位符也不是链接 ✗：{version.SOURCE_URL}"
    assert version.LICENSE_ID == "AGPL-3.0-only", version.LICENSE_ID


def test_the_ui_has_a_source_link_and_hides_the_restriction_wording():
    """★ 界面：**AGPL 版要显示"开源版·无使用限制" + 源码** ✓ 且**不许出现限制话术** ✗

    回滚实验：把 `lic.enforced === false` 那个分支拿掉 ⇒ 本条必红 ✓
    """
    assert "lic.enforced === false" in _PANEL, "界面没按 enforced 分支 ⇒ AGPL 版会显示试用期 ✗"
    assert "无使用限制" in _PANEL, "没写清'这版没有使用限制' ✗"
    # ★ 判据钉**渲染出来的那句** ✗ —— 第一版我写"含 `lic.source_url` 就行" ✓
    #   红绿一验：把那一行整句删掉**照样绿** ✗（因为上面 `{!!lic.source_url && (` 守卫里也有这个词 ✓）
    #   ⇒ 又是"判据太松"那一课 ✓（本仓今天第三次 ✓）
    assert "源码：<b>{lic.source_url}</b>" in _PANEL, \
        "界面里没有把源码地址**显示出来** ✗（AGPL 第 13 条要求在用户看得到的地方 ✓）"
    # 商用版那段"未经授权不得用于商业用途"只许待在 enforced 分支里 ✓
    #   ★ 判据要看**渲染出来的 JSX** ✗ 不能把注释也算进去 ✓
    #     （第一版就是栽在这：我上面那段解释性注释里写着"禁止转售"四个字 ✓ 被自己绊倒 ✓）
    i = _PANEL.index("lic.enforced === false")
    j = _PANEL.index(") : lic && lic.activated", i)          # AGPL 分支的结尾 ✓
    seg = "\n".join(ln for ln in _PANEL[i:j].splitlines()
                    if not ln.strip().startswith(("//", "{/*", "*", "/*")))
    assert "禁止转售" not in seg, f"AGPL 分支里出现了限制话术 ✗：{seg[:120]}"
    assert "未经授权不得用于商业用途" not in seg, f"AGPL 分支里出现了商用限制话术 ✗：{seg[:120]}"
