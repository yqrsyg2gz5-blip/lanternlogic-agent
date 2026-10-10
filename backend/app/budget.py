# -*- coding: utf-8 -*-
"""每个 Key 的花费上限 —— 2026-10-07 用户点名要的（"现在只有成本**显示** ✗ 没有**上限** ✗"）。

## 用户要的是什么

界面上**看得到**花了多少 ✓ 但**拦不住** ✗ —— 一个跑飞的任务可以一直烧下去，
用户只能事后看账单 ✗。所以要一条**闸**：花到上限就**停下**，并且说清楚为什么、怎么继续 ✓。

## 这个上限**管什么**、**不管什么**（写在最前面，免得给了假的安心 ✗）

**管**：**语言模型**的调用 ✓ —— 它是唯一**能算钱**的一档（token × 你在设置里填的单价 ✓）。
**不管**：出图 / 出视频 / 语音识别 / 语音合成 ✗ —— 那几档是**按次/按秒**计费，
  本程序**拿不到它们的单价** ✗ ⇒ 算不出钱 ⇒ **不许编** ✗（编一个数字比不报更糟 ✓——
  用户会拿它当账单 ✓ 这条规矩见 `pricing.py` 的文件头 ✓）。
  所以界面上会**逐条列出"这把 Key 管不了"** ✓ 而不是默默不管 ✓（默默不管 = 假安心 ✗）。

## 记账口径：**只有一份账** ✓

**不另立账本** ✗ —— 直接汇总任务里那份**用量事件**（就是 `/usage` 用的同一份 ✓）。
理由：这个项目已经在"同一件事两处口径"上栽过四次 ✗，再记一份**必然打架** ✓。

· **`since` 之前的不算** ✓（设上限那一刻开始计 ✓ ⇒ "清零重来" = 把 since 推到此刻 ✓）
· 每条用量事件记下**当时那把 Key** ✓（`usage.key_env` ✓ loop.py 落盘时带上 ✓）
· 老记录没有这个字段 ⇒ 归入「未标注」一档 ✓ 并如实写明 ✓（**不许猜**它属于哪把 ✗）
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

from . import pricing

UNLABELED = "（未标注）"   # 早期记录：那时还没记 key_env ✓（不许猜它属于哪把 ✗）

#: 能算钱的只有语言模型这一档 ✓（别的按次/按秒，拿不到单价 ✗）
_MEASURABLE_ROLE = "语言模型"


def key_roles(cfg: Any) -> dict[str, list[str]]:
    """哪把 Key 现在被哪些能力用着 —— **口径只有这一处** ✓（界面和闸门都读它 ✓）。

    为什么值得单列：用户说"每个 Key 花费上限" ✓ 而他一个 Key 往往被好几个能力共用 ✓
    （比如 DASHSCOPE 既出图又出视频 ✓）⇒ 界面上必须能看出来"这把 Key 是谁在用" ✓
    否则他设了上限却不知道影响谁 ✓。
    """
    out: dict[str, list[str]] = {}

    def add(env: Any, role: str) -> None:
        name = str(env or "").strip()
        if name:
            out.setdefault(name, []).append(role)

    model = getattr(cfg, "model", None)
    add(getattr(model, "api_key_env", ""), _MEASURABLE_ROLE)
    add(getattr(getattr(cfg, "image", None), "api_key_env", ""), "出图")
    add(getattr(getattr(cfg, "asr", None), "api_key_env", ""), "语音识别")
    add(getattr(getattr(cfg, "video", None), "api_key_env", ""), "出视频")
    for name, spec in (getattr(getattr(cfg, "video", None), "engines", None) or {}).items():
        if isinstance(spec, dict):
            add(spec.get("api_key_env"), f"出视频（{name}）")
    add(getattr(getattr(cfg, "kb", None), "api_key_env", "DASHSCOPE_API_KEY"), "知识库向量（云端档）")
    return out


def llm_key(cfg: Any) -> str:
    """当前语言模型用的那把 Key 名（空 = 没配/本地 mock ⇒ 闸门不管 ✓）。"""
    return str(getattr(getattr(cfg, "model", None), "api_key_env", "") or "").strip()


def caps(cfg: Any) -> dict[str, dict[str, Any]]:
    """规范化配置里的上限表：`{key名: {"limit": 元, "since": ISO 时间}}`。

    坏数据一律忽略 ✓（不因为一条脏配置就把整台机器拦住 ✗ —— 与 `pricing.configure` 同规矩 ✓）。
    """
    raw = getattr(cfg, "budget", None)
    table = getattr(raw, "caps", None) if raw is not None else None
    out: dict[str, dict[str, Any]] = {}
    for key, spec in (table or {}).items():
        if not isinstance(spec, dict):
            continue
        try:
            limit = float(spec.get("limit") or 0)
        except (TypeError, ValueError):
            continue
        if limit <= 0:
            continue
        since = str(spec.get("since") or "")
        out[str(key)] = {"limit": limit, "since": since}
    return out


def event_cny(u: dict[str, Any]) -> float | None:
    """一条用量事件折成钱（没填单价 ⇒ None ✓ 绝不编 ✓）。"""
    model = str(u.get("model") or "")
    return pricing.estimate(
        model,
        int(u.get("input_tokens") or 0),
        int(u.get("output_tokens") or 0),
        int(u.get("cached_tokens") or 0),
    )


def spend(
    task_ids: Iterable[str],
    read_events: Callable[[str], list[Any]],
    since: str = "",
) -> dict[str, dict[str, Any]]:
    """按 Key 汇总花费 ✓（数据源 = 任务里的用量事件 ✓ 与 `/usage` 同一份 ✓）。

    返回 `{key名: {cny, tokens, calls, priced_calls, unpriced_calls, tasks}}`。
    `since` 非空时只算**那一刻之后**的用量事件（事件的 `ts` ✓）。
    """
    out: dict[str, dict[str, Any]] = {}

    def slot(key: str) -> dict[str, Any]:
        return out.setdefault(key, {"cny": 0.0, "tokens": 0, "calls": 0,
                                    "priced_calls": 0, "unpriced_calls": 0, "tasks": 0})

    for tid in task_ids:
        try:
            events = read_events(tid)
        except Exception:                     # noqa: BLE001 —— 单个任务读不了不该拖垮整张账 ✓
            continue
        touched: set[str] = set()
        for ev in events:
            if getattr(ev, "type", "") != "knowledge":
                continue
            if "用量" not in str((getattr(ev, "payload", None) or {}).get("title", "")):
                continue
            if since and str(getattr(ev, "ts", "") or "") < since:
                continue
            u = (getattr(ev, "payload", None) or {}).get("usage")
            if not isinstance(u, dict):
                continue                            # 旧任务只有文本、没有结构化用量 ⇒ 钱算不了 ✓
            calls = int(u.get("calls") or 0)
            if calls <= 0:
                continue
            key = str(u.get("key_env") or "").strip() or UNLABELED
            s = slot(key)
            s["calls"] += calls
            s["tokens"] += int(u.get("input_tokens") or 0) + int(u.get("output_tokens") or 0)
            cny = event_cny(u)
            if cny is None:
                s["unpriced_calls"] += calls        # 没单价 ⇒ 只记次数、不记钱 ✓（不编 ✗）
            else:
                s["cny"] = round(float(s["cny"]) + cny, 4)
                s["priced_calls"] += calls
            touched.add(key)
        for key in touched:
            out[key]["tasks"] += 1
    return out


def status(
    cfg: Any,
    task_ids: Iterable[str],
    read_events: Callable[[str], list[Any]],
) -> dict[str, Any]:
    """给界面看的一份完整状态 ✓（含"哪些 Key 管不了" ✓ —— 这是诚实的关键 ✓）。"""
    roles = key_roles(cfg)
    table = caps(cfg)
    llm = llm_key(cfg)

    # 所有出现过的 Key：配置里在用的 + 记过账的（含"未标注"✓）
    every = dict.fromkeys([*roles.keys(), *table.keys()])
    tk = list(task_ids)
    # 每把 Key 各按自己的 since 汇总（各设各的上限 ✓ 互不影响 ✓）
    merged: dict[str, dict[str, Any]] = {}
    for key, spec in table.items():
        merged[key] = spend(tk, read_events, since=str(spec.get("since") or "")).get(key, _blank())
    # 没设上限的 Key：按"全部时间"汇总一次就够（只用于显示 ✓）
    all_time = spend(tk, read_events)
    for key in every:
        if key not in merged:
            merged[key] = all_time.get(key, _blank())

    rows: list[dict[str, Any]] = []
    for key in sorted(set(list(every) + list(all_time.keys()))):
        roles_of = roles.get(key, [])
        measurable = _MEASURABLE_ROLE in roles_of
        spec = table.get(key)
        row = {
            "key": key,
            "roles": roles_of,
            "measurable": measurable,               # 上限对这把 Key **管不管用** ✓
            "used": bool(merged.get(key, {}).get("calls")),
            "cny": float(merged.get(key, {}).get("cny") or 0.0),
            "tokens": int(merged.get(key, {}).get("tokens") or 0),
            "calls": int(merged.get(key, {}).get("calls") or 0),
            "unpriced_calls": int(merged.get(key, {}).get("unpriced_calls") or 0),
            "limit": float(spec["limit"]) if spec else 0.0,
            "since": str(spec.get("since") or "") if spec else "",
            "all_time_cny": float(all_time.get(key, {}).get("cny") or 0.0),
            "is_llm": key == llm,
        }
        if spec:
            row["left"] = round(max(0.0, float(spec["limit"]) - row["cny"]), 4)
            row["over"] = row["cny"] >= float(spec["limit"])
        rows.append(row)

    blocked = blocking_reason(cfg, tk, read_events)
    return {
        "ok": True,
        "keys": rows,
        "llm_key": llm,
        "enabled": bool(getattr(getattr(cfg, "budget", None), "enabled", True)),
        "blocked": blocked,
        # 界面上要原样显示的实话 ✓（不许编 ✗）
        "note": ("上限**只管语言模型**的调用（那档才按 token 算得出钱）✓；"
                 "出图 / 出视频 / 语音是**按次或按秒**计费，本程序拿不到单价 ⇒ "
                 "**这几档拦不住** ✗，别指望它 ✓。"),
    }


def _blank() -> dict[str, Any]:
    return {"cny": 0.0, "tokens": 0, "calls": 0, "priced_calls": 0, "unpriced_calls": 0, "tasks": 0}


def blocking_reason(
    cfg: Any,
    task_ids: Iterable[str],
    read_events: Callable[[str], list[Any]],
) -> str | None:
    """语言模型该不该**停下** ✓ —— 返回一句**人能照做**的话，或 None（可以继续 ✓）。

    只在**真超了**的时候返回 ✓：宁可漏报也不能因为脏数据/读不到事件就误拦 ✗
    （把用户的任务无缘无故掐掉，比多花几毛钱糟得多 ✓）。
    """
    if not bool(getattr(getattr(cfg, "budget", None), "enabled", True)):
        return None
    spec = caps(cfg).get(llm_key(cfg))
    if not spec:
        return None
    key = llm_key(cfg)
    got = spend(list(task_ids), read_events, since=str(spec.get("since") or "")).get(key)
    if not got or float(got["cny"]) < float(spec["limit"]):
        return None                                  # 没单价/没用量 ⇒ 算不出钱 ⇒ 不拦 ✓
    return (
        f"「{key}」这把 Key 已花到上限（已花 ¥{float(got['cny']):.4f} / 上限 ¥{float(spec['limit']):.2f}），"
        "**这一步没有发出去** —— 免得继续烧钱。三条路：\n"
        "  ① 提高上限：设置 → 花费上限（改完立刻生效 ✓）\n"
        "  ② 清零重来：同一处点「从现在重新计」（之前的账不会消失，只是不计入本上限 ✓）\n"
        "  ③ 换一把 Key：设置 → 模型设置里换 API Key\n"
        "（说明：这个上限只管**语言模型**；出图/出视频/语音是按次计费，它拦不住 —— 见设置页原话。）"
    )


def over_limit_now(
    cfg: Any,
    task_ids: Iterable[str],
    read_events: Callable[[str], list[Any]],
) -> str | None:
    """`blocking_reason` 的别名（loop 里读起来顺一点 ✓）。"""
    return blocking_reason(cfg, task_ids, read_events)
