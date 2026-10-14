# -*- coding: utf-8 -*-
"""★★ 「思考档位」—— 界面上给中文，各家翻译成自己认的值（2026-10-14 用户要求）。

## 用户原话

> 「你把那个档位最好是用中文，别用英文……你可以**关/低/中/高**……
>   假如说别的模型就两个档位，**关中高**……你看看人家模型怎么设置，
>   你就按人家模型建议来呗」

## 实测出来的各家能力（2026-10-14，我实打实发请求试的）

```
MiMo：    none ✓ / low ✓ / medium ✓ / high ✓    ｜ minimal ✗400 · max ✗400 · xhigh ✗400
DeepSeek：low ✓ / high ✓ / max ✓                ｜ 官方表 minimal→low · medium→high · xhigh→high · ultra→max
本地：    只有 开/关（chat_template_kwargs.enable_thinking）
OpenAI/阿里/智谱：没有这个参数 ⇒ **一个字段都不许塞**（严格网关会 400）
```

## 界面口径（中文，按模型能力给选项）

| 模型 | 界面显示 | 传过去 |
|---|---|---|
| MiMo | 关 / 低 / 中 / 高 | none / low / medium / high |
| DeepSeek | 关 / 低 / 高 / 最高 | thinking:disabled / low / high / max |
| 本地 | 开 / 关 | enable_thinking true/false |
| 其它 | **不显示这一栏** | 不传 |

## 本文件钉什么

1. 有 `reasoning_effort` 这个配置项，取值是**中文口径的四档**：`off/low/medium/high`
2. 各家有**自己的映射**（MiMo / DeepSeek / 本地），且映射结果**必须是实测认的值**
3. 不认识的云端：**一个思考字段都不许塞**
4. 「关」这一档在任何模型上都要**真的关掉**（省钱、快）
5. 映射表要能**说出各模型支持哪几档**（界面以此决定显示什么）
"""
from __future__ import annotations

import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import app.providers.openai_compat as oc  # noqa: E402

HISTORY = [{"role": "user", "content": "hi"}]


def _body(url: str, effort=None, model: str = "m"):
    class Cfg:
        pass

    c = Cfg()
    c.base_url = url
    c.model_name = model
    c.api_key_env = ""
    c.max_tokens = 16384
    c.temperature = 0.7
    c.reasoning_effort = effort
    return oc.OpenAICompatProvider(c)._build_body(HISTORY, [])


# ═══ ① 配置项存在，口径统一（中文四档）═══

def test_effort_vocabulary_is_the_same_everywhere():
    """★ 统一口径：`off` / `low` / `medium` / `high` —— 界面文案是中文，存的值是这四个。"""
    vocab = getattr(oc, "_EFFORT_LEVELS", None)
    assert vocab, "没有 `_EFFORT_LEVELS` ⇒ 档位口径没有单一来源 ✗"
    assert list(vocab) == ["off", "low", "medium", "high"], vocab


def test_each_provider_declares_what_it_supports():
    """★ 各家支持哪几档要能问出来（界面靠它决定显示什么）。"""
    fn = getattr(oc, "supported_efforts", None)
    assert fn is not None, "没有 `supported_efforts()` ⇒ 界面不知道该显示哪几档 ✗"
    mimo = fn("https://api.xiaomimimo.com/v1")
    ds = fn("https://api.deepseek.com/v1")
    local = fn("http://127.0.0.1:8080/v1")
    other = fn("https://api.openai.com/v1")
    assert set(mimo) == {"off", "low", "medium", "high"}, mimo
    assert set(ds) == {"off", "low", "high", "high2"} or set(ds) >= {"off", "low", "high"}, ds
    assert set(local) == {"off", "high"} or set(local) <= {"off", "high"}, local
    assert other == [] or other is None, f"OpenAI 不该声明支持档位：{other}"


# ═══ ② 映射：中文四档 → 各家认的值 ═══

def test_mimo_mapping_uses_only_values_it_accepts():
    """★ MiMo 实测认：none / low / medium / high。**max 会 400** ✗"""
    # ★ "关"那一档：用**模板开关**关（比 reasoning_effort="none" 更明确、也是实测有效的写法）✓
    _off = _body("https://api.xiaomimimo.com/v1", "off")
    _off_ok = ((_off.get("chat_template_kwargs") or {}).get("enable_thinking") is False
               or _off.get("reasoning_effort") == "none"
               or (_off.get("thinking") or {}).get("type") == "disabled")
    assert _off_ok, f"MiMo 的「关」档没真的关掉：{_off.get('chat_template_kwargs')!r} / {_off.get('reasoning_effort')!r}"
    assert _body("https://api.xiaomimimo.com/v1", "low").get("reasoning_effort") == "low"
    assert _body("https://api.xiaomimimo.com/v1", "medium").get("reasoning_effort") == "medium"
    assert _body("https://api.xiaomimimo.com/v1", "high").get("reasoning_effort") == "high"
    for e in ("off", "low", "medium", "high"):
        got = _body("https://api.xiaomimimo.com/v1", e).get("reasoning_effort")
        assert got != "max", "给 MiMo 传了它不认的 max（会 400）✗"


def test_deepseek_mapping_uses_only_values_it_accepts():
    """★ DeepSeek 认：low / high / max（没有 medium）；关用 thinking.disabled ✓"""
    assert _body("https://api.deepseek.com/v1", "low").get("reasoning_effort") == "low"
    assert _body("https://api.deepseek.com/v1", "medium").get("reasoning_effort") == "high"
    assert _body("https://api.deepseek.com/v1", "high").get("reasoning_effort") == "max"
    off = _body("https://api.deepseek.com/v1", "off")
    assert (off.get("thinking") or {}).get("type") == "disabled", off.get("thinking")
    for e in ("low", "medium", "high"):
        got = _body("https://api.deepseek.com/v1", e).get("reasoning_effort")
        assert got in ("low", "high", "max"), f"给 DeepSeek 传了它不认的 {got!r} ✗"


def test_local_mapping_uses_the_boolean_switch():
    """★ 本地只有开/关（没有档位）⇒ 低/中/高都当"开"。"""
    for e in ("low", "medium", "high"):
        b = _body("http://127.0.0.1:8080/v1", e)
        assert (b.get("chat_template_kwargs") or {}).get("enable_thinking") is True, (e, b)
    b = _body("http://127.0.0.1:8080/v1", "off")
    assert (b.get("chat_template_kwargs") or {}).get("enable_thinking") is False, b


def test_unknown_cloud_gets_nothing_even_with_effort():
    """★ 不认识的云端：**一个思考字段都不许塞**（严格网关会 400）✗"""
    for e in ("off", "low", "medium", "high"):
        b = _body("https://api.openai.com/v1", e)
        assert "reasoning_effort" not in b, (e, b.get("reasoning_effort"))
        assert "thinking" not in b, (e, b.get("thinking"))
        assert "chat_template_kwargs" not in b, (e, b.get("chat_template_kwargs"))


# ═══ ③ 没设档位时：一切照旧（不许把现有行为改坏）═══

def test_no_effort_set_keeps_old_behavior():
    """★ 没配档位 ⇒ 云端不传思考字段、本地关思考（和修复前完全一致）✓"""
    assert "reasoning_effort" not in _body("https://api.xiaomimimo.com/v1", None)
    assert "thinking" not in _body("https://api.deepseek.com/v1", None)
    b = _body("http://127.0.0.1:8080/v1", None)
    assert (b.get("chat_template_kwargs") or {}).get("enable_thinking") is False, b


# ═══ ④ 界面文案：中文 ═══

def test_chinese_labels_exist_for_the_ui():
    """★ 用户要求：**界面用中文**（关/低/中/高），不许只给英文。"""
    labels = getattr(oc, "EFFORT_LABELS_ZH", None)
    assert labels, "没有 `EFFORT_LABELS_ZH` ⇒ 界面只能显示英文 ✗"
    assert labels.get("off") == "关", labels
    assert labels.get("low") == "低", labels
    assert labels.get("medium") == "中", labels
    # 最高那一档：DeepSeek 用"最高"，避免和"高"混
    assert labels.get("high") in ("高", "最高"), labels
