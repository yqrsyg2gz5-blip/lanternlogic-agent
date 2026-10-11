"""把 API Key 写进 **User 级环境变量**（Windows = `HKCU\\Environment`；免管理员、不落盘）。

为什么要有它（Phase 1 ① 后半截）：
  设置页填的 Key 目前只写进**当前进程**的 `os.environ` —— 后端一重启就没了。
  2026-10-04 的血案就是这么来的：四个后端 PID 全是"界面能开、HTTP 200、
  发消息永远不回复"，根因就是那一步重启的 shell 环境快照过期 /
  进程里根本没有 Key。正确落点是 **User 级环境变量**：
  `restart-backend.ps1` 正是用
  `[Environment]::GetEnvironmentVariable($KeyName, "User")`（= 读 `HKCU\\Environment`）
  把它注入子进程的 —— 两端用的是同一个位置。

设计要点（安全）：
  · **默认不写**：只有调用方显式要求（`SettingsModelReq.persist=True`）才动注册表。
    API 侧的默认是 False（保守）；设置页的那个勾选框默认勾上（用户看得见、可取消）。
  · **绝不回显**：返回值/日志里没有 Key 值本身，只有变量名、长度与成败。
  · **不落盘**：不写 `config.json`、不写任何文件；只改 `HKCU\\Environment` 里的一个值。
  · **回读校验**：写完立刻读回来比一次 —— 读不回一致的，一律如实报失败
    （宁可让用户看到"没写成功"，也不能让他以为写成了、重启后又是坏的）。
  · **平台**：非 Windows 如实返回"不支持"，并给出可执行的手工做法
    —— 不去猜、不去改用户的 shell 配置文件（那是更危险的越界）。
"""
from __future__ import annotations

import os
import sys

IS_WINDOWS = sys.platform.startswith("win")

# 测试钩子：低层读写可被替换（**测试绝不允许碰用户真实的 HKCU\Environment**）
def _win_set(name: str, value: str) -> None:  # pragma: no cover - 真机路径
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, "Environment", 0,
                            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
    # 通知资源管理器"环境变了"：只影响**之后**由资源管理器启动的进程。
    # 我们的重启走 restart-backend.ps1（自己读注册表），不依赖这条广播；
    # 广播失败不算失败（静默忽略），但**不影响回读校验**。
    try:  # pragma: no cover
        import ctypes

        HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG = 0xFFFF, 0x001A, 0x0002
        ctypes.windll.user32.SendMessageTimeoutW(  # type: ignore[attr-defined]
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", SMTO_ABORTIFHUNG, 5000, None)
    except Exception:
        pass


def _win_get(name: str) -> str | None:  # pragma: no cover - 真机路径
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                            winreg.KEY_QUERY_VALUE) as k:
            val, _ = winreg.QueryValueEx(k, name)
            return str(val)
    except FileNotFoundError:
        return None
    except OSError:
        return None


def read_user_env(name: str) -> str | None:
    """读 User 级环境变量（Windows）。其它平台返回 None（不做猜测）。"""
    if not IS_WINDOWS or not name:
        return None
    return _win_get(name)


def write_user_env(name: str, value: str) -> tuple[bool, str]:
    """把 `name=value` 写进 User 级环境变量。返回 `(ok, 人话说明)`。

    ★ 返回值里**不含 value**：调用方也不许把它拼进日志/响应。
    """
    if not name:
        return False, "没有配置 Key 环境变量名（api_key_env 为空）——先去设置页选一个提供者"
    if not value:
        return False, "Key 为空，未写入"
    if not IS_WINDOWS:
        return False, (f"当前系统（{sys.platform}）不支持自动写入用户级环境变量。"
                       f"请手工执行：export {name}=<你的 Key>（写进 ~/.bashrc 或 ~/.zshrc 后重开终端）")
    try:
        _win_set(name, value)
    except Exception as e:  # 注册表被策略锁、权限不足等
        return False, f"写入用户级环境变量失败：{type(e).__name__}: {str(e)[:120]}"
    back = _win_get(name)
    if back != value:
        return False, (f"写入后回读不一致（读回 {'空' if not back else str(len(back)) + ' 字符'}）"
                       f"——按未写入处理，请检查注册表 HKCU\\Environment 的 {name}")
    return True, f"已写入用户级环境变量 {name}（{len(value)} 字符）——重启后端后长期有效"


def user_env_status(name: str) -> dict[str, object]:
    """给界面用：这个变量在【User 级】有没有、多长（**不回传值**）。"""
    if not IS_WINDOWS or not name:
        return {"supported": False, "set": False, "length": 0}
    v = _win_get(name)
    return {"supported": True, "set": bool(v), "length": len(v) if v else 0,
            # 进程里当前用的是不是这一份（False ⇒ 重启后会变）——界面据此提示"要重启"
            "matches_process": bool(v) and os.environ.get(name) == v}
