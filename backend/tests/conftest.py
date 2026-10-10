"""测试隔离基建 —— 审计 §7.5 / 验证报告 24 修正。

问题：6 个测试文件 `import app.main` → 触发 `load_config()` → **加载开发者本机
真实 config.json**（provider=mimo、sandbox=docker、真实桌面目录）。测试结果
依赖机器、甚至可能把垃圾任务写进真实 data/。

做法：conftest 在**任何测试模块导入之前**（pytest 保证）设置
AGENT_SHELL_CONFIG 指向临时目录里的测试配置：mock provider、sandbox=off、
storage.data_dir 也落在同一临时目录——app.main 的 store/KB/团队/记忆全部隔离。

验证报告 24 修正：
 · 若调用方**显式**设置了 AGENT_SHELL_CONFIG（如红绿证伪实验 `AGENT_SHELL_CONFIG=xxx
   pytest`），**尊重它、不再覆盖**——此前无条件覆盖导致"改配置跑红"永远跑不出来。
 · 临时配置带上 `sandbox_image`（从出厂 contracts/config.example.json 拷贝）——
   此前缺这个键，测试里 `load_config().executor.sandbox_image` 恒等于代码默认值，
   P0-D 的"改回 alpine 就红"断言形同虚设。
"""
import json
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="agent-shell-tests-"))

# 出厂声明的沙箱镜像（contracts/config.example.json 是"新装机默认"的事实来源）；
# 读不到时退回代码默认——test_config.py 另有断言钉住两处不能是 alpine。
_SANDBOX_IMAGE = "python:3.12-slim"
try:
    _example = Path(__file__).resolve().parents[2] / "contracts" / "config.example.json"
    _SANDBOX_IMAGE = str(json.loads(_example.read_text("utf-8"))["executor"]["sandbox_image"])
except Exception:
    pass

_CFG = _TMP / "config.json"
_CFG.write_text(json.dumps({
    "version": 1,
    "model": {"provider": "mock", "model_name": "mock", "api_key_env": "TEST_KEY_ENV"},
    "executor": {
        "type": "local",
        "workspace_root": str(_TMP / "workspace"),
        "allowed_dirs": [str(_TMP / "workspace")],
        "approval_required": ["rm", "del", "rmdir", "rd", "erase", "format", "reg", "remove-item"],
        "sandbox": "off",
        "sandbox_image": _SANDBOX_IMAGE,
        "timeout_seconds": 60,
    },
    "storage": {"type": "fs", "data_dir": str(_TMP / "data")},
}, ensure_ascii=False, indent=1), "utf-8")

# 尊重显式设置（红绿实验的关键）；仅在未设置时才指向隔离配置
os.environ.setdefault("AGENT_SHELL_CONFIG", str(_CFG))


# ── 十二轮 🔴5②：生产 is_inside 供各审批测试使用（替代 lambda p: True 桩）──
# lambda p: True 恒真 = 审批的"越界路径面"在单测里从未被真实驱动过；
# 回滚 _path_is_inside 为 fail-open 时，这些测试必须集体变红。
import pytest  # noqa: E402


@pytest.fixture()
def prod_is_inside(tmp_path):
    from tests.test_approval_capability_matrix import _real_run

    return _real_run(tmp_path)._path_is_inside


# ── ★ 2026-10-10（第二期）：凭据库必须**每个用例各自一个文件** ──
# 现场（我自己踩的 ✗）：凭据库默认绑在 `data_dir` 上，而整个会话**共享同一个临时 data**
#   ⇒ 某个用例往里写一把 Key（保存 Key 的用例都会写 ✓），后面的用例就读得到 ✗
#   ⇒ 实测：`test_envkeys.py` 两条红（"只按注册表补"的断言被凭据库抢先满足了 ✓）
# 这里统一把 `credentials._PATH` 指到本用例自己的 tmp_path ✓
#   ⇒ 用例之间互不影响 ✓ 也绝不碰开发者本机的 `backend/data/credentials.yaml` ✓
@pytest.fixture(autouse=True)
def _isolate_credentials(tmp_path, monkeypatch):
    from app import credentials

    monkeypatch.setattr(credentials, "_PATH", tmp_path / "credentials.yaml")


# 十九轮 🔴5：会话结束时清理自建临时目录（此前每跑一次 pytest 泄漏 1 个）
def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    import shutil
    shutil.rmtree(_TMP, ignore_errors=True)
