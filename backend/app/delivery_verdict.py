# -*- coding: utf-8 -*-
"""交付声明分级：**已实测 / 自证 / 未验证** ✓ —— 治"自己出题自己判卷"✗

## 为什么要有它（2026-10-09 用户实测 ✓）

那天的真事：
  · Agent 交付时写「**验收通过** ✓ 未发现阻断性缺陷」✗
  · 它凭什么说通过？跑的是**项目自带的 `_test.html` 23/23** ✗
  · ★ 而那个 `_test.html` 是**同一个任务自己写出来的** ✓ ⇒ **自己出题、自己判卷** ✗
  · 用户点进去 ⇒ 「▦看板」按钮点了没反应 ✗

⇒ 教训：**"跑通了自测" ≠ "验收通过"** ✗ —— 这两句话的**分量差着十万八千里** ✓
⇒ 产品必须**替用户把这两句话分开** ✓（用户没义务懂"自证"这个概念 ✓）

## 怎么分级（★ 只用**后端已知的事实** ✗ 不解析报告文字 ✓）

解析自然语言的"他说他验过了"是不可靠的 ✗（模型措辞千变万化 ✓ 而且它本来就会往好里说 ✗）
⇒ 分级只看**三个硬事实** ✓：

  | 事实 | 从哪来 |
  |---|---|
  | 有没有跑过**外部**验证（真浏览器真点击 ✓） | ③ 的交付体检结果 `audit["ok"]` ✓ |
  | 工作区里**本次任务自己产出**的测试文件有几个 | 文件 mtime 对比任务开始时间 ✓ |
  | 交付物里有没有可交互的东西（网页） | 入口文件名后缀 ✓ |

分级规则 ✓：
  · 外部验证**真跑成了** ⇒ ★ **已实测** ✓（有真点击证据 ✓ 这才配叫"验过"✓）
  · 没跑成，但存在**本次自产**的测试 ⇒ ⚠️ **自证**（自己判的卷 ✓ 不算验收 ✓）
  · 两样都没有 ⇒ ❌ **未验证** ✓（如实说 ✓）
"""
from __future__ import annotations

import pathlib
from datetime import datetime, timezone

#: 名字像"测试/诊断"的（与 entry_hint 同源思路 ✓ 但这里用于"谁产出的"判定 ✓）
#: ★ 2026-10-09 修：**去掉 "acceptance"** ✗
#:   原因（用户实测点名 ✓）：`ACCEPTANCE.md` 是**交付文档** ✓ 被算成"测试文件"后
#:   交付提示会写成「只跑了自己产出的测试 33 个」✗ —— 把文档也算进去 ⇒ **清单不可信** ✗
#:   ★ 真正的验收测试文件叫 `test_acceptance_*.py` ✓ 由 "test" 这个词兜住 ✓ 不会漏 ✓
_TESTISH = ("test", "check", "probe", "diag", "clicktest", "verify")


def _is_testish(name: str) -> bool:
    return any(w in name.lower() for w in _TESTISH)


def _reference_mtime(ws: pathlib.Path) -> tuple[float, float]:
    """返回 (最早的非测试文件 mtime, 最新的非测试文件 mtime) ✓（拿不到就给 0 ✓）

    ★ 为什么改成"**最早**的交付物当下界"（2026-10-09 第二次修 ✓）：
      · 第一版拿"最老的文件"✗ ⇒ 一周前的测试也被算成自产 ✗（测试变红 ✓）
      · 第二版拿"最新的非测试文件 − 5 分钟"✗ ⇒ **清单偏少** ✗：
        实测今天那个真工作区 ⇒ `_test.html`/`_clicktest*.html` 是**交付物之前 40 分钟**写的
        ⇒ 全落在窗外 ⇒ 只认出 2 个 ✓（**档位判对了 ✓ 但清单不全** ✗ —— 用户点名要补完 ✓）
      · 第三版（现在）：下界 = **最早的那个非测试文件** ✓
        ⇒ 本次任务从"第一个交付物"到"最后一个交付物"之间写的测试 ✓ 全算自产 ✓
        ⇒ 而**更早的老测试**（比最早交付物还早）⇒ 仍然排除 ✓（对应那条单元测试 ✓）
    """
    earliest, latest = 0.0, 0.0
    try:
        for p in ws.iterdir():
            if not p.is_file():
                continue
            m = p.stat().st_mtime
            latest = max(latest, m)
            if not _is_testish(p.name):
                earliest = m if not earliest else min(earliest, m)
    except OSError:
        return 0.0, 0.0
    return earliest, latest


def self_produced_tests(workspace: str | pathlib.Path, *, started_at: float | None = None,
                        grace_s: float = 300.0) -> list[str]:
    """本次任务**自己产出**的测试类文件 ✓

    判据：名字像测试 ✓ **且**落在"本次交付时段"内 ✓（下界 = 最早的交付物 − 余量 ✓）
    `grace_s` 给"测试比第一个交付物还早几分钟"留余量 ✓（实测常见 ✓）
    """
    ws = pathlib.Path(workspace)
    try:
        if not ws.is_dir():
            return []
        earliest, latest = _reference_mtime(ws)
        lower = earliest if started_at is None else started_at
        if lower <= 0:                      # 一个非测试文件都没有 ⇒ 退回"最新文件"当基准 ✓
            lower = latest
        out = []
        for p in ws.iterdir():
            if not p.is_file() or not _is_testish(p.name):
                continue
            try:
                if lower and p.stat().st_mtime >= lower - grace_s:
                    out.append(p.name)
            except OSError:
                continue
        return sorted(out)
    except OSError:
        return []


def classify(workspace: str | pathlib.Path, audit: dict | None = None) -> dict:
    """★ 给这次交付定级 ✓ **永不抛** ✗（它在交付收尾路径上跑 ✓）。

    返回：level（已实测 / 自证 / 未验证）、note（给用户看的一句话 ✓）、self_tests（清单 ✓）
    """
    ws = pathlib.Path(workspace)
    audit = audit or {}
    self_tests = self_produced_tests(ws)

    if audit.get("ok"):
        dead = audit.get("dead") or []
        if dead:
            level, note = "已实测", (
                f"✅ 已实测（真浏览器逐个点过 ✓）—— 但**发现 {len(dead)} 个按钮点了没反应** ✗："
                + "、".join(dead[:5]) + "　⇒ 交付前该修 ✗ 别当成「通过」✓")
        else:
            level, note = "已实测", (
                f"✅ 已实测：真浏览器把 {audit.get('buttons', 0)} 个可见按钮逐个点过 ✓ 都有反应 ✓"
                f"（这是外部验证 ✓ 不是自测 ✓）")
    elif audit.get("skipped_reason"):
        if self_tests:
            level, note = "自证", (
                f"⚠️ **自证**（只跑了本次任务自己产出的测试 {len(self_tests)} 个："
                + "、".join(self_tests[:4]) + ("…" if len(self_tests) > 4 else "")
                + "）✗ —— 自己出题自己判卷 ✓ **不等于验收通过** ✗"
                f"　外部验证没做成：{audit.get('skipped_reason')}")
        else:
            level, note = "未验证", (
                f"❌ **未验证**：外部验证没做成（{audit.get('skipped_reason')}）✓"
                f"　也没有可参考的自测 ✓ —— 请自己点一遍再决定要不要用 ✗")
    else:
        level, note = "未验证", "❌ **未验证**：这次交付没有跑过任何验证 ✓（连自测都没有 ✓）请自己验一遍 ✗"

    return {"level": level, "note": note, "self_tests": self_tests,
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
