# -*- coding: utf-8 -*-
"""★ 第 5 项 · 访问密码的防爆破限流 —— 2026-10-07。

## 查证：这块**一个都没有** ✗

开了「手机直连」（局域网）之后，**同一个 WiFi 下的任何人都能敲这个门** ✓
密码错了回 401 ✓ 然后**可以接着敲，无限次** ✗
密码默认是 32 字符随机串（撞不上 ✓）**但用户自己可以改** ✓（接口只拦"至少 8 位"✗
⇒ 8 位纯数字那种，不设防爆破几下就试出来了 ✓）

★ 而 webhook 那边**早就有**防爆破 ✓（`_hook_rate_ok` / `_hook_record_fail`：
"连错 5 次冷却 60 秒"、"**限流先于比对**，不给探测机会"✓）——
  ⇒ 本项就是把这套思路**补到登录口上** ✓（不另立一套口径 ✗）

## 三条设计（每条都在防"把自家人关在门外"✗）

1. **限流先于比对** ✓ —— 冷却期内一律 429，连"密码对不对"都不说 ✓
2. **回环（本机）不限流** ✓ —— 能走回环的**已经在这台机器上**了 ✓
   它本来就能直接读 `config.json` 里的密码 ✗ 拦它没意义 ✓
   而**把你自己在电脑上打错密码变成"锁门 60 秒"**才是真添乱 ✓
   （局域网来的人 IP 不是回环 ⇒ 照拦不误 ✓ 该防的一个没漏 ✓）
3. **输对了就清账** ✓ —— 不然"昨天错了 9 次"会一直压着 ✓ 今天差一次就锁 ✓ 不合理 ✓

## 还有一处**界面骗人**（顺手修了 ✓）

前端登录框原来**不管什么原因**都只弹一句"**密码不正确**" ✗ ——
被限流（429）时那是**假话** ✓ 用户会以为密码打错了、接着敲 ✓ **越敲锁得越久** ✓
⇒ 现在把后端原话显示出来 ✓（"密码错了太多次，请等 47 秒后再试"✓）
"""
from __future__ import annotations

import pathlib
import time

import pytest
from fastapi.testclient import TestClient

from app import login_guard as LG
from app import main as m


@pytest.fixture(autouse=True)
def _clean():
    LG.reset_all()
    yield
    LG.reset_all()


# ═══ ① 限流本身（纯函数级，不依赖 HTTP）═══

def test_loopback_is_never_locked_out():
    """★★ **本机永远不锁** ✓ —— 打错密码不该把你自己关在门外 ✓
    （能走回环的已经在这台机器上了 ✓ 本来就看得见 config.json 里的密码 ✓ 拦它没意义 ✓）"""
    for _ in range(LG.FAIL_LIMIT * 3):
        LG.record_fail("127.0.0.1")
    assert LG.retry_after("127.0.0.1") == 0.0
    for ip in ("::1", "127.0.0.5", "localhost"):
        for _ in range(LG.FAIL_LIMIT + 1):
            LG.record_fail(ip)
        assert LG.retry_after(ip) == 0.0, ip


def test_remote_ip_gets_locked_after_the_limit():
    """★ 局域网来的人：连错到上限 ⇒ **锁门** ✓ 而且告诉他还差多久 ✓。"""
    ip = "192.168.1.50"
    for i in range(LG.FAIL_LIMIT - 1):
        LG.record_fail(ip)
        assert LG.retry_after(ip) == 0.0, f"才错 {i + 1} 次就锁了 ⇒ 太严 ✗"
    LG.record_fail(ip)                      # 第 FAIL_LIMIT 次 ⇒ 触发 ✓
    wait = LG.retry_after(ip)
    assert 0 < wait <= LG.COOLDOWN_S, f"锁了但时间不对：{wait}"


def test_success_clears_the_record():
    """★ 输对了就把账清了 ✓ —— 不然昨天的错会一直压着，今天差一次就锁 ✓ 那不合理 ✓。"""
    ip = "192.168.1.51"
    for _ in range(LG.FAIL_LIMIT - 1):
        LG.record_fail(ip)
    LG.record_ok(ip)
    for _ in range(LG.FAIL_LIMIT - 1):
        LG.record_fail(ip)
    assert LG.retry_after(ip) == 0.0, "成功没清账 ⇒ 用户被上一轮的错压着 ✗"


def test_failures_expire_after_the_cooldown():
    """★ 冷却期过了 ⇒ 自动放行 ✓（不需要重启后端 ✓ 也不会把人永久锁住 ✓）。"""
    ip = "192.168.1.52"
    for _ in range(LG.FAIL_LIMIT + 2):
        LG.record_fail(ip)
    assert LG.retry_after(ip) > 0
    # 把失败时刻往前挪（= 假装过了一分钟）✓
    LG._FAILS[ip] = [t - LG.COOLDOWN_S - 1 for t in LG._FAILS[ip]]
    assert LG.retry_after(ip) == 0.0, "过了一分钟还没放行 ⇒ 会把人永久锁在外面 ✗"


def test_tracked_ips_are_bounded():
    """★ 内存里记的来源数**有上限** ✓ —— 不然"一堆来源"本身就是个内存攻击面 ✗。"""
    for i in range(LG.MAX_TRACKED + 50):
        LG.record_fail(f"10.0.{i // 250}.{i % 250}")
    assert len(LG._FAILS) <= LG.MAX_TRACKED + 1, f"记了 {len(LG._FAILS)} 条 ⇒ 没上限 ✗"


# ═══ ② 真走一遍 HTTP（局域网模式）—— 限流必须**真发生** ═══

@pytest.fixture()
def lan(monkeypatch):
    """把后端装成"手机直连开着"，密码是 test-token ✓（照 [tests 里的既有做法] ✓）。"""
    monkeypatch.setattr(m, "_BOUND_HOST", "0.0.0.0")
    monkeypatch.setattr(m.cfg.server, "access_token", "test-token", raising=False)
    assert m._lan_mode() is True
    return "test-token"


def _remote_client():
    """★ 客户端 IP **不是回环** ⇒ 才会被测到限流 ✓
    （TestClient 默认 client 是 `testclient` ⇒ login_guard 把它当回环放行 ✓
     所以这里显式指一个局域网 IP ✓）。"""
    return TestClient(m.app, base_url="http://127.0.0.1:8642", client=("192.168.1.77", 51234))


def test_login_probe_locks_out_after_too_many_wrong_passwords(lan):
    """★★ **登录探测口**（中间件豁免的那条）也必须限流 ✓ ——
    不然整条中间件都加了限流、唯独最像登录口的这条没加 ✓ 那就白做了 ✓。

    回滚实验：把 `auth_check` 里那两行 login_guard 调用去掉 ⇒ 本组必红 ✓
    """
    with _remote_client() as c:
        for i in range(LG.FAIL_LIMIT):
            r = c.get("/api/v1/auth/check", params={"token": f"wrong-{i}"})
            assert r.status_code == 401, f"第 {i + 1} 次应该是 401，得到 {r.status_code}"
        r = c.get("/api/v1/auth/check", params={"token": "wrong-again"})
        assert r.status_code == 429, f"错够次数了还没限流 ✗（{r.status_code}）"
        assert "等" in r.json()["detail"] and "防爆破" in r.json()["detail"], r.json()
        assert r.headers.get("retry-after"), "没给 Retry-After ⇒ 客户端不知道该等多久 ✗"
        # ★ 限流期内**连正确密码也不放** ✓ —— 就是不给"猜对了"的探测机会 ✓
        r2 = c.get("/api/v1/auth/check", params={"token": lan})
        assert r2.status_code == 429, "限流期内正确密码竟然能进 ⇒ 爆破方还能靠这个判断对错 ✗"


def test_the_main_api_surface_is_protected_too(lan):
    """★ 数据面（`/api/*` 的中间件那道）也要限流 ✓ —— 那才是爆破方真正会打的地方 ✓。"""
    with _remote_client() as c:
        for _ in range(LG.FAIL_LIMIT):
            assert c.get("/api/v1/tasks", params={"token": "nope"}).status_code == 401
        r = c.get("/api/v1/tasks", params={"token": "nope"})
        assert r.status_code == 429, f"数据面没限流 ✗（{r.status_code}）"


def test_correct_password_still_works_before_the_limit(lan):
    """★ 没到上限时，正确密码照常进 ✓（别把门做成一撞就锁 ✗）。"""
    with _remote_client() as c:
        assert c.get("/api/v1/auth/check", params={"token": "wrong"}).status_code == 401
        r = c.get("/api/v1/auth/check", params={"token": lan})
        assert r.status_code == 200 and r.json()["ok"] is True, r.text


def test_loopback_can_keep_trying(lan):
    """★ 本机（回环）**永远不锁** ✓ —— 你自己在电脑上打错不该被关在门外 ✓。"""
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        for _ in range(LG.FAIL_LIMIT + 3):
            assert c.get("/api/v1/auth/check", params={"token": "typo"}).status_code == 401
        r = c.get("/api/v1/auth/check", params={"token": lan})
        assert r.status_code == 200, "本机被自己锁住了 ✗（你自己的手滑不该变成锁门 ✓）"


# ═══ ③ 界面那处**假话**（顺手修的 ✓）═══

def test_the_login_box_no_longer_lies_about_the_reason():
    """★★ 原来不管什么原因都只弹"**密码不正确**"✗ —— 被限流时那是**假话** ✓
    用户会以为密码打错了、接着敲 ✓ **越敲锁得越久** ✓

    回滚实验：把那句 `alert('密码不正确')` 写回去 ⇒ 本组必红 ✓
    """
    tsx = (pathlib.Path(m.__file__).resolve().parents[2] / "frontend" / "src" / "App.tsx").read_text("utf-8")
    assert "r.status === 429" in tsx, "登录框没区分「被限流」和「密码错」✗"
    assert "detail || '密码不正确'" in tsx or "detail ||" in tsx, \
        "没把后端的原话显示出来 ⇒ 用户看不到「等多少秒」✗"
    # 反面：不许再有"不看原因、一律说密码不正确"的写法 ✗
    assert "} else {\n          alert('密码不正确');" not in tsx, "那句一刀切的提示还在 ✗"


def test_password_change_clears_the_lockout():
    """★ 换了密码 ⇒ 旧账不该继续压着 ✓（门锁换了 ✓ 上一把的错记录没道理还算数 ✓）。"""
    src = pathlib.Path(m.__file__).read_text("utf-8")
    i = src.find("async def set_access_token")
    assert i > 0
    seg = src[i : i + 1400]
    assert "login_guard.reset_all()" in seg, \
        "改密码没有清空失败记录 ⇒ 用户改完密码还得再等一分钟 ✓ 说不通 ✓"


def test_guard_is_wired_into_both_places():
    """★ 两处都要接 ✓（中间件 + 登录探测口 ✓）——"写了不等于接上了"✓ 本仓老教训 ✓。"""
    src = pathlib.Path(m.__file__).read_text("utf-8")
    assert src.count("login_guard.retry_after(") >= 2, "限流只接了一处 ✗"
    assert src.count("login_guard.record_fail(") >= 2, "记账只接了一处 ✗"
    # ★ 顺序也要对：**限流先于比对** ✓（先把人拦在门外，才轮得到"密码对不对"✓）
    i_wait = src.find("login_guard.retry_after(_ip_lan)")
    i_cmp = src.find("_hmac_g.compare_digest(str(token)")
    assert 0 < i_wait < i_cmp, "顺序反了 ⇒ 等于先告诉爆破方'密码错了'再限流 ✗"


def test_guard_matches_the_webhook_style():
    """★ 与 webhook 那处**同一套思路** ✓（连错到上限 ⇒ 冷却 ✓），别一边 5 次一边 99 次 ✗。"""
    src = pathlib.Path(m.__file__).read_text("utf-8")
    assert "_HOOK_FAIL_LIMIT = 5" in src, "webhook 那处的口径变了？确认一下 ✓"
    assert 3 <= LG.FAIL_LIMIT <= 20, f"登录限流阈值 {LG.FAIL_LIMIT} 不像人定的 ✗"
    assert 30 <= LG.COOLDOWN_S <= 300, f"冷却 {LG.COOLDOWN_S}s 不像人定的 ✗"
    assert time is not None
