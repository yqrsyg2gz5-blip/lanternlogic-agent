"""沙箱 shell_exec 真跑回归 —— 复审 P0 回归（import secrets 缺失导致 shell_exec 全废）。

教训（验证报告 20）：测试全绿 ≠ 功能可用——审批判定用纯函数测试、其余用 stub
执行器，跨层断裂（审批 → 沙箱 → docker 容器命名）只有真跑才看得见。
本文件让 shell_exec 的 docker 分支**真实进容器**（无 Docker 时整文件 skip）。
"""
from __future__ import annotations

import shutil
import subprocess

import pytest
from types import SimpleNamespace

from app.config import load_config
from app.executors.local import LocalExecutor

# 复审 P0-D 证据链：镜像必须来自配置（ExecutorCfg 默认 slim），不得硬编码——
# 谁把配置镜像改回无 bash 的 alpine，这条测试就会红
_SANDBOX_IMAGE = load_config().executor.sandbox_image


def _docker_ok() -> bool:
    return shutil.which("docker") is not None and subprocess.run(
        ["docker", "info"], capture_output=True, timeout=30
    ).returncode == 0


@pytest.mark.skipif(not _docker_ok(), reason="Docker 不可用")
@pytest.mark.asyncio
async def test_sandbox_shell_exec_really_runs(tmp_path):
    """★ 真跑：sandbox=docker 的 shell_exec 必须真实返回命令输出（NameError 即炸）。"""
    ex = LocalExecutor(SimpleNamespace(
        type="local", workspace_root=str(tmp_path), allowed_dirs=[str(tmp_path)],
        timeout_seconds=60, sandbox="docker", shell=None, search_url="", searxng_url="",
        browser_channel="msedge", comfyui_url="http://127.0.0.1:8189",
        image_checkpoint="x.safetensors", sandbox_image=_SANDBOX_IMAGE,
        sandbox_network="none", sandbox_memory="512m",
    ))
    # 复审 P0-D：命令里显式用 bash——镜像没有 bash（如 alpine）时这条必红
    res = await ex.run_tool("shell_exec", {"command": "bash -c 'echo BASH_OK_IN_SANDBOX && echo sandbox-ok'"}, tmp_path)
    # ★ 2026-10-09（CI 抓的 ✗）：Docker 在、但**镜像拉不下来**（CI 的 registry 网络问题 ✗）
    #   ⇒ 那不是产品错 ✓ 也不是这条测试要测的东西 ✓ ⇒ skip 掉并说清原因 ✓
    #   （不静默 ✗ 不假装通过 ✗ —— 跳过理由会打在测试报告里 ✓）
    if not res.ok and "拉取沙箱镜像失败" in (res.output or ""):
        pytest.skip(f"沙箱镜像拉不下来（CI 网络的 registry 问题）⇒ 跳过：{res.output[:120]}")
    assert res.ok, f"沙箱 shell_exec 失败：{res.output[:200]}"
    assert "BASH_OK_IN_SANDBOX" in res.output, f"镜像 {_SANDBOX_IMAGE} 缺 bash 或 bash 未执行：{res.output[:200]}"
    assert "sandbox-ok" in res.output


@pytest.mark.skipif(not _docker_ok(), reason="Docker 不可用")
@pytest.mark.asyncio
async def test_sandbox_output_truncated(tmp_path):
    """复审修复：沙箱输出超限被截断（不再把容器内巨量输出抽进内存）。"""
    ex = LocalExecutor(SimpleNamespace(
        type="local", workspace_root=str(tmp_path), allowed_dirs=[str(tmp_path)],
        timeout_seconds=60, sandbox="docker", shell=None, search_url="", searxng_url="",
        browser_channel="msedge", comfyui_url="http://127.0.0.1:8189",
        image_checkpoint="x.safetensors", sandbox_image=_SANDBOX_IMAGE,
        sandbox_network="none", sandbox_memory="512m",
    ))
    res = await ex.run_tool("shell_exec", {"command": "yes | head -c 200000"}, tmp_path)
    if not res.ok and "拉取沙箱镜像失败" in (res.output or ""):   # 同上 ✓ 镜像拉不动 ⇒ 跳过 ✓
        pytest.skip(f"沙箱镜像拉不下来（CI 网络的 registry 问题）⇒ 跳过：{res.output[:120]}")
    assert res.ok
    assert len(res.output) < 100_000, "沙箱输出必须截断"
