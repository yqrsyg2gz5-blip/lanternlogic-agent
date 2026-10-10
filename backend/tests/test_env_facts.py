r"""★ 环境交底：把"本机哪些工具真能用"写进工作单。

## 现场（2026-10-05 第三轮真群回归）

程序员的活**烧完 25 步预算失败**，它最后一句是：
> "code_check 显示 py_compile 通过（可能用了不同环境），**先找系统里可用的完整 Python**"

也就是说：**整个预算花在找一个能用的解释器上**。而这台机器的坑台账里记过（N19）：
PATH 上的 `python` 是残件（只有 exe+DLL、没有 `Lib\`），`python --version` 照常打印，
`-m py_compile` 当场炸 —— **员工不知道，只能靠一次次试错去发现**。

## 规矩

**环境的坑要由系统交底，不能让每个员工各自踩一遍。** 派活时把探测结果写进系统提示：
· 能用的 Python（`AGENT_SHELL_PYTHON` → 后端自己的解释器 → `py -3` → `python`，
  且**必须能 import encodings** —— 残件就卡在这）
· node / npm 有没有
· 明确要求"环境问题反复失败就别硬试"
"""
from __future__ import annotations

from app import envfacts


def test_python_probe_skips_broken_interpreters(tmp_path, monkeypatch):
    """★ 判据是"跑得起来**而且**能 import encodings" —— 残件解释器（缺 Lib\\）会被跳过。"""
    envfacts.python_cmd.cache_clear()
    fake = tmp_path / "python.exe"
    fake.write_text("@echo off\r\nexit /b 1\r\n", encoding="utf-8")     # 跑啥都失败 ⇒ 不可用
    monkeypatch.setattr(envfacts.shutil, "which", lambda name: str(fake) if name == "python" else None)
    monkeypatch.delenv("AGENT_SHELL_PYTHON", raising=False)
    cmd, why = envfacts.python_cmd()
    # 后端自己的解释器一定可用 ⇒ 会兜到它；绝不该返回那个假解释器
    if cmd:
        assert "python.exe" not in cmd or "venv" in cmd.lower(), (cmd, why)
    else:
        assert "没找到" in why, why
    envfacts.python_cmd.cache_clear()


def test_facts_tell_the_agent_what_really_works():
    """交底内容：可用的解释器 + node 情况 + "别硬试"的纪律。"""
    envfacts.facts.cache_clear()
    text = envfacts.facts("/tmp/ws")
    assert "本机实况" in text
    assert "Python" in text and "Node" in text
    assert "别自己找" in text or "别在它上面浪费时间" in text, text
    assert "工作区" in text and "/tmp/ws" in text
    assert "别硬试" in text, text
    envfacts.facts.cache_clear()


def test_dispatch_carries_the_env_facts():
    """接线锚点：派活时把交底拼进系统提示（不然员工还是得自己试错）。"""
    src = (__import__("pathlib").Path(__file__).resolve().parents[1] / "app" / "main.py").read_text("utf-8")
    assert "envfacts.facts(str(gws))" in src, "派发没带环境交底（应连带群工作区路径）"
    assert "system_extra=sys_extra + chr(10)" in src, "交底没拼进系统提示"


def test_group_tasks_share_one_workspace():
    """源码位置锚点：登记必须发生在 _start_run **之前**（晚一步就落回私有目录）。"""
    src = (__import__("pathlib").Path(__file__).resolve().parents[1] / "app" / "main.py").read_text("utf-8")
    i_set = src.find("store.set_task_workdir(task.id, workdir)")
    i_start = src.find("_start_run(task, input_text.strip()")
    assert -1 < i_set and -1 < i_start and i_set < i_start, (i_set, i_start)


def test_step_budget_is_wider_for_code_work():
    """★ 治"跑不完"（2026-10-05 全量试跑）：代码活要"写-跑-改"迭代，25 步必然被砍在半路。

    实测：一个测试任务跑了 280 条事件仍在调试，被步数/熔断掐死 ✗。
    依据 MetaGPT 的做法：工程师自己写单测、真跑、看报错、改（上限 3 次重试）—— 那是迭代，不是一次成型。
    """
    from app import main as m

    assert m._budget_for("测试工程师", "写自检脚本并真跑") == 60
    assert m._budget_for("工程师", "按契约实现 todo.py") == 60
    assert m._budget_for("架构师", "定接口契约") == 40
    assert m._budget_for("文案", "写宣传语") == 25
    assert m._budget_for("认不出的角色", "随便干点啥") == 40      # 中庸值：比 25 宽，但不失控
    # 派发时必须真的把预算带下去（否则改了等于没改）
    src = (__import__("pathlib").Path(__file__).resolve().parents[1] / "app" / "main.py").read_text("utf-8")
    # ★ 2026-10-07：这一枪多带了 `role=`（按角色限权 ✓）⇒ 调用写成多行了 ✓
    #   锚点跟着改成带上逗号的那一段 ✓（意思没变：预算必须真的传下去 ✓）
    assert "max_iterations=_budget," in src, "派发没把步数预算带下去"
    assert "workdir=gws, max_iterations=_budget," in src
    assert 'role=str(emp.get("role") or "")' in src, "群派发没把员工角色带下去（限权就落不了地）"


def test_group_dispatch_really_shares_one_workspace(tmp_path, monkeypatch):
    """★ 行为锚点（源码字符串锚点没判别力 —— 本班踩过：群派发那里还有第二处登记兜着）。

    真调一次群派发：`_launch_task` 收到的 workdir 必须是**群共享目录**，
    并且 `store.workspace_dir(那个任务)` 也得指向它（文件接口/验收低层靠这个解析）。
    """
    from types import SimpleNamespace

    from app import main as me
    from app.team import TeamStore

    data = tmp_path / "data"
    st_store = me.store.__class__(data)                     # 用真 FsStore（只换数据目录）
    monkeypatch.setattr(me, "store", st_store)
    team = TeamStore(tmp_path / "team")
    emp = team.add_employee({"name": "架构师", "dept": "技术部", "role": "架构师",
                             "persona": "定契约", "mode": "expert"})
    g = team.create_group("开发群", [emp["id"]], mode="leader")
    monkeypatch.setattr(me, "_team_store", team)
    monkeypatch.setattr(me, "create_provider", lambda mc: object())
    monkeypatch.setattr(me, "_spawn_bg", lambda coro: coro.close())
    seen: dict = {}

    def _fake_launch(text, project_id=None, provider=None, system_extra=None, workdir=None,
                     max_iterations=None, role=""):
        seen["workdir"] = workdir
        seen["system_extra"] = system_extra or ""
        seen["max_iterations"] = max_iterations
        seen["role"] = role            # ★ 2026-10-07：按角色限权靠它（群任务以员工身份跑 ✓）
        tid = "task_shared01"
        st_store.set_task_workdir(tid, workdir)             # 照真实现登记
        return SimpleNamespace(id=tid)

    monkeypatch.setattr(me, "_launch_task", _fake_launch)
    me._dispatch_to_employee(g["id"], g, "架构师", team.get_employee(emp["id"]), "写契约")
    want = st_store.group_workspace(g["id"])
    assert seen["workdir"] == want, seen
    assert st_store.workspace_dir("task_shared01") == want, "登记没生效（同群看不到彼此的产物）"
    assert str(want) in seen["system_extra"], "环境交底里应带上群工作区路径"
    # ★ 2026-10-07：群任务必须**以这位员工的身份**跑 ✓（限权按"谁"判才有意义 ✓）
    assert seen["role"] == "架构师", seen
    """★ 真群回归暴露的头号坑：每个任务一个独立工作区 ⇒ 同群的人互相看不见产物 ⇒
    任何"按文件交接"都不成立（架构师的契约文档，程序员那边是空的）。"""
    src = (__import__("pathlib").Path(__file__).resolve().parents[1] / "app" / "main.py").read_text("utf-8")
    assert "store.group_workspace(gid)" in src, "群派发没用共享工作区"
    assert "store.set_task_workdir(task.id, gws)" in src, "群共享工作区没登记（文件接口/验收解析不到）"
    # 登记必须发生在 _start_run **之前**（TaskRun 构造时就决定了写在哪）
    i_set = src.find("store.set_task_workdir(task.id, gws)")
    i_start = src.find("_start_run(task, input_text.strip()")
    assert -1 < i_set and -1 < i_start, (i_set, i_start)
    store_src = (__import__("pathlib").Path(__file__).resolve().parents[1] / "app" / "store.py").read_text("utf-8")
    assert "def group_workspace(" in store_src and "def set_task_workdir(" in store_src
    assert "task_workdir.json" in store_src, "映射没落盘（重启后文件接口会解析错）"
