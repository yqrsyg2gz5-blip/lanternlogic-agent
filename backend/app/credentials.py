# -*- coding: utf-8 -*-
"""凭据库 —— 照 DSH 的做法：**一个文件、按名字存、改动前备份**（2026-10-10 第二期）。

## DSH 是怎么做的（从它的实现/数据目录里读出来的 ✓ 不是猜的）

  · 凭据**集中在一个文件**里：`~/.dsh/.credentials.yaml` ✓
      `version: 1` ＋ `refs:`（名字 → 密钥，例：`XIAOMI_API_KEY` / `ZAI_CODING_CN_API_KEY`）
      ＋ `records:`（按插件/服务分组的结构化凭据）
  · **配置里引用的是"名字"** ✓（模型配置写的是 `apiKeyEnv: XIAOMI_API_KEY` ✓）
      ⇒ 每家各引用各的 ⇒ **切来切去串不了格** ✓✓
  · 有**专门的插件**管它（`@deepseek-ai/dsh-credentials-local` ✓ 还 watch 文件变化）
  · 改动前先**备份**（`.credentials.yaml.bak_before_*` ✓）

## 本模块落到本仓（**不引第三方依赖** ✓ 只实现用到的那一小块 YAML）

  · 文件：`<data_dir>/credentials.yaml` ✓（data 目录已在 `.gitignore` 里 ✓ 不进仓库 ✓）
  · 结构：`version: 1` ＋ `refs:` 下面缩进两格的 `名字: "值"` ✓（值按 JSON 字符串转义 ✓）
  · `get()` / `set()` / `names()` / `path()` / `load()`
  · ★ **写入前自动备份** ✓（`credentials.yaml.bak` ✓ 只留最近一份 ⇒ 有界 ✓）
  · ★ **原子写** ✓（临时文件 + `os.replace` ✓ 与 `team.py` 同款 ✓）
  · ★ **回读校验** ✓（写进去要能读回来 ✓ 与 `uservenv` 同一条诚实规矩 ✓）
  · ★ 与既有环境变量**完全兼容** ✓：`envkeys.sync_user_env()` 多一个来源，
      顺序 = 进程环境 → **凭据文件** → 注册表 ✓（进程里显式设的仍然优先 ✓）

★ 为什么是"多一个来源"而不是"换掉环境变量"✗：
  用户今天已经踩过一次"哪把 Key 被谁覆盖了、查不出来"✓
  ⇒ 凭据文件让**每家的 Key 各占一行、看得见、写得有记录** ✓
  而环境变量那条路**原样保留** ✓（重启仍有效、老的 Key 一个不动 ✓）
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

_LOCK = threading.Lock()
_PATH: Path | None = None

_HEADER = (
    "# LanternLogic 凭据库（照 DSH 的做法：一个文件、按名字存、改动前备份）\n"
    "# ★ 这里有明文密钥 —— 别提交进 git、别外发。\n"
    "# 结构：version + refs（名字 → 值）；改这个文件前会自动留一份 .bak\n"
)


def bind(data_dir: Path) -> None:
    """告诉它该写哪儿 ✓（由 main 在启动时调一次 ✓ 与其它存储同源 ✓）。"""
    global _PATH
    _PATH = Path(data_dir) / "credentials.yaml"


def path() -> Path | None:
    return _PATH


def _parse(text: str) -> dict[str, str]:
    """只解析我们自己写出去的那一小块 YAML ✓ —— 顶层 `refs:` 下面缩进的 `名字: "值"`。"""
    refs: dict[str, str] = {}
    in_refs = False
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith((" ", "\t")):                 # 顶层键
            in_refs = line.split(":", 1)[0].strip() == "refs"
            continue
        if not in_refs:
            continue
        k, _, v = line.strip().partition(":")
        k, v = k.strip(), v.strip()
        if not k:
            continue
        if v.startswith('"'):
            try:
                refs[k] = json.loads(v)                      # 我们自己写的：JSON 字符串 ✓
                continue
            except Exception:                                # noqa: BLE001
                pass
        refs[k] = v.strip("'")                               # 手改过的：宽容一点 ✓
    return refs


def load() -> dict[str, str]:
    p = _PATH
    if p is None or not p.exists():
        return {}
    try:
        return _parse(p.read_text("utf-8", errors="replace"))
    except OSError:
        return {}


def get(name: str) -> str:
    return load().get((name or "").strip(), "")


def names() -> list[str]:
    return sorted(load().keys())


def _render(refs: dict[str, str]) -> str:
    out = [_HEADER, "version: 1\n", "refs:\n"]
    for k in sorted(refs):
        out.append(f"  {k}: {json.dumps(refs[k], ensure_ascii=False)}\n")
    return "".join(out)


def set(name: str, value: str) -> tuple[bool, str]:          # noqa: A001 - 语义即"写一条"
    """写一条 ✓ —— 写前备份 ✓ 原子落盘 ✓ 回读校验 ✓ 返回 (ok, 人话) ✓。"""
    p = _PATH
    if p is None:
        return False, "凭据库还没绑定数据目录（启动时没调 bind）"
    n = (name or "").strip()
    if not n or not value:
        return False, "名字或值是空的，没写"
    with _LOCK:
        refs = load()
        if p.exists():
            try:                                             # ★ 改动前先备份 ✓ 只留最近一份 ✓
                p.with_suffix(".yaml.bak").write_bytes(p.read_bytes())
            except OSError:
                pass
        refs[n] = value
        tmp = p.with_suffix(".yaml.tmp")
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(_render(refs), "utf-8")
            os.replace(tmp, p)                               # 原子替换 ✓
        except OSError as e:                                 # noqa: BLE001
            tmp.unlink(missing_ok=True)
            return False, f"凭据库写不进去：{type(e).__name__}: {e}"
        if get(n) != value:                                  # 回读校验 ✓
            return False, "凭据库回读不一致 —— 已写入但读回来不对，请人工看一眼这个文件"
    return True, f"已存进凭据库（{p.name}）"
