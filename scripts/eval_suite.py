# -*- coding: utf-8 -*-
"""★ 2026-10-06：**引擎评测台**（我们的"尺子"）—— 让"调优"从凭感觉变成有数字。

## 为什么要有它

Manus 2.0 发布时能报出「比上一版省 **23.2%** token、快 **28.2%**、便宜 **32%**」——
那是**引擎级 A/B** 的结果（他们的 harness 叫 Cascade）。
我们此前只有"真跑一轮看看"：改完之后只能说"感觉快了/稳了" ✗ —— 说不出百分比 ✓。

这套东西就是补那一环：**同一批任务，改动前后各跑一遍，比数字** ✓。

## 它测什么（指标取自本仓库已有的机制，不额外埋点 ✓）

每个任务跑完，从**群记录 + 用量事件**里读：
· 计划项 **完成/失败/被挡** 数            ← 有没有真的做完
· **因缺小节被打回**次数                  ← 交付质量（降本那几刀就是治它）
· **项目验收门**开了没、结论是什么        ← "做完 ≠ 能跑"那道门
· **总 token**、**总用时**、**人工放行次数** ← 成本与人工干预
· 任务结束后的**产物清单**                ← 顺手确认它真写了文件

## 怎么用

    python scripts/eval_suite.py                       # 跑全部任务（约 ¥1–3）
    python scripts/eval_suite.py --only smoke-python   # 只跑一个
    python scripts/eval_suite.py --tag before-fix      # 给这轮打标签，便于 A/B 对比
    python scripts/eval_suite.py --list                # 看任务清单

结果写到 `backend/data/eval_runs/<时间>-<标签>.json`，并在终端打一张表 ✓。

## 注意

· 它会**真的建群、真的跑模型**（要花钱 ✓）；跑之前确认后端在跑（8642）✓。
· **不改引擎任何一行** ✓ —— 它只是"用"引擎 ✓（所以不存在把产品改坏的风险 ✓）。
· 判分**不做主观打分**：全部来自仓库里已有的客观机制（小节校验 / 验收门 / 用量事件）✓
  —— 这样"好没好"才不是我说了算 ✗，而是数字说了算 ✓。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = pathlib.Path(__file__).resolve().parents[1]
TASKS = ROOT / "scripts" / "eval_tasks.json"
OUTDIR = ROOT / "backend" / "data" / "eval_runs"
BASE = "http://127.0.0.1:8642/api/v1"

CFG = json.loads((ROOT / "config.json").read_text("utf-8"))
TOK = CFG["server"]["access_token"]
MODEL = str((CFG.get("model") or {}).get("model_name") or "?")
H = {"X-Auth-Token": TOK, "Content-Type": "application/json"}


def call(path: str, data=None, method="GET"):
    req = urllib.request.Request(f"{BASE}{path}", headers=H, method=method,
                                 data=json.dumps(data).encode() if data is not None else None)
    try:
        return json.load(urllib.request.urlopen(req, timeout=90))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:200]
        raise SystemExit(f"✗ {method} {path} → {e.code}：{body}") from None


def make_group(stamp: str) -> tuple[str, list[str]]:
    """建三个员工 + 一个组长模式的群（评测统一用组长模式 ✓ —— 它是功能最全的那条路 ✓）。"""
    ids, names = [], []
    for nm, role in (("评测甲", "架构师"), ("评测乙", "程序员"), ("评测丙", "测试工程师")):
        full = f"{nm}{stamp}"
        e = call("/team/employees", {"name": full, "dept": "技术部", "role": role,
                                     "persona": "干活", "mode": "expert"}, "POST")
        ids.append(e.get("id") or e.get("employee", {}).get("id"))
        names.append(full)
    # ★ 组长模式**必须指定组长** ✗ —— 本班第一版漏了 `leader` ⇒ `say` 回来 `dispatched` 是空的 ✓
    #   （没组长就没人拆解目标 ✓）。e2e 驱动脚本也是这么建的 ✓。
    g = call("/team/groups", {"name": f"评测-{stamp}", "members": ids,
                              "leader": ids[0], "mode": "leader"}, "POST")
    return g["id"], names


def group_state(gid: str) -> dict:
    """取某个群的状态。

    ★ 没有 `GET /team/groups/{gid}` 这个接口 ✗（本班踩过 404 ✓）—— 只有**列表**接口 ✓，
    所以从列表里挑出这一个 ✓。
    """
    gs = call("/team/groups").get("groups") or []
    return next((g for g in gs if g.get("id") == gid), {})


def run_one(task: dict, stamp: str, budget_s: float) -> dict:
    t0 = time.time()
    gid, names = make_group(stamp)
    approvals = 0

    # 任务自带 setup ⇒ 先把素材写进群工作区（"改 bug"这种任务需要有个东西可改 ✓）
    if task.get("setup"):
        ws = ROOT / "backend" / "data" / "groups" / gid / "workspace"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "SETUP.md").write_text(str(task["setup"]), encoding="utf-8")

    r = call(f"/team/groups/{gid}/say", {"text": task["goal"]}, "POST")
    if not r.get("dispatched"):
        # ★ 派不出去时**把群里的原话打出来** ✓ —— 否则只有一句"没派出去"，排查全靠猜 ✗
        print("   ⚠️ 回执：", json.dumps(r, ensure_ascii=False)[:160])
        try:
            for m in call(f"/team/groups/{gid}/feed")["messages"][-3:]:
                print(f"   #{m['seq']} [{m.get('from')}] {str(m.get('text') or '')[:110]}")
        except SystemExit:
            pass
        return {"id": task["id"], "ok": False, "why": "没派出去", "gid": gid,
                "elapsed_s": 0, "tokens": 0, "approvals": 0}

    # 边等边放行审批；出现收口行就停
    while time.time() - t0 < budget_s:
        time.sleep(6)
        feed = call(f"/team/groups/{gid}/feed")["messages"]
        handled = {m["seq"] for m in feed if str(m.get("text") or "").startswith("✅ 已在群里处理")}
        for m in feed:
            ap = m.get("approval") or {}
            if ap.get("call_id") and not any(s > m["seq"] for s in handled):
                try:
                    call(f"/team/groups/{gid}/approve",
                         {"task_id": m["task_id"], "call_id": ap["call_id"], "decision": "all"}, "POST")
                    approvals += 1
                except SystemExit:
                    pass                                   # 审批失效/已处理：忽略，别把评测跑挂 ✓
        tail = " ".join(str(m.get("text") or "") for m in feed[-5:])
        if re.search(r"全部完成|收工|✅ 项目验收|❌ 项目验收", tail):
            break

    feed = call(f"/team/groups/{gid}/feed")["messages"]
    texts = [str(m.get("text") or "") for m in feed]
    joined = "\n".join(texts)
    st = group_state(gid)

    # ── 客观指标（全部来自仓库已有机制 ✓ 没有主观打分 ✗）──
    done = failed = blocked = 0
    for it in (st.get("leader_plan") or []):
        done += it.get("status") == "done"
        failed += it.get("status") == "failed"
        blocked += it.get("status") == "blocked"
    rejects = sum(1 for t in texts if "❌ 打回" in t)
    gate_lines = [t for t in texts if "项目验收通过" in t or "项目验收不通过" in t]
    gate = ("通过" if any("✅ 项目验收通过" in t for t in gate_lines)
            else "不通过" if any("❌ 项目验收不通过" in t for t in gate_lines)
            else "没开到")
    tok = 0
    # ★★ 2026-10-06 修正（**尺子自己算错了** ✗）：原来这里 `max()` 取最大值，
    #   于是"谁最贵"变成了总数 ⇒ 第 9 轮报 36.8 万，实际是 **45.2 万**（少了验收那 8.4 万 ✗）。
    #   正确做法：把群里所有 `📊 … = N tok` 的**每一步**加起来 ✓（每步一行 ✓ 互不重叠 ✓）。
    for m in re.finditer(r"=\s*([\d,]+)\s*tok", joined):
        tok += int(m.group(1).replace(",", ""))
    ws = ROOT / "backend" / "data" / "groups" / gid / "workspace"
    arts = sorted(p.name for p in ws.rglob("*") if p.is_file()) if ws.exists() else []

    return {"id": task["id"], "title": task.get("title"), "gid": gid, "ok": True,
            "done": done, "failed": failed, "blocked": blocked,
            "rejects": rejects, "gate": gate, "tokens": tok, "approvals": approvals,
            "elapsed_s": int(time.time() - t0), "artifacts": arts[:12],
            "verdict": bool(done and not failed and gate == "通过")}


def _load_tasks() -> list[dict]:
    """读任务集。

    ★ 任务文件是**带注释的 JSON** ✓（注释是为了让下一个人看懂"为什么有这条" ✓）——
    而 JSON 标准不认注释 ✗ ⇒ 这里**先剥掉 `#` 开头的行**再解析 ✓。
    （本班第一版直接 json.loads ⇒ 报 "Expecting value: line 1 column 1" ✗）
    """
    raw = TASKS.read_text("utf-8")
    body = "\n".join(ln for ln in raw.splitlines() if not ln.lstrip().startswith("#"))
    return json.loads(body)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只跑某个任务 id")
    ap.add_argument("--tag", default="", help="给这轮结果打标签（便于 A/B 对比）")
    ap.add_argument("--list", action="store_true", help="只看任务清单")
    ap.add_argument("--budget-s", type=float, default=1800.0, help="单个任务的等待上限（秒）")
    a = ap.parse_args()

    tasks = _load_tasks()
    if a.list:
        print(f"任务集（{len(tasks)} 个）：")
        for t in tasks:
            print(f"  {t['id']:<18} [{t.get('size','?'):<6}] {t.get('title','')}")
        return 0
    if a.only:
        tasks = [t for t in tasks if t["id"] == a.only]
        if not tasks:
            print(f"✗ 没有这个任务：{a.only}"); return 2

    # ★ 名字要**真唯一** ✗ —— 只用"分"做后缀，同一分钟内跑两次就撞名 ⇒ 422（本班踩过 ✓）
    import random
    stamp = f"{time.strftime('%m%d%H%M%S')}{random.randint(10, 99)}"
    print(f"评测开始 · 模型 {MODEL} · {len(tasks)} 个任务 · 后端 {BASE}")
    results = []
    for t in tasks:
        print(f"\n▶ {t['id']} — {t.get('title','')}")
        try:
            r = run_one(t, stamp, a.budget_s)
        except SystemExit as e:
            r = {"id": t["id"], "ok": False, "why": str(e)}
        results.append(r)
        print("  →", json.dumps({k: v for k, v in r.items() if k != "artifacts"},
                                ensure_ascii=False))

    OUTDIR.mkdir(parents=True, exist_ok=True)
    out = OUTDIR / f"{stamp}{('-' + a.tag) if a.tag else ''}.json"
    out.write_text(json.dumps({"model": MODEL, "tag": a.tag, "tasks": results,
                               "at": time.strftime("%Y-%m-%d %H:%M:%S")},
                              ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n================ 评测结果 ================")
    print(f"{'任务':<18}{'完成/失败/被挡':<16}{'打回':<6}{'验收':<8}{'token':>10}{'用时':>7}{'放行':>5}  判定")
    for r in results:
        if not r.get("ok"):
            print(f"{r['id']:<18}{'—':<16}{'—':<6}{'—':<8}{'—':>10}{'—':>7}{'—':>5}  ✗ {r.get('why')}")
            continue
        trio = f"{r['done']}/{r['failed']}/{r['blocked']}"
        print(f"{r['id']:<18}{trio:<16}{r['rejects']:<6}{r['gate']:<8}"
              f"{r['tokens']:>10,}{r['elapsed_s']:>6}s{r['approvals']:>5}  "
              f"{'✅ 通过' if r['verdict'] else '✗ 未通过'}")
    print(f"\n原始结果：{out}")
    ok = sum(1 for r in results if r.get("verdict"))
    print(f"总计 {ok}/{len(results)} 个任务达标")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
