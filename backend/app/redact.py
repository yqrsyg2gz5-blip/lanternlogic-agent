"""密钥打码管线（单一事实来源）。

验证报告 24 的教训：打码实现曾只挂在 main.py 的调用点上，而 memory.py / kb.py
的**声明**（"写入前过打码管线"）与实现脱节——声明贴在错误的层，换个调用方就漏。
本模块把打码下沉为独立函数，任何"要把文本落盘/外发"的模块都可以直接用。

两层防线：
  ① 环境变量中疑似密钥**值**精确替换（BYOK 语义下的真实 Key——名字含
     KEY/TOKEN/SECRET/PASS 且值 ≥16 字符）；
  ② **Key 形态**串（sk-/pk-/rk- 前缀、AKIA/ASIA、xoxb-、Bearer 长随机段）——
     用户随手贴进聊天的 Key 同样不落盘。

已知限制（第十轮 B4 落盘）：
  sk-/pk-/rk- 形态的下界是 **6** 位——为了盖住 sk-abc123 这类短 Key。
  代价：`sk-model-v2` 这类"sk- 开头 + ≥6 位"的普通标识符（如模型名）
  **也会被打码**成 [已隐藏]。这是有意取舍：宁可多打码一个模型名，
  不可漏放一个真 Key。若未来出现"必须原样展示 sk- 开头标识符"的场景，
  应走白名单而不是降低下界。

已知限制（二十六轮第2批落盘；第6批补全前缀范围）：
  前面紧贴【字母/数字/下划线】的 sk-（ 词边界失效）形态【不打码】——
  如 `xxsk-attached123456`、`MYSK=sk-…`、`1sk-…`、`_sk-…`。
  若放宽为无 \b 匹配，任何以"sk-"结尾的单词后接连字符（task-usage123、
  disk-usage123 之类）都会被误打码，误伤面不可控；权衡后选择文档化
  而非放宽。Stripe 下划线（sk_live_/sk_test_）与大写 SK-/PK-/RK- 已在
  二十六轮补为独立形态（只增不改，现有匹配行为零变化）。

已知误伤面（二十六轮第3批落盘——"宁可多打码"是政策，但必须写明）：
  以下普通文本【会被打码】成 [已隐藏-疑似密钥]：
    SK-2026-001 / PK-000123 / RK-2026-0001（大写前缀+短随机段，如单号/编号）
    SK-ALPHA-BETA（大写前缀+连字符词组）
    sk_test_connection / sk_live_documentation（Stripe 前缀开头的普通英文词组）
  若业务上必须原样展示这类文本，走白名单，不要降低形态匹配标准。
"""
from __future__ import annotations

import os
import re
from typing import Any

_KEY_SHAPE_RE = re.compile(
    # OpenAI / DeepSeek / Anthropic 风格（下界降到 6：sk-abc123 这类短 Key 也要盖，
    # 同时避免误伤 "disk-usage"/"sk-1" —— 6 位随机串在日常文本里极罕见）
    r"(?:\bsk|\bpk|\brk)-[A-Za-z0-9_-]{6,}"
    # AWS
    r"|\b(?:AKIA|ASIA)[A-Z0-9]{16}"
    # Slack
    r"|\bxox[baprs]-[A-Za-z0-9-]{10,}"
    # Bearer（下界降到 12）
    r"|\bBearer\s+[A-Za-z0-9._-]{12,}"
    # GitHub（第六轮复验补：全部前缀形态）
    r"|\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}"
    # GitLab
    r"|\bglpat-[A-Za-z0-9_-]{16,}"
    # npm
    r"|\bnpm_[A-Za-z0-9]{30,}"
    # Google API key
    r"|\bAIza[A-Za-z0-9_-]{30,}"
    # HuggingFace
    r"|\bhf_[A-Za-z0-9]{20,}"
    # Stripe（二十六轮第2批补：sk_live_/sk_test_ 下划线形态；下界 10——
    # 前缀本身是强密钥标识，验证员样例 sk_live_51Habcdefghij 为 13 位）
    r"|\bsk_(?:live|test)_[A-Za-z0-9]{10,}"
    # 大写 SK-/PK-/RK-（二十六轮第2批补；\b 保证 TASK-xxx / disk-xxx 不误伤）
    r"|\b(?:SK|PK|RK)-[A-Za-z0-9_-]{6,}"
    # 私钥头（PEM：整块内容不可见即可，头本身足以触发）
    r"|-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----",
)

# 二十六轮第2批③（性能）：环境变量密钥值缓存 + 合并预筛正则。
# 此前 redact_text 对每个字符串做 N_env 次 `v in text`（本机 74 个环境变量
# 全串扫描）——实测 1MB 单串 75ms / 10MB 764ms / 1 万短串 356ms。
# 现改为：值清单 + re.escape 合并成一个 alternation 预筛正则（模块级缓存，
# 环境快照变化即重建），search 命中才进入【原样保留】的逐值 replace 循环。
# 等价性：search 命中 ⇔ any(v in text)（字面 alternation）；不命中 ⇔
# 循环内任何 replace 都不会发生 ⇒ 跳过零行为差异；替换顺序 = os.environ
# 原始顺序（不排序），与旧实现逐值 replace 的顺序一致。
_ENV_CACHE: tuple[tuple[tuple[str, str], ...], list[str], "re.Pattern[str] | None"] | None = None


def _env_secret_scan() -> tuple[tuple[tuple[str, str], ...], list[str], "re.Pattern[str] | None"]:
    global _ENV_CACHE
    snap = tuple(os.environ.items())
    if _ENV_CACHE is not None and _ENV_CACHE[0] == snap:
        return _ENV_CACHE
    vals = [
        v for k, v in snap
        if any(w in k.upper() for w in ("KEY", "TOKEN", "SECRET", "PASS")) and len(v) >= 16
    ]
    pre = re.compile("|".join(re.escape(v) for v in vals)) if vals else None
    _ENV_CACHE = (snap, vals, pre)
    return _ENV_CACHE


def redact_text(text: str) -> str:
    """两层打码，幂等。任何落盘/外发前的文本都应先过这里。"""
    _snap, vals, pre = _env_secret_scan()
    if vals and len(text) >= 16:
        # 混合预筛：中短串用合并正则一次 search（对 1 万短串场景省掉
        # N_env 次 in）；长串（>4KB）str.in 的 memchr 反而快于正则回溯，
        # 用 any(v in text)——两路判定都与"逐值替换前先判存在"等价。
        if len(text) <= 4096:
            hit = pre.search(text) is not None
        else:
            hit = any(v in text for v in vals)
        if hit:
            for v in vals:
                if v in text:
                    text = text.replace(v, "[已隐藏]")
    return _KEY_SHAPE_RE.sub("[已隐藏-疑似密钥]", text)


# 二十六轮第 4 批第 4 处：工具通道弱一档【豁免形态】。
# ★★ 二十六轮第 6 批第 3 处【文件名豁免分支整条删除】：
#   第 5 批的"低熵判据"名实不符——"小写字母开头 + ≤16 位（字母表含数字）"
#   最坏 log2(36^16) ≈ 82.7 bit、第二段 log2(63^16) ≈ 95.6 bit，根本不是
#   低熵；实测三条高熵小写串（sk-a4f8a2c9e1b7d3a.yaml 等）四面明文穿越。
#   且【短的真密钥（全小写 ≤16 位）与词典词/文件名在熵上不可区分】——
#   sk-abc123（真 Key 下界形态）与 sk-config 同构，任何"保留文件名豁免"
#   的方案都无法回答"为什么不会漏掉短真密钥"。
#   ⇒ 只留两类【结构上确定不是密钥】的形态：
#   ① 版本名形态（sk-model-v2——纯小写词 + v数字）；
#   ② 编号形态（SK/PK/RK-数字4-数字2~4，分段纯数字熵约 22bit）。
#   代价（如实声明）：sk-config.yaml 这类文件名在工具通道会被打码，
#   agent 需要真名时走审批（ask 后用户放行）——安全方向宁可多问。
_FILENAME_SHAPE_RE = re.compile(
    # 版本名形态（sk-model-v2——纯小写词 + v数字）
    r"\b(?:sk|pk|rk)-[a-z]{1,16}-v\d{1,3}\b"
    # 编号形态（SK-2026-001）
    r"|\b(?:SK|PK|RK)-\d{4}-\d{2,4}\b"
)

# 残余风险（如实声明）：把真密钥裁成 "sk-xxx-v3" 或 "SK-2026-001" 形态仍可
# 穿越——但这两类结构无法携带 ≥6 位连续随机段（版本名第二段必为 v+数字、
# 编号形态纯数字），裁剪必然破坏密钥本身。威胁模型是【本地用户自己的密钥
# 防意外泄漏】（BYOK）；值面（环境变量精确替换）在弱通道仍保持强规则。

_PLACEHOLDER = "\x00stash{}\x00"


def redact_text_tool(text: str) -> str:
    """【工具通道】打码（observation 面，给 agent 看）——与 redact_text 同源
    强规则，但先把【结构上确定不是密钥】的形态摘出占位、打码后原样还原。

    ★★ 通道事实（二十六轮第 5 批更正，旧说法"豁免不减密钥覆盖"是错的）：
    loop._redact 的返回值同时流向 events.jsonl、history.json、上游 provider
    payload 三个泄出面（不二次打码）——本函数的每次豁免都是真实出机口。
    因此豁免只留"结构上确定不是密钥"的两类形态（版本名/编号，见
    _FILENAME_SHAPE_RE 注释）；第 6 批删除了文件名豁免分支——"小写+≤16 位"
    与短真密钥在熵上不可区分（82.7bit 最坏熵），sk-config.yaml 这类文件名
    从此走强规则（打码）。"""
    stash: list[str] = []

    def _stash(m: "re.Match[str]") -> str:
        stash.append(m.group(0))
        return _PLACEHOLDER.format(len(stash))

    protected = _FILENAME_SHAPE_RE.sub(_stash, text)
    out = redact_text(protected)
    for i, original in enumerate(stash, 1):
        out = out.replace(_PLACEHOLDER.format(i), original)
    return out


def redact_deep(obj: Any) -> Any:
    """对任意 JSON 形结构（dict / list / tuple / str）的【值层】递归打码。

    ★ 单一实现（二十六轮第 7 批 A1 时下沉）：此前只有 loop.py 内部一份
      `_redact_deep`（给 action 事件的 params 与 history 的 arguments 用）。
      做 A1（用户消息 / assistant 文本落盘打码）时 store.py 也需要同一套递归——
      各写一份迟早漂移，所以搬到本模块（模块 docstring 本来就写着
      "任何要把文本落盘 / 外发的模块都可以直接用"）。

    规则：
      · str → `redact_text`（强规则；密钥形态的实现只此一份）；
      · dict → **只打码值，键不动**（键是结构不是数据）；
      · list / tuple → 逐项打码（tuple 归一成 list，与 JSON 一致）；
      · 其它类型原样返回（数字 / 布尔 / None）。

    ★★ 返回的是【新对象】，绝不原地修改入参 —— 这正是 A1 的关键：
      内存里的 history 必须保持真值给 agent 干活用，**只有落盘的那份副本**被打码。
      （历史教训：直接把 agent 上下文也打码，会重演"title 被打码后 agent 认不出
       自己的路径/取值"那一类坑——审计方为此专门回退过一次。）
    """
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        return {k: redact_deep(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_deep(v) for v in obj]
    return obj
