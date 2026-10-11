# -*- coding: utf-8 -*-
"""★ 成本估算（P0-5 成本可见）。

## 为什么要有

**Anthropic 多智能体复盘**的实测：多智能体系统大约烧 **15×** 普通对话的 token，
"任务价值必须高到付得起这个开销"。所以用户有权在群里看到**这一步花了多少**，
而不是等到月底看账单才发现。

## 为什么默认不带价格表

价格会变，而且各家计费口径不一（缓存命中价、阶梯价、包月…）。**编一个"看起来对"的单价
比不报更糟** —— 用户会拿它当账单。所以：

· **token 数永远报**（这是上游返回的真实值，或明确标注"估算"）
· **¥ 只在配置了单价时才报**，并在文案里写明"按你配置的单价估算"
· 单价从 `config.pricing` 读（形如 `{"mimo-v2.6-flash": {"in": 2.0, "out": 8.0, "cached": 0.5}}`，
  单位：**元 / 百万 token**）；没配就不报钱

这样"机制是通的、数字是诚实的"：用户想看到钱，填一次单价即可（也可以随时改）。
"""
from __future__ import annotations

from typing import Any

# 单价表（元 / 百万 token）。**默认空** —— 理由见文件头。
# 结构：{模型名（或前缀）: {"in": 输入价, "out": 输出价, "cached": 缓存命中价（可选）}}
PRICES: dict[str, dict[str, float]] = {}

# ═══ 官方价（★ 2026-10-06：用户指出"中国是中国定价，美国是美国定价，不能拿美元换算"）═══
#
# 这条批评是对的 ✓：第三方渠道（OpenRouter 等）标的价是**它自己渠道**的价、还是美元 ✗，
# 跟你账单上的**官方人民币价**不是一回事 ✓。所以：
#   · **官方价优先**：下面这张表由**官方定价页**抄录，带**来源链接 + 更新日期** ✓（可核对 ✓）
#   · 第三方查询只作**参考**，界面上会写明"美元换算、仅供参考" ✓
#   · 官方价变了 ⇒ 改这张表 + 改日期 ✓（来源在，谁都能核对 ✓）
#
# 来源：https://mimo.mi.com/docs/zh-CN/price/pay-as-you-go（页面标注：更新时间 2026-09-22）
_MIMO_SRC = "https://mimo.mi.com/docs/zh-CN/price/pay-as-you-go"
_MIMO_UPDATED = "2026-09-22"
OFFICIAL: dict[str, dict[str, Any]] = {
    # 语言模型 · 实时推理（国内价，元/百万 tokens）
    "mimo-v2.6-flash": {"in": 1.00, "cached": 0.02, "out": 2.00,
                        "source": _MIMO_SRC, "updated": _MIMO_UPDATED, "region": "国内"},
    "mimo-v2.5": {"in": 1.00, "cached": 0.02, "out": 2.00,
                  "source": _MIMO_SRC, "updated": _MIMO_UPDATED, "region": "国内",
                  "note": "官方页面标注该模型将于 2026-10-21 下线"},
    "mimo-v2.6-pro": {"in": 3.00, "cached": 0.025, "out": 6.00,
                      "source": _MIMO_SRC, "updated": _MIMO_UPDATED, "region": "国内"},
    "mimo-v2.5-pro": {"in": 3.00, "cached": 0.025, "out": 6.00,
                      "source": _MIMO_SRC, "updated": _MIMO_UPDATED, "region": "国内",
                      "note": "官方页面标注该模型将于 2026-10-21 下线"},
    "mimo-v2.6-pro-ultraspeed": {"in": 30.00, "cached": 0.25, "out": 60.00,
                                 "source": _MIMO_SRC, "updated": _MIMO_UPDATED, "region": "国内"},
    # 批量推理（便宜一半；我们走实时推理，列在这儿备查）
    "mimo-v2.6-flash-batch": {"in": 0.50, "cached": 0.01, "out": 1.00,
                              "source": _MIMO_SRC, "updated": _MIMO_UPDATED, "region": "国内·批量"},
    "mimo-v2.6-pro-batch": {"in": 1.50, "cached": 0.0125, "out": 3.00,
                            "source": _MIMO_SRC, "updated": _MIMO_UPDATED, "region": "国内·批量"},
}

# ── DeepSeek（官方定价页，2026-10-06 抄录）──────────────────────────────
# 来源：https://api-docs.deepseek.com/zh-cn/quick_start/pricing/
# ★ 它有**时段价**：高峰（工作日 9:00-12:00、14:00-18:00）与空闲（其余时段，含周末/节假日）；
#   **空闲价 = 高峰价的一半**。这里存**高峰价**（保守估算，不会少算钱 ✓）。
_DEEPSEEK_SRC = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
_DEEPSEEK_UPDATED = "2026-10-06"
_DEEPSEEK_NOTE = "高峰价（工作日 9:00-12:00、14:00-18:00）；**空闲时段是半价**（含周末与节假日）"
OFFICIAL.update({
    "deepseek-flash": {"in": 2.00, "cached": 0.04, "out": 8.00,
                       "source": _DEEPSEEK_SRC, "updated": _DEEPSEEK_UPDATED,
                       "region": "国内·高峰", "note": _DEEPSEEK_NOTE},
    "deepseek-v4-flash": {"in": 2.00, "cached": 0.04, "out": 8.00,     # 旧名（官方已下线，按 Flash 计费）
                          "source": _DEEPSEEK_SRC, "updated": _DEEPSEEK_UPDATED,
                          "region": "国内·高峰", "note": _DEEPSEEK_NOTE},
    "deepseek-v4-pro": {"in": 9.00, "cached": 0.30, "out": 27.00,
                        "source": _DEEPSEEK_SRC, "updated": _DEEPSEEK_UPDATED,
                        "region": "国内·高峰", "note": _DEEPSEEK_NOTE},
})

# ── 阿里云百炼（通义千问）────────────────────────────────────────────
# ★ 2026-10-06：官方页面**自己就把"华北2（北京）/ 新加坡 / 美国"三套价分列** ✓ ——
#   正是用户批评的那点："中国是中国定价，美国是美国定价" ✓。这里只抄**国内（北京）**价 ✓。
# 来源：https://help.aliyun.com/zh/model-studio/qwen3-14b （抓取于 2026-10-06）
_QWEN_SRC = "https://help.aliyun.com/zh/model-studio/qwen3-14b"
_QWEN_NOTE = ("**只抄了国内（华北2 北京）价** ✓ —— 官方页另有新加坡/美国两套价；"
              "思考模式的**输出**按 ¥10/百万 计费（表里存的是非思考输出价）")
OFFICIAL.update({
    "qwen3-14b": {"in": 1.00, "out": 4.00,
                  "source": _QWEN_SRC, "updated": "2026-10-06",
                  "region": "国内·华北2(北京)", "note": _QWEN_NOTE},
})

# ── 月之暗面 Kimi（国内人民币价）────────────────────────────────────────
# ★ 2026-10-06：**国内站与国际站是两套价** ✓（正是用户那句"中国是中国定价"）——
#   国际站 platform.kimi.ai 的 kimi-k3 是 $3.00/$15.00 ✓；这里抄的是**国内站**的人民币价 ✓。
# 抓取技巧：这类文档站（Mintlify）在页面地址后面加 `.md` 就能拿到**纯文本版** ✓
#   （普通抓取拿到的是前端模板、没有表格数字 ✗ —— 本班先踩了这个坑 ✓）。
_KIMI_SRC = "https://platform.kimi.com/docs/pricing/chat"
OFFICIAL.update({
    "kimi-k3": {"in": 20.00, "cached": 2.00, "out": 100.00,
                "source": _KIMI_SRC, "updated": "2026-10-06", "region": "国内",
                "note": "另有**缓存写入**费（TTL 5min ¥20 / 1h ¥40 每百万）；命中缓存后自动续期、不再收写入费"},
    "kimi-k2.7-code": {"in": 6.50, "cached": 1.30, "out": 27.00,
                       "source": _KIMI_SRC, "updated": "2026-10-06", "region": "国内"},
    "kimi-k2.7-code-highspeed": {"in": 13.00, "cached": 2.60, "out": 54.00,
                                 "source": _KIMI_SRC, "updated": "2026-10-06", "region": "国内"},
    "kimi-k2.6": {"in": 6.50, "cached": 1.10, "out": 27.00,
                  "source": _KIMI_SRC, "updated": "2026-10-06", "region": "国内"},
})

# ── 智谱 AI（GLM，官方人民币价）────────────────────────────────────────
_ZHIPU_SRC = "https://docs.bigmodel.cn/cn/guide/start/pricing"
OFFICIAL.update({
    "glm-5.3": {"in": 8.00, "cached": 2.00, "out": 28.00,
                "source": _ZHIPU_SRC, "updated": "2026-10-06", "region": "国内"},
    "glm-5.3-flash": {"in": 0.80, "cached": 0.23, "out": 2.80,
                      "source": _ZHIPU_SRC, "updated": "2026-10-06", "region": "国内"},
    "glm-5.3-flashx": {"in": 2.00, "cached": 0.57, "out": 7.00,
                       "source": _ZHIPU_SRC, "updated": "2026-10-06", "region": "国内"},
    "glm-5.2": {"in": 8.00, "cached": 2.00, "out": 28.00,
                "source": _ZHIPU_SRC, "updated": "2026-10-06", "region": "国内"},
    "glm-5": {"in": 4.00, "cached": 1.00, "out": 18.00,
              "source": _ZHIPU_SRC, "updated": "2026-10-06", "region": "国内",
              "note": "输入长度 ≥32K 时单价上浮（¥6 / ¥1.5 / ¥22）；表里存的是短上下文档"},
    "glm-4.7-flash": {"in": 0.0, "cached": 0.0, "out": 0.0,
                      "source": _ZHIPU_SRC, "updated": "2026-10-06", "region": "国内",
                      "note": "**官方标价免费** ✓（缓存存储限时免费）"},
})

# ── 官方定价页目录（★ 查不到价时，把你送到**正确的那一页** ✓）──────────────
# 规矩：加一家 = 照着官方页填进 OFFICIAL（含来源 + 更新日期 ✓），**不许拿美元换算** ✗。
# ★ 抓价技巧（本班实测）：Mintlify 系的文档站（Kimi / 智谱）在地址后加 **`.md`** 就能拿到
#   纯文本版 ✓；直接抓页面只会得到前端模板、**没有表格数字** ✗。
PRICING_PAGES: dict[str, dict[str, str]] = {
    "mimo": {"name": "小米 MiMo", "url": _MIMO_SRC},
    "deepseek": {"name": "DeepSeek", "url": _DEEPSEEK_SRC},
    "qwen": {"name": "阿里云百炼（通义千问）",
             "url": "https://help.aliyun.com/zh/model-studio/model-pricing"},
    "zhipu": {"name": "智谱 AI（GLM）", "url": _ZHIPU_SRC},
    "glm": {"name": "智谱 AI（GLM）", "url": _ZHIPU_SRC},
    "kimi": {"name": "月之暗面 Kimi（国内）", "url": _KIMI_SRC},
    "moonshot": {"name": "月之暗面 Kimi（国内）", "url": _KIMI_SRC},
    "openai": {"name": "OpenAI", "url": "https://openai.com/api/pricing/"},
    "anthropic": {"name": "Anthropic", "url": "https://www.anthropic.com/pricing"},
}


def page_for(provider: str = "", model: str = "") -> dict[str, str] | None:
    """找这家（或这个模型）的**官方定价页**。"""
    hay = f"{provider} {model}".lower()
    for key, row in PRICING_PAGES.items():
        if key in hay:
            return row
    return None


def official_for(model: str) -> dict[str, Any] | None:
    """按模型名找**官方价**（前缀匹配；找不到返回 None —— 不许猜 ✗）。"""
    m = (model or "").strip().lower()
    if not m:
        return None
    if m in OFFICIAL:
        return {"model": m, **OFFICIAL[m]}
    best = None
    for name, row in OFFICIAL.items():
        if name in m or m in name:
            if best is None or len(name) > len(best[0]):
                best = (name, row)
    return {"model": best[0], **best[1]} if best else None


def configure(prices: dict[str, Any] | None) -> None:
    """从配置装载单价表（设置里改完调它即可）。只认数值，坏数据一律忽略。"""
    PRICES.clear()
    for model, spec in (prices or {}).items():
        if not isinstance(spec, dict):
            continue
        row: dict[str, float] = {}
        for key in ("in", "out", "cached"):
            v = spec.get(key)
            if isinstance(v, (int, float)) and v >= 0:
                row[key] = float(v)
        if "in" in row or "out" in row:
            PRICES[str(model)] = row


def _lookup(model: str) -> dict[str, float] | None:
    """按精确名 → 前缀（忽略大小写）匹配单价。"""
    if not model:
        return None
    m = model.strip()
    if m in PRICES:
        return PRICES[m]
    low = m.lower()
    for name, row in PRICES.items():
        if low.startswith(name.lower()) or name.lower() in low:
            return row
    return None


def estimate(model: str, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> float | None:
    """估算花费（元）。**没配单价就返回 None**（调用方只报 token，不许编）。"""
    row = _lookup(model)
    if row is None:
        return None
    # 缓存命中的部分按 cached 价（没配 cached 就按输入价）
    cached = max(0, min(int(cached_tokens or 0), int(input_tokens or 0)))
    fresh_in = max(0, int(input_tokens or 0) - cached)
    cny = 0.0
    cny += fresh_in / 1_000_000 * float(row.get("in", 0.0))
    cny += cached / 1_000_000 * float(row.get("cached", row.get("in", 0.0)))
    cny += max(0, int(output_tokens or 0)) / 1_000_000 * float(row.get("out", 0.0))
    return round(cny, 4)


def money(cny: float | None) -> str:
    """金额文案：小于 1 分也要显示出来（别四舍五入成 ¥0.00 让人以为免费）。"""
    if cny is None:
        return ""
    if cny < 0.01:
        return f"约 ¥{cny:.4f}"
    if cny < 1:
        return f"约 ¥{cny:.3f}"
    return f"约 ¥{cny:.2f}"
