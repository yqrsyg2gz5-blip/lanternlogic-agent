# -*- coding: utf-8 -*-
"""★ 2026-10-06 本地 ASR 真接上：**千问3-ASR**（用户拍板选它 ✓ 不用 Whisper ✗）。

## 用户的三句话（这就是验收标准 ✓）

> "当然就是用千问3这个 ASR 了，因为第一个这个中文的……
>  显存没有或者显存少的话，它可以用小米这个……显存够的话，就可以用这个千问3这个，
>  因为毕竟开源嘛……是不是就这个选千问三不用那个 OpenAI 的"

## 改之前是什么样（✗✗ 比"名字对不上"严重）

| 事实 | 后果 |
|---|---|
| 安装指引写 `pip install faster-whisper` ✗（那是 **OpenAI Whisper** 的社区实现 ✓）| 用户装的是**另一个东西** ✗ |
| **没有任何加载模型的代码** ✗（`local_qwen3` 分支无条件抛"还没装"）| **装什么都没用** ✗ |
| 能力探针探的是 `faster_whisper` ✗ | 用户照提示装完 ⇒ 界面显示 **"✅ 可用"** ✓ 而一点就报错 ✗✓ |

⇒ 最后那条是**最坏的**：正是本项目一直在避免的"界面骗人" ✓。

## 改之后（✓）

· 探针探 **`qwen_asr`** ✓ —— **探什么就是用什么** ✓
· 装法写**官方包** ✓ `pip install -U qwen-asr`（Apache-2.0 ✓ 可商用 ✓）
· **真实现转写** ✓（走官方 `Qwen3ASRModel.from_pretrained` + `.transcribe` ✓ 照抄官方 Quick Inference ✓）
· 档位收敛成 **0.6b / 1.7b** ✓（官方只发这两个 ✗ "1.7b-quant" 是我们编的 ✓ 删掉 ✓）
· 模型加载**进程内缓存** ✓（加载要几十秒 ✓ 每次重载等于不可用 ✗）
· 加载丢**线程池** ✓（同步阻塞会卡死整个后端 ✗）
· **绝不偷偷回退云端** ✓（用户选本地就是为了数据不出本机 ✓）
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import asr  # noqa: E402
from app import capabilities as cap  # noqa: E402


def _msg(fn, *a, **kw) -> str:
    with pytest.raises(asr.ASRUnavailable) as ei:
        fn(*a, **kw)
    return str(ei.value)


def test_install_hint_points_at_the_qwen_package_not_whisper():
    """**装法必须指向真会用的那个包** ✓ —— 这条就是那个 bug 的守卫 ✓。"""
    hint = asr.LOCAL_INSTALL_HINT
    assert "qwen-asr" in hint, f"安装指引没指向官方包 ✗：{hint[:80]}"
    assert "faster-whisper" not in hint, "还留着 faster-whisper（那是 OpenAI Whisper ✗ 跟千问无关）"
    assert "faster_whisper" not in hint


def test_tiers_are_only_the_two_official_sizes():
    """档位只留官方真实存在的两个 ✓（0.6B / 1.7B ✓）—— "量化"那个是我们编的 ✗。"""
    assert asr.LOCAL_TIERS == ("0.6b", "1.7b"), asr.LOCAL_TIERS
    assert asr.LOCAL_MODELS == {"0.6b": "Qwen/Qwen3-ASR-0.6B", "1.7b": "Qwen/Qwen3-ASR-1.7B"}, \
        asr.LOCAL_MODELS
    assert {t["id"] for t in cap.LOCAL_TIERS} == {"0.6b", "1.7b"}, "能力表里还留着不存在的规格 ✗"


def test_local_tier_defaults_to_the_small_one():
    """默认档取 0.6b ✓（省显存、快、中文只差约 1 个点 ✓）；认不出来也退回 0.6b ✓（先能用 ✓）。"""
    class _C:
        class asr:                                    # noqa: N801
            local_tier = ""
    assert asr.local_tier_of(_C()) == "0.6b"
    _C.asr.local_tier = "1.7b"
    assert asr.local_tier_of(_C()) == "1.7b"
    _C.asr.local_tier = "1.7b-quant"                  # 官方没有这个 ✗
    assert asr.local_tier_of(_C()) == "0.6b", "认不出来的档位该退回 0.6b ✓"


def test_local_transcribe_really_loads_qwen_model(monkeypatch, tmp_path):
    """**真去加载千问模型** ✓（用假的 `qwen_asr` 与 `torch` 验调用链 ✓ 不真下模型 ✓）。

    这条钉住"代码真的会走到加载那一步" ✓ —— 改之前那个分支**只抛异常** ✗
    ⇒ 装什么都没用 ✓（就是靠这条测试才能防止再退回去 ✓）。
    """
    calls: dict[str, object] = {}

    class _FakeModel:
        @staticmethod
        def from_pretrained(model_id, **kw):           # noqa: ARG004
            calls["model_id"] = model_id
            calls["kwargs"] = kw
            return _FakeModel()

        @staticmethod
        def transcribe(audio, language=None):          # noqa: ARG004
            calls["audio"] = audio
            return [type("R", (), {"language": "Chinese", "text": "你好，这是一段测试"})()]

    fake_mod = type(sys)("qwen_asr")
    fake_mod.Qwen3ASRModel = _FakeModel                     # type: ignore[attr-defined]
    fake_torch = type(sys)("torch")
    fake_torch.cuda = type("c", (), {"is_available": staticmethod(lambda: False)})()  # type: ignore[attr-defined]
    fake_torch.bfloat16 = "bf16"                            # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "qwen_asr", fake_mod)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    asr._MODEL_CACHE.clear()

    got = asr._local_transcribe_sync(tmp_path / "a.wav", "0.6b")
    assert got == "你好，这是一段测试", got
    assert calls["model_id"] == "Qwen/Qwen3-ASR-0.6B", calls
    assert str(calls["audio"]).endswith("a.wav"), calls
    # 第二次必须走缓存 ✓（不然每次转写都重载模型 ⇒ 实际不可用 ✗）
    calls.pop("model_id", None)
    asr._local_transcribe_sync(tmp_path / "a.wav", "0.6b")
    assert "model_id" not in calls, "第二次还在重新加载模型 ✗（必须缓存 ✓）"


def test_local_failure_is_actionable_and_never_silently_uploads(monkeypatch, tmp_path):
    """本地失败 ⇒ ① 报错**可照做** ✓ ② **绝不偷偷回退云端** ✓。

    ★★ 2026-10-07 修（红绿门当场抓到的 ✗✗）：这条原来**会真的去加载模型** ✗ ——
      本机装上 torch/qwen-asr 之后 ✓ 它就会**真的开始下一份几个 GB 的模型** ✗✓
      （或者久到超时 ✓）⇒ 红绿跑出"恢复跑=rc=1" ✓ 三条组一起红 ✗。
      **单元测试绝不能碰真网络/真下载** ✗ —— 现在把加载那一步**换成假的失败** ✓
      这样测的正是我们真正要保证的东西：**失败时的行为** ✓（而不是模型好不好用 ✓）。
    """
    called: list[str] = []

    async def _boom(*a, **kw):                              # noqa: ARG001
        called.append("cloud")
        return "不该走到这儿"

    def _fake_load(path, tier):                             # noqa: ARG001
        raise asr.ASRUnavailable("假装模型加载失败（测试用，不碰真模型）")

    monkeypatch.setattr(asr, "_cloud_mimo", _boom)
    monkeypatch.setattr(asr, "_local_transcribe_sync", _fake_load)   # ★ 不碰真模型 ✓
    asr._MODEL_CACHE.clear()

    class _C:
        class asr:                                          # noqa: N801
            provider = "local_qwen3"
            local_tier = "0.6b"
    msg = _msg(lambda: asyncio.run(asr.transcribe(tmp_path / "a.wav", cfg=_C())))
    assert "假装模型加载失败" in msg, f"报错没把真实原因说出来 ✗：{msg[:120]}"
    assert called == [], "本地失败却偷偷请求了云端 —— 这是最坏的惊喜 ✗"


def test_local_load_failure_carries_the_install_hint(monkeypatch, tmp_path):
    """**没装依赖时**的报错必须带装法 ✓（这条才是"可照做"那条 ✓ 而且不碰真模型 ✓）。"""
    asr._MODEL_CACHE.clear()

    class _C:
        class asr:                                          # noqa: N801
            provider = "local_qwen3"
            local_tier = "0.6b"

    real_import = __import__

    def _fake_import(name, *a, **kw):
        # 假扮"依赖没装" ✓ —— 真环境里它可能已经装上了 ✓ 那样就测不到这条退路 ✗
        if name in ("qwen_asr", "torch"):
            raise ModuleNotFoundError(f"No module named {name!r}")
        return real_import(name, *a, **kw)

    monkeypatch.setattr("builtins.__import__", _fake_import)
    msg = _msg(lambda: asyncio.run(asr.transcribe(tmp_path / "a.wav", cfg=_C())))
    assert "qwen-asr" in msg and "pip install" in msg, f"没给装法 ✗：{msg[:160]}"


def test_probe_and_implementation_use_the_same_package():
    """★★ **探什么 = 用什么** ✓ —— 这条是那个"界面骗人"坑的总守卫 ✓。

    探针探的包名必须出现在实现里 ✓；而**不能**是 faster_whisper ✗。
    """
    src = pathlib.Path(cap.__file__).read_text("utf-8")
    probe = pathlib.Path(asr.__file__).read_text("utf-8")
    assert 'find_spec("qwen_asr")' in src, "探针没探 qwen_asr ✗"
    # ★ 只看**真代码**：注释里写"以前探的是 faster_whisper"是**历史说明** ✓ 不算 ✗
    live = [ln.strip() for ln in src.splitlines()
            if "find_spec" in ln and not ln.strip().startswith("#")]
    assert all("qwen_asr" in ln for ln in live), f"探针还在探别的包 ✗：{live}"
    assert not any("faster_whisper" in ln for ln in live), "探针又去探 faster_whisper 了 ✗"
    assert "from qwen_asr import Qwen3ASRModel" in probe, "实现里没 import 官方类 ✗"
