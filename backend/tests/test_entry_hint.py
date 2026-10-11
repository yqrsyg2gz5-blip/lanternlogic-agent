# -*- coding: utf-8 -*-
"""交付入口提示 —— 用**今天的真实文件名**当考卷 ✓

考卷来源（2026-10-09 用户实测的工作区 ✓ 一字不改地抄进来 ✓）：
    210,613 B  _s_dashboard.png
    114,919 B  _clicktest_dom.html
    114,902 B  _clicktest_desktop.html
    113,650 B  _test_dom.html
    107,842 B  _clicktest.html
    105,665 B  _probetest_dom.html
    104,451 B  _test.html
    103,698 B  _index_dom.html
     99,870 B  _probetest.html
     98,234 B  index.html        ← ★ 真成品（唯一不带下划线的 ✓）
用户当时点进了其中一个测试文件 ⇒ "点啥都不动" ✗
⇒ 本文件锁住：**必须选中 index.html** ✓ 且**必须把其余 9 个列进"不用管"** ✓
"""
from __future__ import annotations

from app.entry_hint import suggest_entry

REAL_WORKSPACE = [
    "_s_dashboard.png", "_clicktest_dom.html", "_clicktest_desktop.html", "_test_dom.html",
    "_clicktest.html", "_probetest_dom.html", "_test.html", "_index_dom.html",
    "_probetest.html", "index.html", "README.md", "ACCEPTANCE.md",
]


def _mk(tmp_path, names):
    for n in names:
        (tmp_path / n).write_text("<html></html>", encoding="utf-8")
    return tmp_path


def test_picks_index_out_of_todays_real_mess(tmp_path):
    """★ 核心用例：今天那 10 个文件里，必须选中 index.html ✓"""
    ws = _mk(tmp_path, REAL_WORKSPACE)
    got = suggest_entry(ws)
    assert got["entry"] == "index.html", f"选错了入口：{got['entry']}"
    # ★ 查"意图"不查死数字 ✗：今天那 9 个必须**全部**被认出来 ✓
    #   （第一版我写死 == 9 ✗ 结果名字清单多一点就误报 ✓ —— 断言该盯"该认的都认了"✓）
    for n in ("_test.html", "_clicktest_dom.html", "_probetest.html", "_index_dom.html"):
        assert n in got["test_like"], f"{n} 没被认成测试文件 ✗"
    assert "index.html" not in got["test_like"]
    assert "不用管" in got["note"] and "沙箱" in got["note"], "交付提示必须同时讲清'点哪个'和'预览为什么点不动'"


def test_png_and_md_are_not_html_candidates(tmp_path):
    """图片/文档不算网页入口 ✓（今天那目录里还有 png 和 md ✓）"""
    ws = _mk(tmp_path, ["_s_ledger.png", "README.md", "index.html"])
    got = suggest_entry(ws)
    assert got["entry"] == "index.html"
    assert not got["test_like"], got["test_like"]


def test_clean_delivery_has_no_noise_words(tmp_path):
    """只有一个入口时 ⇒ 提示里不出现"另有 N 个"✓（别自己制造噪音 ✗）"""
    ws = _mk(tmp_path, ["index.html"])
    got = suggest_entry(ws)
    assert got["entry"] == "index.html"
    assert "另有" not in got["note"]
    assert "沙箱" in got["note"]


def test_all_test_like_still_suggests_one_honestly(tmp_path):
    """全是不带下划线但含 test 的 ⇒ 仍给一个候选 ✓（不返回空 ✓ 但会列进 test_like ✓）"""
    ws = _mk(tmp_path, ["_test.html", "clicktest.html"])
    got = suggest_entry(ws)
    assert got["entry"], "不该返回空 —— 用户总得有个能点的 ✓"
    # 全是测试样名字 ⇒ 仍要给出一个能点的 ✓ 且提示里要说实话 ✓
    assert got["note"].strip(), "必须给一句话 ✓"


def test_missing_dir_is_silent(tmp_path):
    """目录不存在 ⇒ 如实返回空 ✓ **不许抛** ✓（它是收尾路径上跑的 ✓ 不能把任务带崩 ✗）"""
    got = suggest_entry(tmp_path / "nope")
    assert got == {"entry": "", "test_like": [], "others": [], "note": ""}


def test_prefers_index_over_shorter_names(tmp_path):
    """`app.html` 比 `index.html` 短 ✗ 但 index 优先（约定俗成 ✓）"""
    ws = _mk(tmp_path, ["app.html", "index.html"])
    assert suggest_entry(ws)["entry"] == "index.html"
