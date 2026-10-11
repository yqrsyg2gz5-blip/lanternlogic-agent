# -*- coding: utf-8 -*-
"""第十轮复验修复的回退锚点。

B1/B2 快照排除（扩展名+精确名+前缀）的锚点在 test_round9_fixes.py::
test_snapshot_exclusion_rules（误杀用例与排除用例同一清单，两边都必须过）。

本文件锚点：
· B3 按钮对比度：.btn-primary / .btn-save 的启用态与禁用态都必须 ≥4.5:1（WCAG AA），
  且禁用态不得靠 opacity 压暗——回退 CSS（background 换回 var(--accent) 或恢复
  opacity:0.4）本测试必红。
· B4 sk-model-v2 已知限制必须写进 redact.py（git grep 可得）——删掉 docstring 段落必红。
"""
from __future__ import annotations

import re
from pathlib import Path

# ---------- WCAG 2.x 相对亮度 / 对比度 ----------


def _lum(hexc: str) -> float:
    hexc = hexc.lstrip("#")
    r, g, b = (int(hexc[i : i + 2], 16) / 255 for i in (0, 2, 4))

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _ratio(fg: str, bg: str) -> float:
    l1, l2 = sorted((_lum(fg), _lum(bg)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


_CSS_PATH = Path(__file__).resolve().parents[2] / "frontend" / "src" / "styles.css"


def _rule(selector: str, need: tuple[str, ...] = ("background", "color")) -> str:
    """从 styles.css 取选择器规则体。

    CSS 是层叠的：同一选择器可能有多条规则（后条只覆盖部分属性）——
    从最后一条往前找，返回**同时声明了 need 全部属性**的那条
    （等价于浏览器对这两个属性的实际取值）。

    ★★ 2026-10-07 修（红绿门当场抓到的 ✗✗）：**只认"基础规则"** ✓ ——
      加了浅色主题之后多了一条 `[data-theme="light"] .btn-primary { … }` ✓
      而原来的正则会把它也算成 `.btn-primary` 的规则 ✗ ⇒ 取到的是**带作用域的那条** ✗
      ⇒ 于是"把基础规则的背景改回 var(--accent)（对比度不合格）"这个突变
        **测不出来了** ✗✓（红绿组当场报「实验本身失效」✓ 它报得对 ✓）。
      ⇒ 现在要求选择器前是**块边界**（`}` / `{` / 行首 ✓）
        ⇒ 带前缀的作用域规则不再被误认 ✓ 对比度这条**盯的还是基础样式** ✓✓
    """
    text = _CSS_PATH.read_text(encoding="utf-8")
    blocks = re.findall(r"(?:^|[}{;])\s*" + re.escape(selector) + r"\s*\{([^}]*)\}",
                        text, flags=re.M)
    assert blocks, f"styles.css 中找不到 {selector}"
    for body in reversed(blocks):
        if all(re.search(rf"(?<![-\w]){p}\s*:", body) for p in need):
            return body
    raise AssertionError(f"{selector} 没有任何块同时声明 {need}")


def _css_color(decl: str) -> str:
    """从 `background: <value>` 提取可计算的颜色：#hex 直接用；
    var(--x, #fallback) 取 fallback。"""
    m = re.search(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b", decl)
    assert m, f"声明里没有可计算的 hex 颜色：{decl}"
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return "#" + h


def _pair(body: str, prop: str) -> tuple[str, str]:
    bg = _css_color(_pick(body, "background"))
    fg = _css_color(_pick(body, "color"))
    return fg, bg


def _pick(body: str, prop: str) -> str:
    m = re.search(rf"(?<![-\w]){prop}\s*:\s*([^;}}]+)", body)
    assert m, f"规则体里找不到 {prop}：{body}"
    return m.group(1).strip()


class TestButtonContrast:
    """B3：按钮文字对比度 ≥4.5:1（14px/550 不属大文本，AA 全量标准）。

    基线（修复前实测）：accent #6ba3f5 vs #fff = 2.57:1；
    禁用 opacity 0.4 合成后 = 1.77:1。
    """

    def test_btn_primary_enabled(self):
        fg, bg = _pair(_rule(".btn-primary"), "background")
        r = _ratio(fg, bg)
        assert r >= 4.5, f".btn-primary 启用态对比度 {r:.2f}:1 < 4.5（fg={fg} bg={bg}）"

    def test_btn_save_enabled(self):
        fg, bg = _pair(_rule(".btn-save"), "background")
        r = _ratio(fg, bg)
        assert r >= 4.5, f".btn-save 启用态对比度 {r:.2f}:1 < 4.5（fg={fg} bg={bg}）"

    def test_btn_primary_disabled(self):
        body = _rule(".btn-primary:disabled")
        assert "opacity" not in body, f"禁用态不得靠 opacity 压暗：{body!r}"
        fg, bg = _pair(body, "background")
        r = _ratio(fg, bg)
        assert r >= 4.5, f".btn-primary 禁用态对比度 {r:.2f}:1 < 4.5（fg={fg} bg={bg}）"

    def test_btn_save_disabled(self):
        body = _rule(".btn-save:disabled")
        assert "opacity" not in body, f"禁用态不得靠 opacity 压暗：{body!r}"
        fg, bg = _pair(body, "background")
        r = _ratio(fg, bg)
        assert r >= 4.5, f".btn-save 禁用态对比度 {r:.2f}:1 < 4.5（fg={fg} bg={bg}）"


def test_redact_known_limitation_documented():
    """B4：sk-model-v2 的已知限制必须写在 redact.py 里（git grep 可得，不是只在对话里）。"""
    redact = Path(__file__).resolve().parents[1] / "app" / "redact.py"
    text = redact.read_text(encoding="utf-8")
    # 二十六轮第 5 批收紧：断言改 B4 段【标志串】——"sk-model-v2" 此后在
    # 弱通道豁免注释里也合法出现（版本名形态），泛串断言会让删除 B4 段的
    # 回滚实验假绿
    assert "已知限制（第十轮 B4 落盘）" in text, "redact.py 缺少 B4 已知限制段标志"
    assert "sk-model-v2" in text, "redact.py 缺少 sk-model-v2 已知限制说明"
    assert "sk-abc123" in text, "redact.py 缺少下界=6 的动机说明（盖住 sk-abc123）"


# ═══ 二十六轮第2批：sk- 变体补形态（只增不改）+ 已知限制 ═══
import pytest  # 本文件原无 import（B3/B4 用不到）；26r2 参数化需要


@pytest.mark.parametrize("secret", [
    "sk_live_51Habcdefghij",   # Stripe 下划线（验证员样例）
    "sk_test_ABCDEFGHIJK",
    "SK-UPPER1234567890",      # 大写（\b 保证 TASK-xxx 不误伤）
    "PK-lower-case123456",
])
def test_redact_new_key_shapes_26r2(secret):
    from app.redact import redact_text
    out = redact_text(f"echo {secret}")
    assert secret not in out, f"新形态应打码：{secret} → {out}"
    assert "[已隐藏" in out


@pytest.mark.parametrize("plain", [
    "TASK-UPPER1234567",   # 词边界保护：TASK 里的 sk? 否——大写分支前有词字符
    "disk-usage123",       # disk 的 sk- 前是字母 → \b 不成立
    "echo sk_live_short",  # sk_live_ 后不足 10 位（且不是 live/test 语义）→ 原样
])
def test_redact_no_false_positive_26r2(plain):
    from app.redact import redact_text
    out = redact_text(f"echo {plain}")
    assert plain in out, f"普通文本被误打码：{plain} → {out}"


def test_redact_wordless_boundary_documented():
    """xxsk-attached123456（无词边界）不打码——已知限制必须在 docstring 落盘。"""
    from app import redact
    out = redact.redact_text("echo xxsk-attached123456")
    assert "xxsk-attached123456" in out
    assert "xxsk-attached123456" in redact.__doc__, "已知限制必须写进模块 docstring（不许既不打码也不文档化）"
