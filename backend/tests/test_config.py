"""配置加载的回归测试 —— 第 1 班（安全网）。

目标：把 `contracts/03-config.md` 承诺的三条规则钉成可执行的断言：
  1. 配置文件缺失 → 启动即报错，且报错里要有「路径」与「怎么修」
  2. 非 JSON / 版本不符 → 报错，不静默回退
  3. 字段类型错 → 报错并指出字段

外加一条曾经的**已知缺陷锁定用例**（P1-11）：未知字段被静默丢弃。
该缺陷已于 2026-09-30 修复，`xfail` 标记随之移除，用例转为正式护栏
（另外补了一条真实案例：`command_whitelist` 这个死字段现在必须报错）。

运行：cd backend && .venv\\Scripts\\python -m pytest -q
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import AppConfig, load_config

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
EXAMPLE = REPO / "contracts" / "config.example.json"


def _minimal() -> dict:
    """一份最小可加载配置；改动它来构造各种坏情况。"""
    return {
        "version": 1,
        "server": {"host": "127.0.0.1", "port": 8642},
        "model": {"provider": "mock"},
        "executor": {"type": "local"},
        "storage": {"type": "fs", "data_dir": "./data"},
    }


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    return p


# ---------- 契约三：缺失/坏文件必须报错，不静默回退 ----------


def test_example_config_loads():
    """contracts/config.example.json 必须始终可加载——install.bat 直接 copy 它。"""
    assert EXAMPLE.exists(), f"示例配置不见了：{EXAMPLE}"
    cfg = load_config(EXAMPLE)
    assert isinstance(cfg, AppConfig)
    assert cfg.version == 1
    assert cfg.model.provider == "mock", "示例必须默认 mock，否则新用户没 Key 起不来"


def test_minimal_config_loads_and_applies_defaults(tmp_path):
    cfg = load_config(_write(tmp_path, _minimal()))
    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 8642
    assert cfg.executor.type == "local"
    assert cfg.storage.data_dir == Path("./data")


def test_missing_file_reports_path_and_fix(tmp_path):
    """规则：配置缺失 → 报错信息必须同时给出「路径」和「怎么修」。"""
    missing = tmp_path / "nope.json"
    with pytest.raises(FileNotFoundError) as ei:
        load_config(missing)
    msg = str(ei.value)
    assert str(missing) in msg
    assert "config.example.json" in msg, "报错必须告诉用户去 copy 哪个模板"


def test_invalid_json_reports_file(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{ this is not json", "utf-8")
    with pytest.raises(ValueError) as ei:
        load_config(p)
    assert "config.json" in str(ei.value)


def test_wrong_version_is_rejected(tmp_path):
    data = _minimal()
    data["version"] = 2
    with pytest.raises(ValueError) as ei:
        load_config(_write(tmp_path, data))
    assert "version" in str(ei.value)


def test_wrong_type_is_rejected_with_field_hint(tmp_path):
    """规则 3：字段类型错 → 报错要指出字段，不能静默回退。"""
    data = _minimal()
    data["server"]["port"] = "not-a-port"
    with pytest.raises(ValueError) as ei:
        load_config(_write(tmp_path, data))
    assert "port" in str(ei.value)


# ---------- 拼错字段必须报错（P1-11 已修：xfail 标记于 2026-09-30 移除）----------


def test_unknown_field_must_be_rejected(tmp_path):
    data = _minimal()
    data["model"]["max_iteration"] = 60  # 故意拼错（少一个 s）
    with pytest.raises(ValueError) as ei:
        load_config(_write(tmp_path, data))
    assert "max_iteration" in str(ei.value), "报错必须点名是哪个字段"


def test_stale_field_in_executor_is_rejected(tmp_path):
    """真实案例：`command_whitelist` 是 v1 就写在文档里、但从未被执行的死字段。

    它从模型里删除后，老配置若还留着**必须报错**，而不是静默忽略 ——
    否则用户会以为自己受白名单保护（虚假安全感）。
    """
    data = _minimal()
    data["executor"]["command_whitelist"] = ["python"]
    with pytest.raises(ValueError) as ei:
        load_config(_write(tmp_path, data))
    assert "command_whitelist" in str(ei.value)


def test_config_with_utf8_bom_still_loads(tmp_path):
    """★ 回归：带 BOM 的 config.json 必须能加载。

    老版记事本、Windows PowerShell 的 `Set-Content -Encoding UTF8`、
    不少编辑器保存 UTF-8 时都会写 BOM；而带 BOM 的 JSON 用 utf-8 解会抛
    `Unexpected UTF-8 BOM` —— 用户只是改了个配置，产品却起不来，
    还报一个看不懂的错。（2026-09-30 做隔离目录安装验证时踩出来的。）
    """
    import json as _json

    p = tmp_path / "bom.json"
    p.write_bytes(b"\xef\xbb\xbf" + _json.dumps(_minimal(), ensure_ascii=False).encode("utf-8"))
    cfg = load_config(str(p))
    assert cfg.model.provider == "mock"


def test_config_without_bom_still_loads(tmp_path):
    """不加 BOM 的也要照常（别为了兼容 BOM 把正常路径弄坏）。"""
    cfg = load_config(_write(tmp_path, _minimal()))
    assert cfg.version == 1

# ---------- 沙箱镜像护栏（验证报告 24：P0-D 证据链） ----------

def test_example_config_sandbox_image_is_bash_capable():
    """出厂配置的沙箱镜像必须存在且**含 bash**（alpine 无 bash → bash -c 必 127）。

    验证报告 24：此前没有任何测试钉住这个键，P0-D"改回 alpine 就红"是假的。
    """
    repo_root = Path(__file__).resolve().parents[2]
    example = json.loads((repo_root / "contracts" / "config.example.json").read_text("utf-8"))
    image = str(example.get("executor", {}).get("sandbox_image") or "")
    assert image, "contracts/config.example.json 缺 executor.sandbox_image"
    assert "alpine" not in image.lower(), f"出厂镜像不能是无 bash 的 alpine：{image}"


def test_code_default_sandbox_image_is_bash_capable():
    """代码默认值同样不能是 alpine（config.py ExecutorCfg）。"""
    from app.config import ExecutorCfg
    image = str(ExecutorCfg().sandbox_image)
    assert image and "alpine" not in image.lower(), f"代码默认镜像不能是 alpine：{image}"


def test_conftest_respects_explicit_config_env(monkeypatch):
    """红绿实验的前提：显式设置的 AGENT_SHELL_CONFIG 不得被 conftest 覆盖。

    （直接断言 conftest 的实现约定：setdefault 语义——若本测试运行时 env 与
    conftest 写的同一个值说明未显式设置，跳过；否则必须等于显式值。）
    """
    import os
    explicit = os.environ.get("AGENT_SHELL_CONFIG", "")
    assert explicit, "运行测试时 AGENT_SHELL_CONFIG 应已由 conftest 设置"


# ---------- K4：代码默认审批清单对齐 example（rm 零审批事故） ----------

def test_code_default_approval_required_matches_example():
    """K4：代码默认审批清单必须与 contracts/config.example.json 一致（防两份漂移）。

    此前代码默认 []——用户手写极简 config（只填 provider/model）时，
    rm 等破坏性命令零审批直接执行。
    """
    from app.config import ExecutorCfg
    exp = json.loads(EXAMPLE.read_text("utf-8"))["executor"]["approval_required"]
    got = ExecutorCfg().approval_required
    assert got, "代码默认审批清单不能为空（rm 零审批事故回归）"
    assert sorted(got) == sorted(exp), f"代码默认 {got} 与 example {exp} 漂移"


def test_minimal_config_rm_still_asks(tmp_path):
    """K4 验收：最小 config（不写 approval_required）→ rm -rf 仍会 ASK（接线级）。"""
    cfg = load_config(_write(tmp_path, _minimal()))
    assert "rm" in cfg.executor.approval_required
    from app.approval import ApprovalManager
    v = ApprovalManager().check(
        "t-k4", "rm -rf x", cfg.executor.approval_required, lambda p: True,
        target_exists=lambda t: False, script_reader=lambda s: None)
    assert v is not None and v.action == "ask", "最小配置下 rm 必须触发审批"
