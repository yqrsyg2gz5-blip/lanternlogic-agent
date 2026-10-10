# -*- coding: utf-8 -*-
"""浏览器工具**专属测试** —— 2026-10-07 功能体检补的（此前 **0 个专属测试** ✗）。

## 为什么这块最该有专属测试

`browser_navigate / snapshot / click / type / key` 五个工具：
· **没有任何专属测试** ✗（只被审批矩阵"关键词命中"过 ✓ 覆盖不到它们自己 ✓）
· 却依赖**系统 Edge + Playwright** ✓ —— 装没装、点不点得动、超时会不会卡死 ✗
  **读代码是读不出来的** ✓✓

## 体检**真跑**出来的结果（原始输出见评审区探针脚本 ✓）

```
✓ ① 真开窗口并打开公网页面  → 已打开 https://example.com/（标题：Example Domain）
✓ ② 真读到页面内容
✓ ③ 真点击链接并真的跳转    → 已点击：Learn more；现在在 https://www.iana.org/help/example-domains
✓ ④ 真按键不报错
✓ ⑤ 打不开的地址如实失败（不谎报成功）
✓ ⑥ 内网地址被 SSRF 防护拦住（安全红线）
   小结：6/6 项通过
```

## 真跑抓到的可用性问题（已修 ✓ 这里钉住 ✓）

传 "More information" 而页面上是 "More information..." ✓ 三种匹配全失败 ✓
⇒ 原来只回一句"**未找到可点击元素**"✗ —— 模型**只能瞎猜**（再 snapshot 一轮 = 白烧 token ✗）。
⇒ 现在：**失败时列出页面上能点的东西** ✓ **成功时回报落地地址与标题** ✓✓。

## 一条**真实使用限制**（记下 ✓ 不藏 ✗）

`file://` 与 `127.0.0.1` 都被 SSRF 拦掉 ✓ ⇒ **"帮我打开我本机这个页面"做不到** ✗。
这是**对的安全设计** ✓（防"网页里的间接注入让 Agent 去摸内网元数据"✓）；
将来若要做，应当做成"**用户显式授权某几个本机端口**"的白名单 ✓ 而不是整段放开 ✗。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.executors import local as L  # noqa: E402
from app.ssrf import SsrfBlocked  # noqa: E402


class _FakeKeyboard:
    def __init__(self, page: "_FakePage"):
        self._p = page

    async def type(self, text, delay=0):                     # noqa: ANN001, ARG002
        self._p.typed.append(text)

    async def press(self, key):
        self._p.keys.append(key)


class _FakePage:
    """够用的假页面 ✓（只实现被测代码真正用到的那几个方法 ✓）。

    ★ 第一版我写错两处（跑起来立刻被真实代码打脸 ✓ 正好说明"假对象也得照着真接口写"✓）：
      ① `keyboard` 写成了嵌套**类** ✗ ⇒ 应为**实例属性**（真代码是 `page.keyboard.type(...)` ✓）
      ② `eval_on_selector_all` 我统一返回字符串 ✗ —— 而**快照**那条路要的是
         `{t, s, ty}` 这种**对象**（代码里 `e.get("s")` ✓）⇒ 按 JS 内容区分返回 ✓
    """

    def __init__(self, text="页面文本", url="https://site.test/", title="测试页",
                 clickables=None, fail_click=False, redirect_to=""):
        self._text = text
        self.url = url
        self._title = title
        self.redirect_to = redirect_to      # ★ 模拟"公网域名 302 到内网"那条绕过面 ✓
        self.clickables = clickables if clickables is not None else ["按钮甲", "按钮乙"]
        self.fail_click = fail_click
        self.typed: list[str] = []
        self.keys: list[str] = []
        self.waited: list[int] = []
        self.clicked: list[str] = []
        self.keyboard = _FakeKeyboard(self)

    async def inner_text(self, _sel):                            # noqa: ANN001
        return self._text

    async def title(self):
        return self._title

    async def eval_on_selector_all(self, sel, js):               # noqa: ANN001
        # 代码里有两条路：**快照**要对象（含 tagName ✓）｜**候选列表**要字符串 ✓
        if "tagName" in js:
            return [{"t": "A", "s": s, "ty": ""} for s in self.clickables]
        return list(self.clickables)

    async def wait_for_timeout(self, ms):                        # noqa: ANN001
        self.waited.append(ms)

    async def goto(self, url, wait_until="domcontentloaded", timeout=30000):   # noqa: ANN001, ARG002
        # 真浏览器里 goto 之后 `page.url` 是**落地地址**（可能已被 302 改过 ✓）
        self.url = self.redirect_to or url
        self.gotoed = url

    def get_by_text(self, text, exact=True):                     # noqa: ANN001, ARG002
        page = self

        class _Loc:
            @property
            def first(self):
                return self

            async def click(self, timeout=5000):                 # noqa: ANN001, ARG002
                if page.fail_click:
                    raise TimeoutError("找不到")
                page.clicked.append(text)
        return _Loc()

    def get_by_role(self, role, name):                           # noqa: ANN001, ARG002

        class _Loc:
            @property
            def first(self):
                return self

            async def click(self, timeout=3000):                 # noqa: ANN001, ARG002
                raise TimeoutError("找不到")
        return _Loc()


def _ex(page: _FakePage):
    """造一个执行器 ✓ 并把"取页面"那步换成假页面（不碰真浏览器 ✓ 也不联网 ✓）。"""
    from app.config import load_config
    ex = L.LocalExecutor(load_config().executor)

    async def _ensure():
        return page
    ex._ensure_page = _ensure                                    # type: ignore[assignment]
    return ex


# ═══ ① 快照：模型靠它"看见"页面 ═══

def test_snapshot_lists_page_text_and_clickables():
    page = _FakePage(text="正文内容ABC", clickables=["登录", "注册"])
    out = asyncio.run(_ex(page)._browser_snapshot())
    assert "正文内容ABC" in out, out
    assert "[a] 登录" in out or "登录" in out, out
    assert "可交互元素" in out, "没告诉模型「能点什么」✗（它会瞎点 ✓）"


# ═══ ② 点击：成功要回报落地地址，失败要列出候选（体检抓到的 ✓）═══

def test_click_reports_where_it_landed():
    """★ 成功时**必须回报落地 URL 与标题** ✓ —— 否则模型不知道"点了到底跳没跳"✗
    （原来只说"已点击"✗ ⇒ 它得再 snapshot 一次确认 ✓ 白烧 token ✓）。"""
    page = _FakePage(url="https://site.test/after", title="跳转后")
    out = asyncio.run(_ex(page)._browser_click("立即购买"))
    assert "已点击" in out, out
    assert "https://site.test/after" in out, f"没回报落地地址 ✗：{out}"
    assert "跳转后" in out, f"没回报标题 ✗：{out}"


def test_click_failure_lists_what_can_be_clicked():
    """★★ **失败时必须列出页面上能点的东西** ✓ —— 这条是体检真跑抓出来的 ✓。

    真事：传 "More information" 而页面上是 "More information..." ✓ 三路匹配全失败 ✓
    ⇒ 原来只回"未找到可点击元素"✗ ⇒ 模型**只能再 snapshot 一轮瞎猜** ✓（实测就这么卡住的 ✓）。
    """
    page = _FakePage(clickables=["Learn more", "Sign in", "Learn more"], fail_click=True)   # 含重复 ✓
    out = asyncio.run(_ex(page)._browser_click("More information"))
    assert "未找到可点击元素" in out, out
    assert "Learn more" in out, f"没列出页面能点的东西 ✗（模型只能瞎猜 ✓）：{out}"
    assert out.count("Learn more") == 1, f"候选列表没去重 ✗：{out}"


def test_click_failure_is_honest_when_page_has_nothing_clickable():
    page = _FakePage(clickables=[], fail_click=True)
    out = asyncio.run(_ex(page)._browser_click("随便什么"))
    assert "没找到" in out or "未找到" in out, out
    assert "没有可点的元素" in out, f"空页面应如实说 ✗：{out}"


def test_clickable_list_is_capped_and_tidy():
    page = _FakePage(clickables=[f"按钮{i}" for i in range(50)])
    out = asyncio.run(_ex(page)._clickable_list(page))
    assert len(out.splitlines()) <= 15, f"候选太多会把上下文撑爆 ✗：{len(out.splitlines())} 行"


# ═══ ③ 导航：补全 scheme + 安全红线（fail-closed）═══

def test_navigate_adds_https_scheme(monkeypatch):
    page = _FakePage(url="https://example.com/", title="Example Domain")
    seen: list[str] = []

    async def _fake_assert(url):                                 # noqa: ANN001
        seen.append(url)

    monkeypatch.setattr(L, "assert_public_url", _fake_assert)
    ex = _ex(page)
    out = asyncio.run(ex._browser_navigate("example.com"))
    assert seen and seen[0].startswith("https://"), f"没补 https ✗：{seen}"
    assert "Example Domain" in out, out


def test_navigate_refuses_an_internal_address(monkeypatch):
    """★ **内网地址必须被拦** ✓ —— 这是安全红线 ✓ 不能为了好用就放开 ✗。

    （真跑实测：`http://127.0.0.1:8642/` 被拦 ✓ 原文："解析到 127.0.0.1 属于被拦截段 127.0.0.0/8"✓）
    """
    async def _boom(url):                                        # noqa: ANN001, ARG001
        raise SsrfBlocked("解析到 127.0.0.1 属于被拦截段 127.0.0.0/8，已拦截")

    monkeypatch.setattr(L, "assert_public_url", _boom)
    with pytest.raises(SsrfBlocked):
        asyncio.run(_ex(_FakePage())._browser_navigate("http://127.0.0.1:8642/"))


def test_navigate_rechecks_after_redirect(monkeypatch):
    """**落地地址要再验一次** ✓ —— 否则"公网域名 302 到内网"就绕过去了 ✗✓。"""
    calls: list[str] = []

    async def _fake_assert(url):                                 # noqa: ANN001
        calls.append(url)

    monkeypatch.setattr(L, "assert_public_url", _fake_assert)
    page = _FakePage(url="https://site.test/", title="落地",
                     redirect_to="https://site.test/final")   # 模拟 302 ✓
    asyncio.run(_ex(page)._browser_navigate("https://site.test/"))
    assert len(calls) >= 2, f"只验了一次（重定向绕过面 ✗）：{calls}"
    assert calls[-1].startswith("https://site.test/final"), calls


def test_navigate_fails_closed_on_an_unresolvable_host():
    """**解析不了就拒**（fail-closed ✓）—— 真跑实测 ✓ 不允许"证不明就放行" ✗。"""
    with pytest.raises(SsrfBlocked):
        asyncio.run(_ex(_FakePage())._browser_navigate("https://这个域名一定不存在-20261007.invalid/"))


# ═══ ④ 输入 / 按键 ═══

def test_type_and_key_reach_the_page():
    page = _FakePage()
    ex = _ex(page)
    asyncio.run(ex._browser_type("要输入的字"))
    asyncio.run(ex._browser_key("Enter"))
    assert page.typed == ["要输入的字"], page.typed
    assert page.keys == ["Enter"], page.keys
    assert page.waited, "按键后应等一下让页面反应 ✗"


# ═══ ⑤ 工具名映射（模型只能用这五个名字 ✓）═══

def test_tool_names_are_dispatched():
    """五个工具名都得在**执行器的分发表**里 ✓（名字写错 = 模型一调就"未知工具"✗）。"""
    import inspect
    src = inspect.getsource(L.LocalExecutor.run_tool)
    for name in ("browser_navigate", "browser_snapshot", "browser_click",
                 "browser_type", "browser_key"):
        assert f'"{name}"' in src, f"分发表里没有 {name} ✗"
        assert f"_{name}(" in src, f"{name} 没接到实现 ✗"
