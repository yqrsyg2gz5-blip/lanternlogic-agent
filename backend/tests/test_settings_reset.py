"""Phase 3 ⑧（设置页正规化）：每节「恢复默认」的锚点。

要点：
  ① **只动那一节** —— 其它节的字段一个都不许变（用户点"模型恢复默认"不该把执行环境也清了）
  ② **危险节必须拒绝** —— server（清访问密码、关局域网 ⇒ 用户被锁在外面）、
     storage/skills/mcp（"任务/技能去哪了"就是事故）
  ③ 默认值取自配置模型自身（与"新装一台机器"同源），不是界面随手编的
  ④ 落盘走 `_save_config()`（测试里打桩，别写用户真实配置 —— N15 的规矩）
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main as m


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """别落盘、别污染后续用例：_save_config 打桩 + cfg 逐字段快照还原。"""
    monkeypatch.setattr(m, "_save_config", lambda: None)
    for name in ("model", "executor", "video", "image", "asr", "ui", "notify", "memory", "server"):
        obj = getattr(m.cfg, name)
        monkeypatch.setattr(m.cfg, name, obj.model_copy(deep=True), raising=False)
    yield


def _post(section: str):
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        return c.post("/api/v1/settings/reset", json={"section": section})


def test_reset_only_touches_the_target_section():
    m.cfg.model.provider = "mimo"
    m.cfg.model.model_name = "mimo-v2.6-flash"
    m.cfg.executor.timeout_seconds = 999.0          # 这一节待会儿必须**原样保留**
    m.cfg.executor.workspace_root = m.cfg.executor.workspace_root
    ws_before = str(m.cfg.executor.workspace_root)

    r = _post("model")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["section"] == "model"
    assert m.cfg.model.provider == "mock", "模型节没回到默认（默认 provider=mock）"
    assert m.cfg.model.model_name == "deepseek-chat", m.cfg.model.model_name
    # ★ 关键：其它节一个都不许动
    assert m.cfg.executor.timeout_seconds == 999.0, "恢复模型默认时把执行环境也改了"
    assert str(m.cfg.executor.workspace_root) == ws_before


@pytest.mark.parametrize("section", ["server", "storage", "skills", "mcp", "不存在", ""])
def test_dangerous_or_unknown_sections_are_refused(section):
    r = _post(section)
    assert r.status_code == 422, f"{section!r} 竟然被允许恢复默认：{r.text}"
    assert "不支持" in r.json()["detail"]


def test_refused_sections_leave_the_access_token_alone():
    """最能说明问题的一条：server 节被拒 ⇒ 访问密码与 host 原样（否则用户会被锁在外面）。"""
    m.cfg.server.access_token = "keep-me-please"
    m.cfg.server.host = "0.0.0.0"
    assert _post("server").status_code == 422
    assert m.cfg.server.access_token == "keep-me-please"
    assert m.cfg.server.host == "0.0.0.0"


def test_defaults_come_from_the_config_model():
    """默认值必须来自配置模型自身（与"新装一台机器"同源），不是界面随手编的。"""
    from app.config import ExecutorCfg

    m.cfg.executor.timeout_seconds = 999.0
    assert _post("executor").status_code == 200
    assert m.cfg.executor.timeout_seconds == ExecutorCfg().timeout_seconds


def test_response_carries_the_new_section_value():
    body = _post("ui").json()
    assert body["value"]["theme"] == "dark", body
    assert "已恢复默认" in body["note"]


# ═══ 界面接线 + 跨面一致性（前端可恢复列表必须与后端白名单一致）═══
import pathlib  # noqa: E402
import re  # noqa: E402

_SRC = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"
_PANEL = (_SRC / "components" / "SettingsPanel.tsx").read_text("utf-8")
_HEAD = (_SRC / "components" / "SectionHead.tsx").read_text("utf-8")


def test_section_head_component_exists_and_is_rendered():
    for k in ("desc", "status", "onReset"):
        assert f"{k}" in _HEAD, f"节头缺 {k}"
    # ★ 整行匹配（不是 "in"）：否则把节头包进 `{false && …}` 这种"写了但不渲染"的变体照样能通过
    #   （本班实测：第一版锚点就是这么被自己的回滚组抓出来的 —— 组"没有判别力"）
    assert "\n        <SectionHead\n" in _PANEL, "设置页没真正渲染节头"
    assert "{false && <SectionHead" not in _PANEL, "节头被条件关掉了"
    assert "SECTION_META[section as string]?.desc" in _PANEL, "说明没接到节头上"
    assert "sectionStatus()" in _PANEL and "sectionTone()" in _PANEL, "状态徽章没接上"


def test_every_nav_section_has_a_description():
    """★ 漂移防线：NAV 里加一节却忘了写说明 ⇒ 这一节就没有"这节是干什么的"。"""
    nav_keys = set(re.findall(r"\['([a-z]+)', '[^']+'\]", _PANEL))
    meta_keys = set(re.findall(r"^  ([a-z]+): \{ title:", _PANEL, re.M))
    missing = nav_keys - meta_keys
    assert not missing, f"这些节没有说明：{sorted(missing)}"


def test_frontend_resettable_matches_backend_whitelist():
    """★ 跨面一致：前端挂按钮的节 == 后端接受的节（否则用户点了必然 422）。"""
    m = re.search(r"const RESETTABLE = \[([^\]]+)\]", _PANEL)
    assert m, "前端没有可恢复列表"
    fe = set(re.findall(r"'([a-z]+)'", m.group(1)))
    be = set(m_module_resettable())
    assert fe == be, f"前后端白名单不一致：前端 {sorted(fe)} / 后端 {sorted(be)}"


def m_module_resettable():
    return m._RESETTABLE


def test_reset_button_only_shown_for_whitelisted_sections():
    assert "RESETTABLE.includes(section as string)" in _PANEL, \
        "恢复默认按钮没做白名单判断（危险节也会挂按钮）"
    assert "window.confirm" in _PANEL, "恢复默认没二次确认（误点就改了配置）"


def test_settings_head_verification_covers_the_three_promises():
    v = (pathlib.Path(__file__).resolve().parents[2] / "frontend" / "scripts"
         / "verify_settings_head.mjs").read_text("utf-8")
    assert "每节有一句说明" in v and "每节有状态徽章" in v and "可恢复默认的节有按钮" in v, \
        "验证脚本没覆盖 说明/状态/恢复默认 这三件事"
    assert "先弹确认" in v, "没验证二次确认"
