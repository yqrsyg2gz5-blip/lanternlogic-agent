# -*- coding: utf-8 -*-
"""变异测试（第一期）：回答"这 1040 个用例到底有多少是真能抓 bug 的"。

为什么做（审计台账 §四最后一条"变异测试从未做"，主待修清单列为"唯一能治假绿"）：
  2026-10-04 的 P0 是最好的理由 —— C7 把 task_id 从 4 位 hex 提到 8 位，
  契约没跟着放开，**新任务建出来就死**，而 985 个用例 + 55 组红绿**全绿**。
  红绿 harness 只覆盖"我们知道要防的那 65 处"；变异测试覆盖"我们没想到的地方"。

怎么做：
  ① 把源码复制到 %TEMP% 副本（真仓库零接触；与 redgreen_check.py 同套路）
  ② 造变异体（两类）：
     · **阳性对照**（CURATED）：已知被红绿覆盖的站点，**必须被杀死** ——
       它们用来验证本 harness 本身有效（杀不死 ⇒ harness 有问题，不是代码有问题）
     · **通用变异**（GENERIC）：按算子自动改一行（`==`↔`!=`、`is None`↔`is not None`、
       边界 `<`↔`<=`、`and`↔`or`、`True`↔`False`、`!`去反、`"ask"`↔`"allow"`…）
  ③ 杀死判据：跑定向测试子集，**rc != 0 即"被杀死"**；rc == 0 ⇒ **存活（盲区）**
     · 变异体先过 `ast.parse`：语法坏的变异体不算数（记录为 invalid，不进分母）
     · 存活者再用**全量套件**复核一次（子集可能漏网）
  ④ 输出：每个文件的变异得分 + 存活清单（文件:行 + 原行 → 变异行）

用法：
    backend\\.venv\\Scripts\\python.exe scripts\\mutation_check.py
    ... --max 60            限制变异体总数（默认 80）
    ... --only approval.py  只测某个文件（可重复）
    ... --no-confirm        跳过"存活者全量复核"（只做快速筛查）
    ... --seed 7            算子选择随机种子（可复现）
退出码：0 = 所有变异体都被杀死；1 = 有存活（盲区清单见输出）
"""
from __future__ import annotations

import argparse
import ast
import os
import pathlib
import random
import re
import shutil
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
_NO_PYC = shutil.ignore_patterns("__pycache__")

_TMP = pathlib.Path(tempfile.mkdtemp(prefix="mutmut-"))
shutil.copytree(ROOT / "backend" / "app", _TMP / "backend" / "app", ignore=_NO_PYC)
shutil.copytree(ROOT / "backend" / "tests", _TMP / "backend" / "tests", ignore=_NO_PYC)
shutil.copy2(ROOT / "backend" / "pytest.ini", _TMP / "backend" / "pytest.ini")
(_TMP / "frontend").mkdir(parents=True, exist_ok=True)
shutil.copytree(ROOT / "frontend" / "src", _TMP / "frontend" / "src", ignore=_NO_PYC)
shutil.copytree(ROOT / "contracts", _TMP / "contracts", ignore=_NO_PYC)
import atexit

atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))

# 模块 → 定向测试子集（跑得快；存活者再用全量复核补刀）
SUBSETS: dict[str, list[str]] = {
    "approval.py": [
        "tests/test_approval_capability_matrix.py", "tests/test_approval_failclosed.py",
        "tests/test_approval_reaudit.py", "tests/test_approval_security.py",
        "tests/test_round9_fixes.py", "tests/test_round10_fixes.py",
        "tests/test_mcp_approval.py", "tests/test_mcp_sensitive_approval.py",
        "tests/test_assign_prefix.py", "tests/test_git_config_env_inject.py",
    ],
    "redact.py": [
        "tests/test_a1_redaction.py", "tests/test_loop_history_recording.py",
        "tests/test_usage_endpoint.py", "tests/test_notify_redact.py",
        "tests/test_host_mode_notice.py",
    ],
    "ssrf.py": ["tests/test_ssrf.py", "tests/test_webhook_security.py"],
    "store.py": [
        "tests/test_a1_redaction.py", "tests/test_task_delete_files.py",
        "tests/test_task_id_contract_consistency.py", "tests/test_delivery_outcome.py",
    ],
    "notify.py": ["tests/test_notify_redact.py"],
    "local.py": [
        "tests/test_shell_env.py", "tests/test_ssrf.py", "tests/test_host_mode_notice.py",
        "tests/test_install_melotts.py",
    ],
    "loop.py": [
        "tests/test_loop_history_recording.py", "tests/test_approval_flow.py",
        "tests/test_delivery_gate.py", "tests/test_injection_order.py",
        "tests/test_mcp_sensitive_approval.py", "tests/test_host_mode_notice.py",
        "tests/test_a1_redaction.py",
        # 不可信包裹的接线锚点在 test_ssrf.py 里（首版漏了它 ⇒ 对照变异体假存活）
        "tests/test_ssrf.py",
    ],
    # ★ 第三期新增：本班新写/大改过的模块（此前没有子集映射，等于没被变异测过）
    "main.py": [
        "tests/test_settings_reset.py", "tests/test_settings_portability.py",
        "tests/test_access_token_local_only.py", "tests/test_capabilities.py",
        "tests/test_model_list.py", "tests/test_webhook_security.py",
        "tests/test_preflight.py", "tests/test_host_mode_notice.py",
    ],
    "asr.py": ["tests/test_asr_format.py", "tests/test_asr_two_tier.py"],
    "providers/inline_tool.py": ["tests/test_inline_tool_calls.py"],
    "config.py": ["tests/test_config_write_isolation.py", "tests/test_key_persist.py"],
}
SUBSETS["executors/local.py"] = SUBSETS["local.py"]

# ── 阳性对照：已知被 scripts/redgreen_check.py 覆盖的站点，**必须被杀死** ──
# 格式：(标签, 相对 backend/app 的路径, 原文, 变异后)
CURATED: list[tuple[str, str, str, str]] = [
    ("[对照] observation 面打码接线", "loop.py",
     "        return redact_text_tool(text)",
     "        return text  # MUTANT: 打码撤除"),
    ("[对照] 不可信包裹接线", "loop.py",
     '            self.history.append({"role": "tool", "tool_call_id": call_id,\n'
     '                                 "content": _wrap_untrusted(name, output[:4000])})',
     '            self.history.append({"role": "tool", "tool_call_id": call_id,\n'
     '                                 "content": output[:4000]})  # MUTANT'),
    ("[对照] action 事件打码", "loop.py",
     'self.emit("action", {"tool": name, "params": _redact_deep(args), "call_id": call_id})',
     'self.emit("action", {"tool": name, "params": args, "call_id": call_id})  # MUTANT'),
    ("[对照] history arguments 打码", "loop.py",
     '"function": {"name": name, "arguments": json.dumps(_redact_deep(args), ensure_ascii=False)},',
     '"function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},  # MUTANT'),
    ("[对照] A3 宿主模式告知", "executors/local.py",
     "        return text + _HOST_EXEC_NOTE",
     "        return text  # MUTANT"),
    ("[对照] B10 出网面打码", "notify.py",
     '    safe_title = redact_text(str(task_title or ""))\n'
     '    safe_summary = redact_text(str(summary or ""))[:500]',
     '    safe_title = str(task_title or "")  # MUTANT\n'
     '    safe_summary = str(summary or "")[:500]  # MUTANT'),
    ("[对照] A1 对话正文落盘打码", "store.py",
     '            dump["payload"] = _redact_message_payload(dump.get("payload") or {})',
     "            pass  # MUTANT: 落盘不打码"),
    ("[对照] C5 MCP 敏感目标审批", "loop.py",
     "                else:\n"
     "                    # C5：名字像只读的也不能无条件静默——命中敏感目标（凭据/私钥/\n"
     "                    # 密钥库/.env/中文\"密码·密钥\"命名/`..` 穿越）一律先问。\n"
     "                    risky = _mcp_risky_target(args)",
     "                else:\n"
     "                    risky = None  # MUTANT"),
    ("[对照] D1 不可核实 fail-closed", "approval.py",
     "                if state is None:\n"
     "                    # ★ D1（2026-10-04 实测修）：fail-closed **不分模式**。",
     "                if state is None and in_sandbox:  # MUTANT\n"
     "                    # ★ D1（2026-10-04 实测修）：fail-closed **不分模式**。"),
    # ── P0 回归金丝雀（2026-10-04 那次"新任务全废"的两个半边）──
    # 只断言"id 长这样"抓不到它们；能杀死才说明"端到端契约锚点"真的在位。
    ("[金丝雀] C7 生成端回退（8 位 hex → 4 位）", "main.py",
     '        tid = f"task_{datetime.now():%Y%m%d}_{secrets.token_hex(4)}"',
     '        tid = f"task_{datetime.now():%Y%m%d}_{secrets.token_hex(2)}"  # MUTANT'),
    ("[金丝雀] C7 契约端回退（{4,32} → {4}）", "schemas.py",
     r'_TASK_ID_RE = re.compile(r"^task_\d{8}_[a-z0-9]{4,32}$")',
     r'_TASK_ID_RE = re.compile(r"^task_\d{8}_[a-z0-9]{4}$")  # MUTANT'),
]

# 泛化第二遍用的"广谱"子集：模块子集没抓到就再过一遍它（比全量便宜 5-8 倍，
# 用来少跑几次 40 秒的全量复核）。真正的最终判据仍是全量套件。
BROAD = [
    "tests/test_e2e_reverify.py", "tests/test_a1_redaction.py", "tests/test_blindspots.py",
    "tests/test_delivery_outcome.py", "tests/test_usage_endpoint.py",
    "tests/test_lan_onboarding.py", "tests/test_settings_executor.py",
    "tests/test_approval_flow.py", "tests/test_loop_history_recording.py",
]

# ── 通用算子：逐行匹配，一行一变异 ──
GENERIC_OPS: list[tuple[str, str, str]] = [
    ("eq-flip", "==", "!="),
    ("ne-flip", "!=", "=="),
    ("none-flip", "is None", "is not None"),
    ("notnone-flip", "is not None", "is None"),
    ("le-to-lt", "<=", "<"),
    ("ge-to-gt", ">=", ">"),
    ("lt-to-le", "<", "<="),
    ("gt-to-ge", ">", ">="),
    ("and-to-or", " and ", " or "),
    ("or-to-and", " or ", " and "),
    ("true-to-false", "True", "False"),
    ("false-to-true", "False", "True"),
    ("ask-to-allow", '"ask"', '"allow"'),
    ("allow-to-ask", '"allow"', '"ask"'),
    ("return-none-to-true", "return None", "return True"),
    # ★ 第三期新增算子（专门冲本会话那类"看着绿其实没测到"的门）：
    #   · in / not in 反转 —— 白名单/黑名单判定的经典错法（`sec not in _RESETTABLE`、
    #     `host in {"127.0.0.1", ...}`、路径包含判定都在用）
    #   · 状态码 403/401/422 → 200 —— "拒绝"变成"放行"，只有真断言拒绝的测试才杀得掉
    ("in-to-notin", " not in ", " in "),
    ("notin-to-in", " in ", " not in "),
    ("403-to-200", "403", "200"),
    ("401-to-200", "401", "200"),
    ("422-to-200", "422", "200"),
]

# 只变异"像安全判定"的行；跳注释/文档/装饰器/import
_INTERESTING = re.compile(
    r"\b(if|return|raise|assert|ask|allow|deny|blocked|outside|redact|sandbox|dangerous|"
    r"None|True|False|in_sandbox|fail)\b"
)
_SKIP_LINE = re.compile(r"^\s*(#|\"\"\"|'''|@|import |from |\)\s*$|else:|try:|except|finally:|with )")
# 首版 12/80 个变异体因"改完语法坏"白扔（26%）。这几类行天然容易改坏：
#   · 以 `:` 收尾（if/def/for 的头）——`and`→`or` 之类改不出语法错，但 `True`→`False`
#     落在注解/默认值上就会坏； · `def`/`class`/`lambda` 签名行；
#   · 行内只有字符串字面量（"ask" 出现在文案里）。
_BAD_LINE = re.compile(r"^\s*(def |class |lambda )|:\s*(#.*)?$")


def _targets(files: list[str]) -> list[pathlib.Path]:
    out = []
    for f in files:
        p = _TMP / "backend" / "app" / f
        if p.exists():
            out.append(p)
    return out


def _run(test_ids: list[str], full: bool) -> tuple[int, str]:
    args = [str(PY), "-m", "pytest", *([] if full else test_ids),
            "-o", "addopts=", "-p", "no:warnings", "-x", "-q", "--tb=no"]
    r = subprocess.run(args, cwd=_TMP / "backend", capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
    return r.returncode, (tail[-1] if tail else "")


def _mutants_for(path: pathlib.Path, per_file: int, rng: random.Random):
    """产出 (行号, 算子名, 原行, 变异行) —— 每行最多一个算子（随机挑一个可用的）。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    cands = []
    for i, line in enumerate(lines):
        if _SKIP_LINE.match(line) or _BAD_LINE.search(line) or not _INTERESTING.search(line):
            continue
        ops = [(name, old, new) for name, old, new in GENERIC_OPS if old in line]
        if not ops:
            continue
        rng.shuffle(ops)
        picked = None
        for name, old, new in ops:
            mutated = line.replace(old, new, 1)
            if mutated == line:
                continue
            # ★ 用【整文件 ast.parse】预筛：首版 12/80 个变异体改完语法坏、白扔 26%
            #   （如 `True`→`False` 落在注解/默认值上）。这里一次 parse 就能筛掉。
            trial = "\n".join(lines[:i] + [mutated] + lines[i + 1:])
            try:
                ast.parse(trial)
            except SyntaxError:
                continue
            picked = (name, mutated)
            break
        if picked is None:
            continue
        name, mutated = picked
        cands.append((i + 1, name, line, mutated))
    rng.shuffle(cands)
    return cands[:per_file]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=80)
    ap.add_argument("--per-file", type=int, default=14)
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--no-confirm", action="store_true")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    rng = random.Random(a.seed)

    files = a.only or list(SUBSETS)
    plan: list[tuple[str, str, pathlib.Path, str, str]] = []   # (标签, 算子, 文件, 原, 变异)

    # 阳性对照优先（它们决定 harness 可信不可信）
    for label, rel, old, new in CURATED:
        p = _TMP / "backend" / "app" / rel
        if rel.split("/")[-1] in [f.split("/")[-1] for f in files] or not a.only:
            plan.append((label, "curated", p, old, new))
    # 通用
    for p in _targets(files):
        for ln, op, old, new in _mutants_for(p, a.per_file, rng):
            plan.append((f"{p.name}:{ln}", op, p, old, new))

    plan = plan[: a.max]
    print(f"# 变异测试（第一期）｜副本={_TMP}")
    print(f"# 计划变异体 {len(plan)} 个（阳性对照 {sum(1 for x in plan if x[1]=='curated')} + 通用 {sum(1 for x in plan if x[1]!='curated')}）")
    print()

    invalid: list[str] = []
    killed: list[str] = []
    survived: list[tuple[str, str, pathlib.Path, str, str]] = []
    by_file: dict[str, list[int]] = {}

    for idx, (label, op, path, old, new) in enumerate(plan, 1):
        orig = path.read_text(encoding="utf-8")
        if old not in orig:
            invalid.append(f"{label}（锚点文本未找到）")
            print(f"[{idx}/{len(plan)}] {label:<46} SKIP 锚点未找到")
            continue
        patched = orig.replace(old, new, 1)
        try:
            ast.parse(patched)
        except SyntaxError as e:
            invalid.append(f"{label}（语法错：{e.msg}）")
            print(f"[{idx}/{len(plan)}] {label:<46} SKIP 语法错")
            continue
        try:
            path.write_text(patched, encoding="utf-8")
            rc, tail = _run(SUBSETS.get(path.name, []), full=False)
        finally:
            path.write_text(orig, encoding="utf-8")
        st = by_file.setdefault(path.name, [0, 0])
        if rc != 0:
            killed.append(label)
            st[0] += 1
            print(f"[{idx}/{len(plan)}] {label:<46} 杀死({op})  {tail[:60]}")
        else:
            survived.append((label, op, path, old, new))
            st[1] += 1
            print(f"[{idx}/{len(plan)}] {label:<46} ★存活({op})")

    # 存活者复核（子集漏网 vs 真盲区）：先过"广谱"子集，再上全量套件
    confirmed: list[tuple[str, str, pathlib.Path, str, str]] = []
    if survived and not a.no_confirm:
        print()
        print(f"# 存活者复核（{len(survived)} 个）：先广谱子集，再全量套件——"
              f"只有全量也抓不到的才算真盲区")
        still: list[tuple[str, str, pathlib.Path, str, str]] = []
        for label, op, path, old, new in survived:
            orig = path.read_text(encoding="utf-8")
            try:
                path.write_text(orig.replace(old, new, 1), encoding="utf-8")
                rc, _ = _run(BROAD, full=False)
            finally:
                path.write_text(orig, encoding="utf-8")
            if rc != 0:
                print(f"   {label:<46} 广谱子集抓到")
                st = by_file.setdefault(path.name, [0, 0])
                st[0] += 1
                st[1] -= 1
            else:
                still.append((label, op, path, old, new))
        for label, op, path, old, new in still:
            orig = path.read_text(encoding="utf-8")
            try:
                path.write_text(orig.replace(old, new, 1), encoding="utf-8")
                rc, _ = _run([], full=True)
            finally:
                path.write_text(orig, encoding="utf-8")
            if rc != 0:
                print(f"   {label:<46} 全量抓到")
                st = by_file.setdefault(path.name, [0, 0])
                st[0] += 1
                st[1] -= 1
            else:
                print(f"   {label:<46} ★★ 真盲区（全量也没抓到）")
                confirmed.append((label, op, path, old, new))

    print()
    print("# ═══ 截面 ═══")
    for f, (k, s) in sorted(by_file.items()):
        tot = k + s
        print(f"  {f:<24} 杀死 {k:>3} / 存活 {s:>2}（{tot} 个有效变异体，得分 {k/tot:.0%}）" if tot else f"  {f}: 无有效变异体")
    print()
    # ★ 不许在没复核时报"真盲区 0"（那正是本项目最在意的"假绿"）：
    #   `--no-confirm` 跳过复核时，只能说"未复核"，不能给一个看起来很好的 0。
    blind = f"{len(confirmed)}" if not a.no_confirm else "未复核（--no-confirm 跳过了复核，别当 0 看）"
    print(f"有效变异体 {len(killed) + len(survived)}｜杀死 {len(killed)}｜"
          f"存活(子集) {len(survived)}｜★ 真盲区 {blind}｜无效 {len(invalid)}")
    if invalid:
        print("无效（不算分）：" + "; ".join(invalid[:6]) + ("…" if len(invalid) > 6 else ""))
    if confirmed:
        print()
        print("# ★ 真盲区清单（这些地方改坏了 1040 个用例一个都不会红）")
        for label, op, path, old, new in confirmed:
            print(f"  {label}  [{op}]")
            print(f"      -  {old.strip()[:110]}")
            print(f"      +  {new.strip()[:110]}")
    return 0 if not confirmed else 1


if __name__ == "__main__":
    sys.exit(main())
