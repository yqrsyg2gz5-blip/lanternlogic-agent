# -*- coding: utf-8 -*-
"""交付声明分级 —— 用**今天那件事**当考卷 ✓

今天的原样：
  · 工作区里有 Agent 本次写的 `_test.html` / `_persist.html` / `_clicktest*.html` ✓（自证材料 ✓）
  · 它的报告写「验收通过」✗ 依据就是跑这些自测 ✓
  · 而真点击体检**没做过**（那天根本没有这个能力 ✓）
  ⇒ ★ 分级必须是「**自证**」⚠️ 而不是「已实测」✓

三条要点：
  ① 只有**外部验证真跑成**才配叫「已实测」✓（自测永远不够 ✓）
  ② 外部验证里**发现死按钮** ⇒ 仍是「已实测」✓ 但话里必须点名缺陷 ✗（不许含糊成"通过"✗）
  ③ 两样都没有 ⇒ 「**未验证**」✓（如实说 ✓ 别编 ✓）
"""
from __future__ import annotations

import os
import time

from app.delivery_verdict import classify, self_produced_tests


def _mk(ws, names, *, age_s=0.0):
    """造文件 ✓ age_s>0 ⇒ 把 mtime 往前拨（模拟"老文件"= 不是本次产出的 ✓）"""
    for n in names:
        p = ws / n
        p.write_text("<html></html>", encoding="utf-8")
        if age_s:
            t = time.time() - age_s
            os.utime(p, (t, t))
    return ws


def test_todays_case_is_self_certification(tmp_path):
    """★ 核心：今天的场景 ⇒ 必须判「自证」⚠️ 不许判「已实测」✗"""
    ws = _mk(tmp_path, ["index.html", "_test.html", "_persist.html", "_clicktest_dom.html"])
    got = classify(ws, {"ok": False, "skipped_reason": "本机没装 playwright"})
    assert got["level"] == "自证", got
    assert "_test.html" in got["self_tests"], got["self_tests"]
    assert "不等于验收通过" in got["note"], "必须把话说死：自证 ≠ 验收 ✓"


def test_external_verification_earns_the_top_level(tmp_path):
    """外部验证真跑成 ⇒ 「已实测」✓（这是唯一配得上"验过"的一档 ✓）"""
    ws = _mk(tmp_path, ["index.html"])
    got = classify(ws, {"ok": True, "buttons": 29, "dead": [], "skipped_reason": ""})
    assert got["level"] == "已实测", got
    assert "外部验证" in got["note"]
    assert "不是自测" in got["note"], "要明确区分它和自测 ✓"


def test_dead_buttons_stay_top_level_but_name_the_defect(tmp_path):
    """★ 真有死按钮 ⇒ 档位仍是「已实测」✓ 但**必须点名缺陷** ✗（不许写成"通过"✗）"""
    ws = _mk(tmp_path, ["index.html"])
    got = classify(ws, {"ok": True, "buttons": 29, "dead": ["▦看板"], "skipped_reason": ""})
    assert got["level"] == "已实测"
    assert "▦看板" in got["note"], got["note"]
    assert "没反应" in got["note"] and "该修" in got["note"], got["note"]


def test_nothing_at_all_is_unverified(tmp_path):
    """既没外部验证也没自测 ⇒ 「未验证」❌（如实说 ✓ 别编 ✓）"""
    ws = _mk(tmp_path, ["index.html"])
    got = classify(ws, {})
    assert got["level"] == "未验证", got
    assert "未验证" in got["note"]


def test_old_test_files_do_not_count_as_self_produced(tmp_path):
    """★ 区分"本次产出"与"早就有的"：老测试文件**不算自证材料** ✓（否则会误判 ✓）"""
    ws = _mk(tmp_path, ["index.html"])
    _mk(ws, ["_test.html"], age_s=86400 * 7)      # 一周前的 ✓
    got = classify(ws, {"ok": False, "skipped_reason": "没装 playwright"})
    assert got["self_tests"] == [], f"老文件被算成自产了 ✗：{got['self_tests']}"
    assert got["level"] == "未验证", got


def test_acceptance_doc_is_not_counted_as_a_test(tmp_path):
    """★ 2026-10-09 用户点名修：`ACCEPTANCE.md` 是**交付文档** ✓ 不许算成"测试文件" ✗

    （原先 `_TESTISH` 里有 "acceptance" 一词 ⇒ 交付提示会写成
      「只跑了自己产出的测试 33 个」✗ —— 把文档也算进去 ⇒ **清单不可信** ✓）
    """
    ws = _mk(tmp_path, ["index.html", "ACCEPTANCE.md", "ACCEPTANCE_CDP.md", "_test.html"])
    got = classify(ws, {"ok": False, "skipped_reason": "没装 playwright"})
    assert "ACCEPTANCE.md" not in got["self_tests"], got["self_tests"]
    assert "ACCEPTANCE_CDP.md" not in got["self_tests"], got["self_tests"]
    assert "_test.html" in got["self_tests"], "真测试文件仍在清单里 ✓"


def test_never_raises_on_missing_workspace(tmp_path):
    """★ 工作区不存在 ⇒ **不许抛** ✗（收尾路径一抛就把交付带崩 ✓）"""
    got = classify(tmp_path / "nope", {"ok": False, "skipped_reason": "x"})
    assert got["level"] == "未验证"
    assert self_produced_tests(tmp_path / "nope") == []
