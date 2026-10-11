"""Phase 1 ②（一键启动）：启动前体检 + start.bat 的接线锚点。

问题：老的 `start.bat` 假定你已经跑过 install.bat、装了 Node、建了 venv、构建过前端；
任何一步没做，用户看到的就是一句拼音提示 + 窗口一闪，完全不知道缺什么、怎么补。
现在：`scripts/preflight.py` 逐项体检并给出**可复制的下一步**，`start.bat` 先跑它，
不通过就停在那儿说清楚；通过才启动后端（后端自己提供构建好的界面，运行期不需要 Node）。

本文件钉两件事：
  ① 体检逻辑本身：每种"起不来"的原因都能被识别，且给的补法可执行
  ② ★ 接线：start.bat 必须真的调用体检、真的用体检给出的地址
     （脚本存在 ≠ 接上了 —— 本项目的规矩：接线级锚点）
"""
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import preflight  # noqa: E402

# ★ 真探针的原始引用：下面的 autouse 夹具会把 `preflight._probe_python` 打桩，
#   而"真跑一次解释器"的两条锚点必须用**没被打桩的那一个**
#   （第一版忘了这件事 ⇒ 两条真探针锚点假绿：夹具让它们恒为 True）。
_REAL_PROBE = preflight._probe_python

REAL_VENV_PY = Path(sys.executable)   # 跑测试的那个解释器 = venv 里的 python（副本里也成立）
BAT = (ROOT / "start.bat").read_text("utf-8")


def _codes(items) -> dict[str, str]:
    return {i["code"]: i["level"] for i in items}


@pytest.fixture(autouse=True)
def _fake_python_probe(monkeypatch):
    """PATH 里的 `python` 是好是坏**取决于跑测试的机器**（本机就是个残件）。

    绝大多数锚点不测这件事，统一给"完整解释器"的假结论；专测它的两条锚点自己覆盖。
    不这么做，"健康布局"会因为本机 PATH 的 python 坏了而红 —— 那是环境噪声，不是回归。
    """
    monkeypatch.setattr(preflight, "_probe_python", lambda exe: (True, "3.13.13"))


def _mkroot(tmp: Path, *, config: bool = True, dist: bool = True, port: int = 8642,
            skills: int = 8) -> Path:
    (tmp / "backend").mkdir(parents=True, exist_ok=True)
    if config:
        (tmp / "config.json").write_text(json.dumps({"server": {"port": port}}), "utf-8")
    if dist:
        (tmp / "frontend" / "dist").mkdir(parents=True, exist_ok=True)
        (tmp / "frontend" / "dist" / "index.html").write_text("<html></html>", "utf-8")
    # 技能库：用**出厂名单里的真名字**（与真实仓库一致 —— 这正是会被 tar 弄坏的那一类）；
    # 体检是"点名核对"，所以"健康布局"必须用真名，否则它本来就会被判不完整。
    for name in list(preflight.EXPECTED_SKILLS)[:skills]:
        d = tmp / "backend" / "skills" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text("# skill", "utf-8")
    return tmp


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


# ═══ ① 体检逻辑 ═══

def test_healthy_layout_has_no_blocking_issue(tmp_path, monkeypatch):
    root = _mkroot(tmp_path)
    monkeypatch.setattr(preflight, "_venv_python", lambda r: REAL_VENV_PY)   # 用真 venv 跑真探测
    items = preflight.check(root, _free_port())
    assert preflight.blocking(items) == [], [i for i in items if i["level"] == "fail"]
    assert _codes(items)["deps"] == "ok", "依赖探测没走通（真 venv 版）"


def test_missing_venv_is_reported_with_fix(tmp_path, monkeypatch):
    root = _mkroot(tmp_path)
    monkeypatch.setattr(preflight, "_venv_python", lambda r: r / "backend" / ".venv" / "Scripts" / "python.exe")
    items = preflight.check(root, _free_port())
    assert _codes(items)["venv"] == "fail"
    fix = next(i["fix"] for i in items if i["code"] == "venv")
    assert "install.bat" in fix and "venv" in fix, f"补法不可执行：{fix}"


def test_missing_config_is_reported_with_fix(tmp_path, monkeypatch):
    root = _mkroot(tmp_path, config=False)
    monkeypatch.setattr(preflight, "_venv_python", lambda r: REAL_VENV_PY)
    items = preflight.check(root, _free_port())
    assert _codes(items)["config"] == "fail"
    fix = next(i["fix"] for i in items if i["code"] == "config")
    assert "config.example.json" in fix, f"补法没告诉用户模板在哪：{fix}"


def test_missing_ui_dist_is_reported_with_fix(tmp_path, monkeypatch):
    """界面没构建 ⇒ 打开是空白页 ⇒ 必须当成阻断项并说清怎么构建。"""
    root = _mkroot(tmp_path, dist=False)
    monkeypatch.setattr(preflight, "_venv_python", lambda r: REAL_VENV_PY)
    items = preflight.check(root, _free_port())
    assert _codes(items)["ui"] == "fail"
    fix = next(i["fix"] for i in items if i["code"] == "ui")
    assert "npm run build" in fix and "nodejs.org" in fix, f"补法不完整：{fix}"


# ═══ ★ PATH 里的 python 是残件（2026-10-04 装机演练实测）═══
# 本机 PATH 的 `python` 指向一个只剩 python.exe+DLLs、**没有 Lib\** 的残件：
# `python --version` 照常打印，`python -m venv` 当场炸
# "Failed to import encodings module" —— 用户只会看到这句看不懂的话。
# 体检必须**真跑一次**解释器，并给出一条出路。

def test_broken_python_on_path_is_blocking_with_a_way_out(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "_probe_python",
                        lambda exe: (False, "Failed to import encodings module"))
    monkeypatch.setattr(preflight, "_alternative_pythons", lambda: ["C:/uv/python.exe"])
    root = _mkroot(tmp_path)
    monkeypatch.setattr(preflight, "_venv_python", lambda r: REAL_VENV_PY)
    items = preflight.check(root, _free_port())
    assert _codes(items)["python_path"] == "fail", "残件 python 必须拦住（否则装环境必失败）"
    fix = next(i["fix"] for i in items if i["code"] == "python_path")
    assert "AGENT_SHELL_PYTHON" in fix and "python.org" in fix, f"没给出路：{fix}"
    assert "C:/uv/python.exe" in fix, "本机有可用解释器却没告诉用户"


def test_real_probe_accepts_a_complete_interpreter():
    """真跑（用未打桩的探针）：跑测试的这个解释器必然是完整的。"""
    ok, info = _REAL_PROBE(sys.executable)
    assert ok and info, f"完整解释器被判为坏：{info}"


def test_real_probe_rejects_a_non_python(tmp_path):
    """真跑：不是 python 的东西不许被判为可用（哨兵不出现 ⇒ 拒绝）。

    ★★ 2026-10-09 修正 ✗→✓：原来这里是
        `_REAL_PROBE(str(ROOT / "start.bat"))`
      —— 拿**本仓真的 start.bat** 去当"假 python" ✓
      而这函数是**真去执行**它的 ✗ ⇒ 等于**真的跑了一遍启动脚本** ✓
      实测后果：它在仓库里造出一个 **0 字节的 `backend/backend`** ✗
        ⇒ 红绿 harness 的护栏（受保护范围内不许有未跟踪文件 ✓）**拒绝运行** ✓
        ⇒ 全量门从某次起开始报「组数未解析」✗（harness 自己崩了 ✓ 我就是这么一路追到这儿 ✓）
      ⇒ 改成**临时目录里的假文件** ✓ —— 测试要的只是"一个不是 python 的东西" ✓
        不需要、也不该拿真脚本去试 ✓（那是**执行别人的代码** ✗ 测试里绝不该有 ✓）
    """
    fake = tmp_path / "not-a-python.bat"
    fake.write_text("@echo off\r\necho hi\r\n", encoding="utf-8")
    ok, why = _REAL_PROBE(str(fake))
    assert ok is False, "非 python 被判成可用解释器"
    assert why, "拒绝时也要给出原因"


# ═══ ★ 技能库完整性（本班"干净机器"演练抓到的静默丢包）═══
# 实测：本仓 266 个跟踪文件里 90 个名字含中文（技能库目录名全中文），
# Windows 上 `git archive HEAD | tar -x` 会逐条报错跳过它们 ⇒ 解出 201 个、
# 技能库一个不剩，而应用照常启动 —— Agent 只是安静地没了技能。
# 体检必须把它变成响的。

def test_missing_skill_library_is_blocking_and_names_the_tar_trap(tmp_path, monkeypatch):
    root = _mkroot(tmp_path, skills=0)
    monkeypatch.setattr(preflight, "_venv_python", lambda r: REAL_VENV_PY)
    items = preflight.check(root, _free_port())
    assert _codes(items)["skills"] == "fail", "技能库为空必须算阻断项（否则用户拿到残缺包还不知道）"
    fix = next(i["fix"] for i in items if i["code"] == "skills")
    assert "tar" in fix and "7-Zip" in fix and "git clone" in fix, f"补法没说清怎么拿到完整包：{fix}"


def test_mangled_skill_names_are_detected(tmp_path, monkeypatch):
    """★ 本班实测的另一半：解包工具会把技能目录名改成乱码（UTF-8 被按 GBK 读）。

    只数个数会漏（6 个目录看着"差不多"）——必须**点名核对**。
    """
    root = _mkroot(tmp_path, skills=0)
    sd = root / "backend" / "skills"
    for i in range(6):                      # 数量接近、但名字全是错的
        d = sd / f"妗岄潰鏁寸悊{i}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text("# skill", "utf-8")
    monkeypatch.setattr(preflight, "_venv_python", lambda r: REAL_VENV_PY)
    items = preflight.check(root, _free_port())
    assert _codes(items)["skills"] == "fail", "名字被改坏也必须拦（只数个数会漏掉它）"
    msg = next(i["msg"] for i in items if i["code"] == "skills")
    assert "对不上" in msg, f"没指出「名字对不上」这件事：{msg}"


def test_partial_skill_library_is_blocking(tmp_path, monkeypatch):
    root = _mkroot(tmp_path, skills=3)      # 只装了 3 个真名目录
    monkeypatch.setattr(preflight, "_venv_python", lambda r: REAL_VENV_PY)
    items = preflight.check(root, _free_port())
    assert _codes(items)["skills"] == "fail", "缺技能也要拦（包不完整 = 装机失败）"


def test_expected_skill_list_matches_repo():
    """名单防锈：新人加了技能却没同步 EXPECTED_SKILLS ⇒ 这条红，逼着更新。"""
    real = {d.name for d in (ROOT / "backend" / "skills").iterdir() if d.is_dir()}
    assert real == set(preflight.EXPECTED_SKILLS), \
        f"仓库技能库与体检名单不一致：仓库={sorted(real)} 名单={sorted(preflight.EXPECTED_SKILLS)}"


def test_busy_port_is_warning_not_failure(tmp_path, monkeypatch):
    root = _mkroot(tmp_path)
    monkeypatch.setattr(preflight, "_venv_python", lambda r: REAL_VENV_PY)
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = int(srv.getsockname()[1])
        items = preflight.check(root, port)
    assert _codes(items)["port"] == "warn"
    assert preflight.blocking(items) == [], "端口被占不该算阻断项"


def test_port_comes_from_config(tmp_path):
    """端口以 config.json 为准（用户可能改成别的），不是写死 8642。"""
    root = _mkroot(tmp_path, port=8791)
    assert preflight._config_port(root) == 8791


def test_print_url_uses_config_port(tmp_path, monkeypatch, capsys):
    root = _mkroot(tmp_path, port=8791)
    monkeypatch.setattr(preflight, "_venv_python", lambda r: REAL_VENV_PY)
    monkeypatch.setattr(preflight.sys, "argv",
                        ["preflight.py", "--root", str(root), "--print-url"])
    monkeypatch.setattr(preflight, "_out", lambda t: print(t, end=""))
    assert preflight.main() == 0
    assert "http://127.0.0.1:8791/" in capsys.readouterr().out


# ═══ ② start.bat 接线（脚本存在 ≠ 接上了）═══

def test_start_bat_runs_preflight_and_stops_when_blocked():
    assert "preflight.py" in BAT, "start.bat 没调用体检脚本 —— 用户还是看不到缺什么"
    assert "errorlevel 1" in BAT and "pause" in BAT, "体检不通过时必须停住给用户看，而不是一闪而过"


def test_start_bat_uses_the_url_from_preflight():
    """★ 界面地址必须来自体检（后端托管 dist 的端口可能被改过），不许写死 5173。"""
    assert "--print-url" in BAT, "没有向体检要地址"
    assert 'start "" "%URL%"' in BAT, "没有打开体检给出的地址"
    assert "127.0.0.1:5173" not in BAT, "还写死着 vite 开发端口（运行期不该依赖 Node）"


def test_start_bat_waits_and_explains_failure():
    assert "wait_backend" in BAT, "没有等待后端起来的循环"
    assert "backend_restart.log" in BAT, "起不来时没告诉用户去哪看日志"
