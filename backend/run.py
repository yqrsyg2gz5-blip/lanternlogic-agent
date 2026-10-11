"""PyInstaller 打包入口（桌面壳 Tauri 用）—— `pyinstaller -F backend/run.py`。

为什么不直接打包 `app/main.py`：
  · PyInstaller 按 `__main__` 追踪依赖，入口必须是**文件**而不是 `-m app.main` 的包形式；
  · 打包后没有源码目录树，`app.*` 的包导入要靠入口文件把 backend 目录挂进 sys.path。

工作目录策略：桌面版双击启动时 CWD 不可控（可能是 C:\\Windows\\System32），
config.json / data/ / skills/ 都按**可执行文件所在目录**解析（-F 模式下即 exe 旁），
与 install.bat 装出来的目录布局一致。
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_BACKEND_DIR))
# PyInstaller -F：资源解包到 sys._MEIPASS，但 config/data/skills 必须落在 exe 旁才能持久化
if getattr(sys, "frozen", False):
    import os

    os.chdir(Path(sys.executable).resolve().parent)

from app.main import app, cfg  # noqa: E402  （依赖上面的 sys.path 注入)


def _watch_parent() -> None:
    """桌面壳 sidecar 模式的防孤儿守护（分发实测 2026-10-02）：

    壳（agent-shell.exe）把自身 PID 放进 AGENT_SHELL_PARENT_PID。壳崩溃/被强杀时
    Tauri 的 RunEvent::Exit 钩子不会执行——后端必须自己盯着父进程，父进程一死
    立即退出，否则 uvicorn 残留占着 8642 端口，下次启动直接失败。"""
    import os
    import threading

    pid = os.environ.get("AGENT_SHELL_PARENT_PID", "")
    if not pid or not pid.isdigit():
        return

    def _watch() -> None:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
        if not handle:
            return  # 父进程不存在/无权打开——按独立运行处理
        try:
            while kernel32.WaitForSingleObject(handle, 5000) == 0x102:  # WAIT_TIMEOUT = 还活着
                pass
        finally:
            kernel32.CloseHandle(handle)
        print("[sidecar] 桌面壳已退出，后端随之关闭", flush=True)
        os._exit(0)

    threading.Thread(target=_watch, daemon=True).start()


if __name__ == "__main__":
    _watch_parent()
    import uvicorn

    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port)
