# -*- coding: utf-8 -*-
"""访问密码的**防爆破限流**（★ 2026-10-07 第 5 项）。

## 为什么要有（查证：这块**一个都没有** ✗）

开了「手机直连」（局域网）之后，**同一个 WiFi 下的任何人都能敲这个门** ✓ ——
密码错了就回 401 ✓ 然后……**可以接着敲，无限次** ✗。
密码默认是 `secrets.token_urlsafe(24)`（≈192 位熵 ✓ 敲到天荒地老也撞不上 ✓），
但**用户是可以自己改密码的** ✓（接口只拦"至少 8 个字符"✗ 8 位纯数字是允许的 ✓）
⇒ 那种密码不设防爆破 = 几下就试出来了 ✓。

## 口径**照抄 webhook 那一处** ✓（不另立一套 ✗）

webhook 早就有防爆破 ✓（`_hook_rate_ok` / `_hook_record_fail`："连错 5 次冷却 60 秒"✓
"**限流先于比对**——不给探测机会"✓）。这里是同一套思路 ✓ 更好懂、也好对齐 ✓。

## 三条设计（每条都在防"把你自家人关在门外"✗）

1. **限流先于比对** ✓ —— 冷却期内一律 429，**连"密码对不对"都不告诉你** ✓
   （否则爆破方还能从"错得慢一点/快一点"里套信息 ✓）
2. **回环地址（本机）不限流** ✓ —— 理由：能走回环的**已经在这台机器上了** ✓
   它本来就能直接读 `config.json` 里的密码 ✗ 拦它没有意义 ✓
   而**把你自己在电脑上打错 5 次密码变成"锁门 60 秒"**才是真添乱 ✓
   （局域网来的人 IP 不是回环 ⇒ 照拦不误 ✓ 该防的一个没漏 ✓）
3. **成功就清账** ✓ —— 输对了立刻把失败记录清掉 ✓
   （不然"昨天错了 9 次"会一直压着，今天就差一次就锁 ✓ 那不合理 ✓）
"""
from __future__ import annotations

import time

#: 连错几次开始冷却（与 webhook 的 `_HOOK_FAIL_LIMIT` 同一量级 ✓）
FAIL_LIMIT = 10
#: 冷却时长（秒）—— 与 webhook 的 60 秒一致 ✓
COOLDOWN_S = 60.0
#: 内存里最多记多少个来源 ✓ —— 被一堆来源刷爆内存也是攻击面 ✗（Ipv4 全网扫是做不到的，
#: 但"每个来源一条记录"不设上限就是隐患 ✓）
MAX_TRACKED = 512

#: ip -> [失败时刻（monotonic）]
_FAILS: dict[str, list[float]] = {}


def _is_loopback(ip: str) -> bool:
    ip = (ip or "").strip()
    return ip in ("127.0.0.1", "::1", "localhost", "testclient") or ip.startswith("127.")


def retry_after(ip: str) -> float:
    """还要等多少秒（0 = 现在可以试 ✓）。读操作，不改状态 ✓。"""
    if _is_loopback(ip):
        return 0.0
    now = time.monotonic()
    fails = [t for t in _FAILS.get(ip, []) if now - t < COOLDOWN_S]
    if fails:
        _FAILS[ip] = fails
    else:
        _FAILS.pop(ip, None)
    if len(fails) < FAIL_LIMIT:
        return 0.0
    return max(0.0, COOLDOWN_S - (now - fails[0]))


def record_fail(ip: str) -> None:
    """记一次失败 ✓（回环不记 ✓ 见文件头第 2 条 ✓）。"""
    if _is_loopback(ip):
        return
    now = time.monotonic()
    if len(_FAILS) >= MAX_TRACKED and ip not in _FAILS:
        # 满了 ⇒ 先把"已经过期"的清掉 ✓ 再不行就丢最老的那个 ✓（绝不无限涨 ✗）
        alive = {k: v for k, v in _FAILS.items() if any(now - t < COOLDOWN_S for t in v)}
        _FAILS.clear()
        _FAILS.update(dict(list(alive.items())[: MAX_TRACKED - 1]))
    _FAILS.setdefault(ip, []).append(now)


def record_ok(ip: str) -> None:
    """输对了 ⇒ 把这个来源的失败记录清掉 ✓（见文件头第 3 条 ✓）。"""
    _FAILS.pop(ip, None)


def reset_all() -> None:
    """清空（测试与"改了密码"之后用 ✓ —— 改了密码说明门锁换了，旧账不该继续压着 ✓）。"""
    _FAILS.clear()
