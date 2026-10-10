"""★ config.json 写隔离（8f 批的"根治"其实没根治，2026-10-04 第二次血案）。

事故实录（两次，同型）：
  · 8f 批：有一条测试把用户真实 config.json 写成了测试值；
  · 本班：跑 `tests/test_key_persist.py`（端点会 `_save_config()`）后，
    真实 config.json 变成
        server.host = 127.0.0.1     ← 手机直连被关掉
        server.access_token = ""    ← 局域网闸门失效
        storage.data_dir = %TEMP%\\agent-shell-tests-xxx\\data   ← 重启即"任务全消失"
        executor.workspace_root = %TEMP%\\agent-shell-tests-xxx\\workspace
  根因：`load_config()` 认 `AGENT_SHELL_CONFIG`（所以测试**读**的是临时配置），
  但 `main._CONFIG_PATH` 当时**自己算** `__file__` 的路径 ⇒ **写**的仍是用户真实文件。
  8f 批把写路径收成一个模块级变量，却没让它跟环境变量走 ⇒ 隔离依旧是破的。

修法：`config.config_path()` 统一解析（`AGENT_SHELL_CONFIG` > 仓库根），
`main._CONFIG_PATH = config_path()`。本文件把"测试进程绝对写不到仓库那份"钉死：
  ① 测试进程里 `_CONFIG_PATH` 必须就是 `AGENT_SHELL_CONFIG` 指的那个文件
  ② 真调一次 `_save_config()`，仓库 `config.json` 必须**逐字节未变**
  ③ 真 POST 一次 `/api/v1/settings/model`（会触发 _save_config），同上
  ④ 反空扫：conftest 必须真的设了 `AGENT_SHELL_CONFIG`（否则 ①②③ 会假绿）
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main as m

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config.json"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "<不存在>"


@pytest.fixture(autouse=True)
def _isolate_cfg(monkeypatch):
    """端点会改**内存里的 cfg**（provider/model_name/api_key_env）——
    不隔离就会污染同进程的后续用例（本班实测：3 条 test_webhook_security /
    test_startup_selfcheck 变红，报 `model.base_url 未配置`）。"""
    for _k, _v in m.cfg.model.model_dump().items():
        monkeypatch.setattr(m.cfg.model, _k, _v, raising=False)


def test_conftest_redirects_config_env():
    """④ 反空扫：没有这个环境变量，下面三条都会变成"恰好没写到"的假绿。"""
    env = os.environ.get("AGENT_SHELL_CONFIG", "")
    assert env, "conftest 没有设置 AGENT_SHELL_CONFIG —— 测试会去读写用户真实配置"
    assert _sha(Path(env)) != _sha(REPO_CONFIG), "临时配置和仓库配置是同一个文件"


def test_save_config_path_is_the_temp_file():
    """① 写回路径必须与读同源（这就是本班事故的根因）。"""
    assert m._CONFIG_PATH == Path(os.environ["AGENT_SHELL_CONFIG"]), \
        f"_CONFIG_PATH={m._CONFIG_PATH} 不是 AGENT_SHELL_CONFIG —— 写会落到用户真实配置上"


def test_save_config_does_not_touch_repo_config():
    """② 真调 `_save_config()`，仓库 config.json 必须逐字节不变。"""
    before = _sha(REPO_CONFIG)
    m._save_config()
    assert _sha(REPO_CONFIG) == before, "测试进程把用户真实 config.json 写坏了（8f 同型事故）"


def test_settings_endpoint_write_stays_in_temp():
    """③ 端到端：真 POST `/settings/model`（内部会 _save_config），仓库配置仍不许变。

    ★ 同时**自己恢复临时配置**：conftest 那份临时 config 是全测试会话共享的，
      这条测试会把 provider 改成 mock —— 不还原就会污染后面的用例
      （本班实测：不还原时 `test_webhook_security.py` 有 3 条会红）。
    """
    tmp_cfg = Path(os.environ["AGENT_SHELL_CONFIG"])
    snapshot = tmp_cfg.read_bytes()
    before = _sha(REPO_CONFIG)
    try:
        with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
            r = c.post("/api/v1/settings/model", json={
                "provider": "mock", "model_name": "mock",
                "api_key_env": "DSH_ISOLATION_PROBE_KEY", "api_key": "sk-probe-1234567890",
                "persist": False,
            })
            assert r.status_code == 200, r.text
        assert _sha(REPO_CONFIG) == before, "端点把用户真实 config.json 写坏了"
        # 而临时那份**确实**被写了（证明上面不是"什么都没发生"式的假绿）
        tmp = json.loads(tmp_cfg.read_text("utf-8"))
        assert tmp["model"]["provider"] == "mock", f"临时配置没被写入，测试没打中目标面：{tmp['model']}"
    finally:
        tmp_cfg.write_bytes(snapshot)          # 还原共享的临时配置（防跨用例污染）
