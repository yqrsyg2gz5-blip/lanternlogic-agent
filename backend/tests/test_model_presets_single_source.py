"""A5（审计台账 P1）：模型预设只能有一份表 —— 三处硬编码 8/6/10 的对齐与防漂移。

修复前事实（读码 + 本批实测）：
  · 切换器（frontend/src/modelPresets.ts）**6** 项：mimo/deepseek/qwen/glm/kimi/ollama
  · 设置页（SettingsPanel.tsx 内联）    **8** 项：+ anthropic + mock
  · 后端 providers/__init__.py _REGISTRY **10** 个键（那是 **provider 实现别名**，
    含 openai_compatible / claude 两个别名，不是"模型预设"——台账把两者混为一谈，
    本文件把口径写清楚并在锚点里按正确的语义对齐）
  ⇒ 真实后果：设置页能配 Claude（Anthropic），但切换器里没有它 ⇒ 切走之后
    切不回来；且按钮显示原始模型 id（claude-sonnet-4-20250514）而不是友好名。

修法：只有 frontend/src/modelPresets.ts 一张表（8 项 = 设置页的 8 项），
  切换器（首页/任务页）与设置页**都从它取**；显示名走 displayNameForModel。
  后端侧的对应关系不靠"数字相等"，而靠**语义不变式**：每个 preset.provider
  必须存在于 _REGISTRY（否则"配了也起不来"）。

本文件四组锚点：
  ① 共享表自身的完整性与唯一性（8 项、字段齐、键唯一）
  ② ★ 交叉：每个 provider ∈ 后端 _REGISTRY
  ③ ★ 防漂移：两个组件都从共享表取，且**不许再出现本地硬编码清单**
  ④ 与浏览器验证脚本的点击断言对齐（那三串字是 verify_*.mjs 点出来的）
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "frontend" / "src"
PRESETS_TS = (SRC / "modelPresets.ts").read_text("utf-8")
PICKER_TSX = (SRC / "components" / "ModelPicker.tsx").read_text("utf-8")
SETTINGS_TSX = (SRC / "components" / "SettingsPanel.tsx").read_text("utf-8")

ENTRY_RE = re.compile(
    r"^\s*(\w+): \{ provider: '([^']*)', model_name: '([^']*)', base_url: '([^']*)',"
    r" key_env: '([^']*)', label: '([^']+)', full_label: '([^']+)',",
    re.M,
)


def _entries() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for key, provider, model_name, base_url, key_env, label, full_label in ENTRY_RE.findall(PRESETS_TS):
        out[key] = {"provider": provider, "model_name": model_name, "base_url": base_url,
                    "key_env": key_env, "label": label, "full_label": full_label}
    return out


# ═══ ① 共享表自身 ═══

def test_shared_table_has_eight_presets():
    # ★ 2026-10-10：8 → 9（用户点名加的那个本地模型「Bonsai 2 27B」✓ 见 modelPresets.ts 注释）
    #   测试名保持原样不改 ✗ —— 红绿实验组按名字点测试 ✓ 改名会把那些组点空 ✓
    e = _entries()
    assert len(e) == 9, f"共享表应有 9 项（= 设置页项数），实际 {len(e)}：{sorted(e)}"
    assert set(e) == {"mimo", "deepseek", "qwen", "glm", "kimi", "anthropic", "ollama",
                      "bonsai", "mock"}, sorted(e)


def test_every_preset_has_required_fields():
    for key, p in _entries().items():
        assert p["provider"], f"{key} 缺 provider（显式 provider 是十二轮 🔴3 的修复）"
        assert p["model_name"], f"{key} 缺 model_name"
        assert p["label"] and p["full_label"], f"{key} 缺 label/full_label"
        # 有 Key 的必须给 key_env；本地与演示模式不需要
        # ★ 2026-10-10：+bonsai（本地 llama.cpp 服务，无需 Key ✓）
        if key not in ("ollama", "mock", "bonsai"):
            assert p["key_env"], f"{key} 需要 key_env（设置页靠它写环境变量名）"
        if key != "mock":
            assert p["base_url"].startswith("http"), f"{key} 的 base_url 不像地址：{p['base_url']!r}"


def test_model_names_are_unique():
    names = [p["model_name"] for p in _entries().values()]
    assert len(names) == len(set(names)), f"model_name 有重复：{names}"


# ═══ ② 交叉：每个 provider 后端都得认 ═══

def test_every_preset_provider_exists_in_backend_registry():
    """★ 语义对齐（不是"数字相等"）：配了必须能起来。

    后端 _REGISTRY 是 provider **实现**表，含别名（claude→AnthropicProvider、
    openai_compatible→OpenAICompatProvider）⇒ 它的键数（10）本来就不该等于
    预设数（8）。真正要保证的是：每个预设的 provider 都在表里。
    """
    from app.providers import _REGISTRY
    missing = {k: p["provider"] for k, p in _entries().items() if p["provider"] not in _REGISTRY}
    assert not missing, f"这些预设的 provider 后端 _REGISTRY 里没有（配了也起不来）：{missing}"


def test_provider_field_is_not_inferred():
    """provider 必须显式写：providerForModel 的 qwen 前缀会把 ollama 的 qwen3.5:9b 错标。

    （注释里提到它不算——历史说明就写在调用点上方；只查**代码行**。）
    """
    code_lines = [ln for ln in PICKER_TSX.splitlines() if not ln.strip().startswith(("//", "*", "/*"))]
    offenders = [ln.strip() for ln in code_lines if "providerForModel" in ln]
    assert not offenders, f"ModelPicker 的代码里又用回了前缀推断：{offenders}"
    for key, p in _entries().items():
        assert p["provider"] == key or key in ("ollama",), f"{key} 的 provider 与键不一致：{p['provider']}"


# ═══ ③ 防漂移：预设里的默认模型名要**跟得上官方**（不是"跑得起来"就完事）═══

def test_preset_models_are_current_not_retired_names():
    """★ 2026-10-06：预设里的**默认模型名照官方定价页更新过** ✓

    此前写的是 `deepseek-chat` / `glm-4-flash` / `moonshot-v1-8k` —— 那些是**旧名** ✗，
    新用户点一下"DeepSeek"很可能**起不来** ✓（这类错最伤第一次体验 ✓）。
    """
    for old in ("deepseek-chat", "glm-4-flash", "moonshot-v1-8k"):
        assert f"model_name: '{old}'" not in PRESETS_TS, f"预设里还在用旧模型名 {old} ✗"
    for new in ("deepseek-flash", "glm-5.3-flash", "kimi-k2.6"):
        assert new in PRESETS_TS, f"预设里缺少当前模型 {new}"


# ═══ ④ 防漂移：两个组件都从共享表取，不许再本地硬编码 ═══

def test_switcher_renders_from_shared_list():
    assert "SWITCHER_PRESETS" in PICKER_TSX, "切换器没有从共享表取清单"
    assert re.search(r"\{SWITCHER_PRESETS\.map\(", PICKER_TSX), "切换器弹层没直接渲染共享清单"
    # 不许再出现"本地又抄一份模型清单"（硬编码 model_name 字面量）
    hard = re.findall(r"model_name:\s*'", PICKER_TSX)
    assert not hard, f"ModelPicker 里又出现了硬编码 model_name（{len(hard)} 处）——A5 的根因就是这个"


def test_settings_panel_uses_shared_table():
    assert "from '../modelPresets'" in SETTINGS_TSX, "设置页没有引用共享表"
    hard = re.findall(r"\b(mimo|deepseek|qwen|glm|kimi|anthropic|ollama|mock):\s*\{", SETTINGS_TSX)
    assert not hard, f"设置页又自建了一份预设表（{hard}）——A5 的根因就是这个"


def test_switcher_list_is_derived_not_duplicated():
    """SWITCHER_PRESETS 必须是 MODEL_PRESETS 的派生（写死第二份清单即漂移复发）。"""
    m = re.search(r"export const SWITCHER_PRESETS[^=]*=\s*([^\n;]+)", PRESETS_TS)
    assert m, "找不到 SWITCHER_PRESETS 定义"
    assert "Object.values(MODEL_PRESETS)" in m.group(1), f"SWITCHER_PRESETS 不是派生：{m.group(1)}"


# ═══ ④ 与浏览器验证脚本的点击断言对齐 ═══

@pytest.mark.parametrize("needle", ["MiMo（小米）", "智谱 GLM", "本地（Ollama）"])
def test_labels_clicked_by_browser_scripts_still_exist(needle):
    """verify_model_unify.mjs / verify_usage_badge.mjs 会按这些字点项——改名即红。"""
    labels = [p["label"] for p in _entries().values()]
    assert needle in labels, f"切换器标签「{needle}」不在了（浏览器验证脚本会点空）：{labels}"
