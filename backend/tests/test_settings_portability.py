"""Phase 3 ⑧ 第二刀（设置导出 / 导入）的锚点。

两条主线：
  ① **导出的文件里没有秘密** —— 而且是**结构上**没有（白名单里根本没有 server 段；
     跟密钥有关的字段只有 `api_key_env` 这个**变量名**）。这条要用真环境变量值去撞。
  ② **导入先校验再应用** —— 三道闸：节名白名单 / 配置模型逐节校验（extra=forbid）/
     可先 dry_run 预演。任何一道不过，配置**一个字节都不许变**。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main as m


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """不落盘、逐节快照还原（N15 的规矩）。"""
    monkeypatch.setattr(m, "_save_config", lambda: None)
    for name in ("model", "executor", "video", "image", "asr", "ui", "notify", "memory", "server"):
        monkeypatch.setattr(m.cfg, name, getattr(m.cfg, name).model_copy(deep=True), raising=False)


def _c():
    return TestClient(m.app, base_url="http://127.0.0.1:8642")


# ═══ ① 导出：结构上不含秘密 ═══

def test_export_never_contains_key_values(monkeypatch):
    secret = "sk-export-leak-probe-1234567890"
    monkeypatch.setenv("XIAOMI_MIMO_API_KEY", secret)
    monkeypatch.setattr(m.cfg.server, "access_token", "LAN-PASSWORD-abcdefghijklmnop", raising=False)
    body = _c().get("/api/v1/settings/export").text
    assert secret not in body, "导出的文件里带了 API Key 的值"
    assert "LAN-PASSWORD" not in body, "导出的文件里带了访问密码"
    assert "api_key_env" in body, "连变量名都没导出（那导入后用户不知道该配哪个 Key）"


def test_export_covers_the_portable_sections_only():
    data = _c().get("/api/v1/settings/export").json()
    assert set(data["sections"]) == set(m._RESETTABLE), data["sections"].keys()
    for forbidden in ("server", "storage", "skills", "mcp"):
        assert forbidden not in data["sections"], f"{forbidden} 不该出现在导出里"


def test_export_says_what_is_not_included():
    note = _c().get("/api/v1/settings/export").json()["note"]
    assert "API Key" in note and "不在里面" in note, note


# ═══ ② 导入：先校验、再预演、最后应用 ═══

def test_import_dry_run_changes_nothing():
    m.cfg.model.provider = "mimo"
    r = _c().post("/api/v1/settings/import",
                  json={"sections": {"model": {"provider": "deepseek"}}, "dry_run": True})
    assert r.status_code == 200, r.text
    body = r.json()
    # ★ 只报**给了的**那个字段：合并语义下，没给的字段原样保留（"只想改超时"不该重置别的）
    assert body["dry_run"] is True and body["changed"]["model"] == ["provider"], body
    assert m.cfg.model.provider == "mimo", "预演竟然改了配置"


def test_partial_import_keeps_the_other_fields():
    """★ 合并语义：手写片段只改它写的字段，别的原样（否则"只想改超时"会把工作区也重置）。"""
    m.cfg.executor.timeout_seconds = 60.0
    keep = str(m.cfg.executor.workspace_root)
    r = _c().post("/api/v1/settings/import", json={"sections": {"executor": {"timeout_seconds": 120}}})
    assert r.status_code == 200, r.text
    assert m.cfg.executor.timeout_seconds == 120
    assert str(m.cfg.executor.workspace_root) == keep, "没写的字段被改掉了"
    assert r.json()["changed"]["executor"] == ["timeout_seconds"], r.json()["changed"]


def test_import_applies_after_confirmation():
    r = _c().post("/api/v1/settings/import",
                  json={"sections": {"model": {"provider": "deepseek", "model_name": "deepseek-chat"}}})
    assert r.status_code == 200, r.text
    assert m.cfg.model.provider == "deepseek"
    assert "已导入" in r.json()["note"] and "API Key 仍需你在本机设置" in r.json()["note"]


def test_import_refuses_dangerous_sections():
    """导入别人的 server 段会把你的访问密码顶掉 ⇒ 必须拒。"""
    for sec in ("server", "storage", "skills", "mcp", "不存在"):
        m.cfg.server.access_token = "keep-me"
        r = _c().post("/api/v1/settings/import", json={"sections": {sec: {}}})
        assert r.status_code == 422, f"{sec} 竟然被允许导入"
        assert m.cfg.server.access_token == "keep-me"


def test_import_rejects_unknown_fields_via_the_config_model():
    """多一个字段就报错（配置模型是 extra=forbid）——不能静默吃掉、更不能写进配置。"""
    before = m.cfg.executor.model_dump(mode="json")
    r = _c().post("/api/v1/settings/import",
                  json={"sections": {"executor": {"不是字段": 1}}})
    assert r.status_code == 422, r.text
    assert "不合法" in r.json()["detail"]
    assert m.cfg.executor.model_dump(mode="json") == before, "校验失败却改了配置"


def test_import_refuses_wrong_types_too():
    r = _c().post("/api/v1/settings/import",
                  json={"sections": {"executor": {"timeout_seconds": "六十秒"}}})
    assert r.status_code == 422, r.text


def test_round_trip_export_then_import_is_a_no_op():
    """★ 真正的验收：导出 → 原样导入 ⇒ **没有任何改动**（说明导出的是完整可用的设置）。"""
    exported = _c().get("/api/v1/settings/export").json()["sections"]
    r = _c().post("/api/v1/settings/import", json={"sections": exported})
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == {}, f"自己导入自己竟然改了东西：{r.json()['changed']}"


# ═══ 界面接线（脚本存在 ≠ 接上了）═══
import pathlib  # noqa: E402

_SRC = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"
_PANEL = (_SRC / "components" / "SettingsPanel.tsx").read_text("utf-8")
_API_TS = (_SRC / "api.ts").read_text("utf-8")
_UI_VERIFY = (pathlib.Path(__file__).resolve().parents[2] / "frontend" / "scripts"
              / "verify_settings_portability.mjs").read_text("utf-8")


def test_panel_has_export_and_import_entry_points():
    assert "导出设置" in _PANEL and "api.exportSettings" in _PANEL, "没有导出入口"
    assert "导入设置…" in _PANEL, "没有导入入口"
    # 整行匹配（className 里还有 btn-mini，只查 className="port-import" 会假红 —— 本班实测）
    assert '<label className="btn-mini port-import">' in _PANEL, "导入入口不是那个带文件选择的 label"
    # 导出要真的产生文件（不是把 JSON 打在控制台里）
    assert "new Blob(" in _PANEL and "a.download = `agent-shell-设置-" in _PANEL, "导出没生成可保存的文件"


def test_import_always_previews_before_applying():
    """★ 导入必须**先预演**：用户得先看到"会改动什么"，再点确认 —— 别一键盖掉整份设置。"""
    assert "const dry = await api.importSettings(sections, true);" in _PANEL, \
        "导入没有先预演（dry_run=true）"
    assert "void api.importSettings(portPending.sections, false)" in _PANEL, \
        "确认后没应用（或者应用的不是预演过的那份）"
    assert "确认导入" in _PANEL and "将改动" in _PANEL, "预演结果没摊给用户看"


def test_api_layer_implements_export_import():
    assert "exportSettings(" in _API_TS and "importSettings(" in _API_TS, "api 层没实现"
    assert _API_TS.count("exportSettings") >= 3 and _API_TS.count("importSettings") >= 3, \
        "声明/实现/离线实现不齐"


def test_ui_verification_covers_preview_and_rejection():
    assert "先发 dry_run=true" in _UI_VERIFY, "没验证先预演"
    assert "后端拒绝时如实报错" in _UI_VERIFY, "没验证被拒路径"
    assert "真的触发下载" in _UI_VERIFY, "没验证导出真产生文件"