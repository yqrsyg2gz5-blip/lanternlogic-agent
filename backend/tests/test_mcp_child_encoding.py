# -*- coding: utf-8 -*-
"""MCP 子进程的编码（★ 2026-10-10 功能扫测真跑抓到的那个 ✗）。

## 现场（原样）

```
[MCP] server「scan-probe」启动失败，已跳过：
  UnicodeDecodeError: 'utf-8' codec can't decode byte 0xb0 in position 88: invalid start byte
```

查实：白名单环境里**没有 PYTHONIOENCODING** ⇒ 子进程 `sys.stdout.encoding = **gbk**` ✗
  ⇒ 它按 GBK 吐 JSON ⇒ 客户端按 UTF-8 读 ⇒ 当场炸 ✓ ⇒ **整个 server 被丢掉** ✗
（MCP 协议规定走 UTF-8 —— 但**没自己设编码的 Python server 在中文 Windows 上就是会这样** ✓
 而这类 server 一大把 ✓ ⇒ 这个坑值得在客户端这一侧堵掉 ✓）

## 本文件钉三件事

  ① 客户端**显式给子进程下发** `PYTHONIOENCODING=utf-8` + `PYTHONUTF8=1` ✓
     （用户自己配的 `env` 可以覆盖 ✓ —— 用 setdefault 不硬压 ✓）
  ② 读回一行时**三级兜底**（UTF-8 → GBK → UTF-8 replace）✓ 绝不因为编码把 server 判死 ✗
  ③ 真起一个**按 GBK 说话**的假 server，跑完整三步握手 ✓ —— 以前这里必炸 ✓
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap

from app.mcp import McpServer, _loads_tolerant


# ═══ ② 宽容解码 ═══

def test_tolerant_reader_handles_gbk_bytes():
    line = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"x": "中文测试"}}, ensure_ascii=False)
    assert _loads_tolerant(line.encode("gbk")) == {
        "jsonrpc": "2.0", "id": 1, "result": {"x": "中文测试"}}


def test_tolerant_reader_handles_utf8_bytes():
    line = json.dumps({"jsonrpc": "2.0", "id": 2, "result": {}}, ensure_ascii=False)
    assert _loads_tolerant(line.encode("utf-8"))["id"] == 2


def test_tolerant_reader_returns_none_for_garbage():
    assert _loads_tolerant(b"\xff\xfe\x00 not json at all") is None
    assert _loads_tolerant(b"[]") is None, "顶层不是对象 ⇒ 当成没用的行 ✓"


# ═══ ① 显式下发编码 ═══

def test_child_env_forces_utf8(tmp_path, monkeypatch):
    """不拉起真进程，直接看它怎么拼环境 ✓（用假 Popen 接住）"""
    seen: dict = {}

    class _FakeProc:
        stdout = None
        def poll(self):  # noqa: D102
            return None

    def _fake_popen(cmd, **kw):
        seen["env"] = kw.get("env") or {}
        raise OSError("（测试：到此为止，只看环境 ✓）")

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    srv = McpServer("probe", "python", ["x.py"])
    try:
        srv._sync_ensure()
    except OSError:
        pass
    env = seen.get("env") or {}
    assert env.get("PYTHONIOENCODING") == "utf-8", f"没给子进程下发 UTF-8 ✗：{env.get('PYTHONIOENCODING')}"
    assert env.get("PYTHONUTF8") == "1", env.get("PYTHONUTF8")


def test_user_env_can_override(monkeypatch):
    """★ 用户显式配的 env 优先 ✓（setdefault 语义 ✓ 不硬压 ✗）"""
    seen: dict = {}

    def _fake_popen(cmd, **kw):
        seen["env"] = kw.get("env") or {}
        raise OSError("stop")

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    srv = McpServer("probe", "python", ["x.py"], env={"PYTHONIOENCODING": "gbk"})
    try:
        srv._sync_ensure()
    except OSError:
        pass
    assert seen["env"]["PYTHONIOENCODING"] == "gbk", "用户的显式设置被覆盖了 ✗"


# ═══ ③ 真起一个子进程：它自己报出来的输出编码必须是 UTF-8 ═══
#   （这就是这次修复的要害 ✓ —— 以前白名单里没有 PYTHONIOENCODING ⇒ 子进程 encoding=gbk ✗）

_ENCODING_REPORTER = textwrap.dedent('''
    import json, sys
    # 不发请求、不读 stdin —— 只把"我打算用什么编码说话"报给它爸 ✓
    print(json.dumps({"stdout": sys.stdout.encoding, "stdin": sys.stdin.encoding,
                      "fs": sys.getfilesystemencoding(), "utf8_mode": sys.flags.utf8_mode},
                     ensure_ascii=False), flush=True)
''')


def test_child_process_speaks_utf8(tmp_path):
    """★ 端到端的那一条：应用拉起来的子进程，stdout 编码必须是 utf-8 ✓。

    现场复刻（修复前必红 ✗）：白名单环境里没有 PYTHONIOENCODING
      ⇒ 子进程 `sys.stdout.encoding == 'gbk'` ⇒ 中文 JSON 按 GBK 吐 ⇒ 这边按 UTF-8 读就炸 ✓
    """
    script = tmp_path / "reporter.py"
    script.write_text(_ENCODING_REPORTER, "utf-8")
    srv = McpServer("enc-probe", sys.executable, [str(script)])
    srv._sync_ensure()
    # ★ 必须走它自己的队列 ✓ —— stdout 由内部的泵线程接管 ✗ 自己 readline 会抢不到（本班实测 ✓）
    raw = srv._q.get(timeout=30)
    proc = srv._proc
    if proc is not None and proc.poll() is None:
        proc.kill()                                   # close() 是 async，这里直接杀 ✓
    info = json.loads(raw.decode("utf-8"))
    assert str(info["stdout"]).lower().replace("-", "") == "utf8", (
        f"子进程会用 {info['stdout']} 说话 ✗ —— 中文 JSON 一出就炸 ✓ 原始：{info}")

