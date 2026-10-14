# -*- coding: utf-8 -*-
"""★★ 起手模板 `starter.html` —— **照这个改，不要从零写**（2026-10-14 新增）。

## 为什么有它（真机事故）

`task_20261014_a871e642`（MiMo，20 秒片）：

```
52 分钟 / 183 步 / 状态 partial / mp4 = 0 ✗
最近 14 条命令里有 21 次是：sed -n '130,200p' tools/render/render.mjs
                                  ↑ **在读渲染器的源码**，想搞懂怎么调它
```

⇒ 模型**没有能直接用的画面起手样例** ⇒ 只能去读渲染器源码 ⇒ 读到被护栏熔断 ✗

## 这个模板解决什么

把「契约 + 视觉规范 + 常用动效」全写在一个文件里，**改数字和文字就能出片**：

  · 契约三件（`__meta` / `__draw` / `__ready`）已写好
  · 深色底 + 单主色 + 发光/暗角/细网格（统一视觉语言）
  · 缓动函数（动效顺滑的关键，线性会很难看）
  · 逐词蹦字 / 大数字滚动 / 横向对比条 / 透视假 UI 墙 / 底部拍点进度条
  · 场景表化（改 `SCENES` 就能换内容）
  · 三条硬规矩写在注释里（字体 / 方框换字符 / 不许用真实时钟计时）

## 本文件钉什么

1. 模板必须存在，且**契约三件齐全**
2. 必须**真能跑**（其实跑一次要 30 秒，测试里用静态检查 + 关键 API 检查）
3. 必须内建那几样动效（否则模型还得自己造，等于没帮上）
4. 必须写明「照这个改，不要从零写」和三条硬规矩
5. 技能文档必须**指向它**
"""
from __future__ import annotations

import pathlib
import re

SKILL = pathlib.Path(__file__).resolve().parents[2] / "backend" / "skills" / "卡点视频"
STARTER = SKILL / "tools" / "render" / "starter.html"


def _starter() -> str:
    assert STARTER.exists(), (
        f"起手模板不在：{STARTER} ✗\n"
        "  没有它，模型只能去读渲染器源码 —— 真机实测读了 21 次被熔断、连成片都没出 ✗"
    )
    return STARTER.read_text("utf-8", errors="replace")


def test_starter_exists_and_declares_the_three_piece_contract():
    """★ 契约三件缺一个就 `waitForFunction: Timeout` 直接失败。"""
    s = _starter()
    assert "window.__meta" in s, "缺 `window.__meta` ✗"
    assert "window.__draw" in s, "缺 `window.__draw` ✗"
    assert "window.__ready" in s, "缺 `window.__ready` ✗"
    assert re.search(r"DURATION\s*=", s), "没有可改的 DURATION ✗"


def test_starter_has_the_builtin_motion_pieces():
    """★ 内建动效必须齐 —— 不然模型还得自己造，等于没帮上。"""
    s = _starter()
    pieces = {
        "缓动（easeOutCubic）": r"1\s*-\s*Math\.pow\(1\s*-\s*t,\s*3\)",
        "逐词蹦字": r"function\s+words\s*\(",
        "大数字滚动": r"function\s+roll\s*\(",
        "横向对比条": r"function\s+bars\s*\(",
        "透视假 UI 墙": r"function\s+uiWall\s*\(",
        "底部拍点进度条": r"function\s+hud\s*\(",
        "编号+眉标": r"function\s+eyebrow\s*\(",
        "暗角": r"function\s+vignette\s*\(",
        "场景表": r"const\s+SCENES\s*=",
    }
    missing = [k for k, pat in pieces.items() if not re.search(pat, s)]
    assert not missing, f"起手模板缺这些内建件：{missing} ✗"


def test_starter_carries_the_three_hard_rules_in_comments():
    """★ 三条硬规矩要写在文件里（模型读这个文件就会看到）。"""
    s = _starter()
    assert "方框" in s and "换掉那个字符" in s, (
        "没写「方框 ⇒ 换字符」⇒ 模型又会去找字体（实测 88 次没找到）✗"
    )
    assert re.search(r"(不许|不要|别).{0,10}(Date\.now|requestAnimationFrame)", s), (
        "没写「不许用真实时钟计时」⇒ 每帧画得不一样、渲染结果不可复现 ✗"
    )
    assert "Microsoft YaHei" in s, "没写中文字体怎么指定 ✗"


def test_starter_says_to_modify_it_not_write_from_scratch():
    s = _starter()
    assert re.search(r"(照这个改|不要从零写|改数字|改这里)", s), (
        "模板没写明「照着改，别从零写」⇒ 模型可能还是自己造 ✗"
    )


def test_skill_points_at_the_starter():
    """★ 技能文档必须**指向**这个模板 —— 否则模型不知道它存在。"""
    md = (SKILL / "SKILL.md").read_text("utf-8", errors="replace")
    assert "starter.html" in md, (
        "SKILL.md 里没提 `starter.html` ⇒ 模型不知道有起手模板，只能去读源码 ✗\n"
        "  真机实测代价：读渲染器源码 21 次 → 被熔断 → 连成片都没出"
    )


def test_starter_uses_only_safe_glyphs():
    """★ 模板自己不许用容易变方框的冷门字符（它本身就是范例）。"""
    s = _starter()
    BAD = "↔⇄⇌⟷⤄⬄➔➜➤⟶⟹⥂"
    hit = [ch for ch in BAD if ch in s]
    assert not hit, (
        f"起手模板里用了冷门箭头 {hit} —— 它本身是范例，用了就会传染给模型 ✗\n"
        "  安全字符：→ · — | ✓ ★ ● ○ ▲ ▼"
    )
