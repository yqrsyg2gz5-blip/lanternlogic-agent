# -*- coding: utf-8 -*-
"""本地沙箱（Windows 原生）—— **只实现了「作业对象」这一层**。

## ★ 先读这段：为什么只有一层（2026-10-12 三轮实测的结论，别再重新试一遍）

方案本来要三层：① 受限令牌（降权）② 作业对象（限资源）③ 目录 ACL（只给一个可写目录）。
**实测三轮后，①③ 在本机做不到**，过程与证据如下（详见 `docs/沙箱方案-本地三层隔离.md`）：

| 尝试 | 结果 | 卡在哪 |
|---|---|---|
| `CreateRestrictedToken` | ✅ 造得出 | —— |
| ↑ 用它 + `CreateProcessWithTokenW` | ❌ WinError 5 | 普通身份缺 `SeImpersonatePrivilege` |
| ↑ 提权后重试 | ❌ **仍 WinError 5** | **特权已启用（`SeImpersonatePrivilege` = 已启用），依然 Access Denied** |
| ↑ 换 `CreateProcessAsUserW` | ❌ WinError 5 | `SeAssignPrimaryTokenPrivilege` **根本不在令牌里**（提权后也没有） |
| 备用路：AppContainer | ❌ `0xC0000142` | **连 `whoami.exe` 都起不来** ⇒ 对非打包的传统桌面程序不兼容 |

⇒ **「不需要管理员也能做三层隔离」这个假设被推翻**；且**连提权都不行**。
⇒ 因此本模块**只提供作业对象**，服务这三个真实场景，并**明确不提供**目录隔离。

## 本模块能做什么（真做了的）

  · **T3 防跑飞**：`JOB_OBJECT_LIMIT_PROCESS_MEMORY` 限内存、`JOB_OBJECT_LIMIT_JOB_TIME` 限 CPU 时间
  · **T4 防残留**：`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` —— **主人一死，全家一起死**
  · **T3 超时收尸**：`TerminateJobObject` 杀**整棵进程树**（不是只杀直接子进程）

## 本模块**做不到**什么（别对外宣称能做）

  · ❌ **读/写隔离**：沙箱进程仍是**你自己的身份**（没降权）⇒ 它**读得到、也写得了**你的文件
  · ❌ 防"铁了心要越狱"的代码（那要独立 VM）
  · ❌ 防内核级攻击
  · ⛔ 对外口径：**这是"资源与生命周期限制"，不是"文件隔离"** ✗ 说法别混 ✓

## 为什么仍然值得做

"跑飞 / 挂死 / 留下一堆后台进程"是**实测发生过**的痛点（交接单里 60s 命令超时、
`nohup` 后台轮询、容器残留那一类）。这一层**能真解决那几个**，且**不需要任何特权** ✓
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import subprocess
import sys

from ..winconsole import NO_WINDOW_CREATIONFLAGS

__all__ = ["available", "run_in_sandbox", "SandboxUnavailable"]

_IS_WIN = sys.platform == "win32"

# ── 作业对象限制常量 ──
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_JOB_TIME = 0x00000004
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JobObjectExtendedLimitInformation = 9
JobObjectBasicLimitInformation = 2
JOB_OBJECT_MSG_ACTIVE_PROCESS_LIMIT = 3

if _IS_WIN:
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
    _k32.CreateJobObjectW.restype = wt.HANDLE
    _k32.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
    _k32.SetInformationJobObject.restype = wt.BOOL
    _k32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
    _k32.AssignProcessToJobObject.restype = wt.BOOL
    _k32.TerminateJobObject.argtypes = [wt.HANDLE, ctypes.c_uint]
    _k32.TerminateJobObject.restype = wt.BOOL
    _k32.CloseHandle.argtypes = [wt.HANDLE]
    _k32.CloseHandle.restype = wt.BOOL
    _k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    _k32.OpenProcess.restype = wt.HANDLE
    _k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    _k32.WaitForSingleObject.restype = wt.DWORD
    _k32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
    _k32.GetExitCodeProcess.restype = wt.BOOL

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wt.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wt.DWORD),
            ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
            ("PriorityClass", wt.DWORD),
            ("SchedulingClass", wt.DWORD),
        ]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [("ReadOperationCount", ctypes.c_uint64),
                    ("WriteOperationCount", ctypes.c_uint64),
                    ("OtherOperationCount", ctypes.c_uint64),
                    ("ReadTransferCount", ctypes.c_uint64),
                    ("WriteTransferCount", ctypes.c_uint64),
                    ("OtherTransferCount", ctypes.c_uint64)]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", _IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]


class SandboxUnavailable(RuntimeError):
    """本平台没有作业对象可用 —— **明确报错，绝不静默降级**（与 Docker 沙箱同口径）。"""


#: 默认上限
DEFAULT_MEMORY_MB = 2048
DEFAULT_ACTIVE_PROCS = 8


def available() -> bool:
    """作业对象在当前平台可用吗（建一个空作业对象试试）。"""
    if not _IS_WIN:
        return False
    h = _k32.CreateJobObjectW(None, None)
    if not h:
        return False
    _k32.CloseHandle(h)
    return True


def _make_job(memory_mb: int, cpu_seconds: float | None, active_procs: int) -> wt.HANDLE:
    h = _k32.CreateJobObjectW(None, None)
    if not h:
        raise SandboxUnavailable(f"CreateJobObjectW 失败（WinError={ctypes.get_last_error()}）")
    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_PROCESS_MEMORY
    info.BasicLimitInformation.LimitFlags = flags
    info.ProcessMemoryLimit = memory_mb * 1024 * 1024
    if active_procs > 0:
        info.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        info.BasicLimitInformation.ActiveProcessLimit = active_procs
    if cpu_seconds:
        info.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_JOB_TIME
        info.BasicLimitInformation.PerJobUserTimeLimit = int(cpu_seconds * 10_000_000)
    ok = _k32.SetInformationJobObject(h, JobObjectExtendedLimitInformation,
                                      ctypes.byref(info), ctypes.sizeof(info))
    if not ok:
        err = ctypes.get_last_error()
        _k32.CloseHandle(h)
        raise SandboxUnavailable(f"SetInformationJobObject 失败（WinError={err}）")
    return h


def run_in_sandbox(command: str, workdir, timeout: float = 60.0, *,
                   memory_mb: int = DEFAULT_MEMORY_MB,
                   active_procs: int = DEFAULT_ACTIVE_PROCS,
                   env: dict | None = None) -> tuple[int | None, str]:
    """在作业对象里跑一条命令。

    返回 `(退出码, 输出文本)`。**超时返回退出码 124**（与仓里既有口径一致）。

    与"直接跑"的差别：
      · 限内存 / 限 CPU 时间 / 限并发进程数
      · **超时或取消 → `TerminateJobObject` 杀整棵进程树**（子进程不会残留）
      · 作业对象句柄一关，里面**所有**进程一起死（KILL_ON_JOB_CLOSE）

    ⚠️ **它不做文件隔离** —— 沙箱进程仍是你自己的身份，写得了你的文件 ✓
    """
    if not available():
        raise SandboxUnavailable(
            "作业对象不可用（非 Windows 或系统调用失败）——"
            "本次命令未执行（安全相关，不做静默降级）"
        )
    workdir = str(workdir)
    job = _make_job(memory_mb, timeout, active_procs)
    proc = None
    try:
        proc = subprocess.Popen(
            command, shell=True, cwd=workdir,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=env, **NO_WINDOW_CREATIONFLAGS,
        )
        # 把进程放进作业对象。
        # ★ 访问权限必须含 PROCESS_SET_QUOTA(0x0100) + PROCESS_TERMINATE(0x0001)
        #   —— 实测：用 QUERY_INFORMATION|QUERY_LIMITED(0x0200|0x0400) 会 WinError=5 ✗
        _PROCESS_SET_QUOTA_TERMINATE = 0x0100 | 0x0001
        h_proc = _k32.OpenProcess(_PROCESS_SET_QUOTA_TERMINATE, False, proc.pid)
        if not h_proc:
            raise SandboxUnavailable(f"OpenProcess 失败（WinError={ctypes.get_last_error()}）")
        try:
            if not _k32.AssignProcessToJobObject(job, h_proc):
                raise SandboxUnavailable(
                    f"AssignProcessToJobObject 失败（WinError={ctypes.get_last_error()}）")
        finally:
            _k32.CloseHandle(h_proc)

        try:
            raw, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _k32.TerminateJobObject(job, 124)          # ★ 杀整棵树
            try:
                raw, _ = proc.communicate(timeout=5)
            except Exception:
                raw = b""
                proc.kill()
            return 124, _decode(raw) + f"\n（命令超时 >{timeout:.0f}s，已终止整个作业对象）"
        return proc.returncode, _decode(raw)
    finally:
        if proc is not None and proc.poll() is None:
            _k32.TerminateJobObject(job, 1)
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        _k32.CloseHandle(job)      # 关句柄 ⇒ KILL_ON_JOB_CLOSE 收掉残留


def _decode(raw: bytes | None) -> str:
    if not raw:
        return ""
    try:
        from app.executors.local import decode_output
        return decode_output(raw)
    except Exception:
        return raw.decode("utf-8", "replace")
