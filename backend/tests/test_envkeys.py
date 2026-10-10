# -*- coding: utf-8 -*-
"""环境变量 Key **看得到系统那份** —— 2026-10-07 体检真跑抓到的 ✗✗。

## 用户实际撞到的事（原话）

> "我已经把那个 API 贴上去了啊，你到时候做就行"

结果后端一直提示 **needs_key** ✗ —— 查下来：
他的 Key **确实在用户级环境变量里** ✓（`DASHSCOPE_API_KEY` 35 字符 ✓ `MINIMAX_API_KEY` 126 字符 ✓）
**但正在跑的进程看不到** ✗✓ —— 因为：

  **用户级/机器级环境变量只对"之后新开的进程"生效** ✓
  正在跑的进程拿着的是**启动那一刻的快照** ✗
  ⇒ 而且"重启后端"若从一个**更早启动的父进程**（编辑器/终端/宿主）发出 ✓
    继承的**还是那份老环境** ✗✓ ⇒ 用户怎么点都还是"没设置" ✓ 只能重启整台电脑 ✓。

## 修法（一处修好、处处生效 ✓）

启动时 `sync_user_env()` 把系统里的变量**补进 `os.environ`** ✓
⇒ 全项目那十几处 `os.environ.get("XXX_API_KEY")` **一行不用改就都好了** ✓✓
（这比"逐个改成调用 `get()`"稳 ✓ 而且以后新写的代码也不会再忘 ✓）
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import envkeys as E  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """每条测试自己管环境 ✓ 别把真机的 Key 卷进来 ✓。"""
    for name in E.KNOWN_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    yield


def test_get_prefers_the_process_value(monkeypatch):
    """**进程里有的优先** ✓ —— 命令行/测试里的临时覆盖是有意为之 ✓ 不许被系统值盖掉 ✗。"""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "进程里的")
    monkeypatch.setattr(E, "_read_registry", lambda n, s: "系统里的")
    assert E.get("DASHSCOPE_API_KEY") == "进程里的"


def test_get_falls_back_to_the_user_scope(monkeypatch):
    monkeypatch.setattr(E, "_read_registry", lambda n, s: "用户级的" if s == "User" else "")
    assert E.get("DASHSCOPE_API_KEY") == "用户级的"


def test_get_falls_back_to_the_machine_scope(monkeypatch):
    monkeypatch.setattr(E, "_read_registry", lambda n, s: "机器级的" if s == "Machine" else "")
    assert E.get("ARK_API_KEY") == "机器级的"


def test_get_returns_default_when_nothing_has_it(monkeypatch):
    monkeypatch.setattr(E, "_read_registry", lambda n, s: "")
    assert E.get("MINIMAX_API_KEY") == ""
    assert E.get("MINIMAX_API_KEY", "兜底") == "兜底"


def test_sync_fills_missing_and_never_overwrites(monkeypatch):
    """★★ **只补缺失、绝不覆盖** ✓ —— 进程里的临时值不许被系统值顶掉 ✗。"""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "进程里已有的")
    monkeypatch.setattr(E, "_read_registry",
                        lambda n, s: "系统值" if s == "User" else "")
    added = E.sync_user_env(["DASHSCOPE_API_KEY", "MINIMAX_API_KEY"])
    assert "DASHSCOPE_API_KEY" not in added, "把进程里已有的值覆盖了 ✗"
    assert os.environ["DASHSCOPE_API_KEY"] == "进程里已有的", "被系统值顶掉了 ✗"
    assert "MINIMAX_API_KEY" in added, "缺失的没补进来 ✗"
    assert os.environ["MINIMAX_API_KEY"] == "系统值"


def test_sync_reports_names_but_never_values(monkeypatch):
    """★ 同步的**回执里只报名字、不报值** ✓ —— 免得日志里泄密 ✗✓。"""
    monkeypatch.setattr(E, "_read_registry", lambda n, s: "sk-绝密值" if s == "User" else "")
    added = E.sync_user_env(["ARK_API_KEY"])
    assert added == {"ARK_API_KEY": "User"}, added
    assert "sk-绝密值" not in repr(added), "回执里带上了 Key 的值 ✗✗"


def test_sync_marks_itself_done(monkeypatch):
    """启动自检要能问"同步跑过没有" ✓（诊断用 ✓）。"""
    monkeypatch.setattr(E, "_read_registry", lambda n, s: "")
    E.sync_user_env([])
    assert E.synced() is True


def test_config_key_names_include_whatever_the_config_actually_uses():
    """★ **配置里现取的 Key 名也要同步** ✓ —— 测试当场抓出来的 ✓。

    硬名单只列"常见的那几个"✗ 而 `api_key_env` 是**可自定义**的 ✓
    （连测试配置都写着别的名字 ✓）⇒ 只按硬名单同步就会漏 ✗✓
    ⇒ 一律"硬名单 + **配置里现取的**"一起同步 ✓ 才叫一处修好处处生效 ✓。
    """
    from app.config import load_config
    cfg = load_config()
    names = E.config_key_names(cfg)
    for env in (cfg.model.api_key_env, cfg.image.api_key_env,
                cfg.video.api_key_env, cfg.asr.api_key_env):
        if env:                                  # 空名字跳过 ✓（配置里可能没填 ✓）
            assert env in names, f"配置里用的 {env} 不在同步名单里（贴了也读不到 ✗）"
    # 硬名单里的常见 Key 也要在 ✓（用户贴的往往就是这些 ✓）
    assert "DASHSCOPE_API_KEY" in names and "MINIMAX_API_KEY" in names, names


def test_sync_uses_the_config_names_by_default(monkeypatch):
    """不传参时，同步的默认名单**必须是配置里那些** ✓（不是只认硬名单 ✓）。"""
    monkeypatch.setattr(E, "_read_registry", lambda n, s: "值" if s == "User" else "")
    added = E.sync_user_env()                                # 不传 names ✓
    assert added, "默认名单一个都没同步到（配置里的名字没被带上 ✗）"
    assert all(v == "User" for v in added.values()), added


def test_registry_read_never_raises(monkeypatch):
    """读注册表**失败要静默返回空** ✓ —— 不能因为读不到系统环境就让后端起不来 ✗。"""
    import winreg                                            # noqa: PLC0415
    def _boom(*a, **kw):
        raise OSError("注册表坏了")
    monkeypatch.setattr(winreg, "OpenKey", _boom)
    assert E._read_registry("ANY", "User") == ""
