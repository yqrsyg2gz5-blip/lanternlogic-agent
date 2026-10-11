# -*- coding: utf-8 -*-
"""MeloTTS 的 nltk 噪音红绿（2026-10-10 实测抓出来的）。

事实链（别改成猜的）：
    melo.text.english 模块级 `_g2p = G2p()`
      ⇒ g2p_en/g2p.py 里两句**无条件**的
            nltk.download('averaged_perceptron_tagger')
            nltk.download('cmudict')
      ⇒ 而 `nltk.download` **先联网拉包索引**（raw.githubusercontent.com）
      ⇒ 本仓的 URL 守卫（pathsec）按设计把它挡掉
      ⇒ 每次 melo 一 import，日志就多两行
            [nltk_data] Error loading averaged_perceptron_tagger: <urlopen error ...>
        看着像坏了 ✗ 其实无害（数据本地早就有 ✓）

判据：
    · 本地**已经找得到**的包 ⇒ 不许再联网（download 降级成空操作）
    · 本地**没有**的包       ⇒ 照旧走真下载（不掩盖缺件 ✓ 不改变失败语义 ✓）
    · 只有 g2p_en 要的那两个包走静音，别的包一律不动
"""
from __future__ import annotations

import pytest

# ★ 2026-10-10（CI 当场抓到的 ✗ —— 正是"本地能跑 ≠ 别人能跑"那条）：
#   `nltk` 是**可选**依赖（随 MeloTTS 一起装 ✓ 本机有、CI 的干净环境没有 ✗）。
#   原来在模块顶层裸写 `import nltk` ⇒ CI 在**收集阶段**就 ImportError：
#       ModuleNotFoundError: No module named 'nltk'
#   ⇒ 整轮 pytest 收集失败（2068 收集 + 1 error ⇒ 退出码 1）✗
#   ⇒ 改成"没装就**如实跳过**" ✓ —— 与"缺件就说缺件、不假装通过"同一个口径 ✓
nltk = pytest.importorskip("nltk", reason="没装 nltk（可选依赖，随 MeloTTS 一起装）—— 这条红线在本机无从谈起")


def _quiet(monkeypatch, tmp_path, calls: list):
    """装好"假 nltk 环境"再调用被测函数（每个用例都从干净状态开始）。"""
    from app.tts import MeloTTSBackend

    monkeypatch.setattr(nltk.data, "path", [str(tmp_path)])
    monkeypatch.setattr(nltk, "download", lambda *a, **k: calls.append(a) or True)
    monkeypatch.delattr(nltk, "_lanternlogic_quiet_download", raising=False)
    MeloTTSBackend._quiet_nltk_downloads()
    return MeloTTSBackend


def test_already_present_package_is_not_downloaded(monkeypatch, tmp_path):
    """★ 核心：本地有 cmudict ⇒ 一次网络都不该发。"""
    (tmp_path / "corpora" / "cmudict").mkdir(parents=True)
    calls: list = []
    _quiet(monkeypatch, tmp_path, calls)

    assert nltk.download("cmudict") is True
    assert calls == [], "本地已经有 cmudict 了，却还是去联网要了一遍"


def test_missing_package_still_really_downloads(monkeypatch, tmp_path):
    """反面：本地没有 ⇒ 必须照旧真下载（不许把缺件掩盖成"成功"）。"""
    calls: list = []
    _quiet(monkeypatch, tmp_path, calls)

    assert nltk.download("cmudict") is True
    assert calls == [("cmudict",)], "本地没有 cmudict 时必须照旧真下载"


def test_other_packages_are_untouched(monkeypatch, tmp_path):
    """只有 g2p_en 要的那两个包静音，别的包（如 punkt）不许受影响。"""
    calls: list = []
    _quiet(monkeypatch, tmp_path, calls)

    nltk.download("punkt")
    nltk.download("wordnet", quiet=True)
    assert calls == [("punkt",), ("wordnet",)], f"别的包被误伤了：{calls}"
