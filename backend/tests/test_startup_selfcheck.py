"""K6a：provider 启动自检接线级锚点——lifespan 里必须真的执行。

旧实现死在 _automation_loop 的 while True 之后（永不可达），且 lifespan 的
P1-15 注释声称有自检——claimed ≠ happened。本组用 TestClient 真进 lifespan：
  · 正常 provider → 日志打"provider=mock 就绪"
  · provider 不可用 → 打警告横幅但**服务仍启动**（自助修复入口不死）
  · 死代码必须不存在（防"接回"变成"两处都有"的二次漂移）
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_lifespan_prints_provider_ready(capsys):
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app, base_url="http://127.0.0.1:8642"):
        pass
    out = capsys.readouterr().out
    assert "provider=mock 就绪" in out, "启动自检未执行（或日志被吞）"


def test_lifespan_provider_failure_warns_but_server_still_starts(monkeypatch, capsys):
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    import app.main as m
    monkeypatch.setattr(m.cfg.model, "provider", "definitely-not-a-provider")
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.get("/api/v1/tasks")
        assert r.status_code == 200, "自检失败不得阻断启动（设置页是自助修复入口）"
    out = capsys.readouterr().out
    assert "模型提供者不可用" in out
    assert "503" in out, "警告必须指明真正的保护位置（_launch_task 503）"


def test_dead_code_block_removed():
    """死代码（while True 之后的不可达块）必须不存在。"""
    import inspect
    import app.main as m
    src = inspect.getsource(m._automation_loop)
    tail = src.split("await asyncio.sleep(20)", 1)[-1]
    assert "create_provider" not in tail, "_automation_loop 尾部仍有不可达自检"
    assert "_wide_monitor" not in tail, "_automation_loop 尾部仍有不可达 wide 监视"


# ═══ 二十六轮 K6b：真实子进程 + GBK 控制台 ═══
# 上面三条用 capsys——它是**内存缓冲、无编码约束**，所以【结构上不可能】发现
# 下面这个真实缺陷：失败横幅里的 ⚠️(U+26A0+U+FE0F) 在 GBK 控制台（中文 Windows
# 默认 ACP=936，也正是 start.bat 的实际环境）print 会抛 UnicodeEncodeError；
# 而它写在 except 处理器【内部】⇒ 异常冲出 _lifespan ⇒ Starlette startup failed
# ⇒ 进程退出（实测 rc=3、端口从未监听）。讽刺的是：这恰好把「设置页」这个
# 唯一的自助修复入口关死，而那正是最需要它活着的时刻（首次运行/换机/Key 撤销）。
#
# ⇒ 本组**真的起一个子进程**，把 stdout 钉成 gbk:strict，验"失败只警告不拒绝启动"
#   这条设计承诺在真实控制台上成立。

_GBK_LIFESPAN_SNIPPET = """
import asyncio
import app.main as m

# 复刻"provider 不可用"（未配 Key / 首次运行 / 拼错 provider 都归到这里）
m.cfg.model.provider = "zzz-no-such-provider"

async def go():
    async with m._lifespan(m.app):
        pass

asyncio.run(go())
print("LIFESPAN-OK")
"""


def test_provider_failure_banner_survives_gbk_console():
    env = {**os.environ, "PYTHONIOENCODING": "gbk:strict"}
    p = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(_GBK_LIFESPAN_SNIPPET)],
        capture_output=True,
        env=env,
        cwd=str(_BACKEND_DIR),
        timeout=180,
    )
    out = p.stdout.decode("gbk", "replace") + p.stderr.decode("gbk", "replace")
    assert p.returncode == 0, (
        f"lifespan 在 GBK 控制台下崩了（rc={p.returncode}）——"
        f"『只警告不拒绝启动』的承诺不成立：\n{out}"
    )
    assert "LIFESPAN-OK" in out, f"lifespan 未走完：\n{out}"
    # 横幅必须仍然可读：符号可降级成 '?'，但下面这句关键信息一个字都不能丢
    assert "模型提供者不可用" in out, f"失败横幅没打出来：\n{out}"
    assert "503" in out, f"横幅必须指明真正的保护位置（_launch_task 503）：\n{out}"


def test_gbk_console_rule_actually_forced():
    """元测试：确认上面那条测试的环境确实是 GBK-strict。

    否则（例如某天 PYTHONIOENCODING 语义变了、或 Python 改了默认）它会静默
    退化成"在 UTF-8 下测 UTF-8"，变成一条永真锚点——这正是本组要防的那类假绿。
    """
    p = subprocess.run(
        [sys.executable, "-c", "import sys; print(sys.stdout.encoding, sys.stdout.errors)"],
        capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": "gbk:strict"},
        cwd=str(_BACKEND_DIR),
        timeout=60,
    )
    got = p.stdout.decode("gbk", "replace").strip().lower()
    assert got == "gbk strict", f"GBK 环境没钉住，实际 = {got!r}"

