"""音频格式嗅探（`app/asr.py::sniff_audio_format`）的锚点。

为什么必须有这个文件：这段代码是"语音输入根本不好使"的修复核心之一 ——
**按文件名判断格式会被调用方骗**（前端曾把 wav 命名成 draft.webm），必须按**字节**认。
之前只用 `python -c` 手工验过，没有正式测试 ⇒ 变异测试一跑就会报"幸存者"（等于没测）。
"""
from __future__ import annotations

import pytest

from app.asr import ASRUnavailable, sniff_audio_format


def _mk(tmp_path, name: str, head: bytes):
    p = tmp_path / name
    p.write_bytes(head + b"\x00" * 64)
    return p


def test_wav_is_detected_by_magic_even_with_a_lying_name(tmp_path):
    """★ 真身是 wav、名字叫 .webm —— 这正是前端上传时的真实情形。"""
    p = _mk(tmp_path, "draft.webm", b"RIFF\x00\x00\x00\x00WAVEfmt ")
    assert sniff_audio_format(p) == "wav"


def test_webm_is_detected_even_when_named_wav(tmp_path):
    """反向也要认出来：名字叫 .wav 但内容是 webm ⇒ 报 webm（这样才能给出清楚的错误）。"""
    p = _mk(tmp_path, "draft.wav", b"\x1a\x45\xdf\xa3")
    assert sniff_audio_format(p) == "webm"


@pytest.mark.parametrize("name,head,expect", [
    ("a.mp3", b"ID3\x03\x00\x00", "mp3"),
    ("b.mp3", b"\xff\xfb\x90\x00", "mp3"),          # 无 ID3 的帧同步开头
    ("c.bin", b"OggS\x00\x02", "ogg"),
    ("d.m4a", b"\x00\x00\x00\x20ftypM4A ", "m4a"),
])
def test_other_containers(tmp_path, name, head, expect):
    assert sniff_audio_format(_mk(tmp_path, name, head)) == expect


def test_short_or_empty_file_falls_back_to_suffix(tmp_path):
    """读不出魔数时按后缀兜底（别把空文件判成 wav 就行）。"""
    p = tmp_path / "x.mp3"
    p.write_bytes(b"")
    assert sniff_audio_format(p) == "mp3"


def test_missing_file_does_not_crash(tmp_path):
    assert sniff_audio_format(tmp_path / "nope.wav") == "wav"


@pytest.mark.asyncio
async def test_cloud_path_refuses_non_wav_mp3_with_an_actionable_message(tmp_path, monkeypatch):
    """★ 网关只收 wav/mp3：要在**本地**就说清楚，别转发上游那句难懂的 400。"""
    from app import asr

    monkeypatch.setenv("DSH_ASR_TEST_KEY", "sk-test-not-a-real-key")
    p = _mk(tmp_path, "draft.webm", b"\x1a\x45\xdf\xa3")
    with pytest.raises(ASRUnavailable) as ei:
        await asr._cloud_mimo(p, "sk-test-not-a-real-key", "mimo-v2.5-asr")
    msg = str(ei.value)
    assert "只收 wav/mp3" in msg and "ffmpeg" in msg, msg
    assert "浏览器内置录音" in msg, "没告诉用户怎么绕过去"


# ═══ 配置/环境变量回退（第三期变异测试指出：这几条分支此前一条测试都没有）═══
# 5 个"存活变异体"全部落在这里 —— 只测了"传了 cfg"那条路，`cfg=None` 时靠环境变量的
# 那条路没人管（而预检/CLI 正是走那条路）。下面每条都对着一个具体条件。

class _Cfg:
    class asr:  # noqa: N801
        provider = ""
        model = ""
        api_key_env = ""


def test_provider_env_fallback(monkeypatch):
    from app import asr
    monkeypatch.setenv("AGENT_SHELL_ASR_PROVIDER", "local_qwen3")
    assert asr._provider(None) == "local_qwen3", "环境变量设了却不认（cfg=None 那条路）"
    monkeypatch.delenv("AGENT_SHELL_ASR_PROVIDER", raising=False)
    assert asr._provider(None) == "mimo", "没设环境变量时该回落到默认 mimo"


def test_provider_cfg_wins_and_empty_cfg_falls_back(monkeypatch):
    from app import asr

    class C:
        class asr:  # noqa: N801
            provider = "local_qwen3"

    assert asr._provider(C()) == "local_qwen3"
    assert asr._provider(_Cfg()) == "", "cfg 显式给了空串时按空返回（是否回落到默认由调用方决定）"


def test_model_env_fallback(monkeypatch):
    from app import asr
    monkeypatch.setenv("AGENT_SHELL_ASR_MODEL", "my-asr-model")
    assert asr._model(None) == "my-asr-model"
    monkeypatch.delenv("AGENT_SHELL_ASR_MODEL", raising=False)
    assert asr._model(None) == "mimo-v2.5-asr"


def test_model_cfg_branch_and_empty_fallback():
    """★ 与 `_provider` 刻意不同：模型名**空了要回落到默认**（provider 空则返回空，
    由调用方决定怎么兜）—— 这个不对称正是变异测试指出的幸存点。"""
    from app import asr

    class C:
        class asr:  # noqa: N801
            model = "custom-model"

    assert asr._model(C()) == "custom-model", "cfg 给了模型名却不用"
    assert asr._model(_Cfg()) == "mimo-v2.5-asr", "cfg 里的模型名是空串时该回落到默认"


def test_key_env_fallback(monkeypatch):
    from app import asr
    monkeypatch.setenv("AGENT_SHELL_ASR_KEY_ENV", "MY_ASR_KEY")
    assert asr._key_env(None) == "MY_ASR_KEY", "环境变量设了却不认"
    monkeypatch.delenv("AGENT_SHELL_ASR_KEY_ENV", raising=False)
    assert asr._key_env(None) == "XIAOMI_MIMO_API_KEY", "默认 Key 变量名不对"


def test_key_env_from_cfg(monkeypatch):
    from app import asr

    class C:
        class asr:  # noqa: N801
            api_key_env = "CUSTOM_KEY"

    assert asr._key_env(C()) == "CUSTOM_KEY"
