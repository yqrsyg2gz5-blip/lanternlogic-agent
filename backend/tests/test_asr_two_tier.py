"""Phase 2 ⑤（ASR 两档并列）锚点 —— 全程离线（假 HTTP，不碰真网络、不调真 ASR）。

两档设计：① 云端 MiMo（有 Key 就能用、零下载）② 本地千问3-ASR（离线，需自行安装）。

钉住：
  ① ★ 声明表必须与**代码**一致（本班真实翻车点：capabilities 里写的是 local_qwen3 +
     dashscope，而 asr.py 一直走云端 MiMo —— 表错了，用户看到的状态也就错了）
  ② 云端：没 Key 时的报错必须给**两条路**（填 Key / 装本地），且点名环境变量
  ③ 云端：正常返回能解析（假 HTTP）
  ④ 云端：上游 4xx/5xx 与"返回格式变了"都要说人话（不许把栈丢给用户）
  ⑤ 本地：没装时**诚实说没装 + 怎么装 + 推荐档位**，而且**绝不偷偷回退云端**
     （用户选本地就是为了数据不出本机，偷偷上传是最坏的惊喜）
  ⑥ 未知档位：说清可用档位，不崩
"""
from __future__ import annotations

import asyncio
import pathlib
from types import SimpleNamespace

import httpx
import pytest

from app import asr, capabilities as cap
from app import main as m


def _cfg(provider: str) -> SimpleNamespace:
    return SimpleNamespace(asr=SimpleNamespace(provider=provider, model="mimo-v2.5-asr",
                                               api_key_env="XIAOMI_MIMO_API_KEY"))


def _audio(tmp_path: pathlib.Path) -> pathlib.Path:
    p = tmp_path / "a.wav"
    p.write_bytes(b"RIFF....WAVEfmt ")      # 内容无所谓：云端路径只做 base64
    return p


# ═══ ① 声明表 ↔ 代码 一致（本班的真实翻车点）═══

def test_capability_table_matches_code():
    src = (pathlib.Path(__file__).resolve().parents[1] / "app" / "asr.py").read_text("utf-8")
    declared = cap.CAPABILITIES["asr"]["providers"]
    assert "mimo" in declared, "asr.py 走的是云端 MiMo，声明表却没有这一档"
    assert "dashscope" not in declared, "通义听悟从没实现过，声明表不该列它（用户选了就炸）"
    # 环境变量名必须与代码里读的那个一致
    assert declared["mimo"]["key_env"] == "XIAOMI_MIMO_API_KEY"
    assert "XIAOMI_MIMO_API_KEY" in src, "代码读的 Key 变量名变了？声明表要跟着改"
    assert "api.xiaomimimo.com" in src, "云端地址变了？声明表/文档要跟着改"


def test_asr_current_provider_comes_from_config():
    cfg = SimpleNamespace(asr=SimpleNamespace(provider="local_qwen3"))
    assert cap._current_provider("asr", cfg) == "local_qwen3", "ASR 档位没从配置读（写死了）"
    assert cap._current_provider("asr", SimpleNamespace(asr=SimpleNamespace(provider=""))) == "mimo", \
        "空值应回落到云端默认档"


def test_local_tier_recommendation_matches_machine():
    """本机（RTX 5070Ti 12G / 32G）应推荐 1.7B —— 与 ④ 的档位推荐同一套规则。"""
    assert cap.recommend_local_tier(12, 32)["tier"] == "1.7b"


# ═══ ② 云端没 Key：两条路 ═══

def test_cloud_without_key_offers_both_routes(tmp_path, monkeypatch):
    monkeypatch.delenv("XIAOMI_MIMO_API_KEY", raising=False)
    with pytest.raises(asr.ASRUnavailable) as ei:
        asyncio.run(asr.transcribe(_audio(tmp_path), cfg=_cfg("mimo")))
    msg = str(ei.value)
    assert "XIAOMI_MIMO_API_KEY" in msg, f"没点名环境变量：{msg}"
    assert "①" in msg and "②" in msg, f"没给两条路：{msg}"
    assert "本地" in msg, "没提本地那一档"


# ═══ ③ 云端正常返回（假 HTTP）═══

def test_cloud_parses_response(tmp_path, monkeypatch):
    monkeypatch.setenv("XIAOMI_MIMO_API_KEY", "sk-probe-1234567890")

    def handler(req: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(req.content)
        assert body["model"] == "mimo-v2.5-asr", "模型名不对"
        assert "text" not in __import__("json").dumps(body), "网关规则：请求不得带 text 部分"
        assert req.headers["authorization"].startswith("Bearer ")
        return httpx.Response(200, json={"choices": [{"message": {"content": " 你好世界 "}}]})

    real = httpx.AsyncClient

    class _Fake(real):          # type: ignore[misc,valid-type]
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(asr.httpx, "AsyncClient", _Fake)
    out = asyncio.run(asr.transcribe(_audio(tmp_path), cfg=_cfg("mimo")))
    assert out == "你好世界", f"没解析出文字（或没 strip）：{out!r}"


@pytest.mark.parametrize("status,body,expect", [
    (401, {"error": "bad key"}, "上游错误 401"),
    # ★ 2026-10-07 第 4 项：500 现在**先退避重试**（3 次）再放弃 ✓
    #   ⇒ 报的话从"上游错误 500"变成"上游一直忙（HTTP 500）—— 已重试 3 次"✓
    #   **更好**：用户需要知道的正是"它试过了、不是没试" ✓
    #   （401 不变 ✓ 钥匙错重试没意义 ✓ —— 这条测试的其余两组原样保留 ✓）
    (500, {}, "上游一直忙（HTTP 500"),
    (200, {"unexpected": True}, "格式看不懂"),
])
def test_cloud_failures_speak_human(tmp_path, monkeypatch, status, body, expect):
    monkeypatch.setenv("XIAOMI_MIMO_API_KEY", "sk-probe-1234567890")
    # ★ 退避别真等（2+3 秒）✓ —— 测的是"话说不说得明白" ✓ 不是"等得久不久" ✓
    from app import retry as _retry
    monkeypatch.setattr(_retry, "retry_delay", lambda resp, attempt: 0.0)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    real = httpx.AsyncClient

    class _Fake(real):          # type: ignore[misc,valid-type]
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(asr.httpx, "AsyncClient", _Fake)
    with pytest.raises(asr.ASRUnavailable) as ei:
        asyncio.run(asr.transcribe(_audio(tmp_path), cfg=_cfg("mimo")))
    msg = str(ei.value)
    assert expect in msg, f"报错没说人话：{msg}"
    assert "本地" in msg, "失败时没告诉用户还有本地那条路"


# ═══ ⑤ 本地档：诚实 + 不偷偷回退 ═══

def test_local_tier_says_not_installed_and_never_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("XIAOMI_MIMO_API_KEY", "sk-probe-1234567890")   # 就算有 Key 也不许偷偷用
    called: list = []

    def handler(req: httpx.Request) -> httpx.Response:
        called.append(req.url)
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    real = httpx.AsyncClient

    class _Fake(real):          # type: ignore[misc,valid-type]
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(asr.httpx, "AsyncClient", _Fake)
    # ★★ 2026-10-07 修（红绿门当场抓到的 ✗✗）：**单元测试绝不能真去加载模型** ✗ ——
    #   本机装上 qwen-asr/torch 之后 ✓ 这条会真的开始下几个 GB 的模型 ✗（或久到超时 ✓）
    #   ⇒ 红绿三组一起红（"恢复跑=rc=1" ✓）。现在把加载换成假的 ✓
    #   测的仍然是我们真正要保证的那件事：**本地失败时不偷偷改用云端** ✓。
    def _fake_load(path, tier):                             # noqa: ARG001
        raise asr.ASRUnavailable("假装加载失败（测试用，不碰真模型，也不下载）")

    monkeypatch.setattr(asr, "_local_transcribe_sync", _fake_load)
    with pytest.raises(asr.ASRUnavailable) as ei:
        asyncio.run(asr.transcribe(_audio(tmp_path), cfg=_cfg("local_qwen3")))
    msg = str(ei.value)
    # ★ 2026-10-06 改：本地那档**真接上了**（真去加载 qwen-asr ✓）——
    #   测试的**原意没变**：① 必须给可照做的装法 ✓ ② 绝不回退云端 ✓
    #   （"装法"那条的**细节**改由 `test_asr_local_qwen.py` 管 ✓ 那边会把依赖假扮成没装 ✓
    #    因为真环境里依赖可能已经装上了 ⇒ 在这里断言 pip install 会时灵时不灵 ✗）
    assert "假装加载失败" in msg, f"报错没把真实原因说出来：{msg}"
    assert called == [], "选本地却仍然请求了云端 —— 这就是最坏的惊喜"


# ═══ ⑥ 未知档位 ═══

def test_unknown_tier_lists_the_available_ones(tmp_path):
    with pytest.raises(asr.ASRUnavailable) as ei:
        asyncio.run(asr.transcribe(_audio(tmp_path), cfg=_cfg("不存在的档")))
    msg = str(ei.value)
    assert "未知" in msg and "mimo" in msg and "local_qwen3" in msg, msg


# ═══ ⑦ ★ 本地档位的状态必须是**真探测**出来的（本表第二处自我抓到的谎）═══

def test_local_asr_status_is_probed_not_assumed():
    """第一版把所有 kind=local 一律报 ready ⇒ "本地 ASR 可用"是假的、降级链还指着它。"""
    st = cap.status(m.cfg)["capabilities"]["asr"]
    state = st["providers"]["local_qwen3"]["state"]
    assert state in ("ready", "not_installed"), state
    if state == "not_installed":
        assert "pip install" in st["providers"]["local_qwen3"]["detail"], "说没装就要说怎么装"
        # 没装就不能当"可退的地方"
        assert st["next_available"] != "local_qwen3" or st["providers"]["local_qwen3"]["state"] == "ready"


def test_local_asr_status_turns_ready_when_installed(monkeypatch):
    """★ 2026-10-06 改：探针从 `faster_whisper` 改成 **`qwen_asr`** ✓ ——
    因为**真正会 import 的是它** ✓。

    为什么这条要紧（用户实测问出来的 ✗）：以前探的是 faster-whisper ✓
    而代码里**根本没有加载模型的实现** ✗ ⇒ 用户照提示装完之后
    **界面会显示"✅ 可用"** ✓ 而一点就报错 ✗✓ —— 界面骗人 ✓。
    现在"探什么"与"用什么"是同一个 ✓。
    """
    import importlib.util
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name: object() if name == "qwen_asr" else None)
    st = cap.status(m.cfg)["capabilities"]["asr"]
    assert st["providers"]["local_qwen3"]["state"] == "ready", st["providers"]["local_qwen3"]
    # 反面：只装了 faster-whisper（旧提示那个 ✗）**不该**报可用 ✓
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name: object() if name == "faster_whisper" else None)
    st2 = cap.status(m.cfg)["capabilities"]["asr"]
    assert st2["providers"]["local_qwen3"]["state"] == "not_installed", \
        "装了 faster-whisper 就报'可用' ✗ —— 那正是以前那个骗人的坑 ✓"


# ═══ ⑧ 档位切换端点（校验过再写，不许把表里没有的档位写进配置）═══

def test_switch_asr_tier_validates_and_persists(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(m, "_save_config", lambda: None)      # 别落盘（N15 的规矩）
    for _k, _v in m.cfg.asr.model_dump().items():
        monkeypatch.setattr(m.cfg.asr, _k, _v, raising=False)
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.post("/api/v1/settings/asr", json={"provider": "local_qwen3"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert m.cfg.asr.provider == "local_qwen3", "没写进配置"
        assert body["state"] in ("ready", "not_installed"), body
        assert body.get("note"), "回执必须说清「现在能不能用」"
        bad = c.post("/api/v1/settings/asr", json={"provider": "不存在的档位"})
        assert bad.status_code == 422, bad.text


# ═══ ⑨ 机器档位推荐（探测不到就保守）═══

def test_local_recommendation_is_conservative_when_probe_fails(monkeypatch):
    monkeypatch.setattr(cap, "_detect_hardware", lambda: (0.0, 0.0, "探测不到"))
    rec = cap.local_recommendation()
    assert rec["tier"] == "0.6b" and rec["detected"] is False, rec
    assert "保守" in rec["reason"], rec


def test_local_recommendation_uses_detected_hardware(monkeypatch):
    monkeypatch.setattr(cap, "_detect_hardware", lambda: (12.0, 32.0, "nvidia-smi+GlobalMemoryStatusEx"))
    rec = cap.local_recommendation()
    assert rec["tier"] == "1.7b" and rec["detected"] is True, rec
    assert rec["vram_gb"] == 12.0 and rec["ram_gb"] == 32.0


def test_capabilities_endpoint_carries_the_recommendation():
    from fastapi.testclient import TestClient
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        body = c.get("/api/v1/capabilities").json()
    rec = body["local_recommendation"]
    assert rec["tier"] in ("0.6b", "1.7b", "1.7b-quant"), rec
    assert "reason" in rec and "method" in rec, "推荐必须说清依据（不然用户不知道凭什么）"


# ═══ ⑩ 界面接线：设置页的「语音」栏（脚本存在 ≠ 接上了）═══

_PANEL = (pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "components"
          / "SettingsPanel.tsx").read_text("utf-8")
_API_SRC = (pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "api.ts").read_text("utf-8")


def test_voice_section_is_wired():
    assert "'voice'" in _PANEL or '"voice"' in _PANEL, "Section 类型/NAV 里没有 voice"
    assert "['voice', '语音']" in _PANEL, "侧栏没有「语音」入口"
    assert "section === 'voice'" in _PANEL, "没有语音栏的内容"
    assert "api.getCapabilities" in _PANEL or ".getCapabilities(" in _PANEL, "语音栏没读能力槽状态"
    assert ".setAsrTier(" in _PANEL, "没有切换 ASR 档位的接线"
    assert "voice-state" in _PANEL, "没有状态徽章（用户分不清可用/未安装）"
    assert "local_recommendation" in _PANEL, "没显示本地档位推荐"
    assert "还没做" in _PANEL, "TTS 切换没做就该如实标注，不许装作能切"


def test_api_layer_implements_both_capability_methods():
    assert _API_SRC.count("getCapabilities") >= 3, "声明/实现/离线实现不齐"
    assert _API_SRC.count("setAsrTier") >= 3, "声明/实现/离线实现不齐"
