# -*- coding: utf-8 -*-
"""环境变量里的 Key —— **进程看不到时去问系统**（2026-10-07 体检真跑抓到的 ✗✗）。

## 用户实际撞到的事

> "我已经把那个 API 贴上去了" ✓ —— "为什么还提示**没设置**？"

查下来：他的 Key **确实贴上了** ✓（用户级环境变量里 `DASHSCOPE_API_KEY` 35 字符 ✓
`MINIMAX_API_KEY` 126 字符 ✓ `DEEPSEEK_API_KEY` ✓ `XIAOMI_MIMO_API_KEY` ✓ 都在 ✓）。
**但后端读不到** ✗✓ —— 因为：

  **用户级/机器级环境变量只对"之后新开的进程"生效** ✓ 而正在跑的后端进程
  拿着的是**启动那一刻的环境快照** ✗ —— 就算"重启后端" ✓
  若重启命令本身是从一个**更早启动的父进程**（编辑器/终端/宿主）里发出的 ✓
  它继承的还是那份**老环境** ✗✓ ⇒ 用户怎么点都还是"没设置" ✓ 只能重启整台电脑 ✓。

## 所以这里做两件事

1. **`sync_user_env()`**：启动时（以及每次用户保存 Key 之后 ✓）
   把**用户级 + 机器级**的相关变量**补进 `os.environ`** ✓
   ⇒ 全项目那十几处 `os.environ.get("XXX_API_KEY")` **一行不用改就都好了** ✓✓
2. **`get(name)`**：单个取值时也带同样的回退 ✓（给新代码用 ✓）

★ 为什么不改成"每处都调 `get()`" ✗：
  全项目有十几处在读 Key ✓ 逐个改风险大 ✓ 而且**新写的代码还会忘** ✗
  ⇒ 补进 `os.environ` 是**一处修好、处处生效** ✓ 且与现有代码**完全兼容** ✓。

★ 为什么不干脆读注册表并**覆盖** `os.environ` ✗：
  进程里可能是**临时覆盖**（测试、命令行 `set`）✓ 那是有意为之 ✓
  ⇒ 只在**缺失时补齐**（`setdefault` 语义 ✓）✓ 绝不覆盖已有的 ✓。
"""
from __future__ import annotations

import os
import sys
from typing import Iterable

# 项目认识的 Key 变量名（与设置页/各 provider 对齐 ✓ —— 多补几个不存在的名字也没代价 ✓）
KNOWN_KEY_ENVS: tuple[str, ...] = (
    "XIAOMI_MIMO_API_KEY", "DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY", "MINIMAX_API_KEY", "ARK_API_KEY", "KLING_API_KEY",
    "MOONSHOT_API_KEY", "ZHIPU_API_KEY", "OPENROUTER_API_KEY", "SERPAPI_API_KEY",
    "AGENT_SHELL_SMTP_PASSWORD", "AGENT_SHELL_WEBHOOK_SECRET",
)

_SYNCED = False


def _read_registry(name: str, scope: str) -> str:
    """从 Windows 注册表读用户级/机器级环境变量 ✓（非 Windows 直接返回空 ✓）。"""
    if sys.platform != "win32":
        return ""
    try:
        import winreg                                        # noqa: PLC0415
        root = winreg.HKEY_CURRENT_USER if scope == "User" else winreg.HKEY_LOCAL_MACHINE
        sub = (r"Environment" if scope == "User"
               else r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")
        with winreg.OpenKey(root, sub) as k:
            val, _ = winreg.QueryValueEx(k, name)
            return str(val or "")
    except Exception:                                        # noqa: BLE001
        return ""


def get(name: str, default: str = "") -> str:
    """取一个环境变量的值 ✓ —— 进程里有就用进程里的 ✓ 没有就去系统里问 ✓。"""
    cur = os.environ.get(name)
    if cur:
        return cur
    for scope in ("User", "Machine"):
        v = _read_registry(name, scope)
        if v:
            return v
    return default


def config_key_names(cfg: object | None = None) -> list[str]:
    """**从配置里现取**这个项目真正会用到的 Key 变量名 ✓（而不是只靠硬名单 ✓）。

    ★ 为什么要这一步（测试当场抓出来的 ✓）：
      硬名单只列"常见的那几个"✗ —— 而用户可以**自定义** `api_key_env` ✓
      （甚至测试用的配置写的是别的名字 ✓）⇒ 只按硬名单同步就会漏 ✗✓
      ⇒ 一律"**硬名单 + 配置里现取的**"一起同步 ✓ 才叫一处修好处处生效 ✓。
    """
    names = list(KNOWN_KEY_ENVS)
    try:
        if cfg is None:
            from .config import load_config
            cfg = load_config()
        for sec in ("model", "image", "video", "asr"):
            part = getattr(cfg, sec, None)
            env = str(getattr(part, "api_key_env", "") or "")
            if env and env not in names:
                names.append(env)
        for eng in ((getattr(cfg, "video", None) and
                     getattr(cfg.video, "engines", None)) or {}).values():
            env = str((eng or {}).get("api_key_env") or "")
            if env and env not in names:
                names.append(env)
    except Exception:                                        # noqa: BLE001
        pass
    return names


def sync_user_env(names: Iterable[str] | None = None) -> dict[str, str]:
    """把系统里那些**进程还没看到**的变量补进 `os.environ` ✓（只补缺失 ✓ 不覆盖 ✓）。

    返回：本次**补进来**的那些（名字 → 来源 ✓ 不返回值本身 ✗ —— 免得日志泄密 ✓）。

    ★ 2026-10-10 第二期（照 DSH 的做法）：来源现在有**三处**，按这个顺序问 ✓
        ① 进程环境（有就用手上这份 ✓ 显式覆盖优先）
        ② **凭据库**（`data/credentials.yaml` ✓ DSH 那份 `.credentials.yaml` 的同款做法）
        ③ 用户级 / 机器级注册表（老路 ✓ 一个字没动 ✓）
      ⇒ 凭据库排在前 ⇒ "一个文件、按名字存"就是真相源 ✓ 而老的环境变量仍然兜底 ✓
    """
    global _SYNCED
    added: dict[str, str] = {}
    for name in (names if names is not None else config_key_names()):
        if os.environ.get(name):
            continue
        v = ""
        try:                                                 # ② 凭据库
            from . import credentials
            v = credentials.get(name)
        except Exception:                                    # noqa: BLE001
            v = ""
        if v:
            os.environ[name] = v
            added[name] = "credentials"
            continue
        for scope in ("User", "Machine"):                     # ③ 注册表（老路 ✓）
            v = _read_registry(name, scope)
            if v:
                os.environ[name] = v                            # 只补空的 ✓
                added[name] = scope
                break
    _SYNCED = True
    return added


def migrate_into_credentials(names: Iterable[str] | None = None) -> list[str]:
    """把"系统里已经有、凭据库里还没有"的 Key 收进凭据库 ✓（第一次跑的时候用 ✓）。

    ★ 只搬不删 ✗：环境变量/注册表里那份**原样留着** ✓（随时回退得回去 ✓）
      —— 搬进去只是为了"有一个按名字存、看得见、有备份的真相源" ✓ 与 DSH 一致 ✓

    返回：这一次搬进去的名字清单 ✓（给日志/自检说真话用 ✓）
    """
    from . import credentials
    if credentials.path() is None:
        return []
    have = credentials.load()
    moved: list[str] = []
    for name in (names if names is not None else config_key_names()):
        if name in have:
            continue
        for scope in ("User", "Machine"):
            v = _read_registry(name, scope)
            if v:
                ok, _ = credentials.set(name, v)
                if ok:
                    moved.append(name)
                break
    return moved


def synced() -> bool:
    """启动时那一次同步跑过没有（给自检/诊断用 ✓）。"""
    return _SYNCED
