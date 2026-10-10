# -*- coding: utf-8 -*-
"""★ 2026-10-06「这类以后都别问」—— 跨任务、能持久化的那一档审批。

## 为什么加它

实测：一个 10 分钟的小活，用户要被点 **2–5 次**审批 ✓，**等审批常常比干活还久** ✗
（评测台九轮真跑，每轮人工放行 2–5 次 ✓）。

已有的两档都**不跨任务** ✗：
· `allow_all`（本任务全部允许）只活在内存里，后端一重启就失效 ✓
· `remember`（本任务都允许）只记在**这个任务**上 ✓
⇒ 下一个任务又从零开始问 ✓✓。

## 安全边界（三道，一个都不能少 ✓）

① **应用目录的硬保护在它之前就判了** ✓（碰 app/.venv/scripts 的命令连"全部允许"都不豁免 ✓）
② **破坏性动词永不记忆** ✗（rm/del/move/覆盖写… 一律照问 ✓）
③ **结构性命令永不记忆** ✗（`$()` / 管道 / heredoc / 串联… 一次豁免 = 整类任意执行 ✓ 审计 P1 ✓）
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.approval import ApprovalManager  # noqa: E402


def _fresh(tmp_path) -> ApprovalManager:
    """拿一个干净的 ApprovalManager，并且把落盘路径指到临时目录（别污染真配置 ✓）。"""
    m = ApprovalManager()
    m._forever.clear()
    m._forever_path = staticmethod(lambda: tmp_path / "forever.json")  # type: ignore[method-assign]
    return m


def test_remembers_the_program_not_the_whole_command(tmp_path):
    """记的是**程序**（命令首词 ✓）—— 不然"记这条命令"等于没记（参数天天变 ✓）。"""
    m = _fresh(tmp_path)
    assert m.allow_forever("python wordcount.py sample.txt") == "python"
    assert m.allow_forever("pytest -q tests/test_x.py") == "pytest"
    assert m.allow_forever("git status") == "git"
    # 带路径的解释器取**文件名** ✓（实测环境交底里就是全路径写法 ✓）
    assert m.allow_forever(r"C:\AI\agent-shell\backend\.venv\Scripts\python.exe x.py") == "python"
    assert set(m.forever_rules()) == {"python", "pytest", "git"}


def test_destructive_and_structural_commands_are_never_remembered(tmp_path):
    """②③ 两道边界：删除/移动类 ✗ 与 `$()`/管道类 ✗ **永远记不住** ✓。"""
    m = _fresh(tmp_path)
    for cmd in ("rm -rf build", "del /s /q build", "Move-Item a b", "Remove-Item -Recurse x",
                "cat a.txt | python x.py", "python $(echo x).py", "a && rm -rf b",
                "python - <<'EOF'\nprint(1)\nEOF"):
        assert m.allow_forever(cmd) == "", f"这种命令不该被记住 ✗：{cmd}"
    assert m.forever_rules() == {}


def test_rule_persists_across_manager_restarts(tmp_path):
    """**落盘** ✓ —— 这是它和"本任务全部允许"最大的区别（那条重启就没了 ✓）。"""
    p = tmp_path / "forever.json"
    m1 = ApprovalManager()
    m1._forever.clear()
    m1._forever_path = staticmethod(lambda: p)          # type: ignore[method-assign]
    m1.allow_forever("pytest -q")
    assert p.exists(), "没有落盘 ⇒ 重启就白记了 ✗"

    m2 = ApprovalManager()
    m2._forever_path = staticmethod(lambda: p)          # type: ignore[method-assign]
    m2._forever = m2._load_forever()
    assert "pytest" in m2.forever_rules(), "重启后没读回来 ✗"


def test_revoke_one_and_revoke_all(tmp_path):
    """用户随时能收回 ✓（先收一条 ✓ 再一键全收 ✓）。"""
    m = _fresh(tmp_path)
    m.allow_forever("python x.py")
    m.allow_forever("pytest -q")
    assert m.revoke_forever("python") == ["pytest"]
    assert m.revoke_forever() == []
    assert m.forever_rules() == {}


def test_broken_file_does_not_break_startup(tmp_path):
    """配置文件坏了**不能拖垮启动** ✗ —— 读不出来就当空 ✓。"""
    p = tmp_path / "forever.json"
    p.write_text("{ 这不是 JSON", encoding="utf-8")
    m = ApprovalManager()
    m._forever_path = staticmethod(lambda: p)           # type: ignore[method-assign]
    assert m._load_forever() == {}


def test_approved_command_is_reused_for_new_tasks(tmp_path):
    """端到端那一步：记下之后，**另一个任务**的同程序命令不该再问 ✓。"""
    m = _fresh(tmp_path)
    m.allow_forever("python wordcount.py")
    # 模拟"另一个任务"的命令判定：key 命中 + 不是破坏性/结构 ⇒ 走 allow_forever 分支 ✓
    cmd = "python another_script.py --flag"
    key = m._forever_key(cmd)
    assert key == "python" and key in m.forever_rules()
    assert not m._forever_unsafe(cmd), "普通 python 命令不该被判成破坏性 ✗"
    assert m._forever_key("rm -rf x") == "" or m._forever_unsafe("rm -rf x")
    # 落盘内容可读（人也能看懂 ✓）
    data = json.loads((tmp_path / "forever.json").read_text("utf-8"))
    assert data["python"].startswith("20"), data
