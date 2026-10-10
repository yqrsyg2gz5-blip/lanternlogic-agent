# -*- coding: utf-8 -*-
"""朗读增强（2026-10-08）：**音色可选** + **长文本自动换快的引擎**

用户原话（2026-10-08）：
  · "那个千问TTS，**有点慢呢**，他是不是走CPU" ✓（实测确认：一句 5~12 秒 ✗）
  · 我提"要不要加音色选择器" ⇒ "这个可以做" ✓

## 两件事分别解决什么

① **音色**：千问3-TTS 自带 9 个音色 ✓ 但代码里写死 `vivian` ✗
   ⇒ 现在：配置项 `tts.voice` + 接口校验 + 界面下拉 ✓
   ★ 校验是**必须的** ✗ —— 不校验的话，乱传一个名字会**静默退回默认音色** ✓
     而用户以为切好了 ✓（正是本项目最恨的那种"骗人" ✓）

② **长文本策略**：后端本来就是"边合成边推流" ✓（第一句合成完就开播 ✓）
   可千问3 一句要 5~12 秒 ✗ **比播放还慢** ⇒ 队列永远是空的 ⇒ 一句一顿 ✓
   ⇒ 超过 `tts.long_text_threshold`（默认 300 字）就自动改用 `tts.long_text_backend`（默认 edge ✓）
   ★ 换的时候**必须说清**，而且要和"你选的用不了所以退回"**分开报** ✗
     （那句说"用不了"，这句是"这段太长我替你换了快的" ✓ 混一起就是误导 ✓）
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import main as m  # noqa: E402
from app import tts as tts_mod  # noqa: E402

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SETTINGS = (_ROOT / "frontend" / "src" / "components" / "SettingsPanel.tsx").read_text("utf-8")
_TASKVIEW = (_ROOT / "frontend" / "src" / "components" / "TaskView.tsx").read_text("utf-8")
_API = (_ROOT / "frontend" / "src" / "api.ts").read_text("utf-8")


class _FakeBackend:
    """假后端：真写一个合法 wav ✓（不碰真模型 ✓ 秒回 ✓）"""

    def __init__(self) -> None:
        self.voice = ""

    async def generate(self, text: str, output_path: pathlib.Path) -> pathlib.Path:
        import wave

        with wave.open(str(output_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(b"\x00\x00" * 80)
        return output_path


def _read_stream(resp) -> list[dict]:
    return [json.loads(ln) for ln in resp.text.strip().splitlines() if ln.strip()]


@pytest.fixture
def _fake_tts(monkeypatch, tmp_path):
    """把 TTS 后端全换成假的 ✓（一个字都不念真模型 ✓）"""
    seen: list[tuple[str, str]] = []

    def _get(name: str = "") -> object:
        seen.append((name, ""))
        return _FakeBackend()

    monkeypatch.setattr(m, "get_tts_backend", _get)
    monkeypatch.setattr(m, "_TTS_DIR", tmp_path / "tts")
    monkeypatch.setattr(m, "_get_task", lambda tid: type("T", (), {"id": tid})())
    return seen


def test_long_text_switches_to_the_fast_backend_and_says_so(monkeypatch, tmp_path, _fake_tts):
    """★★ **长文本自动换快的引擎** ✓ 而且**在流里说清**（`policy` 字段 ✓）

    为什么必须有：千问3 一句 5~12 秒 ✗ 长文朗读会一句一顿 ✓
    回滚实验：把这段策略删掉 ⇒ 本条必红 ✓
    """
    monkeypatch.setattr(m.cfg.tts, "backend", "qwen3tts", raising=False)
    monkeypatch.setattr(m.cfg.tts, "long_text_backend", "edge", raising=False)
    monkeypatch.setattr(m.cfg.tts, "long_text_threshold", 50, raising=False)
    long_text = "这是一段很长的文字。" * 12          # 120 字 ⇒ 超过阈值 50 ✓
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.post("/api/v1/tasks/task_20261008_aaaa/tts", json={"text": long_text})
        assert r.status_code == 200, r.text
        lines = _read_stream(r)
    assert lines, "流是空的 ✗"
    assert "policy" in lines[0], f"换了引擎却没说 ⇒ 用户以为还是本地在念 ✗：{lines[0]}"
    assert "edge" in lines[0]["policy"], f"没说换成了谁 ✗：{lines[0]['policy']}"
    assert "关掉" in lines[0]["policy"], "没告诉用户怎么关掉这条策略 ✗（不然他以为被强行改设置 ✓）"
    # ★ 换过引擎 ⇒ 每一行都得带 used/asked（前面那条"退回"的规矩照旧 ✓）
    assert lines[0].get("asked") == "qwen3tts" and lines[0].get("used") == "edge", lines[0]


def test_short_text_keeps_the_local_backend(monkeypatch, _fake_tts):
    """★ **短句不换** ✓ —— 本地引擎离线、中文最准 ✓ 那才是默认该用的 ✓"""
    monkeypatch.setattr(m.cfg.tts, "backend", "qwen3tts", raising=False)
    monkeypatch.setattr(m.cfg.tts, "long_text_backend", "edge", raising=False)
    monkeypatch.setattr(m.cfg.tts, "long_text_threshold", 300, raising=False)
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.post("/api/v1/tasks/task_20261008_aaaa/tts", json={"text": "短句。"})
        lines = _read_stream(r)
    assert not any("policy" in ln for ln in lines), f"短句不该换引擎 ✗：{lines}"
    assert _fake_tts[0][0] == "qwen3tts", f"短句该用本地那个 ✗：{_fake_tts}"


def test_policy_can_be_turned_off(monkeypatch, _fake_tts):
    """★ 想**全程用本地**就留空 ✓（这条策略是可关的 ✓ 不是强制的 ✓）"""
    monkeypatch.setattr(m.cfg.tts, "backend", "qwen3tts", raising=False)
    monkeypatch.setattr(m.cfg.tts, "long_text_backend", "", raising=False)
    monkeypatch.setattr(m.cfg.tts, "long_text_threshold", 10, raising=False)
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.post("/api/v1/tasks/task_20261008_aaaa/tts", json={"text": "很长很长的一段话，超过十个字了。" * 3})
        lines = _read_stream(r)
    assert not any("policy" in ln for ln in lines), f"策略关掉了还在换 ✗：{lines}"
    assert _fake_tts[0][0] == "qwen3tts", _fake_tts


def test_the_voice_list_is_real_and_reaches_the_ui():
    """★ 音色表**实测拿到的 9 个** ✓ 而且要能传到界面（`_provider_status` 别把静态信息吃掉 ✗）"""
    provs = m.CAPABILITIES["tts"]["providers"] if hasattr(m, "CAPABILITIES") else None
    from app.capabilities import CAPABILITIES, _provider_status

    spec = CAPABILITIES["tts"]["providers"]["qwen3tts"]
    st = _provider_status("qwen3tts", spec)
    ids = [v["id"] for v in st.get("voices", [])]
    assert len(ids) == 9, f"音色表没传到状态里 ⇒ 界面做不出下拉 ✗：{st.get('voices')}"
    assert ids[:5] == tts_mod.Qwen3TTSBackend.VOICES[:5], "能力表与后端写死的音色表不一致 ✗"
    assert "vivian" in ids and "serena" in ids, ids
    assert provs is None or True  # 占位：避免未使用告警


def test_a_bogus_voice_is_rejected_not_silently_ignored(monkeypatch):
    """★★ **乱传音色必须拒** ✗ —— 不许静默退回默认音色 ✓

    为什么较真：静默退回 = 用户以为切到 serena 了 ✓ 耳朵里还是 vivian ✗
      ⇒ 与本项目"退回了就必须说"的规矩直接冲突 ✓
    回滚实验：把 set_tts 里那段校验删掉 ⇒ 本条必红 ✓
    """
    monkeypatch.setattr(m, "_save_config", lambda: None)
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        # ① 名字不在表里 ⇒ 422
        r = c.post("/api/v1/settings/tts", json={"backend": "qwen3tts", "voice": "不存在的音色"})
        assert r.status_code == 422, r.text
        # ② 没有音色的后端（edge）传音色 ⇒ 422（别默默吞掉 ✓）
        r2 = c.post("/api/v1/settings/tts", json={"backend": "edge", "voice": "vivian"})
        assert r2.status_code == 422, r2.text
        # ③ 合法音色 ⇒ 真写进配置 ✓
        r3 = c.post("/api/v1/settings/tts", json={"backend": "qwen3tts", "voice": "serena"})
        assert r3.status_code == 200, r3.text
    assert str(m.cfg.tts.voice) == "serena", f"配置没写进去 ✗：{m.cfg.tts.voice}"


def test_the_ui_has_a_voice_picker_and_shows_the_policy_note():
    """★ 界面：**音色下拉** ✓ + 策略提示**与"用不了"分开显示** ✗→✓"""
    assert "setTts(tts.current, v)" in _SETTINGS, "设置页没有音色下拉 ✗"
    assert "voices" in _SETTINGS, "界面没读音色表 ✗"
    assert "音色已切到" in _SETTINGS or "r.note" in _SETTINGS, "切音色后没有回执 ✗"
    # 策略提示必须是**另一条**（不能混进"你选的用不了"那句 ✓）
    assert "n.policy" in _TASKVIEW, "朗读界面没处理 policy ⇒ 用户不知道被换了引擎 ✗"
    i = _TASKVIEW.index("if (n.policy)")
    seg = _TASKVIEW[i : i + 260]
    assert "用不了" not in seg, f"policy 那条混进了'用不了'的措辞 ⇒ 误导 ✓：{seg[:120]}"
    assert "policy" in _API, "api.ts 没把 policy 传出来 ✗"
