"""重放：84 条真机命令 × 修好之后的判定 ⇒ 对比"改前 78 次 ask"。

两种口径：
  A. 判据口径：不改任何记忆（每次都是"第一次遇到"）—— 看真实摩擦
  B. 用户口径：模拟用户对每条 ask 点「总是允许」（带**完整命令**✓）—— 看同一任务内
     同一条命令还会不会被问第二遍
只读诊断。
"""
import io
import json
import sys
from collections import Counter
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\AI\agent-shell\backend")

TASK = Path(r"D:\测试-Flash对比\LanternLogic-Agent\backend\data\tasks\task_20261011_90f7d58b")
EVENTS = TASK / "events.jsonl"
WORKSPACE = TASK / "workspace"
REQ = ["rm", "del", "rmdir", "rd", "erase", "format", "reg", "remove-item"]

from app.approval import ApprovalManager, _normalize_path  # noqa: E402


def load_cmds():
    out = []
    for line in EVENTS.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("type") != "action":
            continue
        pl = o.get("payload") or {}
        if pl.get("tool") != "shell_exec":
            continue
        out.append((o.get("seq"), str((pl.get("params") or {}).get("command", ""))))
    return out


def is_inside(p: str) -> bool:
    try:
        base = WORKSPACE.resolve()
        pp = Path(p)
        resolved = (base / pp).resolve() if not pp.is_absolute() else pp.resolve()
        return resolved.is_relative_to(base)
    except Exception:
        return False


def target_exists(tok: str):
    try:
        p = Path(_normalize_path(tok))
        if not p.is_absolute():
            p = WORKSPACE / p
        if p.exists():
            return True
        return False if p.parent.exists() else None
    except Exception:
        return None


def main():
    cmds = load_cmds()
    print("真机 shell 命令总数 =", len(cmds))
    for label, with_memory in (("A 判据口径（不点任何授权）", False),
                               ("B 用户口径（每条都点「总是允许」）", True)):
        am = ApprovalManager()
        am._forever.clear()
        asks = Counter()
        ask_rows = []
        for seq, cmd in cmds:
            v = am.check("task_x", cmd, REQ, is_inside, in_sandbox=False,
                         network_disabled=False, script_reader=None,
                         target_exists=target_exists)
            if v is not None and v.action == "ask":
                asks[v.key] += 1
                ask_rows.append((seq, v.key, cmd))
                if with_memory:
                    am.remember("task_x", v.key, cmd)      # 用户点了「总是允许」（带全文 ✓）
        print(f"\n===== {label} =====")
        print(f"弹审批次数 = {len(ask_rows)} / {len(cmds)}")
        for k, n in asks.most_common(40):
            print(f"   {n:4}  {k}")
        if ask_rows:
            print("   逐条：")
            for seq, key, cmd in ask_rows:
                print(f"     seq {seq:4} [{key[:44]:44}] {cmd.strip().splitlines()[0][:70]}")
    # 哪些 command 被重复问（同一条原文出现多次 ⇒ 记忆口径的价值）
    norm = Counter(" ".join(c.split()) for _, c in cmds)
    dup = {k: n for k, n in norm.items() if n > 1}
    print(f"\n同一条命令（归一空白后）出现 ≥2 次的种类 = {len(dup)}，多问次数 = {sum(n-1 for n in dup.values())}")


main()
