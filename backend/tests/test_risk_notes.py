"""Phase 3 ⑧ 收尾批：高风险项说明（RiskNote）的锚点。

这一批最要紧的性质是"**它什么也不改**"：说明只是把"你现在开着什么口子"摆到眼前，
不许弹请求、不许改值、不许因为文案判断而影响保存。锚点围绕这条钉：
  · 组件里不许出现 `api.` / `fetch(` / `localStorage.setItem`
  · 只读地算风险等级（从已有 state 推），保存路径一个字没动
  · 浏览器验证里那条"零写请求"就是它的动态证据
"""
from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SRC = _ROOT / "frontend" / "src"
_PANEL = (_SRC / "components" / "SettingsPanel.tsx").read_text("utf-8")
_NOTE = (_SRC / "components" / "RiskNote.tsx").read_text("utf-8")
_CSS = (_SRC / "styles.css").read_text("utf-8")
_VERIFY = (_ROOT / "frontend" / "scripts" / "verify_risk_notes.mjs").read_text("utf-8")


def _code_only(src: str) -> str:
    """剔掉注释行再扫描（本会话栽过四次：锚点被注释里的同名字样满足/绊倒）。"""
    out = []
    for line in src.splitlines():
        s = line.lstrip()
        if s.startswith("//") or s.startswith("*") or s.startswith("/*"):
            continue
        out.append(line.split("//")[0])
    return "\n".join(out)


def test_risk_note_is_pure_display():
    """★ 核心性质：纯展示 —— 不发请求、不写 localStorage、不改配置。"""
    code = _code_only(_NOTE)
    for banned in ("api.", "fetch(", "localStorage.setItem", "sessionStorage.setItem"):
        assert banned not in code, f"风险说明里出现了 {banned}（它必须什么都不改）"


def test_risk_note_shows_summary_first_and_detail_on_demand():
    assert "useState(false)" in _NOTE, "详情应当默认收起（别给设置页添乱）"
    assert "aria-expanded={open}" in _NOTE, "展开状态没告诉读屏器"
    assert "{open && <div className=\"risk-detail\">" in _NOTE, "详情没接上"


def test_only_the_two_undocumented_items_got_a_note():
    """★ 刻意只补两条：沙箱/Key/局域网此前已有明确提示，重复加说明只会让设置页又变乱
    （用户明确说过"感觉特别乱套"）。"""
    assert _PANEL.count("<RiskNote") == 2, f"说明条数量不对（{_PANEL.count('<RiskNote')} 条）"
    assert _PANEL.count("<RiskNote") == _PANEL.count("level={dirsRisk().level}") + _PANEL.count("level={approvalRisk().level}"), \
        "说明条的风险等级没接到只读计算上"


def test_risk_levels_are_computed_read_only():
    assert "const dirsRisk = (): { level:" in _PANEL and "const approvalRisk = (): { level:" in _PANEL, \
        "没有只读的风险等级计算"
    # 危险判定要覆盖真实危险写法：盘符根、系统/用户目录、删除类命令缺失、清单为空
    for k in ("^[a-zA-Z]:[\\\\/]?$", "windows|users|program", "remove-item", "!list.length"):
        assert k in _PANEL, f"风险判定少了：{k}"
    assert "setDirsText" in _PANEL and "setApprovalText" in _PANEL, "输入框的原有绑定被破坏了"


def test_styles_and_dynamic_proof_exist():
    for k in (".risk-note", ".risk-danger", ".risk-toggle", ".risk-detail"):
        assert k in _CSS, f"样式缺 {k}"
    assert "零写请求" in _VERIFY, "浏览器验证里没有'零写请求'这条动态证据"
    assert "POST', 'PUT', 'PATCH', 'DELETE'" in _VERIFY, "写请求的计数没覆盖全方法"
    assert "都不会再问你" in _VERIFY, "没验证'清单清空'的最坏后果文案"
