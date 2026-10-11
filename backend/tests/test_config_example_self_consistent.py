# -*- coding: utf-8 -*-
"""`contracts/config.example.json` —— 用户**照抄它来配模型**，所以它必须自洽（★ 2026-10-12）。

## 要解决什么（交接单 A 类第 8 条："提供商标签与实际不符"）

  实测（2026-10-12，修前）这份示例的 `model` 段三处互相打架：

      "provider":   "mock"                          ← 演示模式（假数据，不调真模型）
      "model_name": "deepseek-chat"                 ← **旧名**（官方定价页 2026-10-06 已改名 deepseek-flash）
      "base_url":   "https://api.deepseek.com/v1"   ← 却是 **DeepSeek 真地址**

  ⇒ 用户照着填：`provider` 说自己是演示模式，地址却指向 DeepSeek ✗
  ⇒ 选了它跑起来行为与预期不符（要么静默走 mock、要么拿着 DeepSeek 地址却按别的牌子理解）
  ⇒ 旧模型名 `deepseek-chat` 在新账号上很可能**直接起不来** ✓

  同一份旧名还在别处出现过（`test_model_presets_single_source.py` 注释里记着：
  此前写的是 deepseek-chat / glm-4-flash / moonshot-v1-8k —— 那些是旧名 ✗）。

## 本文件钉两条

  ① 示例配置的 `provider` 必须与 `base_url` **自洽**（不许"标着 A 牌子、地址是 B 家"）
  ② 示例里的 `model_name` 必须是**当前在用的名字**，不许是已弃用旧名
     （当前名字以 `frontend/src/modelPresets.ts` 为单一事实来源 ✓）
"""
from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "contracts" / "config.example.json"
DOC = ROOT / "docs" / "provider-setup.md"
PRESETS = (ROOT / "frontend" / "src" / "modelPresets.ts").read_text("utf-8", errors="replace")

#: 已弃用的旧模型名（照抄 modelPresets.ts 注释里那份教训清单）
RETIRED = ("deepseek-chat", "glm-4-flash", "moonshot-v1-8k")


def _example_model() -> dict:
    return json.loads(EXAMPLE.read_text("utf-8-sig"))["model"]


def _preset_base_urls() -> dict[str, str]:
    """从单一事实来源取 provider → base_url（预设表）。"""
    out: dict[str, str] = {}
    for m in re.finditer(
            r"(\w+): \{ provider: '([^']*)', model_name: '([^']*)', base_url: '([^']*)'", PRESETS):
        out[m.group(2)] = m.group(4)
    return out


# ═══ ① provider 与 base_url 必须自洽 ═══

def test_example_provider_matches_its_base_url():
    m = _example_model()
    provider, url = m.get("provider", ""), m.get("base_url", "")
    presets = _preset_base_urls()

    if provider == "mock":
        # 演示模式不许配真地址/真 Key 环境变量——那会让用户以为在调真模型
        # （★ 2026-10-12 实测：修前正是 provider=mock + DeepSeek 真地址 + DEEPSEEK_API_KEY ✗）
        assert not url, (
            f"示例配置的 provider 是 mock（演示模式），base_url 却是 {url!r} ✗\n"
            "  演示模式不该带真地址 ⇒ 用户会以为在调真模型 ✓\n"
            "  ⇒ 这两个字段要么同属一家，要么一起留空 ✓"
        )
        assert not m.get("api_key_env"), (
            f"provider=mock 却配了 api_key_env={m.get('api_key_env')!r} ✗ —— 演示模式不需要真 Key ✓"
        )
        assert m.get("model_name") == "mock", (
            f"provider=mock 的 model_name 应是 'mock'，实际 {m.get('model_name')!r} ✗\n"
            "  ⇒ 示例里出现真模型名会被误读成'已经配好了' ✓"
        )
        return

    assert provider in presets, (
        f"示例配置的 provider={provider!r} 不在预设表里（frontend/src/modelPresets.ts）✗"
    )
    assert url == presets[provider], (
        f"示例配置的 provider={provider!r} 配的却是 {url!r}，"
        f"而预设表里 {provider} 的地址是 {presets[provider]!r} ✗\n"
        "  ⇒ 这就是『牌子与地址不符』：用户按这个配，请求会打到别的服务商 ✓"
    )


def test_example_stays_runnable_without_a_key():
    """`install.bat` 直接 copy 这份示例当用户的 config.json ⇒ 它必须**没 Key 也能起来**。

    （这条与 tests/test_config.py::test_example_config_loads 呼应 —— 那边断言 provider 必须是 mock ✓
      这里补上"mock 的三个相关字段不能自相矛盾" ✓ 两条一起才挡得住"半 mock 半真地址"那种写错 ✓）
    """
    m = _example_model()
    assert m.get("provider") == "mock", (
        "示例配置的 provider 不是 mock ✗ —— install.bat 会把它 copy 成用户的 config.json，\n"
        "  新用户没有 Key ⇒ 起不来 ✓（这条是既有约定，见 tests/test_config.py）"
    )


# ═══ ② 模型名不许是已弃用旧名 ═══

def test_example_model_name_is_not_retired():
    name = _example_model().get("model_name", "")
    hits = [old for old in RETIRED if name == old or name.startswith(old + "-")]
    assert not hits, (
        f"示例配置里的 model_name={name!r} 是已弃用旧名 {hits} ✗\n"
        "  ⇒ 新用户照抄它很可能**直接起不来**（官方已改名）✓\n"
        "  当前名字见 frontend/src/modelPresets.ts（单一事实来源）✓"
    )


# ═══ ③ 示例配置必须能被解析（防止有人手改坏了） ═══

def test_example_config_is_valid_json_with_model_section():
    raw = EXAMPLE.read_text("utf-8-sig")
    data = json.loads(raw)
    assert isinstance(data.get("model"), dict), "示例配置缺 model 段 ✗"
    assert data.get("version") == 1, f"示例配置 version 应为 1，实际 {data.get('version')!r}"


# ═══ ④ 给用户抄的文档，也不许还在教旧名（★ 2026-10-12）═══def test_provider_setup_doc_does_not_teach_retired_models():
    """`docs/provider-setup.md` 是**给用户照抄的配置示例**。

    实测（2026-10-12 修前）：它同时教着 `deepseek-chat` / `glm-4-flash` / `moonshot-v1-8k`
    三个**已弃用旧名** ✗ —— 用户照抄很可能**起不来** ✓
    同一份文档还把 qwen 的 Key 环境变量写成 `QWEN_API_KEY`，
    而代码别处（出图 / 知识库）读的是 `DASHSCOPE_API_KEY` ✗ ⇒ 抄了会"没 Key" ✓
    """
    doc = DOC.read_text("utf-8", errors="replace")
    for old in RETIRED:
        assert f'"model_name": "{old}"' not in doc, (
            f"provider-setup.md 还在教用户填旧模型名 {old} ✗\n"
            "  ⇒ 用户照抄很可能起不来 ✓ 当前名字见 frontend/src/modelPresets.ts ✓"
        )
    for bad_env in ("QWEN_API_KEY", "MOONSHOT_API_KEY"):
        assert bad_env not in doc, (
            f"provider-setup.md 里还有 {bad_env} ✗ —— 与代码别处读的名字不一致 ⇒ 抄了报『没 Key』✓\n"
            "  现在的口径：通义用 DASHSCOPE_API_KEY（出图/知识库同此名）· Kimi 用 KIMI_API_KEY ✓"
        )


# ═══ ⑤ 云端视频：示例必须保持"未启用云端 = 回退本地"（★ 2026-10-12）═══

def test_example_leaves_video_disabled_so_it_falls_back_to_local():
    """`VideoCfg.provider` 空 = **未启用云端视频（回退 ComfyUI 本地）**（见 config.py 注释）。

    实测背景（交接单待办 7）：示例里**根本没有 video 段** —— 这是对的 ✓
    但它没有任何东西挡着 ⇒ 谁哪天往示例里塞一个云端引擎，就变成
    「新用户没 Key ⇒ 视频功能报没 Key」✗（与 model 段"必须 mock"是同一个道理 ✓）

    这里钉：示例要么不写 video，要么写了但 **provider 留空 / engines 为空** ⇒ 行为仍是回退本地 ✓
    同时钉住 `executor.comfyui_url` 在 —— 本地回退那条路必须有地址可连 ✓
    """
    data = json.loads(EXAMPLE.read_text("utf-8-sig"))
    v = data.get("video")
    if v is not None:
        assert not str(v.get("provider") or "").strip(), (
            f"示例配置里 video.provider={v.get('provider')!r} ✗\n"
            "  ⇒ 新用户照抄后视频功能会去找云端引擎，而他没有 Key ⇒ 报没 Key ✓\n"
            "  空 = 回退本地 ComfyUI（这才是示例该有的默认）✓"
        )
        assert not (v.get("engines") or {}), (
            "示例配置里配了云端视频引擎 ✗ —— 同理：新用户没 Key 会用不了 ✓"
        )
    comfy = (data.get("executor") or {}).get("comfyui_url")
    assert comfy, "示例配置没给 executor.comfyui_url ✗ —— 本地回退那条路会没有地址可连 ✓"
