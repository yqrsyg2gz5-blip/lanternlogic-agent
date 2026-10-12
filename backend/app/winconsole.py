# -*- coding: utf-8 -*-
"""Windows 子进程窗口控制 —— 一处常量，免得十处重复魔数（2026-10-13）。

## 为什么有这个文件（用户真机反馈）

> 现在这个应用，我不管点哪个都感觉像闪黑屏那种感觉，就是终端就是闪烁一下子，
> 不管点，就是比如说点设置它就闪一下终端

**根因**：Windows 上从 GUI 进程起**控制台程序**（`nvidia-smi` / `where` / `node` /
`cmd` / git bash …）时，若不给 `CREATE_NO_WINDOW`，系统会**新建一个控制台窗口**再立刻销毁
⇒ 用户看到"黑框闪一下"，**每次调用闪一次** ✗

**为什么点设置就闪**：设置页会打 `/api/v1/capabilities`，那里要探测显卡 ⇒ 调 `nvidia-smi`；
环境探测还会调 `node --version`、`where bash` 等。

## 用法

    from .winconsole import NO_WINDOW_CREATIONFLAGS   # 包里
    from app.winconsole import NO_WINDOW_CREATIONFLAGS  # 包外

    subprocess.run([...], capture_output=True, **NO_WINDOW_CREATIONFLAGS)
    subprocess.Popen([...], **NO_WINDOW_CREATIONFLAGS)
    await asyncio.create_subprocess_shell(cmd, **NO_WINDOW_CREATIONFLAGS)

★ **只用 `**` 展开**：非 Windows 上它是空字典，不会给平台不认的参数 ✓
★ 测试钉在 `backend/tests/test_no_console_flash.py`：app 下**任何**新起的子进程
  漏了这个都会让它变红 ✓
"""
from __future__ import annotations

import sys

#: `CREATE_NO_WINDOW` —— 子进程不新建控制台窗口（Windows 专有常量）
CREATE_NO_WINDOW = 0x08000000

#: 直接 `**` 展开给 subprocess / asyncio 子进程用
NO_WINDOW_CREATIONFLAGS: dict[str, int] = (
    {"creationflags": CREATE_NO_WINDOW} if sys.platform == "win32" else {}
)

__all__ = ["CREATE_NO_WINDOW", "NO_WINDOW_CREATIONFLAGS"]
