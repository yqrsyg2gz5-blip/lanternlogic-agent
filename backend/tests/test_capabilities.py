"""Phase 2 ④（统一「能力槽 + 提供者」）的锚点。

这一批的价值全在"**不许再漂移**"上：以前"某能力能用哪些提供者"散在四个模块里，
谁也不知道有没有对齐。本文件把这些对齐关系变成断言：

  ① 代码里**真能构造**的提供者（`providers._REGISTRY`）必须都在声明表里
     —— 或者被**显式**列进"别名/通用"白名单（逼着做一次有意识的选择，而不是漏掉）
  ② 出视频的可用集合必须与 `main._VIDEO_PROVIDERS` 一致（同一份事实两处读）
  ③ 配置里"现在选的是谁"必须在声明表里存在（配置漂移 → 红）
  ④ 降级链必须是本能力提供者的子集，且**终点是本地或离线**（云端全挂也得能用）
  ⑤ 状态翻译正确：本地/mock = ready；云端无 Key = needs_key；有 Key = ready
  ⑥ ★ 响应里**绝不出现任何 Key 的值**（只报"设没设"）
  ⑦ 机器档位推荐（显存/内存 → 0.6B / 1.7B / 量化）是纯函数，边界可测
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app import capabilities as cap
from app import main as m
from app.providers import _REGISTRY

# 这些是"通用/别名"入口：它们不是给用户选的独立服务商，而是同一条通道的不同名字
# （anthropic=claude 的正式名、openai_compatible=任意 OpenAI 兼容端点、mock=演示）。
# 显式列出来，是为了让"新加了一个提供者却忘了进声明表"这件事**必然**被发现。
_ALIAS_OR_GENERIC = {"anthropic", "openai_compatible"}


def _walk(node, path="$"):
    if isinstance(node, dict):
        for k, v in node.items():
            yield path, str(k)
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, str(node)


# ═══ ① 代码里真能构造的提供者，必须在声明表里（或显式白名单）═══

def test_every_constructible_chat_provider_is_declared():
    declared = set(cap.CAPABILITIES["chat"]["providers"])
    missing = set(_REGISTRY) - declared - _ALIAS_OR_GENERIC
    assert not missing, (
        f"这些提供者代码能构造、但没进能力声明表：{sorted(missing)}"
        "（新加提供者时请同步 app/capabilities.py，否则界面/诊断看不到它）")


def test_declared_chat_providers_all_exist_in_registry():
    """反向：声明表里写了、代码却造不出来 —— 用户选了就炸（比漏声明更糟）。"""
    declared = set(cap.CAPABILITIES["chat"]["providers"])
    unknown = declared - set(_REGISTRY)
    assert not unknown, f"声明表里有代码不认识的提供者：{sorted(unknown)}"


# ═══ ② 出视频：与 main 的集合一致（同一份事实，不许两处各写各的）═══

def test_video_providers_match_main():
    declared = set(cap.CAPABILITIES["video"]["providers"])
    real = {p for p in m._VIDEO_PROVIDERS if p}          # 去掉空串（= 未启用）
    assert declared >= real, f"main 支持但声明表没有：{sorted(real - declared)}"
    assert real >= declared - {"comfyui"}, \
        f"声明表里有 main 不认的云端引擎：{sorted(declared - real - {'comfyui'})}"


# ═══ ③ 配置漂移 ═══

def test_current_selection_exists_in_table():
    st = cap.status(m.cfg)
    for c, info in st["capabilities"].items():
        cur = info["current"]
        if not cur:
            assert info["current_state"] == "unset", f"{c}: 没选提供者却报 {info['current_state']}"
            continue
        assert cur in info["providers"], \
            f"{c} 的当前提供者 {cur!r} 不在声明表里（配置漂移）——可用：{sorted(info['providers'])}"


def test_tts_claim_matches_settings_endpoint():
    """★ 2026-10-06 改：以前这条盯的是"设置接口把它硬编码成 melotts，声明表也得跟着说 melotts" ✗
    —— 那是在**保住一致的口径** ✓ 而口径本身是**错的** ✗（实际跑的是 edge ✓）。

    现在两处都从 `config.tts.backend` 读 ✓ ⇒ 这条测试的**意义升级**了：
    它保证"**界面显示的后端 = 真正会用的后端**" ✓（这才是用户在乎的那件事 ✓）。
    """
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        real = c.get("/api/v1/settings").json()["tts"]["backend"]
    assert cap._current_provider("tts", m.cfg) == real, \
        f"声明表说 TTS 是 {cap._current_provider('tts', m.cfg)}，设置接口说 {real}"
    # 而且必须等于**配置里那个** ✓（不是某一处写死的常量 ✗）
    assert real == str(m.cfg.tts.backend), \
        f"设置接口报的 {real} ≠ 配置里的 {m.cfg.tts.backend} ⇒ 又有人写死了 ✗"


# ═══ ④ 降级链契约 ═══

def test_fallbacks_are_subset_and_end_local():
    for c, spec in cap.CAPABILITIES.items():
        fbs = spec.get("fallbacks", [])
        assert fbs, f"{c} 没有降级链（云端挂了就彻底不能用）"
        assert set(fbs) <= set(spec["providers"]), f"{c} 的降级链里有不存在的提供者：{fbs}"
        last = spec["providers"][fbs[-1]]
        assert last["kind"] in ("local", "mock"), \
            f"{c} 的降级链终点是 {last['kind']}（{fbs[-1]}）——终点必须是本地或离线，否则断网即全废"


def test_next_available_points_into_the_chain():
    st = cap.status(m.cfg)
    for c, info in st["capabilities"].items():
        nxt = info["next_available"]
        if nxt is not None:
            assert nxt in info["fallbacks"], f"{c} 的 next_available={nxt} 不在降级链里"


# ═══ ⑤ 状态翻译 ═══

def test_status_translation(monkeypatch):
    monkeypatch.delenv("XIAOMI_MIMO_API_KEY", raising=False)
    st = cap.status(m.cfg)["capabilities"]
    assert st["chat"]["providers"]["mock"]["state"] == "ready"
    assert st["chat"]["providers"]["ollama"]["state"] == "ready"      # 本地不需要 Key
    assert st["chat"]["providers"]["mimo"]["state"] == "needs_key"    # 云端且没设
    monkeypatch.setenv("XIAOMI_MIMO_API_KEY", "sk-probe-1234567890")
    st2 = cap.status(m.cfg)["capabilities"]
    assert st2["chat"]["providers"]["mimo"]["state"] == "ready"


# ═══ ⑥ 绝不回传 Key 的值 ═══

def test_response_never_leaks_key_values(monkeypatch):
    secret = "sk-capability-leak-probe-9876543210"
    monkeypatch.setenv("XIAOMI_MIMO_API_KEY", secret)
    monkeypatch.setenv("DASHSCOPE_API_KEY", secret + "-dash")
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        body = c.get("/api/v1/capabilities").json()
    leaks = [p for p, s in _walk(body) if secret in s]
    assert not leaks, f"能力接口回传了 Key 的值：{leaks}"
    assert json.dumps(body, ensure_ascii=False).count("sk-") == 0, "响应里出现了 Key 形态的字符串"


# ═══ ⑧ 降级路径：报错必须把三条路说清楚（且由声明表生成，不是手写）═══

def test_fallback_hint_offers_three_routes(monkeypatch):
    monkeypatch.setattr(m.cfg.model, "provider", "mimo", raising=False)   # 场景：云端首选、没 Key
    monkeypatch.delenv("XIAOMI_MIMO_API_KEY", raising=False)
    hint = cap.fallback_hint(m.cfg, "chat", "模型提供者不可用（provider=mimo）：ValueError: 缺 Key")
    assert hint.startswith("模型提供者不可用"), "首行要保留用户熟悉的原话（不断旧锚点）"
    assert "①" in hint and "②" in hint and "③" in hint, f"三条路不齐：{hint}"
    assert "XIAOMI_MIMO_API_KEY" in hint, "第①条没告诉用户要填哪个环境变量"
    assert "用户级环境变量" in hint, "第①条没提「可以长期保存」这回事"
    assert "Ollama" in hint, "第②条没给出本地选项"
    assert "Mock" in hint, "第③条没给出演示模式"


def test_fallback_hint_is_generated_from_the_table(monkeypatch):
    """★ 数据驱动：改声明表的降级链，提示语必须跟着变（否则就是手写死的）。"""
    monkeypatch.setitem(cap.CAPABILITIES["chat"], "fallbacks", ["mock"])
    hint = cap.fallback_hint(m.cfg, "chat", "boom")
    assert "Mock" in hint and "Ollama" not in hint, \
        f"提示语没跟着声明表走（还是写死的）：{hint}"


def test_fallback_hint_survives_unknown_capability():
    assert cap.fallback_hint(m.cfg, "nonexistent", "原样返回") == "原样返回"


def test_task_creation_503_carries_the_three_routes(monkeypatch):
    """端到端：建任务时 provider 用不了 → 503 里必须带三条路（用户不用猜）。"""
    from fastapi.testclient import TestClient

    def boom(_cfg):
        raise ValueError("model.base_url 未配置（OpenAI 兼容提供者必须提供）")

    monkeypatch.setattr(m, "create_provider", boom)
    monkeypatch.setattr(m.cfg.model, "provider", "mimo", raising=False)
    monkeypatch.delenv("XIAOMI_MIMO_API_KEY", raising=False)
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.post("/api/v1/tasks", json={"input": "随便一句话"})
        assert r.status_code == 503, r.text
        detail = r.json()["detail"]
    assert "模型提供者不可用" in detail, detail
    assert "①" in detail and "②" in detail and "③" in detail, f"503 没带降级路径：{detail}"
    assert "XIAOMI_MIMO_API_KEY" in detail and "Ollama" in detail, detail


# ═══ ⑦ 机器档位推荐 ═══

def test_local_tier_recommendation():
    """★ 2026-10-06 改：千问3-ASR 官方**只发两个规格**（0.6B / 1.7B ✓）——
    原来还测了个 `1.7b-quant` ✗ 官方没有这个型号 ✓ 写着等于骗人 ✓ 已删 ✓。"""
    assert cap.recommend_local_tier(12, 32)["tier"] == "1.7b"        # 本机（RTX 5070Ti 12G / 32G）
    assert cap.recommend_local_tier(2, 32)["tier"] == "0.6b"         # 显存紧（2G）→ 0.6B ✓ 它只要约 2G
    assert cap.recommend_local_tier(0, 8)["tier"] == "0.6b"          # 纯 CPU/小内存
    assert cap.recommend_local_tier(12, 8)["tier"] == "0.6b"         # 显存够但内存不够 → 保守
    assert cap.recommend_local_tier(0, 0)["tier"] == "0.6b"          # 探测不到 → 最保守
    # 档位表里不许再出现官方没有的型号 ✗
    ids = {t["id"] for t in cap.LOCAL_TIERS}
    assert ids == {"0.6b", "1.7b"}, f"档位表混进了不存在的规格 ✗：{ids}"


def test_endpoint_shape():
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.get("/api/v1/capabilities")
        assert r.status_code == 200, r.text
        body = r.json()
    for c in ("chat", "image", "video", "asr", "tts"):
        assert c in body["capabilities"], f"能力 {c} 没出现在接口里"
        assert body["capabilities"][c]["label"], f"{c} 没有中文标签"
    assert body["local_tiers"], "没有本地档位信息"
