# -*- coding: utf-8 -*-
"""红绿证明 harness（第十轮 B8：证据必须落盘、可复核、可重跑）。

对每一处安全/UI 修复：临时把实现回退成"修复前"的样子 → 指定锚点测试必须变红
→ 恢复 → 必须回绿。全部实验结束后源码**逐字节还原**（异常也走 finally 恢复）。

运行（仓库根目录）：
    backend\\.venv\\Scripts\\python.exe scripts\\redgreen_check.py
输出 human-readable 报告到 stdout；建议重定向到
    docs\\collab\\red-green-回滚必红证据.md
作为当班证据留档。
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import ast  # --dryrun 校验"替换后仍能解析"用

# 第十二轮 🟠9 加固：
#  · 启动前提：三个被实验文件必须在 git 干净状态，否则拒绝运行（防旧改动被冲掉）
#  · 每组实验结束后【父进程】逐字节核对还原（子进程/中断遗留的补丁当场被修复）
#  · SIGINT/SIGTERM/解释器退出时兜底还原全部已动文件
#  · 残余风险：SIGKILL（taskkill /F）无法拦截——窗口缩到一个实验内，
#    且下一次运行会因"树不干净"拒绝启动并提示先 git checkout
import atexit
import signal

try:  # 十七轮 5-3：GBK 控制台下组名/断言含中文会 UnicodeDecodeError，截断报告
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"

# 第十三轮 🟠9（收尾）：所有实验补丁改打在 %TEMP% 的【全量副本】上——
# 项目源文件零接触（SIGKILL 窗口归零）。副本结构镜像：
#   TMP/app/…（backend/app）、TMP/tests/…（backend/tests）、
#   TMP/frontend/src/styles.css
# pytest 以 TMP 为 cwd 运行（python -m 把 TMP 放进 sys.path，app/tests 均解析到副本）。
import shutil
import tempfile

_TMP_ROOT = pathlib.Path(tempfile.mkdtemp(prefix="redgreen-"))
# 结构必须镜像 repo 相对层级（backend/… + frontend/src/…）——
# 测试用 Path(__file__).parents[N] 反查 CSS/redact，层级错了会读回真仓库
# 二十一轮口径回撤（审查证伪）：__pycache__ 曾是候选根因但【不成立】
# （带/不带 pyc 打同一补丁都 RED；CPython 判据：源变则 pyc 失效必重编译）。
# ignore 保留仅为卫生（不复制缓存目录），不再声称它影响锚点真假。
_NO_PYC = shutil.ignore_patterns("__pycache__")
shutil.copytree(ROOT / "backend" / "app", _TMP_ROOT / "backend" / "app", ignore=_NO_PYC)
shutil.copytree(ROOT / "backend" / "tests", _TMP_ROOT / "backend" / "tests", ignore=_NO_PYC)
# ★ 必须连 pytest.ini 一起复制：里面有 `asyncio_mode = auto`。此前只复制
# app/ + tests/，副本里没有 ini ⇒ async 测试会以
# "async def functions are not natively supported" 失败（rc=1），
# 表现为"恢复跑=rc=1"的假红（本班 §4 组踩过，原始输出见报告）。
shutil.copy2(ROOT / "backend" / "pytest.ini", _TMP_ROOT / "backend" / "pytest.ini")
# ★ A3 批扩容（2026-10-04）：此前只复制 styles.css ⇒ 任何"读 TSX 组件源码"的
# 锚点在副本里都读不到文件（FileNotFoundError = 非断言红，实验结构性失效）。
# 改为整目录复制：CSS 组行为完全不变（路径同一），TSX 组件组从此可做回滚实验。
shutil.copytree(
    ROOT / "frontend" / "src", _TMP_ROOT / "frontend" / "src", ignore=_NO_PYC
)
# ★ C7 契约批：契约 JSON 也要进副本——锚点 `tests/test_task_id_contract_consistency.py`
#   会读 `contracts/events.schema.json`（parents[2] = 副本根）并与 schemas.py 交叉校验；
#   不复制的话副本里根本没有该文件 ⇒ 锚点 FileNotFoundError（非断言红，实验失效）。
shutil.copytree(ROOT / "contracts", _TMP_ROOT / "contracts", ignore=_NO_PYC)
# ★ Phase 1 ② 批：`scripts\preflight.py` 与根目录 `start.bat` 也要进副本 ——
#   `tests/test_preflight.py` 会 import 体检脚本、并读 start.bat 做接线断言；
#   副本里缺了就是 **rc=2 采集错**（完整门实测抓出：该组"回滚跑=rc=2"）。
(_TMP_ROOT / "scripts").mkdir(parents=True, exist_ok=True)
# ★ 2026-10-07：`scripts/` 下的 **.py 全拷** ✓ —— 原来只拷 preflight.py ✗
#   而"锚点读什么文件，副本里就得有什么"这条规矩已经撞过三次了
#   （契约 JSON / start.bat / frontend/scripts / frontend/package.json ✓ 每次都要补一行 ✗）
#   ⇒ 索性把这一类一次收干净：**scripts 下的 .py 都进副本** ✓
#     （含本文件自己 ✓ 体积可忽略 ✓ 代价是每次多拷几十 KB ✓ 换来的是"以后不用再补"✓）
for _s in (ROOT / "scripts").glob("*.py"):
    if _s.name != "preflight.py":          # 下面那行是原有的显式拷贝，保留不动 ✓
        shutil.copy2(_s, _TMP_ROOT / "scripts" / _s.name)
shutil.copy2(ROOT / "scripts" / "preflight.py", _TMP_ROOT / "scripts" / "preflight.py")
shutil.copy2(ROOT / "start.bat", _TMP_ROOT / "start.bat")
# ★ 干净机器批：`backend/skills` 也要进副本 —— 体检锚点里有一条"名单防锈"
#   会拿**仓库真实技能库**和 EXPECTED_SKILLS 比对；副本缺这个目录时那条会
#   直接 FileNotFoundError ⇒ 整组"恢复跑=rc=1"（完整门实测抓出）。
if (ROOT / "backend" / "skills").is_dir():
    shutil.copytree(ROOT / "backend" / "skills", _TMP_ROOT / "backend" / "skills", ignore=_NO_PYC)
# ★ 对话原型批：`frontend/scripts/`（真浏览器验证脚本）也要进副本 ——
#   `tests/test_chat_proto.py` 会读 verify_chat_proto.mjs 做"验证脚本要看几何"的锚点；
#   缺它 = 模块级 FileNotFoundError ⇒ 整组 rc=2（完整门实测抓出）。
if (ROOT / "frontend" / "scripts").is_dir():
    shutil.copytree(ROOT / "frontend" / "scripts", _TMP_ROOT / "frontend" / "scripts", ignore=_NO_PYC)
# ★ 2026-10-07（版本检查批 / 依赖查漏洞批）：`frontend/` 下的 **json 配置全拷** ✓
#   `tests/test_version_update.py` 要拿 `package.json` 与后端版本号对（"两处必须一致"✓）；
#   `tests/test_dep_audit.py` 要读 `package-lock.json` 验"那条能修的漏洞真修了"✓
#   缺任何一个 = FileNotFoundError ⇒ **恢复跑=rc=1** ⇒ 那一组报"红源可疑"✗
#   （完整门实测抓出过两次 ✓ 而这正是本文件反复记的那条规律：
#     **锚点读什么文件，副本里就得有什么** ✓ 不然实验本身失效 ✓）
#   ⇒ 这次**按类收口**：`frontend/*.json` 全进去 ✓（package / package-lock / tsconfig* ✓ 都小 ✓）
#     不拷 `node_modules` 与 `dist` ✗（那才是真大件 ✓ 而且测试也不该读它们 ✓）
for _j in (ROOT / "frontend").glob("*.json"):
    shutil.copy2(_j, _TMP_ROOT / "frontend" / _j.name)
# ★ 2026-10-08（协议批）：**仓库根目录的文档也要进副本** ✓ —— 又是那条规律：
#   **锚点读什么文件，副本里就得有什么** ✓（这已经是第四次撞它了 ✓）
#   这次的锚点是 `LICENSE`（协议实验 ✓）与 `README.md`/`TERMS.md`（口径一致性断言 ✓）
#   —— `tests/test_license_terms.py` 用 `parents[2]` 定位仓库根 ✓ 副本里没有它 ⇒
#      harness 自己 `path.read_text()` 就 FileNotFoundError ⇒ **整轮红绿 rc=1**
#      ⇒ 汇总里显示"组数未解析"✓（比单组失败更难查 ✓ 幸好日志里有完整栈 ✓）
#   ⇒ 按类收口：根目录这几份**法律/门面文档**全拷 ✓（都只有几十 KB ✓）
for _doc in ("LICENSE", "TERMS.md", "PRIVACY.md", "README.md", "THIRD-PARTY.md", "CHANGELOG.md",
             # ★ 2026-10-08 晚（AGPL 批）：又撞了同一条规律第四次 ✗ ——
             #   `tests/test_license_terms.py` 还读了 `COMMERCIAL.md`（双授权说明 ✓）
             #   与 `docs/版权头模板.md`（SPDX 两行式的出处 ✓）⇒ 副本里没有 ⇒
             #   模块级 `read_text` 抛 FileNotFoundError ⇒ **rc=2 采集错** ✗
             #   （汇总里显示"回滚跑=rc=2（非断言红）" ✓ 恢复跑也是 rc=2 ✓ 一眼能认出是采集错 ✓）
             "COMMERCIAL.md"):
    _p = ROOT / _doc
    if _p.is_file():
        shutil.copy2(_p, _TMP_ROOT / _doc)
# `docs/` 只拷测试真正读的那几份 ✓（整目录里有截图几张 MB ✗ 没必要 ✓）
(_TMP_ROOT / "docs").mkdir(parents=True, exist_ok=True)
for _d in ("版权头模板.md",):
    _p = ROOT / "docs" / _d
    if _p.is_file():
        shutil.copy2(_p, _TMP_ROOT / "docs" / _d)
atexit.register(lambda: shutil.rmtree(_TMP_ROOT, ignore_errors=True))

APV = _TMP_ROOT / "backend" / "app" / "approval.py"
LOOP = _TMP_ROOT / "backend" / "app" / "loop.py"
REDACT = _TMP_ROOT / "backend" / "app" / "redact.py"
REDACT_TOOL = REDACT  # 同文件（第5批第1处弱通道豁免实验）
MAIN = _TMP_ROOT / "backend" / "app" / "main.py"
R11 = "tests/test_approval_orphan.py"
CSS = _TMP_ROOT / "frontend" / "src" / "styles.css"

R9 = "tests/test_round9_fixes.py"  # pytest 以 backend/ 为 cwd 运行
R10 = "tests/test_round10_fixes.py"


# ── D1 批：approval.py 两处 fail-closed 站点的"回滚态"文本（供多组复用） ──
# 背景见文件末尾 D1 组：宿主模式曾因 `... is None and in_sandbox` 放行链接逃逸。
# 「③ _path_is_inside 回退」那组需要**跨文件两站同撤**（否则 ② 兜住、观察不到红），
# 所以这两份文本必须在文件前部就绪（那组定义在中间）。
def _gate_back_to_in_sandbox(src: str, ret_snippet: str) -> str:
    """把 ret_snippet 所属的那个 `if state is None:` 改回 `and in_sandbox`（回滚用）。"""
    i = src.index(ret_snippet)
    j = src.rindex("if state is None:", 0, i)
    return src[:j] + "if state is None and in_sandbox:" + src[j + len("if state is None:"):]


_APV_NOW = APV.read_text(encoding="utf-8")
_APV_ROLLED = _gate_back_to_in_sandbox(
    _gate_back_to_in_sandbox(
        _APV_NOW,
        'return (f"overwrite:{head}", f"写操作（{head}）目标',
    ),
    'return Verdict(f"破坏性写（{dhead}）的目标「{tgt_found}」无法核实',
)
# 自检（本班踩过：漏一个右括号 → 表达式变成 **1 元组**，一路带到 experiment 里
# 才以 `replace() argument 2 must be str, not tuple` 崩掉，整条门白跑一趟）：
assert isinstance(_APV_NOW, str) and isinstance(_APV_ROLLED, str), "回滚文本必须是字符串"
assert _APV_ROLLED != _APV_NOW, "回滚文本与现文本相同——实验会失效"

def _require_clean_tree() -> None:
    """十三轮收紧：全量 porcelain（含 untracked），范围覆盖 backend/ frontend/src/
    scripts/ docs/——不再只查 4 个文件、不再漏未暂存/暂存/未跟踪。
    二十二轮：排除脚本自身的输出物（证据文件）——shell 重定向会先改它再启动本
    进程，纳入护栏会造成"永远拒绝运行"的鸡生蛋。"""
    r = subprocess.run(
        ["git", "status", "--porcelain", "-uall", "--",
         "backend", "frontend/src", "scripts", "docs",
         ":(exclude)docs/collab/red-green-回滚必红证据.md"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if r.stdout.strip():
        print("拒绝运行：受保护范围内有未提交/未跟踪文件（护栏见 scripts/redgreen_check.py）：")
        print(r.stdout)
        raise SystemExit(2)


# 二十六轮第 4 批第 7 处 c：自验入库——`--selfcheck "<组名>"` 对指定组做
# 完整反事实（打补丁 → 跑锚点测试抓红例 → 逐字节恢复 → 恢复跑），打印
# rc 与红例后退出。此前的"手工自验"是未入库的一次性脚本，无法复核；
# 入库后每条红绿证据都可从仓库单组复跑。
# ★ D1 批新增：`--dryrun` —— **只校验每组实验的定义**（不跑 pytest、不动任何文件）：
#   old 文本必须在目标文件里找得到；替换后的 .py 目标必须仍能 ast.parse；
#   old/new 必须是字符串。为什么值得有：本班漏了一个右括号 ⇒ old/new 变成元组 ⇒
#   跑到第 N 组才 TypeError **崩掉整条门**（10 分钟白跑，且日志里只有 traceback）。
#   有它之后，"定义错误"2 秒内全暴露。
#   ★ 参数解析必须在 `_require_clean_tree()` **之前**：dryrun 恰恰是"边改边查"用的，
#     不能反过来被"树必须干净"挡住（本班实测就被挡了一次）。
_DRYRUN = "--dryrun" in sys.argv
if _DRYRUN:
    sys.argv = [a for a in sys.argv if a != "--dryrun"]

_SELFCHECK = None
if len(sys.argv) >= 3 and sys.argv[1] == "--selfcheck":
    _SELFCHECK = sys.argv[2]
    sys.argv = sys.argv[:1]  # 防止 pytest 吃到剩余参数

if not _DRYRUN:
    _require_clean_tree()

results: list[tuple[str, bool, str]] = []
_SAVED: dict[pathlib.Path, str] = {}


def _restore_all() -> None:
    for f, content in _SAVED.items():
        if not f.exists() or f.read_text(encoding="utf-8") != content:
            f.write_text(content, encoding="utf-8")


atexit.register(_restore_all)


def _on_signal(signum, _frame):
    _restore_all()
    raise SystemExit(130)


signal.signal(signal.SIGINT, _on_signal)
signal.signal(signal.SIGTERM, _on_signal)


def _dump_tail(tag: str, test_id: str, r) -> None:
    """诊断通道：把 pytest 的尾部输出留档（用来分清断言红 / 语法红 / 抖动红）。"""
    tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-18:]
    print(f"[selfcheck] {tag} rc={r.returncode} test_id={test_id!r}")
    for ln in tail:
        print("   #", ln[:220])


def run_test(test_id: str, tb: str = "--tb=no"):
    # 二十六轮第 3 批：PYTHONDONTWRITEBYTECODE——本班实测【同秒 mtime 碰撞 +
    # size 相同】会让回滚→恢复序列读到 stale pyc（运行时 frozenset 只剩
    # --tlsverify，与源码不符），恢复跑误红。副本实验同样适用。
    r = subprocess.run(
        # ★ test_id 可能是【空格分隔的多个 id】（一个回滚组配多条锚点）——
        #   必须 split 成多个位置参数；整串当一个 id 会得到
        #   "ERROR: not found: …::a tests/…::b" + rc=4（本班 §4 组踩过）。
        [str(PY), "-m", "pytest", *test_id.split(), "-q", "-p", "no:warnings", tb],
        cwd=_TMP_ROOT / "backend", capture_output=True, text=True,
        encoding="utf-8", errors="replace",  # 十三轮：gbk 解码会掩盖中文断言输出
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if tb != "--tb=no":
        _dump_tail("跑", test_id, r)
    return r.returncode


def experiment_multi(name: str, patches: list[tuple[pathlib.Path, str, str]], test_id: str) -> None:
    """**跨文件**的多站同撤实验：patches = [(文件, old, new), ...] 一起打、一起还原。

    为什么要有它（D1 批实测）：`experiment()` 的多站同撤限定在**同一个文件**里；
    而"纵深防御"经常跨文件——例如 ③ 越界写这一组，修复前只有 ④越界面（loop.py 的
    `_path_is_inside`）拦，D1 之后 ②写动词面（approval.py 的 fail-closed）也会拦
    ⇒ 只撤 `_path_is_inside` 已经观察不到红（本批完整门实测：该组"回滚跑=green"，
    组 FAIL）。要恢复判别力必须**两处一起撤**（回到修复前形态）。
    """
    origs: dict[pathlib.Path, str] = {}
    patched: dict[pathlib.Path, str] = {}
    bad = [type(x).__name__ for _, o, n in patches for x in (o, n) if not isinstance(x, str)]
    if bad:
        results.append((name, False, f"实验定义错误：old/new 必须是字符串，收到 {bad}"))
        return
    if _DRYRUN:
        before = len(results)
        for path, old, new in patches:
            _dryrun_check(name, path, old, new)
        if len(results) > before and all(ok for _, ok, _ in results[before:]):
            del results[before:]          # 全 OK 只记一条（免得 dryrun 把组数算多）
            results.append((name, True, f"[dryrun] 定义 OK（{len(patches)} 站）"))
        return
    for path, old, new in patches:
        cur = patched.get(path, path.read_text(encoding="utf-8"))
        if path not in origs:
            origs[path] = cur
            _SAVED[path] = cur
        if old not in cur:
            results.append((name, False, f"锚点文本未找到——实验本身失效，需人工检查：{old[:60]!r}"))
            return
        patched[path] = cur.replace(old, new, 1)
    try:
        for p, content in patched.items():
            p.write_text(content, encoding="utf-8")
        red = run_test(test_id, tb="--tb=line" if _SELFCHECK is not None and _SELFCHECK in name else "--tb=no")
        if _SELFCHECK is not None and _SELFCHECK in name:
            r = subprocess.run(
                [str(PY), "-m", "pytest", *test_id.split(), "-q", "-p", "no:warnings", "--tb=line"],
                cwd=_TMP_ROOT / "backend", capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            fails = [ln.split("FAILED ")[1].split(" - ")[0]
                     for ln in (r.stdout or "").splitlines() if "FAILED" in ln]
            print(f"[selfcheck] 组={name}")
            print(f"[selfcheck] 回滚跑 rc={red}  红例: {fails[:4]}")
    finally:
        for p, content in origs.items():
            p.write_text(content, encoding="utf-8")     # 逐字节（文本级）还原
            if p.read_text(encoding="utf-8") != content:
                p.write_text(content, encoding="utf-8")
                results.append((name, False, "还原校验失败——已强制回写，请人工复核 git diff"))
                return
    back = run_test(test_id, tb="--tb=line" if _SELFCHECK is not None and _SELFCHECK in name else "--tb=no")
    ok = red == 1 and back == 0
    note = (f"回滚跑={'RED(断言)' if red == 1 else f'rc={red}（非断言红，判FAIL）' if red else 'green'}"
            f"  恢复跑={'GREEN' if back == 0 else f'rc={back}'}")
    results.append((name, ok, note))


def _dryrun_check(name: str, path: pathlib.Path, old: str, new: str) -> None:
    """--dryrun 用：只做定义级校验（不写文件、不跑测试）。"""
    try:
        orig = path.read_text(encoding="utf-8")
    except OSError as e:
        results.append((name, False, f"[dryrun] 读不到目标文件：{e}"))
        return
    if old not in orig:
        results.append((name, False, f"[dryrun] 锚点文本未找到：{old[:60]!r}"))
        return
    patched = orig.replace(old, new, 1)
    if path.suffix == ".py":
        try:
            ast.parse(patched)
        except SyntaxError as e:
            results.append((name, False, f"[dryrun] 替换后语法错（rc 会是 4，不是断言红）：{e}"))
            return
    results.append((name, True, "[dryrun] 定义 OK"))


def experiment(name: str, path: pathlib.Path, old, new, test_id: str) -> None:
    """回滚实验：把 old 替换成 new → 跑锚点（应红）→ 逐字节还原 → 再跑（应绿）。

    ★ 二十六轮第 7 批：`old`/`new` 现在也接受**等长列表**（多站同撤）。
    为什么需要：纵深防御下同一个行为可能由**多个站点**共同保证，只撤一站
    另一站仍兜住 ⇒ rc=0（观察不到），组会误报 FAIL。这正是 59a9f12 的教训
    （"单撤 _saw_read 被 n==1 规则兜住 rc=0 不可观察"）。
    单字符串入参行为完全不变（向后兼容，50+ 组照旧）。
    """
    orig = path.read_text(encoding="utf-8")
    _SAVED[path] = orig
    olds = [old] if isinstance(old, str) else list(old)
    news = [new] if isinstance(new, str) else list(new)
    if len(olds) != len(news):
        results.append((name, False, f"实验定义错误：old/new 个数不等（{len(olds)}/{len(news)}）"))
        return
    # ★ 类型校验（D1 批踩过）：漏一个右括号会让 old/new 变成**元组**，一路带到
    #   `.replace()` 才以 TypeError 崩掉——**整个 harness 中止、整条门白跑**。
    #   这里提前挡下，降级成"这一组 FAIL + 明确原因"，其余组照跑。
    bad = [type(x).__name__ for x in (olds + news) if not isinstance(x, str)]
    if bad:
        results.append((name, False, f"实验定义错误：old/new 必须是字符串，收到 {bad}"))
        return
    if _DRYRUN:
        _dryrun_check(name, path, olds[0], news[0])
        return
    patched = orig
    for o, n in zip(olds, news):
        if o not in patched:
            # 十九轮 🔴1 教训固化：锚点文本未命中 = 实验失效 = FAIL（不再打印后继续）
            results.append((name, False, f"锚点文本未找到——实验本身失效，需人工检查：{o[:60]!r}"))
            return
        patched = patched.replace(o, n, 1)
    try:
        path.write_text(patched, encoding="utf-8")
        red = run_test(test_id, tb="--tb=line" if _SELFCHECK is not None and _SELFCHECK in name else "--tb=no")
        if _SELFCHECK is not None and _SELFCHECK in name:
            r = subprocess.run(
                # ★ 与 run_test 同口径：test_id 可能是【空格分隔的多个 id】——
                #   此前这里整串当【一个】位置参数传，多 id 组的红例永远打印成
                #   `红例: []`（pytest 报 not found，没有 FAILED 行）。判据本身没受影响
                #   （rc 由 run_test 给），但诊断通道对多 id 组失明，A3 批实测发现。
                [str(PY), "-m", "pytest", *test_id.split(), "-q", "-p", "no:warnings", "--tb=line"],
                cwd=_TMP_ROOT / "backend", capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            fails = [l.split("FAILED ")[1].split(" - ")[0]
                     for l in (r.stdout or "").splitlines() if "FAILED" in l]
            print(f"[selfcheck] 组={name}")
            print(f"[selfcheck] 回滚跑 rc={red}  红例: {fails[:4]}")
    finally:
        path.write_text(orig, encoding="utf-8")  # 无论如何逐字节还原
        if path.read_text(encoding="utf-8") != orig:  # 父进程核对（🟠9）
            path.write_text(orig, encoding="utf-8")
            results.append((name, False, "还原校验失败——已强制回写，请人工复核 git diff"))
            return
    back = run_test(test_id, tb="--tb=line" if _SELFCHECK is not None and _SELFCHECK in name else "--tb=no")
    # 二十六轮第 4 批第 7 处 a：rc==1 才算"红在断言上"——rc>=2 是采集/
    # 语法错误（本班实测：回滚补丁破坏括号 → rc=4、0 红例，曾被算作 RED）
    red_assertion = red == 1
    ok = red_assertion and back == 0
    note = (f"回滚跑={'RED(断言)' if red == 1 else f'rc={red}（非断言红，判FAIL）' if red else 'green'}"
            f"  恢复跑={'GREEN' if back == 0 else f'rc={back}'}")
    results.append((name, ok, note))


# ═══ 第九轮修复（审批/快照判定层） ═══

experiment(
    "xargs 阶段门移除（实为⑦误伤修复的锚点：test_tee_devnull_passes）", APV,
    'if re.search(r"(?:^|[\\s|;&(])xargs(?:\\s|$)", command):',
    'if True:  # ROLLBACK',
    f"{R9}::test_tee_devnull_passes",
)
experiment(
    "②b ln 回退成'判全部 cands'（源存在即误拦）——十二轮统一实现版", APV,
    '''        if head == "ln":
            # 无 -t：链接名在末位（ln [-s] TARGET LINK_NAME）——源永不被写
            return ([cands[-1]] if cands else []), not cands''',
    '''        if head == "ln":
            return cands, not cands  # ROLLBACK''',
    f"{R9}::test_ln_new_link_name_passes",
)
experiment(
    "②b cp/install/mv/ln -t 落点判定关闭——十二轮统一实现版", APV,
    "            tdir = cls._split_target_dir(head, toks)",
    "            tdir = None  # ROLLBACK",
    f"{R9}::test_cp_dash_t_new_landing_passes",
)
experiment(
    "③ _target_exists 回退 exists→is_file（目录目标漏判）", LOOP,
    "            return real.exists()",
    "            return real.is_file()  # ROLLBACK",
    f"{R9}::test_dir_target_asks",
)
experiment_multi(
    # ★ D1 批更新（2026-10-04）：这一组改成**跨文件两站同撤**。
    #   原因：D1 让 ②写动词面在"目标不可核实"时 fail-closed（不再分模式），
    #   于是"越界写"这件事变成 ④越界面 + ②写动词面**双保险**——只撤
    #   `_path_is_inside`（④）时 ② 仍然拦住，锚点照绿、组失去判别力
    #   （本批完整门实测：该组 `回滚跑=green` ⇒ FAIL）。要回到"修复前形态"
    #   （只有 ④ 在守）必须把 ② 的 fail-closed 也一起撤掉。
    #   TMP 副本上操作，两个文件都逐字节还原。
    "③ _path_is_inside 回退 fail-open（越界放行）——跨文件两站同撤", [
        (LOOP,
         '''    def _path_is_inside(self, path: str) -> bool:
        """给审批模块用：该路径是否落在工作区或 allowed_dirs 内。"""
        try:
            self.executor.resolve_in_workspace(self.workdir, path)
            return True
        except Exception:
            return False''',
         '''    def _path_is_inside(self, path: str) -> bool:
        try:
            self.executor.resolve_in_workspace(self.workdir, path)
            return True
        except Exception:
            return True  # ROLLBACK'''),
        (APV, _APV_NOW, _APV_ROLLED),
    ],
    f"{R9}::test_outside_write_asks",
)
# 十九轮🟠5④：白名单反转后，⑥ 的旧单点回退不再红（podman 走白名单仍 ASK）——
# 组合回滚：_CONTAINER_CLIS 收窄 + 安全动词白名单清空 = 修复前行为
experiment(
    "⑦ 裸 env 特判移除", APV,
    '''            # 第九轮复验⑦：裸 env（无内层命令）= 打印环境变量，只读放行
            if head == "env" and len(toks) == 1:
                return None
''',
    "",
    f"{R9}::test_bare_env_passes",
)
experiment(
    "⑦ tee/truncate 不再切 `< 输入源`——十二轮统一实现版", APV,
    '''        if head in ("tee", "truncate"):
            # `< src` 是输入源不是写目标（切掉首个 `<` 及其后全部）
            cut = next((i for i, t2 in enumerate(cands) if t2 == "<"), None)
            if cut is not None:
                cands = cands[:cut]
            cands = [c for c in cands if not c.startswith("<")]
            return cands, not cands''',
    '''        if head in ("tee", "truncate"):
            return cands, not cands  # ROLLBACK''',
    f"{R9}::test_tee_stdin_source_new_file_passes",
)
# 十二轮 🔴5①：xargs+inplace 真规则（①）——此前 16 组里没有这一组，
# 且旧锚点因 stub reader 落 script-unreadable 兜底、替真规则打掩护；
# 现 test_xargs_inplace_asks 断言 key=struct:inplace:*，规则删除必红。
experiment(
    "① xargs+inplace 真规则删除（生产 reader 下无兜底，必红）", APV,
    """                # 第九轮复验②：xargs + perl/ruby/sed -i（就地改写由管道喂入的文件）
                if dhead in _INPLACE_FLAG_HEADS and any(
                    t2.strip(_QUOTES) == "-i"
                    or (t2.strip(_QUOTES).startswith("-i") and not t2.strip(_QUOTES).startswith("--"))
                    for t2 in dtoks[1:]
                ):
                    return Verdict(f"就地改写（{dhead} -i）的目标由管道/上游喂入，无法静态判定，需人工确认", f"struct:inplace:{dhead}")
""",
    "",
    R9 + "::test_xargs_inplace_asks",
)

experiment(
    "②b mklink 名字位回退成末位——十二轮统一实现版", APV,
    '''        if head == "mklink":
            lc = [c for c in cands if c.lower() not in ("/d", "/h", "/j", "/?")]
            return ([lc[0]] if lc else []), not lc''',
    '''        if head == "mklink":
            return ([cands[-1]] if cands else []), not cands  # ROLLBACK''',
    f"{R9}::test_mklink_new_name_passes",
)

# ═══ 十三/十五轮新锚点（容器生命周期 / 孤儿清扫 / 快照可观测性） ═══

# 容器生命周期面（十三轮 🔴1）：独立于联网面——删除后沙箱+断网放行回归
experiment(
    "十三轮🔴1 容器生命周期面删除（沙箱+断网放行回归）", APV,
    """        # ②d 容器生命周期（第十三轮 🔴1）：独立于联网面——沙箱+断网
        # （network_disabled=True，生产默认 sandbox_network="none"）会跳过 ③，
        # 但容器 run/exec/up 依然是"拉镜像+执行任意代码"的换入口，必须恒问。
        # 拍板口径（十二轮提出、十三轮落实）：compose up/build/pull 与
        # docker/podman/nerdctl/ctr 的 run/exec/pull/build/create 恒 ASK，
        # 与 sandbox_network 无关；struct 键不允许"总是允许"。
        for variant in struct_variants:
            life = self._container_lifecycle(variant)
            if life:
                slug, reason = life
                key = f"struct:{slug}"
                if key not in remembered:
                    return Verdict(reason, key, "ask")
""",
    "",
    R9 + "::test_compose_lifecycle_decided_ask",
)

# 孤儿审批（用户实测）：启动清扫 + approve 诚实降级
experiment(
    "approve 端点诚实降级删除（孤儿审批复活）", MAIN,
    """    task = tasks.get(task_id)
        run = runs.get(task_id)
        live = run and run.aio_task and not run.aio_task.done()
        if task is not None and task.status == "waiting_approval" and not live:
            task.status = "failed"
            task.updated_at = _now()
            _save_index()
            detail = "后端重启，原等待中的审批已失效——请重新发起任务（此审批无法跨重启恢复）"
            _emit_standalone(task_id, "status", {"state": "failed", "detail": detail})
            raise HTTPException(409, f"审批已失效（{detail}）task={task_id} call_id={call_id}")""",
    """    raise HTTPException(409, f"没有等待中的审批：task={task_id} call_id={call_id}")  # ROLLBACK""",
    R11 + "::test_approve_on_orphan_waiting_degrades_honestly",
)

# 快照可观测性（十五轮 🔴1 出血点）——十九轮 🔴1 更正为【真·回滚形态】：
# 移除【可观测性本身】（emit+print 一起删，静默 return），锚点红在
# "knowledge 事件不存在"断言上，而不是 UnboundLocalError 崩溃。
# （核实记录：上轮声称"组13 已换真形态"为空描述——补丁未落盘，本组即原形态。）
experiment(
    "十五轮🔴1 快照可观测性移除（emit+print 删除，静默吞复活）", LOOP,
    """            try:
                self.emit("knowledge", {
                    "title": "🛟 快照未生成（规则异常，已跳过）",
                    "content": f"{type(e).__name__}: {e}",
                })
            except Exception:
                pass
            print(f"[快照] 规则异常，本次跳过：{type(e).__name__}: {e}", flush=True)
            return
""",
    """            return  # ROLLBACK：可观测性移除（旧行为：静默吞，不发事件不留日志）
""",
    R9 + "::test_snapshot_rule_error_is_observable",
)

# 十九轮 🔴4-1：超大文件（>200MB）计入 failed——回滚成静默 continue 必红
experiment(
    "十九轮🔴4-1 超大文件退回静默 continue（不计 failed）", LOOP,
    """                if p.stat().st_size > 200 * 1024 * 1024:
                    # 十九轮 🔴4-1：超大文件不再静默跳过（与 5-2 同族盲区——
                    # video_gen 产物可达此限）——计入 failed 并在事件里说明
                    oversized += 1
                    failed.append(str(p.relative_to(self.workdir)))
                    print(f"[快照] 超大文件跳过（>200MB）：{p.name}", flush=True)
                    continue""",
    """                if p.stat().st_size > 200 * 1024 * 1024:
                    continue  # ROLLBACK：静默跳过（不计 failed、不发事件）""",
    R9 + "::test_snapshot_oversized_and_rolling",
)

# 十九轮 🔴4-2：滚动条件回退成 `if not failed:`（partial 常态下无限增长）
experiment(
    "十九轮🔴4-2 滚动条件退回 if not failed（partial 不滚）", LOOP,
    """            if copied:
                keep = 5""",
    """            if not failed:
                keep = 5  # ROLLBACK：partial 时滚动整体跳过（目录无限增长）
            if False:
                keep = 5""",
    R9 + "::test_snapshot_oversized_and_rolling",
)

# ═══ 二十轮 🔴0（容器面整族绕过）——真·回滚：整函数替换为修复前版本（git 6474f04）═══
# 二十轮🔴0 容器面回退——动态同源（§C：锚点从源码运行时提取，杜绝失配）：
# 运行时读副本 approval.py 提取当前 _container_lifecycle 函数体；旧版从 git 6474f04 取。
_grabbed = APV.read_text(encoding="utf-8")
_i = _grabbed.index("    def _container_lifecycle")
_j = _grabbed.index("    @classmethod", _i)
_CUR_FN = _grabbed[_i:_j].rstrip() + chr(10)
_OLD_FN = subprocess.run(
    ["git", "show", "6474f04:backend/app/approval.py"],
    cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
).stdout
# ★ 2026-10-09（CI 第 10 轮抓的 ✗，真问题不只是 CI）：
#   这里**依赖本仓库的 git 历史**（要 `6474f04` 那个提交 ✓）
#   而公开仓库是**全新历史** ✗（内部记录不能带出去 ✓ 所以导出时重开了仓库 ✓）
#   ⇒ `git show` 返回空 ⇒ 下面的 `.index()` 直接崩 ✗ ⇒ **任何人 clone 下来红绿都跑不起来** ✓
#   ⇒ 拿不到历史就**跳过这一组**并说清原因 ✓（不静默 ✗ 不假装绿 ✗）
#     其余各组照跑 ✓ 所以 clone 之后至少能跑绝大多数 ✓
if "def _container_lifecycle" in _OLD_FN:
    _oi = _OLD_FN.index("    def _container_lifecycle")
    _oj = _OLD_FN.index("    @classmethod", _oi)
    _OLD_FN = _OLD_FN[_oi:_oj].rstrip() + chr(10)
else:
    print("⚠️ 跳过 1 组红绿：「容器面回退为修复前版本」需要 git 历史里的 6474f04 ✓ "
          "而这份 clone 是新历史（公开仓库正常如此 ✓）⇒ 拿不到旧版函数体 ✓ "
          "其余各组不受影响 ✓", flush=True)
    _OLD_FN = ""
if _CUR_FN.strip() and _OLD_FN.strip() and _CUR_FN != _OLD_FN:
    experiment(
        "二十轮🔴0 容器面回退为修复前版本（对象名形态绕过复活）", APV,
        _CUR_FN, _OLD_FN,
        R9 + "::test_compose_lifecycle_decided_ask",
    )
else:
    # ★ 2026-10-09（CI 第 11 轮）：这里原来一律记 FAIL ✗
    #   它当初的用意是对的 ✓ —— 防"提取失败还静默算绿" ✓
    #   但**公开仓库是全新历史**（不带内部记录 ✓）⇒ 必然提取失败 ✗
    #   ⇒ 在那种 clone 里这组**永远红** ⇒ 任何人 clone 下来红绿都过不了 ✗
    #   ⇒ 区分两种情形：**历史拿不到**（跳过 ✓ 说清原因）+ **真提取失败**（仍然 FAIL ✗）
    if not _OLD_FN.strip():
        results.append(("二十轮🔴0 容器面回退（跳过：这份 clone 没有 6474f04 历史）", True,
                        "公开仓库为不带内部记录而重开了历史 ⇒ 取不到旧版函数体 ⇒ 跳过 ✓ "
                        "其余各组照跑 ✓（本地完整仓仍会真跑这一组 ✓）"))
    else:
        results.append(("二十轮🔴0 容器面回退（动态提取失败）", False,
                        "当前函数体与旧版相同或提取失败——实验失效"))

# ═══ 第十轮 B1/B2（快照排除三类规则） ═══

_SNAP_SUBSTRING = '''            skip_pats = (
                ".env", ".npmrc", ".netrc", ".pgpass", ".gitignore",
                "credential", "secret", "password", "token", "apikey", "api_key",
                "private", "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
                "config.json", "settings.json",
            )
            skip_exts = (".key", ".pem", ".pfx", ".p12", ".jks", ".keystore", ".ppk")
            skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", ".ssh", ".aws", ".kube"}

            def _is_sensitive(name: str) -> bool:
                low = name.lower()
                if low.endswith(skip_exts):
                    return True
                if low.startswith(("id_rsa", "id_ed25519", "id_ecdsa", "id_dsa")):
                    return True
                return any(pat in low for pat in skip_pats)'''

_SNAP_START = "            # 第六轮复验④：排除规则全部**大小写不敏感**"
_SNAP_END = "            files = ["


def _snapshot_rollback(variant_body: str, name: str, test_id: str) -> None:
    orig = LOOP.read_text(encoding="utf-8")
    if _SNAP_START not in orig or _SNAP_END not in orig:
        results.append((name, False, "快照排除块锚点未找到"))
        return
    if _DRYRUN:
        results.append((name, True, "[dryrun] 定义 OK（块级替换）"))
        return
    try:
        LOOP.write_text(orig[: orig.index(_SNAP_START)] + variant_body + "\n\n" + orig[orig.index(_SNAP_END):], encoding="utf-8")
        red = run_test(test_id)
    finally:
        LOOP.write_text(orig, encoding="utf-8")
    back = run_test(test_id)
    # 二十六轮第 7 批（审计方）：判据从 `red != 0` 收紧为 `red == 1`。
    # 旧判据把 rc=2/3/4（interrupted / 内部错 / usage 错，含【语法红】）也算 PASS，
    # 与 experiment() 的 `red == 1` 口径不一致 ⇒ 本 helper 的组**无法从汇总行证明
    # 红源是断言消息**（独立验证员的原始输出：B1/B2 的 note 只有 "RED" 二字，
    # 与其余 50 组的 "RED(断言)" 不同格式）。收紧后与 experiment() 同口径、同 note
    # 格式；B1/B2 已由验证员独立实测为 rc=1 断言红，收紧后仍应 PASS。
    ok = red == 1 and back == 0
    results.append((name, ok,
                    f"回滚跑={'RED(断言)' if red == 1 else f'rc={red}（非断言红，判FAIL）' if red else 'green'}"
                    f"  恢复跑={'GREEN' if back == 0 else f'rc={back}'}"))


_snapshot_rollback(
    _SNAP_SUBSTRING, "B1 快照排除回退成裸子串（误杀 10/16 正常文件）",
    f"{R9}::test_snapshot_exclusion_rules",
)
_snapshot_rollback(
    _SNAP_SUBSTRING.replace(
        "            return any(pat in low for pat in skip_pats)",
        "            return low in skip_pats"),
    "B2 快照排除回退成精确名（settings.local.json / service-account.json / .htpasswd 漏网）",
    f"{R9}::test_snapshot_exclusion_rules",
)

# ═══ 二十四轮欠账 + 二十五轮 🔴P0（豁免收窄 / 入口 fail-closed / 包装器 / VAR / 白名单）═══

# 二十五轮 🔴1：帮助/版本豁免收窄回退成"选项区见到 help flag 即放行"
experiment(
    "二十五轮🔴1 flag 豁免回退（-v 后带子命令重新放行）", APV,
    """            if help_seen:
                if sub_after_flag:
                    return (f"{head}-flag-prefix",
                            f"容器 {head} 帮助/版本 flag 之后仍带子命令（真机实测 -v/--version 不阻断执行），需人工确认")
                continue  # 纯版本/帮助自述（flag 后无子命令，实测不执行资源操作）""",
    """            if help_seen:
                continue  # ROLLBACK：二十五轮前（选项区见到 help/version 即放行）""",
    f"{R9}::test_flag_prefix_with_subcommand_asks",
)

# 二十五轮 🔴2：格式识别入口 fail-closed 回退成"表外 head 无条件溜走"
experiment(
    "二十五轮🔴2 入口 fail-closed 回退（未知包装器重新放行）", APV,
    """                if head in _BENIGN_NONCONTAINER_HEADS:
                    continue
                if any(cls._head_of(t) in _CONTAINER_CLIS for t in toks[1:]):
                    return (f"wrap-unknown:{head}",
                            f"未知命令「{head}」包装容器命令（执行/喂入方式不可知），需人工确认")
                continue""",
    """                continue  # ROLLBACK：二十五轮前（表外 head 无条件交给其它面）""",
    f"{R9}::test_unknown_wrapper_with_container_asks",
)

# 二十五轮 🔴2：无关命令白名单清空（grep docker build.log 误拦）
experiment(
    "二十五轮🔴2 白名单清空（grep docker 误拦复活）", APV,
    '''_BENIGN_NONCONTAINER_HEADS: frozenset[str] = frozenset({
    "ls", "dir", "cat", "echo", "printf", "grep", "egrep", "fgrep", "rg",''',
    '''_BENIGN_NONCONTAINER_HEADS: frozenset[str] = frozenset({  # ROLLBACK：白名单清空''',
    f"{R9}::test_benign_head_with_container_literal_passes",
)

# 二十四轮欠账①：_WRAPPER_DEF 统一表整表关闭（透明包装变未知 head）
experiment(
    "二十四轮🔴P0 包装器统一表关闭（timeout 形态变未知）", APV,
    "            while toks and (wkind := _WRAPPER_DEF.get(cls._head_of(toks[0]))):",
    "            while False and (wkind := _WRAPPER_DEF.get(cls._head_of(toks[0]))):  # ROLLBACK",
    f"{R9}::test_lifecycle_pass_sentinels",
)

# 二十四轮欠账②：argjump 参数跳过关闭（nice -n 10 残留 → 误拦观察类）
experiment(
    "二十四轮🔴P0 argjump 参数跳过关闭（nice -n 10 误拦复活）", APV,
    """                if kind == "argjump":
                    while toks and (toks[0].startswith("-") or re.fullmatch(r"[\\d.]+[a-zA-Z]?", toks[0])
                                    or (len(toks[0]) <= 4 and ("'" in toks[0] or '"' in toks[0]))):
                        toks = toks[1:]""",
    """                if False:  # ROLLBACK：argjump 参数跳过关闭
                    while toks and (toks[0].startswith("-") or re.fullmatch(r"[\\d.]+[a-zA-Z]?", toks[0])
                                    or (len(toks[0]) <= 4 and ("'" in toks[0] or '"' in toks[0]))):
                        toks = toks[1:]""",
    f"{R9}::test_lifecycle_pass_sentinels",
)

# 二十四轮欠账③：VAR 前缀剥离关闭（VAR=x docker ps 误拦复活）
# ★ 二十六轮第 7 批：前缀剥离现在有【两个站点】——咽喉点 `_deep_parts` 出口
#   （_strip_assign_prefix_str，管 git/net/写入/动词等所有面）与容器面自身
#   （_strip_var_prefix，管包装器链）。纵深防御 ⇒ 只撤一站另一站仍兜住，
#   回滚跑 rc=0（观察不到"误拦复活"）——本班完整跑实测到这一点（该组报
#   "回滚跑=green" ⇒ FAIL，正是 59a9f12 同型的"纵深防御下需选有判别力的层"）。
#   故改成【两站同撤】（experiment 现支持等长列表 = 多站同撤），恢复判别力。
experiment(
    "二十三轮🔴1 VAR 前缀剥离关闭（VAR=x 观察类误拦复活）", APV,
    ["""            toks = _strip_var_prefix(toks)
            if not toks:
                continue""",
     """            part = _strip_assign_prefix_str(part)
            if not part.strip():
                continue  # 纯赋值片段（`FOO=bar` 单独成段）不是命令，跳过"""],
    ["""            toks = toks  # ROLLBACK：VAR 前缀不剥（容器面站点）
            if not toks:
                continue""",
     """            part = part  # ROLLBACK：VAR 前缀不剥（咽喉点站点）"""],
    f"{R9}::test_lifecycle_pass_sentinels",
)

# 二十四轮欠账④：安全动词白名单新增 4 条撤除（buildx du / stack services
# / swarm ca / context show 误拦复活）
experiment(
    "二十四轮🟠6 白名单新增撤除（buildx du 等误拦复活）", APV,
    '    "du", "services", "ca", "show",  # 二十四轮：buildx du / stack services / swarm ca / context show',
    "",
    f"{R9}::test_container_observe_family_pass",
)

# 二十六轮第 3 批第 2 处：--debug 组【恢复并换锚点】——第 2 批以 docker
# --debug ps 为锚点时"无可观察差异"的判断只对那两条成立；「无值flag +
# 观察子命令 + 位置参数」形态（docker --debug inspect abc）回滚后
# ASK struct:docker-abc-unknown（本班复现）——组恢复，锚点换之。
experiment(
    "二十六轮第3批第2处 --debug 无值单跳回退（inspect/logs 被吞复活）", APV,
    """_DOCKER_GLOBAL_OPTS: frozenset[str] = frozenset({
    "--context", "-c", "--host", "-H", "--config", "--log-level", "-l",
    "--tls", "--tlscacert", "--tlscert", "--tlskey", "--registry-mirror",
})
# 无值全局选项（单跳，不消费下一个 token）。真机实测：`docker --debug ps`
# 正常执行——--debug/-D/--tlsverify 无值；此前混进带值表双跳会吞掉子命令
# （`docker --debug -h` 误判"只有选项无动词"）。
_DOCKER_GLOBAL_FLAGS: frozenset[str] = frozenset({
    "--debug", "-D", "--tlsverify",
})""",
    """_DOCKER_GLOBAL_OPTS: frozenset[str] = frozenset({
    "--context", "-c", "--host", "-H", "--config", "--log-level", "-l", "--debug", "-D",  # ROLLBACK
    "--tls", "--tlscacert", "--tlscert", "--tlskey", "--registry-mirror",
})
_DOCKER_GLOBAL_FLAGS: frozenset[str] = frozenset({
    "--tlsverify",
})""",
    f"{R9}::test_debug_valueless_flag_keeps_subcommand",
)

experiment(
    "二十五轮 _is_wrapper_arg 路径规则关闭（flock /tmp/l 误拦复活）", APV,
    """    if t.startswith(("/", "./", "../")) or re.match(r"[A-Za-z]:[\\\\/]", t):
        return True""",
    """    return False  # ROLLBACK：路径参数规则关闭""",
    f"{R9}::test_lifecycle_pass_sentinels",
)

# 二十六轮第3处：git/pip 间接执行判定回退（-c/--exec-path 重新放行）
experiment(
    "二十六轮第3处 git/pip 间接执行判定回退", APV,
    """            else:
                exec_flags = _PIP_EXEC_FLAGS if head in ("pip", "pip3") else None
                if exec_flags:
                    for t in toks[1:]:
                        tt = t.strip(_QUOTES)
                        if tt in exec_flags or any(
                                tt.startswith(f + "=") for f in exec_flags if f.startswith("--")):
                            return (f"indirect:{head}",
                                    f"{head} 的间接执行参数（{tt}，可注入 pager/ssh helper/filter 等外部命令），需人工确认")""",
    "",
    f"{R9}::test_git_pip_indirect_exec_asks",
)

# 二十六轮第2批：git -c 位置盲判修正回滚（恢复"全体扫 -c"的误拦）
experiment(
    "二十六轮第2批 git-c 位置盲判回滚（git commit -c HEAD 误拦复活）", APV,
    """            if head == "git":
                _sub_i = next((k for k, t in enumerate(toks[1:], 1)
                               if not t.strip(_QUOTES).startswith("-")), None)
                _git_scope = toks[1:_sub_i] if _sub_i is not None else toks[1:]""",
    """            if head == "git":
                _git_scope = toks[1:]  # ROLLBACK: 恢复全体扫（位置盲判）""",
    f"{R9}::test_indirect_bypass_2b_normal_pass",
)

# 二十六轮第2批：三条间接执行旁路封堵回滚（tar/rsync/git config 重新放行）
experiment(
    "二十六轮第2批 tar/rsync/git-config 旁路封堵回滚", APV,
    """            elif head == "tar":
                for t in toks[1:]:
                    tt = t.strip(_QUOTES)
                    if any(tt == f or tt.startswith(f + "=") for f in _TAR_EXEC_FLAGS):
                        return ("indirect:tar",
                                f"tar 的执行参数（{tt.split('=')[0]}，其值作为外部命令运行归档内容），需人工确认")
            elif head == "rsync":
                for t in toks[1:]:
                    tt = t.strip(_QUOTES)
                    if any(tt == f or tt.startswith(f + "=") for f in _RSYNC_EXEC_FLAGS):
                        return ("indirect:rsync",
                                f"rsync 的远程 shell 参数（{tt.split('=')[0]}，其值作为外部命令运行），需人工确认")""",
    "",
    f"{R9}::test_indirect_bypass_2b_asks",
)
# 二十六轮第2批 git-config 基础键封堵回滚——【动态提取】（第6批第2处选项表
# 重写后该块已三度漂移，静态文本第 4 次失配；改为运行时从副本源码提取
# config 判定块作 old、删除作 new，永不失配）。
_g2s = APV.read_text(encoding="utf-8")
_g2a = _g2s.index('_cfg_rest = toks[_sub_i + 1:]')
_g2b = _g2s.index('需人工确认")', _g2a) + len('需人工确认")')
_G2B_OLD = _g2s[_g2s.index("                if _sub_i is not None and cls._head_of(toks[_sub_i]) == \"config\"", _g2s.index("== \"config\"") - 400):_g2b] + chr(10)
experiment(
    "二十六轮第2批 git-config 基础键封堵回滚（动态提取，core.pager 等复活）", APV,
    _G2B_OLD,
    "",
    f"{R9}::test_indirect_bypass_2b_asks",
)

# 二十六轮第2批第3处：选项-only 自述统一回滚（裸 -D 重新 ASK、-v 仍 PASS）
experiment(
    "二十六轮第2批第3处 选项-only 统一回滚（裸 -D ASK 复活）", APV,
    """            # 二十六轮第2批第3处：【选项-only = 纯自述】统一口径——
            # head 之后只有选项、无任何位置 token（子命令）时，真机实测
            # （docker 29.8.0）：`docker -v` / `-D` / `--tlsverify` /
            # `--context x` / `--log-level ps`（值非法）全部打印 Usage 或报错
            # 退出，无资源操作。此前裸 -D/--tlsverify ASK 而裸 -v PASS
            # （同族不同判）；资源操作必须经子命令，选项-only 结构性不执行。
            if i >= len(toks):
                continue""",
    """            if i >= len(toks):
                return (f"{head}-bare", f"容器 {head} 只有选项无动词（无法判定意图），需人工确认")  # ROLLBACK""",
    f"{R9}::test_selfdescription_options_only_pass",
)

# 二十六轮第2批第3处：compose --help 豁免收窄回滚（三模式不一致复活）
experiment(
    "二十六轮第2批第3处 compose --help 收窄回滚（三模式不一致复活）", APV,
    """                _hi = next((k for k, t in enumerate(toks)
                            if t.strip(_QUOTES) in ("--help", "-h")), -1)
                if _hi != -1 and not any(
                        not t.strip(_QUOTES).startswith("-") for t in toks[_hi + 1:]):
                    continue""",
    """                if "--help" in toks or "-h" in toks:
                    continue  # ROLLBACK: 无条件豁免""",
    f"{R9}::test_compose_help_with_sub_unified_asks",
)

# 二十六轮 S-003：action 事件打码回滚（events.jsonl 明文复活）
experiment(
    "二十六轮 S-003 action 打码回滚（events.jsonl 明文复活）", LOOP,
    """            # 二十六轮 S-003：action 的 params 落盘前打码（与 observation 面
            # 同源 redact_text）——此前 `echo sk-…` 的命令明文进 events.jsonl
            self.emit("action", {"tool": name, "params": _redact_deep(args), "call_id": call_id})""",
    """            self.emit("action", {"tool": name, "params": args, "call_id": call_id})  # ROLLBACK""",
    "tests/test_loop_history_recording.py::test_action_event_secret_in_command_is_redacted",
)

# 二十六轮第2批：history arguments 打码回滚（history.json/上游明文复活）
experiment(
    "二十六轮第2批 history arguments 打码回滚（落盘+上游明文复活）", LOOP,
    """                    # 二十六轮第2批：arguments 值层打码（同源 _redact_deep）——
                    # history 既落盘 history.json 也发给上游 provider，此前
                    # `echo sk-…` 的命令在这两条路径上都是明文（泄漏面×2，
                    # 独立复现）。执行用的仍是原 args（打码只进 history 副本）。
                    "function": {"name": name, "arguments": json.dumps(_redact_deep(args), ensure_ascii=False)},""",
    """                    "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},  # ROLLBACK""",
    "tests/test_loop_history_recording.py::test_history_tool_calls_arguments_redacted_on_disk_and_upstream",
)

# 二十六轮第 4 批第 4 处：观察面分通道回退（agent 重新读不到文件真名）
experiment(
    "二十六轮第4批第4处 观察面弱一档回退（文件名误打码复活）", LOOP,
    # ★ 锚点只取【代码行】：此前把 _redact 上方注释一起写进 old，§8a 同步注释
    #   （"文件名豁免已删"）时本组立刻失配 → "锚点文本未找到"（二十六轮第 7 批
    #   实测：审计方自己造成的回归）。收窄为纯代码行后，注释随便改都不会再打断本组。
    """        return redact_text_tool(text)""",
    """        return redact_text(text)  # ROLLBACK: 弱一档撤除""",
    "tests/test_loop_history_recording.py::test_observation_filename_shape_preserved_e2e",
)

# 二十六轮第 4 批第 5 处：git config 补口回退（credential.helper 等重新放行）
experiment(
    "二十六轮第4批第5处 git config 补口回退（credential.helper 等复活）", APV,
    """                            or key.endswith((".clean", ".smudge", ".process", ".textconv",
                                             ".helper", ".uploadpack", ".receivepack", ".command"))
                            or key.startswith(("credential.", "includeif.", "protocol."))
                            or key.endswith((".program", ".allow"))""",
    "",
    f"{R9}::test_git_config_exec_keys_4b_asks",
)
experiment(
    "二十六轮第4批第5处 git config 精确键回退（hooksPath 等复活）", APV,
    """    "credential.helper",           # git 最经典任意命令执行向量
    "core.askpass", "core.gitproxy", "core.alternaterefscommand",
    "core.hookspath",              # hooksPath 指向攻击者目录 = 钩子全换
    "sequence.editor", "merge.tool", "include.path",  # include.* = 配置注入""",
    "",
    f"{R9}::test_git_config_exec_keys_4b_asks",
)

# 二十六轮第 3 批第 1 处：观察面形态层回退（cat .env/echo $KEY 的 sk- 形态
# 明文复活——真 shell_exec 端到端实测三面各 1 次；桩工具名锚点曾对此结构性失明）
experiment(
    "二十六轮第3批第1处 观察面形态层回退（observation 明文复活）", LOOP,
    # ★ 同上一组：锚点同样收窄为纯代码行，避免注释同步时失配（§8a 回归教训）。
    """        return redact_text_tool(text)""",
    """        for v in self._secret_values:
            if v in text:
                text = text.replace(v, "[已隐藏]")
        return text  # ROLLBACK: 撤回第3批前的纯env值替换（无形态层）""",
    "tests/test_loop_history_recording.py::test_observation_shape_secret_redacted_e2e",
)

# 二十六轮第 6 批第 3 处：文件名豁免分支删除——回滚实验（第 5 批曾以
# "强通道兜底使宽/紧不可区分"为由不设，验证员证伪：新旧正则 13 例对比
# 9 例可区分，.env/.pem/.key/.io 四面明文正是第 4 批自己实测过的）。
# old=动态提取当前（只留版本+编号）正则块；new=第 4 批宽豁免（任意扩展名）。
_b6t = REDACT_TOOL.read_text(encoding="utf-8")
B = chr(92)
BS2 = B + B
_b6s = _b6t.index("_FILENAME_SHAPE_RE = re.compile(")
_b6e = _b6t.index('d{2,4}', _b6s)
_b6e = _b6t.index(')', _b6e) + 1
_B6_OLD = _b6t[_b6s:_b6e] + chr(10)
_B6_NEW = (
    "_FILENAME_SHAPE_RE = re.compile(" + chr(10)
    + '    r"' + B + "b(?:sk|pk|rk)-[A-Za-z0-9_-]{1,32}" + B + ".[A-Za-z0-9]{1,8}" + B + 'b"' + chr(10)
    + '    r"|(?:sk|pk|rk)-[a-z]{1,16}-v' + B + "d{1,3}" + B + 'b"' + chr(10)
    + '    r"|(?:SK|PK|RK)-' + B + "d{4}-" + B + "d{2,4}" + B + 'b"  # ROLLBACK: 第4批宽豁免（任意扩展名+低熵误判）' + chr(10)
    + ")" + chr(10)
)
experiment(
    "二十六轮第6批第3处 文件名豁免删除回退（高熵穿越复活）", REDACT_TOOL,
    _B6_OLD, _B6_NEW,
    "tests/test_loop_history_recording.py::test_observation_env_ext_secret_redacted_e2e",
)

# 二十六轮第 7 批第 1 处：`--config-env` 补口回退——【动态提取】新增的两个 flag
# 后按源码原文删掉它们（该 frozenset 在重写中已多次漂移；静态文本易失配）。
# 回滚后 `git --config-env=<k>=<v> log` 与 `--attr-source=<tree>` 重新 PASS。
# ★ 只摘新增项：`-c/--exec-path` 保留 ⇒ 第 3 处的旧锚点 test_git_pip_indirect_exec_asks
#   仍绿，本组红的来源唯一（15 个用例，全部是第 7 批新锚点）。
_g7s = APV.read_text(encoding="utf-8")
# ★ old 必须【只含被删的两行】、new 里【不能】再补 `})`——
#   frozenset 的收尾 `})` 是 old 之后独立的一行，不在替换范围内。
#   本班两次踩坑：① 第一次 new 漏 `})` ⇒ SyntaxError '{' was never closed（rc=4）；
#   ② 第二次 new 补 `})` ⇒ 与原有的 `})` 重复 ⇒ SyntaxError unmatched '}'（rc=4）。
#   判据永远是：替换后 approval.py 必须能 ast.parse（harness 只认 rc==1 的断言红）。
_G7_OLD = (
    '    "-c", "--config-env", "--exec-path", "--upload-pack", "--receive-pack", "--send-pack",'
    + chr(10) + '    "--attr-source",' + chr(10)
)
_G7_NEW = (
    '    "-c", "--exec-path", "--upload-pack", "--receive-pack", "--send-pack",' + chr(10)
    + "  # ROLLBACK" + chr(10)
)
experiment(
    "二十六轮第7批第1处 git --config-env/--attr-source 补口回退（静默放行复活）", APV,
    _G7_OLD, _G7_NEW,
    f"{R9}::test_git_config_env_indirect_exec_asks",
)

# 二十六轮第 7 批第 2 处（K5 下载体积上限）回退——【动态提取】边读边计数块，
# 还原成"只有 EOF 才停"的旧 while 循环（无 content-length 校验、无字节上限）。
# 回滚后：无 CL 的 chunked 假流被【读完 12MiB】才因哈希不符停手 ⇒ 锚点红在
# pytest.raises(match="体积超限") 的断言上（不是语法错误）。
# 注：install_melotts.py 在仓库 scripts/ 下、不在 TMP 副本里——测试用
# 实测这一条链：测试 import 的是真仓库文件（backend/tests 里 sys.path.insert
# parents[2]/scripts），而 redgreen 的 _TMP_ROOT/backend/tests 副本同样
# parents[2] = _TMP_ROOT ⇒ 副本测试会去读 _TMP_ROOT/scripts（不存在）。
# ⇒ 本组改用【父进程内自建实验】：补丁打真文件，跑真仓库 tests（见下方
#   _experiment_real_repo，不经过 TMP 副本；还原同样逐字节核对）。
_IM = ROOT / "scripts" / "install_melotts.py"


def _run_test_real_repo(test_id: str) -> int:
    """跑真仓库（backend/ 为 cwd）的锚点——用于 TMP 副本覆盖不到的 scripts/ 目标。

    test_id 可用空格分隔多个 id（pytest 位置参数）。
    """
    r = subprocess.run(
        [str(PY), "-m", "pytest", *test_id.split(), "-o", "addopts=",
         "-p", "no:warnings", "--tb=no"],
        cwd=ROOT / "backend", capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return r.returncode


def _experiment_real_repo(name: str, path: pathlib.Path, old: str, new: str, test_id: str) -> None:
    """与 experiment() 同语义，但目标在真仓库（TMP 副本未覆盖的 scripts/）。

    old 用【动态切片】提取调用方传入的片段，避免静态文本漂移失配。

    ★ 逐字节还原（2026-10-04 B10 批实测修）：此前用
        orig = path.read_text(encoding="utf-8")   # 读时把 CRLF 归一成 LF
        path.write_text(orig, encoding="utf-8")   # 写时又把 LF 变成 os.linesep
      于是**原本是 LF 的文件被还原成 CRLF**——"逐字节还原"是假的，而且
      `read_text(...) != orig` 那句校验比的是"解码后的文本"，结构上抓不到换行差异。
      实测后果：本班跑完整门后 `git status` 里 scripts/check_all.ps1 变脏
      （仅换行变化；`git hash-object` 仍等于 HEAD ⇒ 内容没坏，但工作区被污染，
      且违反本仓 .gitattributes 的 `* text=auto eol=lf`）。
      现在改为：**保留原始字节，还原时直接回写原始字节**；校验也比字节。
    """
    orig_bytes = path.read_bytes()
    orig = orig_bytes.decode("utf-8")          # 解码（含通用换行归一）供锚点匹配
    crlf = b"\r\n" in orig_bytes               # 原文件是不是 CRLF 风格

    def _write(text: str) -> None:
        data = text.encode("utf-8")
        if crlf:                               # 保持一致的行尾风格，不引入混合
            data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        path.write_bytes(data)

    if old not in orig:
        results.append((name, False, "锚点文本未找到——实验本身失效，需人工检查"))
        return
    if _DRYRUN:
        # ★ 真仓库通道：dryrun 只校验、**绝不写盘**（这条通道会动 scripts/ 下的真文件）
        _dryrun_check(name, path, old, new)
        return
    try:
        _write(orig.replace(old, new, 1))
        red = _run_test_real_repo(test_id)
        if _SELFCHECK is not None and _SELFCHECK in name:
            r = subprocess.run(
                [str(PY), "-m", "pytest", *test_id.split(), "-o", "addopts=",
                 "-p", "no:warnings", "--tb=line"],
                cwd=ROOT / "backend", capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            fails = [ln.split("FAILED ")[1].split(" - ")[0]
                     for ln in (r.stdout or "").splitlines() if "FAILED" in ln]
            print(f"[selfcheck] 组={name}")
            print(f"[selfcheck] 回滚跑 rc={red}  红例: {fails[:4]}")
    finally:
        path.write_bytes(orig_bytes)           # ★ 逐字节还原：回写原始字节
        if path.read_bytes() != orig_bytes:    # 父进程核对（比字节，不比文本）
            path.write_bytes(orig_bytes)
            results.append((name, False, "还原校验失败——已强制回写，请人工复核 git diff"))
            return
    back = _run_test_real_repo(test_id)
    ok = red == 1 and back == 0
    note = (f"回滚跑={'RED(断言)' if red == 1 else f'rc={red}（非断言红，判FAIL）' if red else 'green'}"
            f"  恢复跑={'GREEN' if back == 0 else f'rc={back}'}")
    results.append((name, ok, note))


_im_s = _IM.read_text(encoding="utf-8")
_im_a = _im_s.index("            with urllib.request.urlopen(req, timeout=300) as r:")
_im_b = _im_s.index("            got = sha256_file(dest)")
_IM_OLD = _im_s[_im_a:_im_b]
_IM_NEW = (
    '            with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f:' + chr(10)
    + "                while True:" + chr(10)
    + "                    chunk = r.read(1 << 20)" + chr(10)
    + "                    if not chunk:" + chr(10)
    + "                        break" + chr(10)
    + "                    f.write(chunk)  # ROLLBACK: 无 CL 校验、无字节上限" + chr(10)
)
_experiment_real_repo(
    "二十六轮第7批第2处 K5 下载体积上限回退（只有 EOF 才停复活）", _IM,
    _IM_OLD, _IM_NEW,
    "tests/test_install_melotts.py::test_download_refuses_and_deletes_when_stream_exceeds_cap "
    "tests/test_install_melotts.py::test_download_precheck_rejects_oversized_content_length",
)

# 二十六轮第 7 批第 3 处（K2 的 >5MB 拒抓）回退——【动态提取】流式块，
# 还原成 `await client.get(...)`（缓冲整体）+ 读 content-length 的旧形态。
# 回滚后：D7（无 CL 的 chunked）根本不拒绝、D6 读完 6,000,000 才判 ⇒ 锚点红在
# "DID NOT RAISE ValueError" / "无 content-length 的超大响应也必须拒绝"。
_LO = _TMP_ROOT / "backend" / "app" / "executors" / "local.py"
_lo_s = _LO.read_text(encoding="utf-8")
_lo_a = _lo_s.index("                ips = await assert_public_url(current)")
_lo_b = _lo_s.index('            else:\n                raise SsrfBlocked("重定向跳数超限')
_LO_OLD = _lo_s[_lo_a:_lo_b]
_LO_NEW = (
    "                ips = await assert_public_url(current)" + chr(10)
    + "                resp = await client.get(current)" + chr(10)
    + "                await recheck_still_public(current, ips)" + chr(10)
    + "                if resp.status_code in (301, 302, 303, 307, 308):" + chr(10)
    + '                    loc = resp.headers.get("location") or ""' + chr(10)
    + "                    nxt = urljoin(current, loc)" + chr(10)
    + '                    if not nxt.startswith(("http://", "https://")):' + chr(10)
    + '                        raise SsrfBlocked(f"重定向到非 http(s) 协议（{loc[:60]}），已拦截")' + chr(10)
    + "                    current = nxt" + chr(10)
    + "                    continue" + chr(10)
    + "                resp.raise_for_status()" + chr(10)
    + '                if int(resp.headers.get("content-length") or 0) > 5_000_000:' + chr(10)
    + '                    raise ValueError("响应体过大（>5MB），已放弃抓取")  # ROLLBACK' + chr(10)
    + '                ctype = resp.headers.get("content-type", "")' + chr(10)
    + "                text = resp.text" + chr(10)
    + "                break" + chr(10)
)
experiment(
    "二十六轮第7批第3处 _web_fetch 流式计数回退（缓冲整体、读完才判复活）", _LO,
    _LO_OLD, _LO_NEW,
    "tests/test_ssrf.py::test_web_fetch_streams_and_aborts_at_cap "
    "tests/test_ssrf.py::test_web_fetch_no_content_length_still_refused",
)

# 二十六轮第 7 批第 4 处（接线级锚点）回退——撤掉 loop.py 的 _wrap_untrusted 接线。
# ★ 本组同时证明【旧锚点是瞎的】：旧 test_wrap_actually_applied_in_history 只调
#   纯函数 + 断言 SYSTEM_PROMPT，回滚态下照样绿（二十六轮验收结论，本班复现）。
experiment(
    "二十六轮第7批第4处 不可信包裹接线回退（history 内裸正文复活）", LOOP,
    """            self.history.append({"role": "tool", "tool_call_id": call_id,
                                 "content": _wrap_untrusted(name, output[:4000])})""",
    """            self.history.append({"role": "tool", "tool_call_id": call_id,
                                 "content": output[:4000]})  # ROLLBACK""",
    "tests/test_ssrf.py::test_wrap_actually_applied_in_history",
)

# ═══ 二十六轮第 7 批 §6：恢复第 6 批被【无理由删除】的两组回滚组 ═══
# 二十六轮第 6 批验收逐条实测：46→44 的真相是"删 4 加 2"，其中两组删除理由不成立
# ——锚点在源码里逐字仍在，把修复原样打回依然 rc=1。白丢两组判别力，其中 ④ 更是
# usage 那处声称"交叉覆盖"的那一组（删掉造成连锁失效）。此处按原文恢复。

# §6-③ 第 5 批第 2 处 gpg/trailer/protocol 漏键回退
# 锚点仍在：approval.py 的 key.startswith(("credential.","includeif.","protocol."))
#           + key.endswith((".program", ".allow"))
# 回滚态实测（本班，%TEMP% 副本）：rc=1，红例恰为三条——
#   gpg.openpgp.program / gpg.ssh.program / protocol.ext.allow
#   红源 = 断言消息"必须 ask：git config protocol.ext.allow 'always' → None"
experiment(
    "二十六轮第5批第2处 gpg/trailer/protocol 漏键回退", APV,
    """                            or key.startswith(("credential.", "includeif.", "protocol."))
                            or key.endswith((".program", ".allow"))""",
    "",
    f"{R9}::test_git_config_global_bypass_5b_asks",
)

# §6-④ 第 5 批第 4 处 full_label 无条件脱敏回退（短标题明文复活）
# 锚点仍在：main.py 的 _compose_usage_label 结尾
# 回滚态把"无条件"改回第 4 批的条件式（len>=30 才打码）→ 短串明文进外发面。
experiment(
    "二十六轮第5批第4处 full_label 无条件脱敏回退（短标题明文复活）", MAIN,
    '    return _redact_text(base)[:120] if base else "（无标题）"',
    '    return (_redact_text(base)[:200] if len(base) >= 30 else base) if base else "（无标题）"  # ROLLBACK: 短串不打码',
    "tests/test_loop_history_recording.py::test_usage_full_label_unconditionally_redacted",
)

# ═══ 二十六轮第 7 批 §7：GET /api/v1/usage 的【接线级】回滚组（补零锚点） ═══
# 现状（第 6 批验收，决定性原始输出）：
#   把 `label = _usage_label(full_title)` 回退成 `full_title[:42]` → rc=0（不红）
#   撤掉 `t_model = _usage_model(t_model)`                        → rc=0（不红）
#   全仓 grep 'api/v1/usage|get_usage' 在 tests 里 → 0 命中
# ⇒ 端点接线没有任何锚点，既有锚点只测三个纯函数。
# 锚点是 tests/test_usage_endpoint.py（本批新增）：TestClient 真调端点，
# 再【递归遍历整个响应 JSON（含 dict 键名）】断言三条密钥明文 0 出现。
# 两组的回滚态实测（本班，%TEMP% 副本）：
#   label  组 → rc=1，红源 = "$.by_task[0].label 含 …"（断言消息）
#   t_model组 → rc=1，红源 = "$.by_model.AKIAIOSFODNN7EXAMPLE（键名）含 …"
#              ★ 键名那条正是"只查 value 会漏"的证据：by_model 的键就是模型名。
experiment(
    "二十六轮第7批第7处 usage label 接线回退（full_title[:42] 明文复活）", MAIN,
    "            label = _usage_label(full_title)",
    "            label = full_title[:42]  # ROLLBACK: 接线撤除",
    "tests/test_usage_endpoint.py::test_usage_endpoint_has_no_plaintext_secret_anywhere",
)
experiment(
    "二十六轮第7批第7处 usage by_model 键接线回退（_usage_model 撤除）", MAIN,
    "            t_model = _usage_model(t_model)",
    "            t_model = t_model  # ROLLBACK: 接线撤除",
    "tests/test_usage_endpoint.py::test_usage_endpoint_has_no_plaintext_secret_anywhere",
)

# ═══ 二十六轮第 7 批第 9 处：shell 赋值前缀穿透（_deep_parts 咽喉点） ═══
# 现状（独立验证员在 0d4bee4 上实测 + 本班复现）：代码里只有容器包装器那一条面会
# 剥赋值前缀（旧局部闭包），而 git/pip/net/写入/动词等所有按 head 判定的面都是
# part.split()[0] 直接取 head ⇒ 看到 "FOO=bar" 就不认识本体，整族静默放行：
#   FOO=bar git -c core.pager='…' log → PASS（本体 ASK）
#   FOO=bar git push origin main      → PASS（本体 ASK，net 面）
#   FOO=bar dd if=/dev/zero of=$HOME/x→ PASS（本体 ASK，写入面）
# 回滚态实测（本班，%TEMP% 副本，补丁 ast.parse 通过）：rc=1，【19 条红例全部落在
# 断言上】，红源原文 = "赋值前缀改变了判定：'FOO=bar git push origin main' → PASS
# （无前缀本体是 ASK）——赋值前缀必须穿透到真 head"。
experiment(
    "二十六轮第7批第9处 赋值前缀穿透回退（_deep_parts 不剥前缀）", APV,
    """            part = _strip_assign_prefix_str(part)
            if not part.strip():
                continue  # 纯赋值片段（`FOO=bar` 单独成段）不是命令，跳过
            out.append(part)""",
    """            out.append(part)  # ROLLBACK: 不剥赋值前缀""",
    "tests/test_assign_prefix.py",
)

# ═══ 二十六轮第 7 批第 10 处：git 配置注入【环境变量】面（施工单风险点②） ═══
# 现状（独立验证员在 0d4bee4 上实测 + 本班复现）：payload 走环境变量传给 git，
# **不在命令行参数里** ⇒ 参数面（_GIT_EXEC_FLAGS）结构上拦不到：
#   GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.pager GIT_CONFIG_VALUE_0='!echo PWNED' git log
#   → PASS（而 `git -c core.pager=… log` 是 ASK）；真机实测该形态能执行 shell。
# 回滚态实测（本班，%TEMP% 副本，补丁 ast.parse 通过）：rc=1，【7 条红例全部落在
# 断言上】，红源原文 = "GIT_CONFIG 注入必须拦：GIT_CONFIG_COUNT=1
# GIT_CONFIG_KEY_0=alias.pwn GIT_CONFIG_VALUE_0='!echo PWNED' git pwn → None"。
experiment(
    "二十六轮第7批第10处 GIT_CONFIG 注入环境变量面回退（静默放行复活）", APV,
    """    names: list[str] = []
    i, n = 0, len(part)""",
    """    names: list[str] = []
    return names  # ROLLBACK: 不再识别赋值前缀名（注入面失效）
    i, n = 0, len(part)""",
    "tests/test_git_config_env_inject.py",
)

# ═══ 二十六轮第 7 批第 11 处：API Key 卫生（strip + 拒非 ASCII） ═══
# 事故来源（2026-10-04 用户报"发消息不回复"）：日志里反复出现
#   [记忆] 提取失败（不影响任务）：UnicodeEncodeError: 'ascii' codec can't encode
#   characters in position 7-25
# 其中 7 正是 "Bearer " 的长度 ⇒ 报错对象是 Authorization 头，即那一刻进程里的
# Key 前 18 个字符全是非 ASCII（复制时带进全角空格/中文标点）。
# 构造 provider 只检查"非空"⇒ 填的时候一切正常，只在真调用时才炸，且报错看不懂。
# 回滚态实测（本班，%TEMP% 副本，补丁 ast.parse 通过）：rc=1，4 条红例全在断言上
# （"assert '  sk-abc…  \n' == 'sk-abc…'" 与 "DID NOT RAISE ValueError"）。
experiment(
    "二十六轮第7批第11处 API Key 卫生回退（strip/非ASCII 校验撤除）", MAIN,
    # ★ Phase 1 ① 批：端点里多了一行 `cleaned = …`（为了给用户级写入复用清洗结果），
    #   旧锚点文本随之失配 —— 由 `--dryrun` 在 2 秒内抓出（整条门跑一次要 10 分钟）。
    """        cleaned = _clean_api_key(req.api_key, m.api_key_env)
        os.environ[m.api_key_env] = cleaned""",
    """        cleaned = req.api_key  # ROLLBACK: 不 strip 不校验
        os.environ[m.api_key_env] = cleaned""",
    "tests/test_api_key_hygiene.py",
)

# ═══ 第十轮 B3（按钮对比度）/ B4（docstring） ═══

experiment(
    "B3 .btn-primary 启用态回退成 var(--accent)（2.57:1）", CSS,
    "background: #1d4ed8; color: #fff; font-weight: 600;",
    "background: var(--accent); color: #fff; font-weight: 600; /* ROLLBACK */",
    f"{R10}::TestButtonContrast::test_btn_primary_enabled",
)
experiment(
    "B3 .btn-primary 禁用态回退成 opacity:0.4（1.77:1）", CSS,
    ".btn-primary:disabled { background: #1a2c55; color: #9fb4dd; cursor: not-allowed; }",
    ".btn-primary:disabled { opacity: 0.4; cursor: not-allowed; } /* ROLLBACK */",
    f"{R10}::TestButtonContrast::test_btn_primary_disabled",
)
experiment(
    "B3 .btn-save 启用态回退成 var(--accent)", CSS,
    "background: #1d4ed8;\n  color: #fff;",
    "background: var(--accent, #6ba3f5);\n  color: #fff; /* ROLLBACK */",
    f"{R10}::TestButtonContrast::test_btn_save_enabled",
)
experiment(
    "B3 .btn-save 禁用态回退成 opacity:0.4", CSS,
    ".btn-save:disabled { background: #1a2c55; color: #9fb4dd; cursor: default; }",
    ".btn-save:disabled { opacity: 0.4; cursor: default; } /* ROLLBACK */",
    f"{R10}::TestButtonContrast::test_btn_save_disabled",
)
experiment(
    "B4 redact.py 删掉 sk-model-v2 已知限制段", REDACT,
    """已知限制（第十轮 B4 落盘）：
  sk-/pk-/rk- 形态的下界是 **6** 位——为了盖住 sk-abc123 这类短 Key。
  代价：`sk-model-v2` 这类"sk- 开头 + ≥6 位"的普通标识符（如模型名）
  **也会被打码**成 [已隐藏]。这是有意取舍：宁可多打码一个模型名，
  不可漏放一个真 Key。若未来出现"必须原样展示 sk- 开头标识符"的场景，
  应走白名单而不是降低下界。
""",
    "",
    f"{R10}::test_redact_known_limitation_documented",
)

# ═══ A3（审计台账 P1）：宿主模式（sandbox="off"）风险告知 ═══
# 修复前事实（本批开工实测，原始输出）：
#     repr(await ex._shell(tmp, "echo A3-PROBE"))  ==  'A3-PROBE'
# 同一能力面的沙箱分支却会追加 sandbox_note（"沙箱内执行：…"）——
# ⇒ 一个告知、一个不告知；用户看到的宿主命令回执与"模拟执行"长得一模一样。
# 四组各撤一层（后端告知 / 任务头徽标 / 审批栏 / 设置页），
# 锚点都是 tests/test_host_mode_notice.py 里的断言（不是语法红）。
_LO_A3 = _TMP_ROOT / "backend" / "app" / "executors" / "local.py"
_TV_A3 = _TMP_ROOT / "frontend" / "src" / "components" / "TaskView.tsx"
_SP_A3 = _TMP_ROOT / "frontend" / "src" / "components" / "SettingsPanel.tsx"
_HOST_NOTICE_TEST = "tests/test_host_mode_notice.py"

experiment(
    "A3 宿主模式告知回退（命令回执重新变成裸输出）", _LO_A3,
    "        return text + _HOST_EXEC_NOTE",
    "        return text  # ROLLBACK: 无告知（修复前形态）",
    f"{_HOST_NOTICE_TEST}::test_host_shell_output_carries_risk_notice "
    f"{_HOST_NOTICE_TEST}::test_notice_reaches_task_timeline_observation",
)


def _cut_block(src: str, start_marker: str, end_marker: str = ")}") -> str:
    """从源码里动态切出【一个 JSX 条件块】（含结尾 `)}`）——避免静态多行文本漂移。"""
    a = src.index(start_marker)
    b = src.index(end_marker, a) + len(end_marker)
    return src[a:b] + chr(10)


experiment(
    "A3 任务头「本机执行」徽标删除（沙箱关着也看不出是本机跑）", _TV_A3,
    _cut_block(_TV_A3.read_text(encoding="utf-8"), '{!sandboxOn && (\n              <span\n                className="host-exec-tag"'),
    "",
    f"{_HOST_NOTICE_TEST}::test_taskview_header_tag_when_host_mode",
)
experiment(
    "A3 审批栏宿主模式告知删除（批准前不再说明落在真实电脑上）", _TV_A3,
    _cut_block(_TV_A3.read_text(encoding="utf-8"), '{!sandboxOn && (\n            <div className="approval-host-warn">'),
    "",
    f"{_HOST_NOTICE_TEST}::test_taskview_approval_bar_warns_when_host_mode",
)
experiment(
    "A3 设置页宿主模式告警块删除（配置面不再告知风险）", _SP_A3,
    _cut_block(_SP_A3.read_text(encoding="utf-8"), "{String(settings?.executor?.sandbox ?? 'off') !== 'docker' && ("),
    "",
    f"{_HOST_NOTICE_TEST}::test_settings_panel_warns_when_host_mode",
)

# ═══ A3 批基建：`.ps1` 必须带 UTF-8 BOM（本班实测踩到，代价是"守门整条跑不起来"）═══
# 原始事实：用编辑器重写 scripts/check_all.ps1 后 BOM 丢失（EF BB BF → 3C 23 0A），
# Windows PowerShell 5.1 按 GBK 解码中文注释 ⇒
#     Missing '=' operator after key in hash literal.
#     At D:\AI\agent-shell\scripts\check_all.ps1:25 char:44
# 连"跑不出结果"都算不上——脚本根本没被解析。操作备忘早写了这条规则，但无机制保证。
# 本组打在【真仓库】（BOM 是文件级属性，%TEMP% 副本里没有 .ps1）：
# 摘掉 check_all.ps1 的 BOM → 新增的守卫锚点必红 → 逐字节还原 → 回绿。
_CA = ROOT / "scripts" / "check_all.ps1"
_experiment_real_repo(
    "守门 .ps1 BOM 丢失回退（PowerShell 5.1 按 GBK 解析 ⇒ 脚本语法错）", _CA,
    chr(0xFEFF) + "<#", "<#",
    "tests/test_ps1_encoding_guard.py::test_every_ps1_has_utf8_bom",
)

# ═══ B10（审计台账 P2）：出网面（Slack/邮件通知）每个字段都要打码 ═══
# 修复前事实（本批实测，假 store + 假渠道，原始输出见 B10-接口面扫描.log）：
#   LEAK slack.text / email.subject / email.body 各 1 处：
#     【LanternLogic Agent】任务完成：迁移密钥 sk-title-9f3a2b7c8d1e4f5a
#     好的，我看到 [已隐藏-疑似密钥]        ← 同一条消息：正文打了码、标题是明文
# 回滚态把咽喉点的两行打码还原成"原样拼接"（补丁 ast.parse 通过）。
experiment(
    "B10 出网面打码回退（通知正文/邮件标题里标题明文复活）", _TMP_ROOT / "backend" / "app" / "notify.py",
    """    safe_title = redact_text(str(task_title or ""))
    safe_summary = redact_text(str(summary or ""))[:500]""",
    """    safe_title = str(task_title or "")  # ROLLBACK: 标题不打码
    safe_summary = str(summary or "")[:500]  # ROLLBACK: 摘要不打码""",
    "tests/test_notify_redact.py",
)

# ═══ C5（审计台账 P1）：MCP 只读工具读敏感目标必须审批 ═══
# 修复前事实：auto_edit 下 `mcp__*` 只对"工具名含写入类动词"的调用弹审批，
# 名字像只读的直接静默执行（无审批、无留痕）⇒ 叠加 C2 网页注入即
# "读 ~/.ssh/id_rsa → 外发"整条链。
# 回滚态：把新增的 `else: risky = _mcp_risky_target(args) …` 整块删掉（动态切片，
# 不依赖手抄多行文本）；补丁后 loop.py 仍可 ast.parse。
_lp_s = LOOP.read_text(encoding="utf-8")
_c5_a = _lp_s.index("                else:\n                    # C5：名字像只读的也不能无条件静默")
_c5_b = _lp_s.index("            t0 = time.monotonic()", _c5_a)
C5_OLD = _lp_s[_c5_a:_c5_b]
experiment(
    "C5 MCP 只读工具敏感目标审批回退（静默读到凭据复活）", LOOP,
    C5_OLD,
    "",
    "tests/test_mcp_sensitive_approval.py",
)

# ═══ A5（审计台账 P1）：模型预设只能有一份表（8/6 漂移防复发） ═══
# 修复前事实：切换器（modelPresets.ts）6 项 vs 设置页（SettingsPanel 内联）8 项
# ⇒ 设置页配得了 Claude，切换器里没有它，切走就切不回来、按钮显示原始 id。
# 回滚态：把 ModelPicker 的弹层清单退回"本地硬编码 6 项"（= 漂移复发的那一版）。
# 锚点 tests/test_model_presets_single_source.py 里"防漂移"两条必须红。
_TV_PICKER = _TMP_ROOT / "frontend" / "src" / "components" / "ModelPicker.tsx"
_TV_PRESETS = _TMP_ROOT / "frontend" / "src" / "modelPresets.ts"
_pk_s = _TV_PICKER.read_text(encoding="utf-8")
_pk_a = _pk_s.index("          {/* A5：清单来自共享表（SWITCHER_PRESETS）——不许在这里再写死一份 */}")
_pk_b = _pk_s.index("          ))}", _pk_a) + len("          ))}")
_PK_OLD = _pk_s[_pk_a:_pk_b] + chr(10)
_PK_NEW = (
    "          {[{ model_name: 'mimo-v2.6-flash', label: 'MiMo（小米）' }," + chr(10)
    + "            { model_name: 'deepseek-chat', label: 'DeepSeek' }," + chr(10)
    + "            { model_name: 'qwen-max', label: '通义千问' }," + chr(10)
    + "            { model_name: 'glm-4-flash', label: '智谱 GLM' }," + chr(10)
    + "            { model_name: 'moonshot-v1-8k', label: 'Kimi' }," + chr(10)
    + "            { model_name: 'qwen3.5:9b', label: '本地（Ollama）' }].map((p) => (" + chr(10)
    + "            <button" + chr(10)
    + "              key={p.model_name}" + chr(10)
    + "              className={'ctl-item' + (p.model_name === modelName ? ' ctl-on' : '')}" + chr(10)
    + "              onClick={() => pick(p.model_name)}" + chr(10)
    + "            >" + chr(10)
    + "              {p.label}" + chr(10)
    + "              <span>{p.model_name}</span>" + chr(10)
    + "            </button>" + chr(10)
    + "          ))}" + chr(10)
)
experiment(
    "A5 切换器清单退回本地硬编码 6 项（8/6 漂移复发）", _TV_PICKER,
    _PK_OLD, _PK_NEW,
    "tests/test_model_presets_single_source.py",
)

# ═══ D1（审计台账"待仲裁"）：链接逃逸在宿主模式必须同样 fail-closed ═══
# 仲裁结论（本批实测，原始输出见 agent-shell-评审/D1-symlink探针-修复后.log）：
#   **K3 的根因描述是对的**，审计方的"复现不出"是因为锚点自己瞎了：
#   `_real_run` 同步夹具时对目录链接走 copytree（跟随链接 → 把外靶内容复制成
#   工作区内的普通目录）⇒ 真实工作区里没有逃逸形态，`escape/target.txt` 成了
#   存在的工作区内文件，②"覆写已有文件"顺手问了一次 ⇒ 测试绿得纯属巧合。
#   真形态（真 junction + 生产回调）实测：
#     _target_exists('escape/target.txt')=None、_path_is_inside=False
#     cp /dev/null escape/target.txt   host=PASS(不拦)  sandbox=ASK   ← 洞
#   根因：两处 `if ... is None and in_sandbox:`（写动词面 + xargs/管道面）
#   都假设"宿主模式由④越界面兜底"，而④只扫绝对路径（_ABS_PATH_RE），
#   相对路径的链接逃逸进不去 ⇒ 两个面都不拦。
# 回滚态：把两处站点改回 `and in_sandbox`（用各自唯一的 return 文案定位，不手抄多行；
# 回滚文本 `_APV_ROLLED` 定义在文件前部——「③ _path_is_inside」那组要与它跨文件两站同撤）。
experiment(
    "D1 链接逃逸 fail-closed 回退（两处站点改回 `and in_sandbox`）", APV,
    _APV_NOW, _APV_ROLLED,
    "tests/test_approval_capability_matrix.py::test_matrix_a_symlink_escape_asks "
    "tests/test_approval_capability_matrix.py::test_matrix_a_symlink_escape_asks_in_sandbox",
)

# ═══ C7 契约回归（★ 上一班留下的 P0）：新任务 id 必须真能用 ═══
# 事故（本班在真机实测发现）：POST /tasks 返回 201，但任务 4ms 后 failed、events.jsonl
# 根本不存在 —— 用户看到的是"新任务全废"。根因：C7（880f887）把 `_new_task_id()` 的
# 随机位从 4 位 hex 提到 8 位，**契约校验没跟着放开**（schemas.py 与
# contracts/events.schema.json 都还是 `{4}` 定长）⇒ 第一条事件的 EventEnvelope
# 构造就 ValidationError。全仓 1036 个用例绿着，因为测试里的 task_id 全是手写 4 位 ——
# C7 只断言了"id 长这样"，没断言"这样的 id 能用"。
# 回滚态：两端同撤（schemas.py 的 re + 契约 JSON 的 pattern 都改回 4 位定长）。
_SCHEMAS_C7 = _TMP_ROOT / "backend" / "app" / "schemas.py"
_CONTRACT_C7 = _TMP_ROOT / "contracts" / "events.schema.json"
experiment_multi(
    "C7 契约回归：新任务 id 必须能用（pattern 两端同撤回 4 位定长）", [
        (_SCHEMAS_C7, r"^task_\d{8}_[a-z0-9]{4,32}$", r"^task_\d{8}_[a-z0-9]{4}$"),
        (_CONTRACT_C7, r"^task_\\d{8}_[a-z0-9]{4,32}$", r"^task_\\d{8}_[a-z0-9]{4}$"),
    ],
    "tests/test_task_id_contract_consistency.py",
)

# ═══ Phase 1 ①（首次上手 · 第一刀）：没配 Key 时第一眼要看得见 ═══
# 修复前：Hero 是个"能输入但发出去没动静"的空态（provider 构造失败 ⇒ 建任务 503），
# 没有任何指引。回滚态 = 把 needKey 的读取与提示条整块删掉（动态切片，两站同撤）。
_HERO = _TMP_ROOT / "frontend" / "src" / "components" / "Hero.tsx"
_hero_s = _HERO.read_text(encoding="utf-8")
_h_a = _hero_s.index("  // ★ Phase 1 ①「首次上手」第一步")
_h_b = _hero_s.index("  }, []);", _h_a) + len("  }, []);") + 1
_HERO_EFFECT = _hero_s[_h_a:_h_b]
_h_c = _hero_s.index("        {needKey && (")
_h_d = _hero_s.index("        )}\n", _h_c) + len("        )}\n")
_HERO_STRIP = _hero_s[_h_c:_h_d]
experiment_multi(
    "首次上手：Key 未配提示条回退（空态重新变成「发出去没动静」）", [
        (_HERO, _HERO_EFFECT, ""),
        (_HERO, _HERO_STRIP, ""),
    ],
    "tests/test_first_run_setup.py",
)

# ═══ ★ config.json 写隔离（8f 批"根治"没根治，2026-10-04 第二次血案）═══
# 事故：跑一次 `tests/test_key_persist.py`（端点会 `_save_config()`），用户真实
# config.json 被写成测试那份（host 打回 127.0.0.1、storage.data_dir 指向 %TEMP%
# ⇒ 重启即"任务全消失" + 手机直连断掉）。根因：`load_config()` 认
# `AGENT_SHELL_CONFIG`（读隔离 ✓），但 `main._CONFIG_PATH` 自己算 `__file__` 路径
# （写隔离 ✗）。修法：`config.config_path()` 统一解析，写读同源。
# 回滚态：把 `_CONFIG_PATH` 改回硬编码仓库路径 —— 锚点"写回路径必须是临时文件"必红。
experiment(
    "config.json 写隔离回退（_CONFIG_PATH 改回硬编码仓库路径）", MAIN,
    # ★ 第一版把补丁打在 import 行上 ⇒ 后面那行 `_CONFIG_PATH = config_path()`
    #   照样执行、把硬编码值覆盖掉 ⇒ **回滚跑=green（组没有判别力）**，
    #   由完整门当场抓出（`--dryrun` 只查"锚点找得到 + 能解析"，查不出这种）。
    #   教训：回滚补丁必须打在**真正生效的那一行**上。
    "_CONFIG_PATH = config_path()",
    "_CONFIG_PATH = Path(__file__).resolve().parents[2] / \"config.json\"  # MUTANT: 写隔离失效",
    "tests/test_config_write_isolation.py",
)

# ═══ Phase 1 ① 后半截：Key 写 User 级环境变量（默认不写 / 不回显 / 回读校验）═══
experiment(
    "Key 持久化回退（persist 分支撤除：写了也不落用户级）", MAIN,
    """        if req.persist:
            ok, why = write_user_env(m.api_key_env, cleaned)      # ← 不回显 Key
            persisted = ok
            note = ("配置已保存。" + why +
                    ("；当前进程已立即生效。" if ok else "；当前进程仍可用，但重启后会丢。"))""",
    "        pass  # MUTANT: 不写用户级环境变量",
    "tests/test_key_persist.py",
)

# ═══ Phase 1 ②（一键启动）：start.bat 的接线 ═══
# 修复前：start.bat 只认 vite 开发端口（127.0.0.1:5173）、不体检、缺件时窗口一闪。
# 回滚态：把"向体检要地址"改回写死 5173（= 运行期又依赖 Node）。
_BAT = _TMP_ROOT / "start.bat"
_bat_s = _BAT.read_text(encoding="utf-8")
_BAT_OLD = 'for /f "usebackq delims=" %%u in (`"%PY%" "%ROOT%scripts\\preflight.py" --print-url`) do set "URL=%%u"'
_BAT_NEW = 'set "URL=http://127.0.0.1:5173/"  # ROLLBACK: 写死 vite 开发端口'
experiment(
    "一键启动回退（地址写死 5173、不向体检要）", _BAT,
    _BAT_OLD, _BAT_NEW,
    "tests/test_preflight.py",
)

# ═══ Phase 2 ④（能力槽 + 提供者）：漂移必须被抓 ═══
# 回滚态 = 把"对话"能力里的 mimo 声明删掉（= 代码能构造、声明表却没有）。
# 期望：至少两条锚点红 —— ①"能构造的必须在表里" ②"当前配置选的必须在表里"。
_CAPS = _TMP_ROOT / "backend" / "app" / "capabilities.py"
experiment(
    "能力槽声明漂移（删掉 mimo 声明：代码能构造、表里没有）", _CAPS,
    """            "mimo": {"label": "小米 MiMo", "kind": "cloud", "key_env": "XIAOMI_MIMO_API_KEY",
                     "models": ["mimo-v2.6-flash", "mimo-v2.5", "mimo-v2.5-pro"]},
""",
    "            # MUTANT: mimo 声明被删掉（代码仍能构造它）\n",
    "tests/test_capabilities.py",
)

# ═══ Phase 2 ④ 第二刀：报错里的三条降级路径 ═══
# 回滚态 = 503 只回原句、不挂降级路径（= 用户知道坏了、不知道往哪走）。
experiment(
    "降级路径回退（503 不挂三条路）", MAIN,
    """            fallback_hint(cfg, "chat",
                          f"模型提供者不可用（provider={cfg.model.provider}）：{type(e).__name__}: {e}"),""",
    """            f"模型提供者不可用（provider={cfg.model.provider}）：{type(e).__name__}: {e}",  # MUTANT""",
    "tests/test_capabilities.py",
)

# ═══ Phase 2 ⑥：模型列表接线（进设置页自动拉 + 刷新绕缓存）═══
# 回滚态 = 去掉"进入模型设置自动拉一次"的效果（用户得先猜着按刷新）。
_SETTINGS_TSX = _TMP_ROOT / "frontend" / "src" / "components" / "SettingsPanel.tsx"
experiment(
    "模型列表接线回退（进设置页不自动拉）", _SETTINGS_TSX,
    """  useEffect(() => {
    if (section !== 'model') return;
    loadModels();
  }, [section, provider, loadModels]);""",
    "  // MUTANT: 自动拉取被删掉（进设置页看到空下拉）",
    "tests/test_model_list.py",
)

# ═══ Phase 2 ⑤：本地 ASR 档位不许"偷偷回退云端" ═══
# 回滚态 = 选了本地但没装时，静默改用云端（用户选本地就是为了数据不出本机）。
_ASR = _TMP_ROOT / "backend" / "app" / "asr.py"
experiment(
    "ASR 本地档回退（没装时静默改用云端）", _ASR,
    # ★ 2026-10-06 换锚点：本地档**真接上之后**，原来那段"无条件抛还没装"的代码没了 ✗
    #   （红绿组当场报"锚点文本未找到——实验本身失效" ✓ 正是它该干的事 ✓）。
    #   但这组守的**保证没变**：**本地失败时绝不静默改用云端** ✗ ——
    #   用户选本地就是为了数据不出本机 ✓（偷偷上传是最坏的惊喜 ✓）。
    #   ⇒ 回滚态改成"真的偷偷回退云端"（正是要防的那个形态 ✓）。
    '        tier = local_tier_of(cfg)\n'
    '        return await asyncio.to_thread(_local_transcribe_sync, audio_path, tier)',
    '        return await _cloud_mimo(audio_path, os.environ.get(_key_env(cfg), ""), '
    '_model(cfg))  # MUTANT: 偷偷改用云端',
    "tests/test_asr_local_qwen.py",
)

# ═══ Phase 2 ⑤ 第二刀：设置页「语音」栏的切换接线 ═══
# 回滚态 = 去掉"用这个"按钮对 api.setAsrTier 的调用（按钮变成摆设）。
experiment(
    "语音栏接线回退（切换按钮变摆设）", _SETTINGS_TSX,
    "      .setAsrTier(pid)", "      Promise.resolve({ note: '' })  // MUTANT: 不真的切换",
    "tests/test_asr_two_tier.py",
)

# ═══ Phase 3 ⑦：对话界面原型（?proto=chat）═══
# 回滚态 = 原型模式下照样拉业务数据（评审时就得先起后端，且"原型偷偷依赖接口"复活）。
_APP_TSX = _TMP_ROOT / "frontend" / "src" / "App.tsx"
experiment(
    "对话原型回退（原型模式下仍拉业务数据）", _APP_TSX,
    """    // 原型模式（?proto=chat）下不拉业务数据：原型要能**脱离后端**单独看
    //（设计评审时不必先起服务），也避免"原型偷偷依赖接口"。
    if (proto) return;
    refreshTasks();""",
    "    refreshTasks();  // MUTANT: 原型模式也拉",
    "tests/test_chat_proto.py",
)

# ═══ Phase 3 ⑦ 第二刀：真界面的工具调用卡片（图标 + 卡片相接）═══
# 回滚态 = 动作行去掉按工具名取的图标（退回"一行裸文字"的观感）。
_EVENT_ITEM = _TMP_ROOT / "frontend" / "src" / "components" / "EventItem.tsx"
_TASKVIEW = _TMP_ROOT / "frontend" / "src" / "components" / "TaskView.tsx"
# 设置页各批都要用（**定义必须在使用之前** —— 本班 pyflakes 抓到过一次"先用后定义"）
_SETTINGS_PANEL = _TMP_ROOT / "frontend" / "src" / "components" / "SettingsPanel.tsx"
experiment(
    "工具卡片回退（动作行不再带图标）", _EVENT_ITEM,
    "          <Icon size={12} className=\"tool-icon\" />",
    "          {/* MUTANT: 图标没了 */}",
    "tests/test_chat_proto.py",
)

# ═══ 去重第二刀：拼接型重复要被抓（语料必须拼接、且含助手自己的历史回复）═══
# 回滚态 = 退回"逐条比"（对拼接型回复每条覆盖率只有 ~50% ⇒ 漏判）。
experiment(
    "拼接型去重回退（退回逐条比）", _TASKVIEW,
    "if (isDuplicateOfEarlier(text, [buildCorpus(seen)])) dup.add(e.id);\n        seen.push(text);",
    "if (isDuplicateOfEarlier(text, seen)) dup.add(e.id);  // MUTANT: 逐条比（拼接型会漏）",
    "tests/test_chat_proto.py",
)

# ═══ 组长分工单解析批：模型写人话时也要尽量认出来（用户实测的真事故）═══
# 回滚 = 去掉「@行文」兜底（组长写「T1 需求 @产品经理 …」时一个人都派不出去）
experiment(
    "分工单兜底解析回退（行文里的 @ 认不出）", _TMP_ROOT / "backend" / "app" / "team.py",
    "        return TeamStore._plan_from_mentions(text)",
    "        return []  # MUTANT: 不做兜底",
    "tests/test_leader_plan_parse.py",
)

# ═══ 续跑状态批：续跑要把分工单那一项改回"进行中"（否则群里提前喊"收工"）═══
# 回滚 = 续跑不动分工单状态（全量试跑实测：任务在跑、群里却说"收工"）
experiment(
    "结构化交付回退（缺小节也不打回）", _TMP_ROOT / "backend" / "app" / "main.py",
    "    if missing_secs:",
    "    if False:  # MUTANT: 缺小节也放行",
    "tests/test_structured_delivery.py",
)

experiment(
    "项目验收门回退（不追加验收项）", _TMP_ROOT / "backend" / "app" / "main.py",
    "    if _ensure_acceptance(gid, st) is None:",
    "    if True:  # MUTANT: 不追加项目验收",
    "tests/test_acceptance_gate.py",
)

experiment(
    "续跑改状态回退（不动分工单，群里会提前收工）", _TMP_ROOT / "backend" / "app" / "main.py",
    "        _team_store.leader_mark_running(gid, item[\"name\"])",
    "        pass  # MUTANT: 不改分工单状态",
    "tests/test_resume.py",
)

# ═══ 缺陷回环批：下游发现上游产物的问题要打回给上游（第六轮真群暴露：测试报了缺陷没人修）═══
# 回滚 = 缺陷不回环（退回"把缺陷记在自己的报告里就完事"）
experiment(
    "缺陷回环回退（收到缺陷也不打回上游）", _TMP_ROOT / "backend" / "app" / "main.py",
    "        batch = _team_store.leader_reopen(gid, owner, d[\"what\"]) or batch",
    "        batch = batch  # MUTANT: 不回环",
    "tests/test_defect_loop.py",
)

# ═══ 验收调用救回批：模型把答案塞进工具参数时不许当成"空响应" ═══
# 回滚 = 不从工具调用里救答案（真群实测：验收连挂 5 轮的根因就是这个）
experiment(
    "验收救回回退（不从工具参数里取答案）", _TMP_ROOT / "backend" / "app" / "main.py",
    "    tc = getattr(turn, \"tool_call\", None)\n    if tc is None:\n        return \"\"",
    "    return \"\"  # MUTANT: 不救\n    tc = getattr(turn, \"tool_call\", None)",
    "tests/test_leader_verify.py",
)

# ═══ 群共享工作区批：同群的人必须能互相看到产物（真群回归暴露的头号坑）═══
# 回滚 = 不登记共享工作区（退回"每个任务一个私有目录"⇒ 按文件交接永远不成立）
# ★ 补丁打在**登记那一行**：群派发处还有第二处登记，只改一处会被兜住（本班踩过：
#   回滚跑=green 没判别力）。行为锚点 test_group_dispatch_really_shares_one_workspace
#   会同时验"派发收到的工作区"和"登记后 workspace_dir 指向它"，所以两处都断得掉。
experiment(
    "群共享工作区回退（不登记，退回任务私有目录）", _TMP_ROOT / "backend" / "app" / "main.py",
    "        store.set_task_workdir(task.id, workdir)",
    "        pass  # MUTANT: 不登记共享工作区",
    "tests/test_env_facts.py",
)

# ═══ 成本可见批（P0-5）：花了多少要看得见；没配单价就绝不编数字 ═══
# 回滚 = 没配单价时也报一个 ¥0.00（用户会以为免费 / 或以为这是账单）
experiment(
    "成本可见回退（没单价也报钱）", _TMP_ROOT / "backend" / "app" / "main.py",
    '        parts.append("（未填单价，只报 token）")',
    '        parts.append("约 ¥0.00")  # MUTANT: 没单价也报钱',
    "tests/test_cost_visible.py",
)

# ═══ 接着跑批（P0-4）：从出错处继续，不从头烧钱 ═══
# 回滚 = 工作单不带"已有产物"清单（退回"等于重新发一次目标"）
# ★ 补丁要打在**取文件列表**那一行：只删标题行的话，下面的清单照样列出来 ⇒ 回滚跑=green（本班踩过）
experiment(
    "接着跑回退（工作单不列已有产物）", _TMP_ROOT / "backend" / "app" / "main.py",
    "        files = sorted((p for p in ws.rglob(\"*\") if p.is_file()),",
    "        files = []  # MUTANT: 不列产物\n        _unused = sorted((p for p in ws.rglob(\"*\") if p.is_file()),",
    "tests/test_resume.py",
)

# ═══ 重复动作熔断批（P0-3）：同一动作反复重试要提醒/熔断（实证最高频失败 17.1%）═══
# 回滚 = 判定永远返回 ok（退回"一遍遍重试直到把步数烧完"）
experiment(
    "重复动作熔断回退（不提醒也不熔断）", LOOP,
    '    if count >= trip_at:\n        return "trip"',
    '    if False:\n        return "trip"  # MUTANT',
    "tests/test_repeat_guard.py",
)

# ═══ 验收环节批（P0-2）：交付后先验收，通过才推进 ═══
# 回滚 = 低层不查文件（"只在回复里说做了"也算合格）
experiment(
    "验收低层回退（不查产物文件是否真在）", _TMP_ROOT / "backend" / "app" / "main.py",
    "    if not attachments:\n        return (False, \"没有交付任何文件（若这一步本该产出文件，则不合格）\")",
    "    if not attachments:\n        return (True, \"（MUTANT: 不查）\")",
    "tests/test_leader_verify.py",
)

# ═══ 依赖波次批（P0-1）：有前置的活必须等前置交付 ═══
# 回滚 = ready 判定不看依赖（退回"一把全并行"⇒ 程序员在架构师交付前就开工）
experiment(
    "依赖波次回退（不看前置，一把全并行）", _TMP_ROOT / "backend" / "app" / "team.py",
    "                if all(d[\"status\"] == \"done\" for d in deps):",
    "                if True:  # MUTANT: 不看前置",
    "tests/test_leader_waves.py",
)

# ═══ 工具参数健壮性批：参数畸形不许弄死任务（用户实测 AttributeError 崩掉整个任务）═══
# 回滚 = 不递归解析 JSON 字符串形态的 steps（退回"逐字符 .get()"⇒ 当场崩）
experiment(
    "工具参数容错回退（steps 的 JSON 字符串不解析）", LOOP,
    "        return _coerce_steps(parsed)",
    "        return [text]  # MUTANT: 不解析嵌套",
    "tests/test_tool_arg_robustness.py",
)

# ═══ 群内审批批：审批就地能批（用户要求"他们的活、允许一次都该在群里"）═══
# 回滚 = 去掉播报去重（看门每几秒轮询一次 ⇒ 同一条审批刷屏）
experiment(
    "群内审批播报去重回退（同一审批反复播报）", _TMP_ROOT / "backend" / "app" / "main.py",
    "        if _team_store.has_notice(gid, task_id, f\"🔐{call_id}\") and (",
    "        if False and (  # MUTANT: 不去重",
    "tests/test_group_approval.py",
)

# ═══ 纪要接地批：收口"编内容"要拦得住（用户实测：纪要讲的是另一个话题）═══
# 回滚 = 不做接地校验（编的也照样当纪要发出去）
# ★ 注意别打"收口失败→兜底"那句：它被接地校验**二次兜住**，变异体会被掩盖（本班踩过）
experiment(
    "纪要接地校验回退（跑题的也照样发）", _TMP_ROOT / "backend" / "app" / "main.py",
    # ★ 2026-10-06：锚点跟着实现更新 ✓ —— 这句现在多了 `and moderator is not None`
    #   （跑题时先**复问一次**、再退兜底 ✓）。锚点不跟着改，回滚就"打不中" ⇒
    #   回滚跑成了 green ✗（红绿组当场报 `回滚跑=green` 把它抓出来了 ✓）。
    "    if _summary_grounding(g, summary) < SUMMARY_GROUNDING_MIN and moderator is not None:",
    "    if False and _summary_grounding(g, summary) < SUMMARY_GROUNDING_MIN and moderator is not None:  # MUTANT: 不校验",
    "tests/test_team_meeting.py",
)

# ═══ 开会模式批：每轮问主持人"够了没"（提前收口 = 省钱 + 不空转）═══
# 回滚 = 从不问主持人（一定跑满所有轮次）
experiment(
    "开会收口回退（从不问主持人）", _TMP_ROOT / "backend" / "app" / "main.py",
    "        if moderator is not None and r < rounds:",
    "        if False and moderator is not None and r < rounds:  # MUTANT: 不问主持人",
    "tests/test_team_meeting.py",
)

# ═══ 接力模式批：上一棒的交付必须进下一棒的工作单 ═══
# 回滚 = 交付后不推进（接力永远停在第一棒，"串行协作"就变成"只跑第一棒"）
experiment(
    "接力交接回退（交付后不推进下一棒）", _TMP_ROOT / "backend" / "app" / "team.py",
    "            nxt = int(expect_pos) + 1",
    "            nxt = int(expect_pos)  # MUTANT: 不推进",
    "tests/test_team_relay.py",
)

# ═══ 团队可试性批：成员一键选择（@ 自动补全之外，给一个不用记名字的入口）═══
# 回滚 = 面板恒不渲染（按钮点了没反应）
experiment(
    "团队成员选择回退（点了不出名单）", _TMP_ROOT / "frontend" / "src" / "components" / "TeamView.tsx",
    "                        {pickOpen && (",
    "                        {false && pickOpen && (",
    "tests/test_team_composer.py",
)

# ═══ 高风险说明批：只加说明、一个字节的权限都不动 ═══
# 回滚 = 把"默认要求审批的删除类命令"判定改成恒真（风险提示永远绿 ⇒ 该红）
experiment(
    "高风险说明回退（审批清单为空也不报警）", _SETTINGS_PANEL,
    "    if (!list.length) {",
    "    if (true) {  /* MUTANT: 空清单也不报警 */",
    "tests/test_risk_notes.py",
)

# ═══ 预览批：工作区图片必须带 token（否则 <img> 401 —— 元素在、字节没下来）═══
# 回滚 = 对话侧的取文件地址不再走 authedUrl
experiment(
    "预览批回退（取文件地址不带 token）", _EVENT_ITEM,
    "  return authedUrl(`${API_BASE}/tasks/${taskId}/files/raw?path=${encodeURIComponent(name)}`);",
    "  return `${API_BASE}/tasks/${taskId}/files/raw?path=${encodeURIComponent(name)}`;  /* MUTANT */",
    "tests/test_image_preview.py",
)

# ═══ Markdown 代码块批：语言标签 + 一键复制 ═══
# 回滚 = 不挂自定义外壳（复制按钮与语言标签一起消失）
experiment(
    "代码块外壳回退（复制按钮与语言标签消失）",
    _TMP_ROOT / "frontend" / "src" / "components" / "Markdown.tsx",
    "          pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,",
    "          /* MUTANT: 不挂外壳 */",
    "tests/test_markdown_code.py",
)

# ═══ 语音输入批（两个输入框共用一份实现 + Ctrl+空格 快捷键）═══
# 回滚一：首页退回"自己那份录音实现"（用户的"没修好"就是这么来的）
experiment(
    "语音输入回退（首页不用共用实现）", _HERO,
    "const voice = useVoiceDraft({",
    "const voice = ({ on: false, busy: false, hint: '', setHint: () => {}, toggle: () => {} }); void useVoiceDraft({",
    "tests/test_voice_input.py",
)
# 回滚二：stopDraft 里 WAV 句柄判晚了 ⇒ 走 WAV 时直接 return（点了没反应）
experiment(
    "语音停止回退（WAV 句柄判晚了 ⇒ 点了没反应）", _TASKVIEW,
    "    if (wavRef.current) {\n      const w = wavRef.current;",
    "    if (false && wavRef.current) {\n      const w = wavRef.current;",
    "tests/test_voice_input.py",
)

# ═══ 零碎批（时间戳 / 对话内搜索）：timeline 不 memo ⇒ 搜索的"下一个"被立刻重置 ═══
experiment(
    "搜索高亮回退（timeline 不 memo）", _TASKVIEW,
    "const timeline = useMemo(() => events.filter((e) => e.type !== 'message_delta'), [events]);",
    "const timeline = events.filter((e) => e.type !== 'message_delta');  /* MUTANT: 不 memo */",
    "tests/test_find_and_time.py",
)

# ═══ 设置导出/导入批：导入必须**先预演**（别一键盖掉整份设置）═══
experiment(
    "设置导入回退（跳过预演直接应用）", _SETTINGS_PANEL,
    "const dry = await api.importSettings(sections, true);",
    "const dry = await api.importSettings(sections, false);  /* MUTANT: 不预演 */",
    "tests/test_settings_portability.py",
)

# ═══ A-4 批：查看密码（仅本机）—— 入口必须在，且不许把"仅本机"这层承诺抹掉 ═══
# ★ 补丁要打在**按钮文案**那一行：第一版打在裸字符串上，命中的是上面注释里的同名字样
#   （`// ★ A-4：查看密码（仅本机）…`）⇒ 回滚跑=green、组没有判别力（完整门抓出来的）。
experiment(
    "查看密码入口回退（按钮不再声明仅本机）", _SETTINGS_PANEL,
    "{tokenBusy ? '读取中…' : '查看密码（仅本机）'}",
    "{tokenBusy ? '读取中…' : '查看密码'}  /* MUTANT: 抹掉「仅本机」承诺 */",
    "tests/test_access_token_local_only.py",
)

# ═══ 多轮"改一句重发"批：运行中不给按钮（跑了改会被后端 409）═══
# 回滚态 = 不看运行状态，永远给按钮（用户点了必然失败一次）
experiment(
    "改一句重发回退（运行中也给按钮）", _TASKVIEW,
    "onEditResend={running ? undefined : editResend}",
    "onEditResend={editResend}  /* MUTANT: 不看运行状态 */",
    "tests/test_edit_resend.py",
)

# ═══ 设置页正规化批：节头（说明 + 状态 + 恢复默认）═══
# 回滚态 = 不渲染节头（设置页又变成"一堆控件堆着，不知道这节干啥、现在什么状态"）
_SETTINGS_PANEL = _TMP_ROOT / "frontend" / "src" / "components" / "SettingsPanel.tsx"
experiment(
    "设置页节头回退（不渲染说明与状态）", _SETTINGS_PANEL,
    "        <SectionHead\n",
    "        {false && <SectionHead\n",
    "tests/test_settings_reset.py",
)

# ═══ 内联工具调用批：模型把工具调用写成文本时要救回来 ═══
# 回滚态 = 流式路径不做救回（那一轮白跑：参数不全 ⇒ 校验失败）
_OPENAI_COMPAT = _TMP_ROOT / "backend" / "app" / "providers" / "openai_compat.py"
experiment(
    "内联工具调用回退（流式路径不救回 ⇒ 白跑一轮）", _OPENAI_COMPAT,
    "                return recover_inline_tool_call(turn)",
    "                return turn  # MUTANT: 不救回",
    "tests/test_inline_tool_calls.py",
)

# ═══ 应用层去重批：重复回复要收起来（用户点名要改的那条）═══
# 回滚态 = 不传重复标记（重复的长篇回复又原样铺满屏幕）。
experiment(
    "应用层去重回退（重复回复不再收起）", _TASKVIEW,
    "duplicate={dupIds.has(e.id)}",
    "duplicate={false}  /* MUTANT: 不去重 */",
    "tests/test_chat_proto.py",
)

# ═══ 折叠小结批：小结必须挂在对话流最底下 ═══
# 回滚态 = 不挂小结（用户点名要的功能消失：想看全过程只能往上翻）。
experiment(
    "折叠小结回退（不挂「这一趟做了什么」）", _TASKVIEW,
    "          <TaskDigest events={events} running={running} />",
    "          {/* MUTANT: 小结没了 */}",
    "tests/test_chat_proto.py",
)

# ═══ 验收闭环批（2026-10-06 晚）═══
#
# 这三组钉的是"今天大半失败的单一根因"以及它的两个伴生问题 ✓：
#   · 验收人**从来拿不到交付正文**（提示词递的是"验收前还没写 reply"的计划项 ✗）
#   · 交付正文被**三把刀**各砍一段（800 / 800 / 500 ✗），真实输出永远落在刀口之前
#   · 验收步数一度掐到 12 步 ⇒ 验收人在写结论前被掐断 ✗
# 每组都是"回到修复前的形态" ✓ 锚点必须变红 ✓ 不变红就说明测试没钉住 ✓。

_APP_MAIN = _TMP_ROOT / "backend" / "app" / "main.py"
_APP_TEAM = _TMP_ROOT / "backend" / "app" / "team.py"
_APP_CAP = _TMP_ROOT / "backend" / "app" / "capabilities.py"

experiment(
    "验收人拿不到交付正文回退（递裸 item）", _APP_MAIN,
    '            judged = dict(item, reply=reply or item.get("reply") or "")',
    "            judged = item  # MUTANT: 递验收前还没写 reply 的计划项",
    "tests/test_leader_verify.py",
)

experiment(
    "交付正文被砍到 800 回退", _APP_TEAM,
    'it["reply"] = str(reply or "")[:DELIVERY_TEXT_MAX]',
    'it["reply"] = str(reply or "")[:800]  # MUTANT: 真实输出又被砍掉',
    "tests/test_leader_verify.py",
)

experiment(
    "验收步数掐回 12 回退", _APP_MAIN,
    '    if "项目验收" in hay or ("验收" in hay and "确认性" in hay):\n        return 20',
    '    if "项目验收" in hay or ("验收" in hay and "确认性" in hay):\n'
    "        return 12  # MUTANT: 验收人在写结论前被掐断",
    "tests/test_acceptance_gate.py",
)

experiment(
    "经验注入回退（栽过的坑不带上）", _APP_TEAM,
    "        if not tip:\n            return text\n        return text + chr(10) + tip",
    "        return text  # MUTANT: 教训不注入",
    "tests/test_lessons.py",
)

# ★ 群里点产物弹「需要访问密码」那次（同一个坑犯过两回 ✓ 任务页一次、群聊一次 ✓）
#   回滚态 = 产物链接不带 token（就是用户看到 401 的那个形态 ✓）
_TEAMVIEW = _TMP_ROOT / "frontend" / "src" / "components" / "TeamView.tsx"
experiment(
    "群聊产物不带 token 回退（点了 401）", _TEAMVIEW,
    "href={authedUrl(`${API_BASE}/tasks/${m.task_id}/files/raw?path=${encodeURIComponent(a)}`)}",
    "href={`${API_BASE}/tasks/${m.task_id}/files/raw?path=${encodeURIComponent(a)}`}  /* MUTANT */",
    "tests/test_group_approval.py",
)

# ★「这活到哪一步了」那块进度面板（2026-10-06 用户提的 ✓）
#   回滚态 = 没数据也照样渲染（就是"留个空框占地方" ✗ 那种形态 ✓）
experiment(
    "进度面板空则隐藏回退", _TMP_ROOT / "frontend" / "src" / "components" / "GroupProgress.tsx",
    "  if (!plan.length) return null;",
    "  // MUTANT: 没分工单也渲染（留个空框 ✗）",
    "tests/test_group_progress_panel.py",
)

# ★ Webhook 被全局密码挡死那次（用户问"这个能触发吗" ⇒ 真去试 ⇒ 根本触发不了 ✗）
#   回滚态 = 不放行 hooks 路径（外部系统一律 401 ✓ 就是用户撞到的那个形态 ✓）
experiment(
    "Webhook 被全局密码挡死回退（外部触发不了）", _APP_MAIN,
    '                       and not path_lan.startswith("/api/v1/hooks/"))',
    "                       )  # MUTANT: 又挡上了",
    "tests/test_webhook_security.py",
)

# ★ 语音合成"口径打架"那次（同一个后端，代码里四处各说各的 ✗ 用户当然一头雾水 ✓）
#   回滚态 = 能力总览又硬编码 melotts（与实际用的 edge 不一致 ✓）
experiment(
    "语音合成口径打架回退（总览写死 melotts）", _APP_MAIN,
    '"tts": {"backend": str(getattr(cfg.tts, "backend", "") or "edge")},',
    '"tts": {"backend": "melotts"},  # MUTANT: 又写死一个',
    "tests/test_tts_backends.py",
)

# ═══ ★★ 2026-10-08：用户当场问出来的两件（"本地朗读不是显示安装了吗"）═══
#
# ★ **退回必须说出来** ✗ —— 回滚态 = 又变成**静默**换引擎 ✓（正是修复前的形态 ✓）
#   实测：点 melotts（没装）与点 pyttsx3，两个 wav **字节数一模一样（104796）** ✓
#   而界面上一个字都没有 ⇒ 用户以为"本地朗读装好了" ✓ —— 这就是那个错觉的来源 ✓
experiment(
    "朗读静默退回回退（你点 A 它念 B 不说）", _APP_MAIN,
    '            if used != asked:\n'
    '                line.update({"used": used, "asked": asked, "why": why})',
    '            # MUTANT: 静默退回 ✗（用户点名 A、耳朵听到 B，回执里一个字都没有 ✓）',
    "tests/test_tts_backends.py",
)

# ★ **别再给假指路** ✗ —— 回滚态 = 安装提示又退回那句 `pip install melotts` ✓
#   （实测：本机 Python 3.13 上它**装不上** —— 源码包缺 requirements.txt + 写死 torch<2.0 ✓
#    而本仓自己那个带哈希校验的安装器才是正路 ✓）
experiment(
    "MeloTTS 提示退回假指路（pip install 那条走不通）", _TMP_ROOT / "backend" / "app" / "tts.py",
    '        "install": ("**别用 `pip install melotts`** ✗ —— 本机（Python 3.13）装不上："\n'
    '                    "PyPI 上它只有源码包且**包里缺 requirements.txt**；它还写死 `torch<2.0`，"\n'
    '                    "而 1.x **没有 3.13 的轮子**。正路是跑本仓自带的安装器："\n'
    '                    "`python scripts\\\\install_melotts.py`（钉死 commit + 哈希校验，"\n'
    '                    "装 GitHub tarball + 最新 torchaudio，不碰那个旧 pin）"),',
    '        "install": ("`pip install melotts`（首次用时自动下模型）"),  # MUTANT: 假指路 ✗',
    "tests/test_tts_backends.py",
)

# ═══ ★★ 2026-10-08：本地朗读「一键装」（用户："就像上面这个千问3似的，点一下它就安装"）═══
#
# ★ **界面那个按钮不许被拿掉** ✗ —— 回滚态 = 按钮没了（只剩"用这个" ✓ 点了还是发不出声 ✗）
#   那正是修复前的形态：本仓早有安装器（scripts/install_melotts.py ✓）却没接到界面上 ✓
experiment(
    "一键装按钮被拿掉回退（有安装器却没入口）", _SETTINGS_TSX,
    "                            {!ready && (pid === 'melotts' || pid === 'qwen3tts') ? (",
    "                            {false ? (  // MUTANT: 一键装按钮没了 ✗",
    "tests/test_tts_install.py",
)

# ★ **下模型不许用 `python -m modelscope`** ✗ —— 回滚态 = 又用回命令行那种写法 ✓
#   （新版 modelscope 没这个入口 ✓ 实测报 `No module named modelscope.__main__` ⇒ 必失败 ✓）
experiment(
    "千问3下模型回退成 -m modelscope（新版没这入口必失败）", _TMP_ROOT / "backend" / "app" / "local_install.py",
    '                code = _run([sys.executable, "-c", _QWEN3_DL_SNIPPET, str(dest)], watch=dest)',
    '                code = _run([sys.executable, "-m", "modelscope", "download", "--model",\n'
    '                             "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice", "--local_dir", str(dest)], watch=dest)  '
    '# MUTANT ✗',
    "tests/test_tts_install.py",
)

# ★★ 2026-10-08：**探针必须便宜** ✗ —— 回滚态 = MeloTTS 的分档没了（`deep` 参数被拿掉 ✓）
#   实测那个 bug：`available()` 每次 `from melo.api import TTS` ⇒ 一次卡 **161 秒** ✗
#     （设置页卡死 ✓ 全量门"像卡死"✓ 就是它 ✓ 逐探针计时抓出来的 ✓）
#   ★ 为什么不直接变异成"永远深探"✗ —— 那样**红是红了，但要跑 15 分钟** ✓
#     红绿组每组都要跑两遍（回滚 + 恢复）⇒ 进门就是 +15 分钟 ✗ 不值当 ✓
#     ⇒ 用**结构变异**（分档消失 ✓）秒级变红 ✓；而"它到底贵不贵"由
#       `tests/test_probe_is_cheap.py` 的**计时断言**常年钉着 ✓（平时 1 秒 ✓ 谁改回深探它自己会红 ✓ 只是慢 ✓）
experiment(
    "能力探针的分档被拿掉回退（又变成一次 161 秒）", _TMP_ROOT / "backend" / "app" / "tts.py",
    "    def available(deep: bool = False) -> bool:\n"
    "        \"\"\"★★ 2026-10-08：**默认走廉价探针** ✓",
    "    def available() -> bool:  # MUTANT: 分档没了 ✗\n"
    "        \"\"\"★★ 2026-10-08：**默认走廉价探针** ✓",
    "tests/test_probe_is_cheap.py",
)

# ★★ 2026-10-08（用户截图报的 ✗）：**浅色下右下角那两个浮动件**必须点名补 ✓
#   （它们是写死的深色底 `rgba(24,28,36,…)` / `rgba(30,34,42,…)` ✗ ⇒ 不跟变量走 ✓
#     回滚态 = 浅色层里那两条没了 ⇒ 又变成灰底浅字 ✓）
experiment(
    "浅色主题漏掉右下角浮动件回退（灰底浅字）", _TMP_ROOT / "frontend" / "src" / "styles.css",
    '[data-theme="light"] .usage-badge {\n  background: #ffffff;',
    '[data-theme="light"] .usage-badge-NOPE {\n  background: #ffffff;  /* MUTANT ✗ */',
    "tests/test_theme.py",
)

# ★★ 2026-10-08：**朗读**那两件（音色 + 长文本策略）——
#   ① 音色乱传必须拒 ✗（不校验就会**静默退回默认音色** ✓ 用户以为切好了 ✓ 那是骗人 ✓）
#   ② 长文本必须换快的引擎 ✓（千问3 一句 5~12 秒 ✗ 比播放还慢 ⇒ 一句一顿 ✓）
experiment(
    "音色不校验回退（乱传也静默用默认）", _TMP_ROOT / "backend" / "app" / "main.py",
    '        if not voices:\n'
    '            raise HTTPException(422, f"「{req.backend}」不支持选音色（只有千问3-TTS 有 9 个音色）")',
    '        if not voices:\n'
    '            voices = [req.voice]  # MUTANT：不校验了 ✗（啥名字都收 ✓）',
    "tests/test_tts_voice_and_policy.py",
)
experiment(
    "长文本不换引擎回退（长文朗读一句一顿）", _TMP_ROOT / "backend" / "app" / "main.py",
    "    if _fast and _fast != primary and primary in _slow and len(req.text.strip()) >= _thr:",
    "    if False and _fast and _fast != primary and primary in _slow and len(req.text.strip()) >= _thr:"
    "  # MUTANT：策略没了 ✗",
    "tests/test_tts_voice_and_policy.py",
)

# ★★ 2026-10-09（AGPL 合规批）：**公开版的授权闸必须是关的** ✗
#   用户一句"你得按照 agpl3.0 那个模式走，是不是"逼出来的复查 ✓ 查出真不合规 ✓：
#     · 试用 30 天后锁"创建新任务" ✗ + 文案"未经授权不得用于商业用途" ✗
#     · AGPL 第 10 条：**不得附加任何进一步限制** ✗（既限使用 ✓ 又限商用 ✓ 双重违规 ✓）
#   回滚态 = 默认值又变成 True ✓（谁 clone 下来都带着锁 ⇒ 分发一个违反 AGPL 的版本 ✓）
experiment(
    "授权闸默认又打开回退（AGPL 版带 30 天锁）", _TMP_ROOT / "backend" / "app" / "config.py",
    "    enforce: bool = False",
    "    enforce: bool = True  # MUTANT: 又默认开闸 ✗",
    "tests/test_agpl_compliance.py",
)
#   ② 界面里的**源码入口**不许被拿掉 ✗ —— AGPL 第 13 条的兑现方式之一 ✓
#      （另一处是 README ✓ 两处都要在 ✓ 因为"用网络访问的人"看不到仓库文件 ✓）
experiment(
    "关于页拿掉源码入口回退（违反第 13 条）", _SETTINGS_TSX,
    "                      <br />源码：<b>{lic.source_url}</b>",
    "                      <br />{/* MUTANT: 源码入口被拿掉了 ✗ */}",
    "tests/test_agpl_compliance.py",
)

# ★★ 2026-10-08 晚：口径又变了一次 ⇒ 这两组跟着换成 **AGPL** 的锚点 ✓
#   （用户拍板："我想做这个 agpl 开源" ✓ "你就按这个 agpl3.0 这个方式这么改就行" ✓）
#   ★ 原来那两组钉的是 v2 自家协议的句子 ✗ —— 那些句子随着换协议已经不存在了 ✓
#     留着它们只会报"锚点文本未找到"✗（红源可疑 ✓）⇒ 换掉 ✓
#
#   ① **LICENSE 必须是 AGPL-3.0 官方全文** ✗ 不许是"参考 AGPL"的自拟文本 ✓
experiment(
    "LICENSE 换成自拟协议回退（假 AGPL）", _TMP_ROOT / "LICENSE",
    "                    GNU AFFERO GENERAL PUBLIC LICENSE\n                       Version 3, 19 November 2007",
    "              LanternLogic Agent 源码许可（参照 AGPL 精神自行拟定）  # MUTANT ✗",
    "tests/test_license_terms.py",
)
#   ② **第 13 条的兑现方式（源码链接位）不许被删** ✗ —— 删了就等于自己没遵守 AGPL ✓
#   ★ 2026-10-09 更新锚点：README 顶部改成了"两个镜像并列"（GitHub + Gitee ✓ 用户要求互相加 ✓）
#     ⇒ 老锚点（单行 `> ★ 源码仓库：**https://...**`）不存在了 ⇒ harness 报"锚点文本未找到" ✓
#     ★ 那是**护栏在正常干活** ✓ —— 它拒绝静默通过 ✓ 逼人来更新锚点 ✓（本次就是它逼的 ✓）
experiment(
    "README 删掉源码链接位回退（违反 AGPL 第 13 条）", _TMP_ROOT / "README.md",
    "> ★ 源码仓库（AGPL 第 13 条要求：用网络访问本程序的人能拿到源码 ✓）：",
    "> ★ （MUTANT：源码链接被删了 ✗ 网络用户拿不到源码 ✓）",
    "tests/test_license_terms.py",
)

# ★★ 2026-10-08：作者卡三组 —— "只读 + 防伪签名"这事的**要害**都在这儿 ✓
#   ① 验签不许写死通过 ✗（写死 ⇒ "正版"两个字就是装饰 ✓）
#   ② 联系方式不许默认摆在页面上 ✗（用户要的是"点一下才显示" ✓）
#   ③ 卡里的工作室名不许跟 version.VENDOR 打架 ✗（一处声明处处读 ✓）
experiment(
    "作者卡验签写死通过回退（篡改也报正版）", _TMP_ROOT / "backend" / "app" / "author.py",
    '    except InvalidSignature:\n        return False, "签名对不上（内容被改过，或不是原版）"',
    '    except InvalidSignature:\n        return True, ""  # MUTANT: 篡改也报正版 ✗',
    "tests/test_author_card.py",
)
experiment(
    "作者卡联系方式默认直接显示回退（点一下才显示没了）", _SETTINGS_TSX,
    "                  {authOpen ? (",
    "                  {true ? (  // MUTANT: 联系方式默认就摆出来了 ✗",
    "tests/test_author_card.py",
)
experiment(
    "作者卡工作室名与 version 打架回退（两处各说各的）", _TMP_ROOT / "backend" / "app" / "author_card.json",
    '  "org": "丹东振兴云杉互联网服务工作室",',
    '  "org": "丹东云杉网络工作室",  // MUTANT: 旧名字 ✗',
    "tests/test_author_card.py",
)

# ★★ **Kokoro 不许回到列表里** ✗ —— 回滚态 = 又把它注册回去 ✓
#   （2026-10-08 加、当天就下架 ✓：用户听完原话"这根本不行啊" ✓
#    我把它 20 多个中文音色逐个量了一遍 —— **全**把「本地」念成「喷嚏」✗ 换音色救不了 ✓）
#   ★ 这不是洁癖：一个"念不准中文"的档摆在中文朗读的列表里就是坑人 ✓
experiment(
    "Kokoro 被加回朗读列表回退（念不准中文还摆着）", _TMP_ROOT / "backend" / "app" / "tts.py",
    '    "qwen3tts": Qwen3TTSBackend,    # ★★ 用户点名那档：**中文词全对** ✓ 2.4GB、Apache-2.0 ✓',
    '    "kokoro": MeloTTSBackend,  # MUTANT: 拿别的类冒充一下，只为触到"注册表里不许有 kokoro" ✓\n'
    '    "qwen3tts": Qwen3TTSBackend,    # ★★ 用户点名那档：**中文词全对** ✓ 2.4GB、Apache-2.0 ✓',
    "tests/test_tts_install.py",
)

# ★ 本地 ASR 那个"装了也没用 + 界面还骗人"的坑（用户实测问出来的 ✗✗）
#   回滚态 = 探针又去探 faster_whisper（装了它界面显示"可用"✓ 而实现根本不认它 ✗）
experiment(
    "本地ASR探错包回退（探 Whisper 却用千问）", _APP_CAP,
    '    if importlib.util.find_spec("qwen_asr") is None:',
    '    if importlib.util.find_spec("faster_whisper") is None:  # MUTANT: 探错包',
    "tests/test_asr_local_qwen.py",
)

# ★ 本地 ASR「一键装」那套（2026-10-07 用户要的："点了就能用" ✓）
#   回滚态 = 用 shell=True 跑命令（那就成了"任意命令执行口子" ✗ —— 正是要防的形态 ✓）
experiment(
    "一键装回退（改用 shell=True 跑命令）", _TMP_ROOT / "backend" / "app" / "local_install.py",
    "            cmd, cwd=str(cwd) if cwd else None, env=env,",
    '            " ".join(cmd), shell=True, env=env,  # MUTANT: 走 shell 了 ✗',
    "tests/test_asr_oneclick_install.py",
)

# ★ 体检⑤ 真跑抓到的**静默数据丢失**（2026-10-07 ✗✗）：
#   `GET /settings` 不回传 max_iterations ⇒ 界面表单回填成默认 25 ⇒ 一保存就把用户设的 60 重置掉 ✗
#   （真接口复现过 ✓ 评审区 `_repro_limits_wipe.py`）
#   回滚态 = 又不回传它（正是修复前的形态 ✓）
experiment(
    "设置存了读不回（一保存就重置步数上限）", _APP_MAIN,
    '            "max_iterations": int(getattr(m, "max_iterations", 0) or 0),',
    "            # MUTANT: 不回传步数上限了（界面回填成默认 25 ✗）",
    "tests/test_settings_readback.py",
)

# ★ 提示注入防御：**技能正文**必须跟网页内容一样被降到"数据"（2026-10-07 ✗→✓）
#   技能是这个项目自己认定的"语义控制面/供应链攻击面" ✗
#   回滚态 = 又把它从不可信名单里拿掉（正是修复前的形态 ✓ 谁放个 SKILL.md 就能下指令 ✗）
experiment(
    "技能正文没被当外部内容（放个SKILL.md就能指挥Agent）", _TMP_ROOT / "backend" / "app" / "loop.py",
    '    "load_skill",\n',
    "    # MUTANT: 技能正文不再降级成数据 ✗\n",
    "tests/test_untrusted_content.py",
)

# ═══ 2026-10-07 用户报的两个真 bug（下一轮第一件事那两个 ✓）═══
#
# ★ 朗读不说话（用户："以前好用，现在就不好用了" ✗）—— 本班真点按钮 + 真抓包量出来的 ✓：
#     `POST /tts` → 200 ✓ 而浏览器 `<audio>` 去取那段 mp3 → **401** ✗（地址里没带访问密码 ✓）
#     ⇒ 局域网（手机直连）模式下所有 /api/* 都要密码 ✓ 而 `<audio>` 发不了自定义头 ✗
#   回滚态 = 把后端给的地址**原样**推给播放器（正是修复前的形态 ✓ 一点声音都没有 ✓）
experiment(
    "朗读地址不带密码回退（音频 401、一点声音都没有）", _TMP_ROOT / "frontend" / "src" / "api.ts",
    "if (j.url) onUrl(authedUrl(j.url));",
    "if (j.url) onUrl(j.url);  // MUTANT: 不带密码 ⇒ 浏览器取音频 401 ✗",
    "tests/test_tts_playback_token.py",
)

# ★ 组件里又有裸 fetch('/api/...')（"每留一处就多一个会忘带密码的地方" ✓ 朗读栽的就是这个 ✓）
#   回滚态 = 记忆库那个开关绕开统一入口、自己 fetch（正是修复前的形态 ✓）
experiment(
    "组件绕过 api 层自己 fetch（又一处会忘带密码）", _TMP_ROOT / "frontend" / "src" / "components" / "SettingsPanel.tsx",
    "await api.setMemory({ enabled: !memory?.enabled });",
    "await fetch('/api/v1/memory', { method: 'POST' });  // MUTANT: 绕过统一入口 ✗",
    "tests/test_tts_playback_token.py",
)

# ★ 会议录不进去（用户："读了好几遍也不生成" ✗）—— 本班假麦克风真录一段量出来的 ✓：
#     前端录的是 **webm** ✗ 后端要 WAV/MP3 而转码要 **ffmpeg**（干净机器没有 ✗）
#     ⇒ `LibsndfileError: Format not recognised`（0.0 秒就返回 ✓）
#     而"语音输入（草稿）"那条路**早就修过同一个坑**了 ✓ —— 唯独会议没跟上 ✗
#   回滚态 = 会议那一段不再走浏览器侧 WAV 录音（正是修复前的形态 ✓）
experiment(
    "会议又录 webm 回退（本机没 ffmpeg ⇒ 转写必然失败）", _TMP_ROOT / "frontend" / "src" / "components" / "TaskView.tsx",
    "const w = await startWavRecording({ stream, gain: 1 });",
    "const w = null as never;  // MUTANT: 不用 WAV 录音（录 webm ⇒ 后端读不了 ✗）",
    "tests/test_meeting_wav_upload.py",
)

# ★ 会议转写失败**界面一个字都不说**（用户看到的就是"录了、然后什么都没有" ✗）
#   根因：后端转写失败**也回 201** ✓ 只是 body 里 `ok:false` ✓ 而前端把返回值**直接丢掉** ✗
#   回滚态 = 又把返回值丢掉（正是修复前的形态 ✓ 静默失败 ✓ 最难查的那种 ✓）
experiment(
    "会议段落失败不显示回退（录完一片空白）", _TMP_ROOT / "frontend" / "src" / "components" / "TaskView.tsx",
    "const r = await api.meetingChunk(taskId, blob);",
    "const r = { ok: true, text: '' };\n      await api.meetingChunk(taskId, blob);  // MUTANT: 丢掉结果 ✗",
    "tests/test_meeting_wav_upload.py",
)

# ★ 后端把"注定读不了的文件"继续往下传（webm 一路传到 ASR ⇒ 报一句没人看得懂的话 ✗）
#   回滚态 = 转不了码就 `return audio_path`（正是修复前的形态 ✓ 真正的原因一个字都不露 ✓）
experiment(
    "转码失败静默退回原文件回退（只说 Format not recognised）", _APP_MAIN,
    '    raise RuntimeError(\n        f"这段音频是 {sniff_audio_format(audio_path)} 格式，而本机没有 ffmpeg 可供转码，"',
    '    return audio_path  # MUTANT: 读不了也往下传 ✗\n'
    '    raise RuntimeError(\n        f"这段音频是 {sniff_audio_format(audio_path)} 格式，而本机没有 ffmpeg 可供转码，"',
    "tests/test_meeting_wav_upload.py",
)

# ═══ 2026-10-07「每个 Key 花费上限」（用户点名要的：只有显示 ✗ 没有上限 ✗）═══
#
# ★ 闸门**必须接在发请求之前** ✓ —— 接在后面 = 钱已经花出去了 ✗（等于没闸 ✓）
#   回滚态 = 闸门失效（超了上限也照发请求 ✓）
experiment(
    "花费上限闸门失效回退（超了还照花钱）", _TMP_ROOT / "backend" / "app" / "loop.py",
    "            if self.budget_check is not None:",
    "            if False and self.budget_check is not None:  # MUTANT: 闸门失效 ✗",
    "tests/test_budget_cap.py",
)

# ★ 用量事件必须记下**是哪把 Key 花的钱** ✓ —— 不记就分不清该记谁的账 ✗
#   （`budget.py` 就是按这个字段分组的 ✓）
#   回滚态 = 不记 key_env（正是修复前的形态 ✓）
experiment(
    "用量事件不记哪把 Key 花的回退（账分不清）", _TMP_ROOT / "backend" / "app" / "loop.py",
    '"key_env": str(getattr(self.provider, "api_key_env", "") or ""),',
    '"key_env": "",  # MUTANT: 不记哪把 Key ✗',
    "tests/test_budget_cap.py",
)

# ★ `GET /settings` 不回传花费上限 ⇒ 界面回填成空 ⇒ **一保存就把上限静默抹掉** ✗✗
#   ——与体检⑤ 抓到的 max_iterations / pricing 是**同一个形状的坑** ✓
#   回滚态 = 又不回传它（正是修复前的形态 ✓）
experiment(
    "设置存了读不回·花费上限（一保存就抹掉上限）", _APP_MAIN,
    '        "budget": {\n            "enabled"',
    '        "budget_gone": {\n            "enabled"',
    "tests/test_budget_cap.py",
)

# ═══ 2026-10-07「按角色限权」（用户点名要的：测试只能看/跑 ✗ 不能改 ✓；
#     现在审批**只按命令判** ✗ 不按"谁"判 ✗）═══
#
# ★ 只读角色（测试/安全/审校）**必须真拦得住"改"** ✓ —— 这是"独立验证"成立的前提 ✓
#   （测试亲手改了被测代码，然后说"我验过了" ⇒ 这个"验过"就没有意义了 ✓）
#   回滚态 = 那段角色检查等于不存在（正是修复前的形态 ✓）
experiment(
    "角色限权失效回退（测试也能改被测代码）", _TMP_ROOT / "backend" / "app" / "loop.py",
    "        if self._role_readonly:",
    "        if False and self._role_readonly:  # MUTANT: 角色检查失效 ✗",
    "tests/test_role_permissions.py",
)

# ★ **默认必须等于现状** ✓✓ —— 认不出的角色/没设身份 ⇒ 一律不受限 ✓
#   （这条是整个功能"不可能弄坏现有行为"的保证 ✓ 128 组红绿一个都不该动 ✓）
#   回滚态 = 未知角色也算只读 ⇒ **普通单聊突然不能写文件** ✗✗
experiment(
    "未知角色也被限住回退（普通单聊突然写不了文件）", _TMP_ROOT / "backend" / "app" / "permissions.py",
    '    return ROLE_PROFILE.get(str(role or "").strip(), FULL)',
    "    return READONLY  # MUTANT: 未知角色也限住 ⇒ 把普通单聊弄坏 ✗",
    "tests/test_role_permissions.py",
)

# ★「本任务全部允许」是**对某条命令的信任** ✓ 角色管的是**谁** ✗ —— 前者盖不过后者 ✓
#   回滚态 = 放行记录直接盖过角色限制（测试点一下"全部允许"就能改代码 ✗ = 限权当场失效 ✓）
experiment(
    "全部允许盖过角色限制回退（一个按钮废掉限权）", _TMP_ROOT / "backend" / "app" / "permissions.py",
    "    if not is_readonly(role) or verdict is None:\n        return None",
    "    if not is_readonly(role) or verdict is None:\n        return None\n"
    '    if getattr(verdict, "action", "") in ("allow_all", "allow_forever"):\n'
    "        return None  # MUTANT: 放行记录盖过角色 ✗",
    "tests/test_role_permissions.py",
)

# ═══ 2026-10-07「一键清干净」（用户点名要的：⚠️破坏性 ⇒ 必须二次确认 ✓）═══
#
# ★ 清完**必须把目录骨架建回来** ✗ —— 否则 `_save_index()` 写 `tasks/index.json` 时
#   目录已经不存在 ⇒ FileNotFoundError ⇒ **清完当场 500** ✓
#   （本班在隔离目录里真跑那条链时抓到的 ✓ 光看代码看不出来 ✓）
#   回滚态 = 不清完补目录（正是第一版的形态 ✓）
experiment(
    "清空后没补目录骨架回退（清完当场 500）", _APP_MAIN,
    '    for _d in ("tasks", "memory", "kb", "team", "groups", "approvals", "tts"):\n'
    "        (_DATA_DIR / _d).mkdir(parents=True, exist_ok=True)",
    "    pass  # MUTANT: 不补目录 ⇒ 下一次写索引就炸 ✗",
    "tests/test_data_clear.py",
)

# ★「一键清干净」**不是真删，是先备份** ✓ —— 用户那套规矩里"每步可回滚"✓
#   回滚态 = 直接删掉（原件没了 ⇒ 想找回也没了 ✗）
experiment(
    "清空改成真删回退（原件找不回来）", _APP_MAIN,
    "                src.rename(dst)          # ★ 同一个盘上**瞬时**完成 ✓ 而且可回滚 ✓",
    "                import shutil as _sh2; _sh2.rmtree(src, ignore_errors=True) if src.is_dir() else src.unlink()  # MUTANT: 真删 ✗",
    "tests/test_data_clear.py",
)

# ★ **二次确认**：确认词没打对 ⇒ 一个字节都不许动 ✗
#   回滚态 = 不校验确认词（点一下按钮就清 ⇒ 正是"手滑就没了"✗）
experiment(
    "清空不校验确认词回退（手滑就没了）", _APP_MAIN,
    '    if str(req.confirm or "").strip() != _CLEAR_PHRASE:',
    "    if False:  # MUTANT: 不校验确认词 ✗",
    "tests/test_data_clear.py",
)

# ═══ 2026-10-07 第 1 项「统计口径三条」查出来并修掉的两个真毛病 ═══
#
# ★ 毛病 A：同一个"花了多少"**有两本账** ✗
#   「使用统计」页把该任务所有用量记录**加起来** ✓ 而群里那行**只取最后一条** ✗
#   实测（真数据）：求和 4,676,537 vs 取末条 3,031,020 ⇒ **少报 35%** ✗
#   回滚态 = 又只取最后一条（正是修复前的形态 ✓）
experiment(
    "群里账单只取最后一条回退（比使用统计少报）", _APP_MAIN,
    "        total[\"calls\"] += int(u.get(\"calls\") or 0)",
    "        total[\"calls\"] += int(u.get(\"calls\") or 0)\n"
    "        if total[\"calls\"] > 0 and e is not evs[-1]:\n"
    "            continue  # MUTANT: 只算最后一条 ✗",
    "tests/test_usage_accounting.py",
)

# ★ 毛病 B：被**强杀**的任务**账会丢** ✗
#   用量原来只在跑完那一刻记一笔 ⇒ 进程被强杀（重启脚本就是强杀 ✓）就永远发不出来 ✗
#   实测 12 个任务这么死的、跑了 175 次动作却没留下账 ✗
#   回滚态 = 不边跑边记（正是修复前的形态 ✓）
experiment(
    "边跑边记用量关掉回退（被强杀就丢账）", _TMP_ROOT / "backend" / "app" / "loop.py",
    "            self._flush_usage_inflight()",
    "            pass  # MUTANT: 不边跑边记 ⇒ 被掐死就丢账 ✗",
    "tests/test_usage_accounting.py",
)

# ★★ **不能重复计** ✗✗ —— 边跑边记 + 跑完记账，两条都在 ⇒ 一不小心就算两遍 ✓
#   （少报只是数字小一点 ✓ 多报会让用户以为花了更多钱 ✓ 那更糟 ✓）
#   去重靠 run_id ✓ 回滚态 = 不看 run_id、快照一律加上（就是"算两遍"✗）
experiment(
    "用量快照不去重回退（同一笔算两遍）", _APP_MAIN,
    '    if live and int(live.get("calls") or 0) > 0 and str(live.get("run_id") or "") not in seen_runs:',
    '    if live and int(live.get("calls") or 0) > 0:  # MUTANT: 不看 run_id ⇒ 算两遍 ✗',
    "tests/test_usage_accounting.py",
)

# ═══ 2026-10-07 第 2 项 体检⑥ 本地出图：真跑一趟拽出来的三个"界面骗人"缺口 ═══
#
# ★ 本地那一档的探针原来是**废话** ✗ —— `kind == "local"` 直接给「本机运行，不需要 Key」+ ✅可用 ✓
#   而它没回答唯一重要的问题：**ComfyUI 到底在不在跑** ✓
#   ⇒ ComfyUI 没开时界面照样写"可用" ✗ 用户点出图 ⇒ 卡住 ✓（跟 ASR 那次栽的坑一模一样 ✓）
#   回滚态 = 探针拿掉（正是修复前的形态 ✓）
experiment(
    "本地出图探针拿掉回退（ComfyUI 没开也说可用）", _TMP_ROOT / "backend" / "app" / "capabilities.py",
    '                        "probe": lambda: _probe_comfyui()},',
    "                        },  # MUTANT: 不探了 ⇒ 又变成那句废话 ✗",
    "tests/test_local_image_gen.py",
)

# ★ 出图引擎**在界面上切不了** ✗ —— 其余每一档都有「用这个」，唯独出图要手改配置文件 ✓
#   回滚态 = 把切换按钮拿掉（正是修复前的形态 ✓）
experiment(
    "出图引擎切换按钮拿掉回退（只能手改配置文件）", _TMP_ROOT / "frontend" / "src" / "components" / "SettingsPanel.tsx",
    "                              void api.setImage(pid)",
    "                              void Promise.resolve(pid);  // MUTANT: 没有切换这回事 ✗",
    "tests/test_local_image_gen.py",
)

# ★ 用户点「用这个」要有反应 ⇒ 后端得有那个接口 ✓（没有就是点了报 404 ✗）
#   回滚态 = 接口不存在（正是修复前的形态 ✓）
experiment(
    "出图切换接口拿掉回退（点了 404）", _APP_MAIN,
    '@app.post("/api/v1/settings/image")',
    '@app.post("/api/v1/settings/image-gone")  # MUTANT: 接口没了 ✗',
    "tests/test_local_image_gen.py",
)

# ═══ 2026-10-07 第 3 项 版本检查 + 升级提示 ═══
#
# ★ 版本号此前**三处各说各的** ✗：package.json 一份 ✓ 关于页硬编码一份 ✗ 后端**压根没有** ✗
#   ⇒ 现在后端 `version.py` 是唯一来源 ✓ 关于页显示它报的 ✓
#   回滚态 = 关于页又写死版本号（正是修复前的形态 ✓）
experiment(
    "关于页又写死版本号回退（三处各说各的）", _TMP_ROOT / "frontend" / "src" / "components" / "SettingsPanel.tsx",
    '                {ver ? `${ver.name} v${ver.version}` : \'版本（正在读…）\'}',
    '                LanternLogic Agent v0.1.0  {/* MUTANT: 又写死了 ✗ */}',
    "tests/test_version_update.py",
)

# ★ **默认不查** ✓ —— 没配地址就一个字节都不往外发 ✓（本项目是本地优先 ✓）
#   回滚态 = 不管有没有配地址都去请求（就是"偷偷联网"✗）
experiment(
    "升级检查偷偷联网回退（没配地址也发请求）", _APP_MAIN,
    '    if not url:\n        return {"ok": False, "current": cur, "has_update": False, "latest": "", "notes": "", "url": "",\n'
    '                "note": ("还没配「更新检查地址」',
    '    if False:\n        return {"ok": False, "current": cur, "has_update": False, "latest": "", "notes": "", "url": "",\n'
    '                "note": ("还没配「更新检查地址」',
    "tests/test_version_update.py",
)

# ═══ 2026-10-07 第 4 项 限流退避（查证发现：聊天/视频**早就有** ✓
#     缺的是 **出图 / 语音 / 知识库** 三档 + "重试期间用户看不见" ✗）═══
#
# ★ 退避本身**必须真起作用** ✓ —— 回滚态 = 第一次撞 429 就交回（正是那三档修复前的形态 ✓）
#   测试真起了一个会回 429 的小服务器 ⇒ 必须真请求三次（2 次 429 + 1 次成功）✓
experiment(
    "限流不重试回退（撞一次 429 就判死）", _TMP_ROOT / "backend" / "app" / "retry.py",
    "        if not is_retryable(resp.status_code):",
    "        if True:  # MUTANT: 不重试了 ✗",
    "tests/test_upstream_backoff.py",
)

# ★ **重试要让用户看得见** ✓ —— 回滚态 = 默默等（最多 60 秒界面一片安静 ⇒ 像卡死 ✗）
experiment(
    "重试不吭声回退（等 60 秒像卡死）", _TMP_ROOT / "backend" / "app" / "retry.py",
    '        _tell(on_wait, f"上游忙（{last}），{wait:.0f} 秒后重试（第 {attempt + 2}/{attempts} 次）")',
    "        pass  # MUTANT: 不吭声 ✗",
    "tests/test_upstream_backoff.py",
)

# ★ 光引擎里有 on_wait 不算 ✓ —— **loop 得把它接到事件流上** ✓（否则用户还是看不见 ✓）
#   回滚态 = 不接（正是修复前的形态 ✓）
experiment(
    "出图重试没接到事件流回退（用户干等）", _TMP_ROOT / "backend" / "app" / "loop.py",
    '                    eng = DashscopeImageEngine(\n'
    "                        self.image_cfg,\n"
    '                        on_wait=lambda m: self.emit("status", {\n'
    '                            "state": "running", "detail": f"出图：{m}", "call_id": call_id,\n'
    "                        }),\n"
    "                    )",
    "                    eng = DashscopeImageEngine(self.image_cfg)  # MUTANT: 不告诉用户在等 ✗",
    "tests/test_upstream_backoff.py",
)

# ═══ 2026-10-07 第 5 项 访问密码防爆破（查证：这块**一个都没有** ✗
#     而 webhook 那边**早就有** ✓ ⇒ 把这套思路补到登录口 ✓）═══
#
# ★ 登录探测口（中间件**豁免**的那条）自己也得限流 ✓
#   回滚态 = 那两行 login_guard 调用去掉（正是修复前的形态 ✓ 无限次敲门 ✓）
experiment(
    "登录探测口不限流回退（可以无限试密码）", _APP_MAIN,
    "    wait = login_guard.retry_after(ip)\n"
    "    if wait > 0:\n"
    "        raise HTTPException(\n"
    "            status_code=429,",
    "    wait = 0.0\n"
    "    if wait > 0:\n"
    "        raise HTTPException(\n"
    "            status_code=429,",
    "tests/test_login_lockout.py",
)

# ★ 数据面（中间件那道）也要限流 ✓ —— 爆破方真正会打的就是那儿 ✓
#   回滚态 = 不查不记（正是修复前的形态 ✓）
experiment(
    "数据面不限流回退（爆破方随便打）", _APP_MAIN,
    "            _ip_lan = request.client.host if request.client else \"\"\n"
    "            _wait = login_guard.retry_after(_ip_lan)",
    "            _ip_lan = request.client.host if request.client else \"\"\n"
    "            _wait = 0.0  # MUTANT: 不限流 ✗",
    "tests/test_login_lockout.py",
)

# ★ **限流必须先于比对** ✓ —— 反了就等于"先告诉对方密码错了，再限流" ✓ 探测机会还在 ✓
experiment(
    "限流排在比对之后回退（先泄露密码对错）", _APP_MAIN,
    "            if not _hmac_g.compare_digest(str(token), str(cfg.server.access_token or \"\")):\n"
    "                login_guard.record_fail(_ip_lan)",
    "            if not _hmac_g.compare_digest(str(token), str(cfg.server.access_token or \"\")):\n"
    "                pass  # MUTANT: 不记账 ⇒ 限流永远不触发 ✗",
    "tests/test_login_lockout.py",
)

# ★ 本机（回环）**不许被锁** ✓ —— 自己的手滑不该变成"把自己关在门外"✓
#   回滚态 = 回环也限流（正是最容易做错的那种"安全"✗）
experiment(
    "回环也限流回退（自己打错就把自己锁了）", _TMP_ROOT / "backend" / "app" / "login_guard.py",
    '    return ip in ("127.0.0.1", "::1", "localhost", "testclient") or ip.startswith("127.")',
    "    return False  # MUTANT: 回环也拦 ⇒ 自己手滑就锁门 ✗",
    "tests/test_login_lockout.py",
)

# ═══ 2026-10-07 第 6 项 依赖查漏洞（工具类：**只查不改**是底线 ✓）═══
#
# ★ 查依赖的脚本**绝不能顺手升级** ✗ —— 自动升依赖 = 悄悄把用户环境换掉 ✓
#   （"升级"和"修漏洞"不是一回事 ✓ 升完可能跑不起来 ✓ 那是拿"能用"换"看起来安全" ✓）
#   回滚态 = 命令里带上 fix/--force（就是"顺手帮你升"✗）
experiment(
    "查依赖的脚本偷偷升级回退（顺手改环境）", _TMP_ROOT / "scripts" / "audit_deps.py",
    '["npm", "audit", f"--registry={OFFICIAL}"]',
    '["npm", "audit", "fix", "--force", f"--registry={OFFICIAL}"]',
    "tests/test_dep_audit.py",
)

# ★ 但**必须指定官方源** ✓ —— 国内镜像（npmmirror）**没实现**审计接口 ✗
#   （本机实测：直接跑 `npm audit` 只得到一句 404 NOT_IMPLEMENTED ✓ 等于白跑 ✓）
#   回滚态 = 不指定源（正是"以为查过了、其实没查"✗）
experiment(
    "查依赖不指定官方源回退（镜像下白跑一次）", _TMP_ROOT / "scripts" / "audit_deps.py",
    '["npm", "audit", f"--registry={OFFICIAL}"]',
    '["npm", "audit"]',
    "tests/test_dep_audit.py",
)

# ═══ 2026-10-07 第 7 项 自动化剩四项 ═══
#
# ★ "每小时 / 每周 / 每月"原来**表达不出来** ✗（只有"隔 N 分钟"和"每天几点"）
#   回滚态 = 把这三种从判断里去掉（正是修复前的形态 ✓）
experiment(
    "每小时/每周/每月频率取消回退（用户建不出来）", _APP_MAIN,
    '    if sch.get("kind") in ("daily", "weekly", "monthly", "hourly"):',
    '    if sch.get("kind") in ("daily",):  # MUTANT: 只有每天 ✓',
    "tests/test_automation_extras.py",
)

# ★ **单次成本上限**原来没有 ✗ —— 定时任务最怕"某一次跑飞了"✓ 一次烧掉一天额度 ✓
#   回滚态 = 那道闸去掉（正是修复前的形态 ✓）
experiment(
    "单次成本上限取消回退（跑飞了没人拦）", _APP_MAIN,
    '        cap = float(_TASK_COST_CAPS.get(task_id, 0.0) or 0.0)\n        if cap > 0 and task_id:',
    '        cap = 0.0  # MUTANT: 不拦单次 ✗\n        if cap > 0 and task_id:',
    "tests/test_automation_extras.py",
)

# ═══ 2026-10-07 第 8 项 按任务类型推荐角色 ═══
#
# ★ **没把握就不推** ✓ —— 这是这块功能成不成的事：
#   乱推比不推更烦人 ✓ 用户会开始无视它 ✓ 那就等于没有 ✓
#   回滚态 = 总能推一个"最接近的"（正是最容易写成的那种 ✗）
experiment(
    "推荐变成'总能推一个'回退（没把握也推）", _TMP_ROOT / "backend" / "app" / "roles.py",
    "        if hits:\n            scored.append((len(hits), -idx, role, hits))",
    "        scored.append((len(hits), -idx, role, hits))  # MUTANT: 全推 ✗",
    "tests/test_role_suggest.py",
)

# ★ 推荐要**说得出为什么** ✓ —— 不说理由 ⇒ 用户不知道该不该听 ✓ 那就是瞎指挥 ✓
experiment(
    "推荐不说理由回退（瞎指挥）", _TMP_ROOT / "backend" / "app" / "roles.py",
    '            "why": "因为你提到了「" + "」「".join(hits[:3]) + "」",',
    '            "why": "",  # MUTANT: 不说为什么 ✗',
    "tests/test_role_suggest.py",
)

# ═══ 2026-10-07 第 9 项 跨任务审计流水 ═══
#
# ★ 审批决议是**最该留痕**的一件事 ✓ —— 原来它只落在各自任务的事件流里 ✗
#   （"这一个月我批过哪些命令" ⇒ 202 个任务谁能翻得动 ✓）
#   回滚态 = 不记（正是修复前的形态 ✓）
experiment(
    "审批决议不进总账回退（查不到批过什么）", _APP_MAIN,
    '    audit.record("approval", decision=decision, task=task_id,\n'
    '                 command=(_srv_cmd or command or "")[:200] or None)',
    '    pass  # MUTANT: 不记账 ✗',
    "tests/test_audit_ledger.py",
)

# ★ **命令必须由 loop 交给服务端** ✗ —— 用户真点了一次审批才发现的洞：
#   原来只有"批过一次"，没有那条命令 ✓（界面压根没传 ✓ 而命令只在界面手里 ✓）
#   回滚态 = loop 不交（正是修复前的形态 ✓ 那账本里永远缺这条 ✓）
#   ★ 第一版我打在 `_do_approve` 那行上 ✗ —— **变异不红** ✓
#     因为对应的测试是"在测试里自己模拟一遍取法"✗ **没真调用 `_do_approve`** ✓
#     ⇒ 门判"红源可疑"（回滚跑=green）✓ 门是对的 ✓ 我把锚点换到**真能变红**的那处 ✓
#     （这条也顺带印证了我早先跟你说过的短板：那条测试确实比其他几条弱 ✓
#       要变强得让它**真调一次 `_do_approve`** ✓ 那是下一轮的活 ✓）
experiment(
    "loop 不把命令交给服务端回退（账本缺命令）", _TMP_ROOT / "backend" / "app" / "loop.py",
    '        self.approval.note_command(self.task.id, call_id, str(context or ""))',
    '        pass  # MUTANT: 不交给服务端 ✗',
    "tests/test_audit_ledger.py",
)

# ★ **清空数据必须留痕** ✗ —— 用户当天亲手清完 202 → 4 个任务 ✓ 而账本一个字都没有 ✓
#   （这功能其实是挪到备份目录 ✓ 所以记账里带上备份路径 ⇒ 账本就是"找回东西的线索"✓）
#   回滚态 = 不清空时记账（正是修复前的形态 ✓）
experiment(
    "清空数据不留痕回退（事后说不清谁清的）", _APP_MAIN,
    '    audit.record("cleared", parts=",".join(want), moved=len(moved), backup=str(backup))',
    '    pass  # MUTANT: 不留痕 ✗',
    "tests/test_audit_ledger.py",
)

# ═══ 2026-10-07 第 10 项 · 多审批汇总（后端那一半）═══
#
# ★ **必须把"还差几个决定"数对** ✓ —— 用户看不到的正是这个数：
#   一个群同时开几个任务时，审批卡片一条条刷屏 ✓ 点完一条才发现下面还有 ✓
#   回滚态 = 只看第一个任务（正是"一条条来"的形态 ✓）
experiment(
    "多审批汇总只数第一个任务回退（还是得一个个点）", _APP_MAIN,
    "    for tid in tids:\n        for cid in approval.pending_for(tid):",
    "    for tid in tids[:1]:  # MUTANT: 只数第一个 ✗\n        for cid in approval.pending_for(tid):",
    "tests/test_group_approval_summary.py",
)

# ★★ **侧栏那个叉不许再做不可逆销毁** ✗ —— 用户真机上就是这么没的 198 个任务：
#   他的原话"我以为那个叉只是表面删除"✓ 而它当时是 `rmtree` ✓
#   永久 · 无确认 · 无备份 · 无留痕 ✗ 连回收站里都没有 ✓
#   回滚态 = 真删（正是那个形态 ✓ 也正是**更早那次修复**留下的轻重倒挂 ✓）
experiment(
    "删任务又变回不可逆销毁回退（198 个任务就是这么没的）", _APP_MAIN,
    "        info = store.delete_task_files(task_id, trash=trash, stamp=stamp)",
    "        info = store.delete_task_files(task_id)  # MUTANT: 真删 ✗",
    "tests/test_audit_ledger.py",
)

# ★ **删之前要问一句** ✓ —— 用户上次就是一次点掉 198 个任务的：
#   "我以为那个叉只是表面删除"✓ 而它当场就销毁了 ✓
#   回滚态 = 不管确认结果直接删（问了等于没问 ✓ 正是"白问一句"的形态 ✓）
experiment(
    "删任务的确认变成白问回退（还是说删就删）", _TMP_ROOT / "frontend" / "src" / "components" / "Sidebar.tsx",
    "                      if (ok) onDeleteTask(t.id);",
    "                      onDeleteTask(t.id);  // MUTANT: 不问结果，直接删 ✗",
    "tests/test_audit_ledger.py",
)

# ═══ 2026-10-07 第 10 项 ③ 员工头像 ═══
#
# ★ **同一名字必须同一颜色** ✓ —— 颜色要是随机算的 ⇒ 每次刷新都变一个色 ✓ 比没有更乱 ✗
#   回滚态 = 用随机数算颜色（最容易写成的那种 ✗）
experiment(
    "头像颜色改成随机回退（刷新一次换一个色）", _TMP_ROOT / "frontend" / "src" / "components" / "TeamView.tsx",
    "    for (const c of s) h = (h * 31 + c.charCodeAt(0)) % 360;   // 同名字 ⇒ 同颜色 ✓（不抖 ✗）",
    "    h = Math.floor(Math.random() * 360);  // MUTANT: 随机色 ✗",
    "tests/test_team_avatars.py",
)

# ═══ 2026-10-07 A-1 收尾：汇总改接**后端权威接口** ═══
#
# ★ **主数必须来自那个接口** ✓ —— 回滚态 = 退回"只数**这一屏 feed 里**的审批" ✓
#   （正是收尾前的形态 ✓）那时藏在**还没加载到的更早消息**里的审批**数不到** ✗ ⇒ N 偏小 ✓
experiment(
    "多审批汇总退回只数这一屏（更早消息里的数不到）", _TMP_ROOT / "frontend" / "src" / "components" / "TeamView.tsx",
    "  const pend: (FeedMsg & { title?: string })[] = (pendApi === null\n"
    "    ? fromFeed\n"
    "    : pendApi.map(asMsg)\n"
    "  ).filter((m) => !!m.approval?.call_id && !handled[m.approval.call_id]);",
    "  const pend: (FeedMsg & { title?: string })[] = fromFeed;  // MUTANT: 只数这一屏 ✗",
    "tests/test_group_approval_ui.py",
)

# ★ **已经批过的不许再显示** ✓ —— 回滚态 = 最终那份不排除已批的 ✓
#   （数永远不减 ⇒ 用户刚点完「批准」还看到 1 ⇒ 以为没批上 ✓ 会再点一次 ✓）
experiment(
    "多审批汇总不排除已批的回退（数永远不减）", _TMP_ROOT / "frontend" / "src" / "components" / "TeamView.tsx",
    "  ).filter((m) => !!m.approval?.call_id && !handled[m.approval.call_id]);",
    "  ).filter((m) => !!m.approval?.call_id);  // MUTANT: 不排除已批的 ✗",
    "tests/test_group_approval_ui.py",
)

# ★ **接口挂了不许把群聊页弄崩** ✓ —— 回滚态 = 不接 `.catch`（坏响应/断网时无人兜底 ✓）
#   ★ 关键是失败后还得**退回 feed 那份** ✓（不然会一直挂着上一份 / 显示空 ✓）
experiment(
    "拉权威待批失败不兜底回退（界面可能崩/挂着旧数）", _TMP_ROOT / "frontend" / "src" / "components" / "TeamView.tsx",
    "    void api.groupApprovals(g)\n"
    "      .then((r) => setPendApi(r?.approvals ?? []))\n"
    "      .catch(() => setPendApi(null));",
    "    void api.groupApprovals(g).then((r) => setPendApi(r?.approvals ?? []));  // MUTANT: 不兜底 ✗",
    "tests/test_group_approval_ui.py",
)

# ★ 汇总里的「批准」**必须走原来那条 `doApprove`** ✓ —— 不另写提交路径 ✗
#   回滚态 = 自己又调一次 teamApprove（正是"两条路"的形态 ✓ 迟早语义不一致 ✓）
experiment(
    "汇总里另开一条提交路径回退（两条路迟早不一致）", _TMP_ROOT / "frontend" / "src" / "components" / "TeamView.tsx",
    "            title=\"只允许这次\" onClick={() => doApprove(m, 'once')}>批准</button>",
    "            title=\"只允许这次\" onClick={() => void api.teamApprove(gid ?? '', m.task_id ?? '', m.approval!.call_id, 'once', m.approval?.detail ?? '')}>批准</button>",
    "tests/test_group_approval_ui.py",
)

# ═══ 2026-10-07 A-2 回收站清理（我自己新加的功能带出来的新债）═══
#
# ★ **只留最近 N 次** ✓ —— 不设上限的话，回收站自己就变成新垃圾堆 ✓
#   （跟当年那 213 个"界面上已删、硬盘还在"的目录 470.5MB **同一个病** ✓）
#   回滚态 = 不设上限全清（把用户**还能找回的**也清掉 ✓ 比不清理更糟 ✗）
experiment(
    "回收站清理不设上限回退（把能找回的也清了）", _APP_MAIN,
    "    for d in dirs[max(1, int(keep)) :]:",
    "    for d in dirs:  # MUTANT: 全清 ✗",
    "tests/test_trash_prune.py",
)

# ★ **命令里的密钥必须打码** ✗ —— 这本账是给用户翻的 ✓ 原样记下来 = 又开一个泄漏面 ✓
#   （`curl -H "Authorization: Bearer sk-…"` 这种命令真实存在 ✓）
experiment(
    "审计账不打码回退（密钥进账本）", _TMP_ROOT / "backend" / "app" / "audit.py",
    "            row[str(k)[:24]] = redact_text(str(v))[:400] if isinstance(v, str) else v",
    "            row[str(k)[:24]] = str(v)[:400] if isinstance(v, str) else v",
    "tests/test_audit_ledger.py",
)

# ★ **界面上的筛选项，后端得真会写它** ✗ —— 自查抓出的洞：
#   我原来列了「放权/收回」「任务起止」两项，而后端只写 approval/blocked ⇒ 选了永远是空的 ✓
#   回滚态 = 把那个死选项加回去（正是修复前的形态 ✓）
experiment(
    "审计筛选有死选项回退（选了永远空）", _TMP_ROOT / "frontend" / "src" / "components" / "SettingsPanel.tsx",
    '                  <option value="approval">审批决议</option>',
    '                  <option value="forever">放权/收回</option>\n'
    '                  <option value="approval">审批决议</option>',
    "tests/test_audit_ledger.py",
)

# ★ **账本记了，界面就得能筛出来** ✗ —— 一体两面里我漏的那一面：
#   后端早在写 `deleted_task` ✓ 界面却没给这一项 ⇒ 用户**筛不出来** ✓（记了等于白记 ✓）
#   回滚态 = 把「删任务」这一项从界面上拿掉（正是我漏掉时的形态 ✓）
experiment(
    "删任务筛选项被拿掉回退（记了却查不到）", _TMP_ROOT / "frontend" / "src" / "components" / "SettingsPanel.tsx",
    '                  <option value="deleted_task">删任务</option>',
    '',
    "tests/test_audit_ledger.py",
)

# ★ **回收站清理也得能筛** ✗ —— 同一类洞里最后一个（"真删了东西却查不到" ✓）
#   回滚态 = 把这一项从界面上拿掉 ✓
experiment(
    "回收站清理筛选项被拿掉回退（真删了却查不到）", _TMP_ROOT / "frontend" / "src" / "components" / "SettingsPanel.tsx",
    '                  <option value="trash_pruned">回收站清理</option>',
    '',
    "tests/test_audit_ledger.py",
)

# ═══ 2026-10-07 A-4：审计账本加「任务起止」（一个 kind + phase ✓ 不拆两个 kind ✗）═══
#
# ★ **开了任务就得有"起"** ✓ —— 回滚态 = 那行记账不写 ✓
#   （账本此前只有审批/挡下/清空/删任务四类 ⇒ "什么时候开的"根本查不到 ✓）
experiment(
    "任务起止的「起」不记账回退（开了任务查不到）", _APP_MAIN,
    '    audit.record("task", phase="start", task=task.id, title=input_text[:60])\n',
    '',
    "tests/test_audit_ledger.py",
)

# ★ **"止"那条的状态必须是当场那个真值** ✓ —— 回滚态 = 写死 `done` ✗
#   （最容易写成的那种 ✗ 而"昨天那次到底做成没有"正是这本账要回答的 ✓）
experiment(
    "任务结束的状态写死成 done 回退（账本与真实状态不符）", LOOP,
    '                audit.record("task", phase="done", task=self.task.id,\n'
    '                             status=str(getattr(self.task, "status", "") or ""))',
    '                audit.record("task", phase="done", task=self.task.id, status="done")  # MUTANT ✗',
    "tests/test_audit_ledger.py",
)

# ═══ 2026-10-07 A-3：群聊页**手机档**（照着 390px 实看的结果改的三处）═══
#
# ★ **窄档必须收成一列** ✓ —— 回滚态 = 去掉 `has-group` 那个类（正是并排两列的形态 ✓）
#   实量：并排时聊天列只剩 192px ⇒ 气泡 115px、一句话折 4~5 行 ✓
experiment(
    "窄档没收成一列回退（手机上仍并排两列）", _TEAMVIEW,
    "`team-chat-grid${gid && curGroup ? ' has-group' : ''}`",
    '"team-chat-grid"  // MUTANT: 窄档也并排 ✗',
    "tests/test_team_mobile_chat.py",
)

# ★ **`has-group` 得认"这个群真的还在"** ✗ —— 回滚态 = 只看 `gid` ✓
#   （群在别处被删后 ⇒ 窄档"列表收起 + 一句选个群" = **死路** ✓ 自己带出来的边角自己钉住 ✓）
experiment(
    "has-group 只看 gid 回退（群被别处删掉后窄档死路）", _TEAMVIEW,
    "${gid && curGroup ? ' has-group' : ''}",
    "${gid ? ' has-group' : ''}  /* MUTANT: 不认群还在不在 ✗ */",
    "tests/test_team_mobile_chat.py",
)

# ★ **汇总块必须在滚动区外面** ✗ —— 回滚态 = 把滚动容器挪到它上面 ✓
#   （正是搬走前的形态 ✓ 那时 feed 一进来就自动滚到底 ⇒ 这块被顶出屏幕 573px ✓ 用户看不见 ✓）
experiment(
    "汇总块又回到滚动区里回退（进了群看不见）", _TEAMVIEW,
    "                  {curGroup && <GroupProgress group={curGroup as never} />}\n"
    "                  {/* ★★ 2026-10-07（第 10 项 ②）",
    "                  {curGroup && <GroupProgress group={curGroup as never} />}\n"
    "                  <div ref={feedRef} style={{ flex: 1, overflowY: 'auto', padding: '10px 14px', "
    "display: 'flex', flexDirection: 'column', gap: 8 }}>  {/* MUTANT: 滚动容器挪到汇总块上面 ✗ */}\n"
    "                  {/* ★★ 2026-10-07（第 10 项 ②）",
    "tests/test_team_mobile_chat.py",
)

# ★ **窄档那条命令不许被切** ✗ —— 回滚态 = 去掉窄档的换行/独占一行 ✓
#   （实量：153px 的卡里它只剩 13~41px ✓ 而命令本身 265px ✓ 用户看不出要批什么 ✓）
experiment(
    "窄档命令不换行回退（挤成两三个字）", _TMP_ROOT / "frontend" / "src" / "styles.css",
    "  .pend-cmd { flex: 1 1 100% !important; white-space: normal !important; overflow-wrap: anywhere; }",
    "  .pend-cmd { overflow-wrap: anywhere; }  /* MUTANT: 窄档也不换行 ✗ */",
    "tests/test_team_mobile_chat.py",
)

# ═══ 报告 ═══

print("# 红绿证明 · 回滚必红（原始输出）")
print()
print("> 生成方式：`backend\\.venv\\Scripts\\python.exe scripts\\redgreen_check.py`")
print("> 每组实验：临时把实现回退成修复前形态 → 指定锚点测试必须变红 → 恢复 → 必须回绿。")
print("> 跑完源码逐字节还原；本文件由脚本真实输出重定向生成，可随时重跑复核。")
print()
ok_all = True
for name, ok, note in results:
    ok_all = ok_all and ok
    print(f"{'PASS' if ok else '!!FAIL!!'}  {name}")
    print(f"      {note}")
print()
if _SELFCHECK is not None:
    hit = [(n, ok, note) for n, ok, note in results if _SELFCHECK in n]
    if not hit:
        print(f"[selfcheck] 未找到组名含 {_SELFCHECK!r} 的实验")
        sys.exit(3)
    for n, ok, note in hit:
        print(f"[selfcheck] {n}: {'PASS' if ok else 'FAIL'}  {note}")
    sys.exit(0 if all(ok for _, ok, _ in hit) else 1)
print(f"总计 {sum(1 for _, ok, _ in results if ok)}/{len(results)} 组 PASS")
sys.exit(0 if ok_all else 1)
