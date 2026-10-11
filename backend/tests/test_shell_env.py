"""shell 子进程环境白名单回归（K3 整改）——与 MCP 子进程同源 _CHILD_ENV_KEYS。

接线证明（规矩⑨）：用【真实 _shell 执行 env/echo/python】观察输出——
父进程 os.environ 里明文放着 Key，子进程输出里必须一个都看不到；
同时证明正常命令（echo/git --version/python -c）不因缺变量而坏。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def _executor():
    from app.executors.local import LocalExecutor
    return LocalExecutor(SimpleNamespace(
        type="local", workspace_root=".", allowed_dirs=[], timeout_seconds=60,
        sandbox="off", shell=None, cfg_shell="", search_url="", searxng_url="",
        browser_channel="msedge", comfyui_url="http://127.0.0.1:9",
        image_checkpoint="x", sandbox_image="python:3.12-slim",
        sandbox_network="none", sandbox_memory="512m",
    ))


@pytest.fixture()
def fake_keys(monkeypatch):
    """父进程环境放入仿真 Key（真实场景：运行时 set_model 写入的 BYOK Key）。"""
    keys = {
        "DEEPSEEK_API_KEY": "sk-fake-deepseek-key-for-test",
        "SOME_ACCESS_TOKEN": "tok-fake-token-for-test",
        "MY_APP_SECRET": "secret-fake-for-test",
    }
    for k, v in keys.items():
        monkeypatch.setenv(k, v)
    return keys


async def test_shell_env_contains_no_api_keys(fake_keys, tmp_path):
    """接线锚点：真实 _shell 查询指定变量 → 子进程一个都看不到。

    判据口径（首轮锚点教训）：不能用裸 `env` 全量输出——_OUTPUT_LIMIT 截断 4000
    字符，Key 若排在截断点之后锚点会假绿（回滚实验抓出过）。改为【精确查询
    每个变量名】（输出短、无截断面）+ 计数校验。
    """
    ex = _executor()
    names = list(fake_keys)
    probe = "; ".join(f'echo "{n}=[${n}]"' for n in names)
    out = await ex._shell(tmp_path, probe)
    for n, v in fake_keys.items():
        assert f"{n}=[]" in out, f"子进程仍能读到 {n}：{out!r}"
        assert v not in out, f"子进程泄露了 {n} 的值"
    # 旁证：env 全量里 API_KEY/TOKEN/SECRET 命名的行数必须为 0（grep 计数，短输出）
    out2 = await ex._shell(tmp_path, "env | grep -cE 'API_KEY|TOKEN|SECRET' || echo 0")
    assert out2.strip().startswith("0"), out2
    # 但系统必需变量必须在（否则正常命令会坏）
    out3 = await ex._shell(tmp_path, "echo \"PATH=[${PATH:0:5}]\"; echo \"TEMP=[$TEMP]\"")
    assert "PATH=[]" not in out3 and "TEMP=[]" not in out3


async def test_shell_normal_commands_still_work(fake_keys, tmp_path):
    ex = _executor()
    out = await ex._shell(tmp_path, "echo hello-k3")
    assert "hello-k3" in out
    out2 = await ex._shell(tmp_path, "python -c \"print(1+1)\" || py -c \"print(1+1)\"")
    assert "2" in out2
    out3 = await ex._shell(tmp_path, "git --version")
    assert "git version" in out3


async def test_shell_home_and_temp_present(fake_keys, tmp_path):
    """git/node/python 等工具依赖 HOME/USERPROFILE/TEMP——白名单必须保留。"""
    ex = _executor()
    out = await ex._shell(tmp_path, "echo ${HOME:-$USERPROFILE}; echo $TEMP")
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    assert lines and lines[0], "HOME/USERPROFILE 缺失会弄坏 git 等工具"


def test_whitelist_shared_with_mcp():
    """同源证明：shell 白名单与 MCP 白名单是同一个对象（不是抄了一份会漂移）。"""
    from app.executors.local import _CHILD_ENV_KEYS as SHELL_KEYS
    from app.mcp import _CHILD_ENV_KEYS as MCP_KEYS
    assert SHELL_KEYS is MCP_KEYS


def test_child_env_strips_key_like_names(fake_keys):
    """纯函数锚点：白名单结果不含任何 *KEY*/*TOKEN*/*SECRET* 命名。"""
    from app.executors.local import _child_env
    env = _child_env()
    for name in env:
        upper = name.upper()
        assert "API_KEY" not in upper and "TOKEN" not in upper and "SECRET" not in upper, name
    # 例外核验：白名单里本就不含这类命名的系统变量（USERNAME 等不含上述子串）
    assert "PATH" in env and "TEMP" in env
