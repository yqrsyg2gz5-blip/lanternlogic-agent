# -*- coding: utf-8 -*-
"""★★ bug A：**朗读不说话**（用户："以前好用，现在就不好用了" ✗）—— 2026-10-07 真定位 + 钉死。

## 现场（本班真点按钮 + 真抓包量出来的 ✓ 不是猜 ✗）

```
POST /api/v1/tasks/<id>/tts        → 200 ✓   （后端合成本来就是好的 ✓）
GET  /api/v1/tasks/<id>/tts-audio/tts_6d53_00.mp3
     [🎵媒体]                       → 401 ✗   （**浏览器 <audio> 去取的时候** ✓）
控制台：Failed to load resource: the server responded with a status of 401 (Unauthorized)
后端日志：`"GET /api/v1/tasks/…/tts-audio/tts_6d53_00.mp3 HTTP/1.1" 401 Unauthorized`
```

## 根因（一句话）

**播放地址没带访问密码** ✗。

· `<audio>` / `<img>` 这类**浏览器自己发起的请求带不了自定义头** ✗ ⇒
  `absorbTokenFromUrl()` 与 `window.fetch` 包装器**都够不着它们** ✓
· 而**手机直连（局域网）模式**下后端要求**所有 `/api/*` 都带 token** ✓
  ⇒ 少了 `?token=` 就是 401 ✓ —— 跟"密码打错"是一模一样的待遇 ✓。
· 项目里早有统一函数 `authedUrl()` ✓（产物图片/预览一直在用 ✓）——
  **唯独朗读这一处漏了** ✗ ⇒ "以前好用"（开手机直连之前）"现在不好用"✓ 完全对得上 ✓。

## 两条规矩（本文件钉住）

1. **播音频的地址必须在 api 层就补好密码** ✓（下一个写朗读的人**没法忘** ✓）
2. **组件里不许再有裸 `fetch('/api/…')`** ✓（这一类 bug 的温床 ✓ 见 C12 那段注释 ✓）
"""
from __future__ import annotations

import pathlib
import re

from fastapi.testclient import TestClient

from app import main as m

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SRC = _ROOT / "frontend" / "src"
_API = (_SRC / "api.ts").read_text("utf-8")
_TV = (_SRC / "components" / "TaskView.tsx").read_text("utf-8")


# ═══ ① 前端：地址在 api 层补密码（根治）═══

def test_tts_stream_urls_go_through_authed_url():
    """★ 后端回的音频地址**必须过 `authedUrl`** ✓ —— 这就是"朗读不出声"的那一步 ✓。"""
    assert "async ttsSpeakStream(" in _API, "朗读的收流入口不见了 ✗"
    assert "onUrl(authedUrl(j.url))" in _API, \
        "音频地址没补访问密码 ✗ ⇒ 局域网模式下浏览器取它必然 401（就是这次用户遇到的）"
    # 反面：不许把后端给的地址**原样**推给播放器（那正是修之前的样子）
    assert "onUrl(j.url)" not in _API, "还有一条路把没带密码的地址直接推出去 ✗"


def test_read_aloud_does_not_fetch_the_backend_itself():
    """★ 朗读那条路**不许自己 `fetch`** ✓ —— 收流 + 补密码统一在 api 层 ✓。

    回滚实验：把 `api.ttsSpeakStream(...)` 换回原来的裸 `fetch(...)` ⇒ 必须变红 ✓。
    """
    seg = _TV.split("const speakText = ")[1].split("const lastAssistantText = ")[0]
    code = "\n".join(ln for ln in seg.splitlines() if not ln.strip().startswith("//"))
    assert "api.ttsSpeakStream(" in code, "朗读没收流入口 ✗"
    assert "fetch(" not in code, "朗读还在自己 fetch（带密码这件事就又落到调用点头上了）✗"
    # 反面：光"写了 ttsSpeakStream"不算 ✓ 得真拿它的结果去播 ✓
    assert "const urls: string[] = [];" in code, "urls 队列没了 ✗"


# ═══ ② 组件里不许再有裸 fetch（根治"这一类"）═══

# 故意保留的例外（**不是漏改，是有原因的** ✓）：
#   · App.tsx 那两处问的就是"密码对不对"本身：一处带 localStorage 里**已有的**那个，
#     一处带**正在试的**那个（而 request() 只会带 localStorage 那份 ⇒ 换成它反而测不出错密码 ✗）
_ALLOWED_BARE_FETCH = {"App.tsx"}


def test_no_component_fetches_the_api_by_hand():
    """扫全部组件 ✓：`fetch('/api/...')` 这种写法**一处都不许剩** ✓。

    为什么值得一条测试（P1-8 / C12 的老账 ✓）：
      每留一处，就多一个"**得自己记着带访问密码**"的地方 ✗ ——
      朗读这次栽的就是这个（旁边 7 处靠全局包装器兜着才没出事 ✓ 而 `<audio>` 兜不住 ✗）。
    """
    bad: list[str] = []
    for f in sorted((_SRC / "components").glob("*.tsx")):
        if f.name in _ALLOWED_BARE_FETCH:
            continue
        for i, ln in enumerate(f.read_text("utf-8").splitlines(), 1):
            if ln.strip().startswith(("//", "*", "/*")):
                continue                                   # 注释里提到不算 ✓
            if re.search(r"fetch\(\s*[`'\"]/api/", ln):
                bad.append(f"{f.name}:{i}  {ln.strip()[:80]}")
    assert not bad, "组件里还有裸 fetch('/api/...')（每留一处就多一个会忘带密码的地方）：\n" + "\n".join(bad)


# ═══ ③ 后端：真的是"不带密码就 401"（把这条现场钉住 ✓）═══

def test_audio_url_without_token_is_401_with_token_is_200(tmp_path, monkeypatch):
    """★★ 把本班量到的那一枪**原样复现** ✓ —— 这是整个 bug 的现场 ✓。

    局域网模式（绑 0.0.0.0 + 设了访问密码）下：
      · `<audio src="/api/v1/tasks/<id>/tts-audio/x.mp3">`（**不带密码**）⇒ 401 ✗
      · 同一个地址带上 `?token=`                                  ⇒ 200 ✓
    ⇒ 所以"后端没坏、前端也没坏，坏在**播的时候没带密码**"✓。
    """
    task_id = "task_ttstest_0001"
    d = m._TTS_DIR / task_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "tts_abcd_00.mp3").write_bytes(b"ID3\x03\x00\x00\x00" + b"\x00" * 32)   # 够它返回就行 ✓

    monkeypatch.setattr(m, "_BOUND_HOST", "0.0.0.0")          # 假装是"手机直连"那次启动 ✓
    monkeypatch.setattr(m.cfg.server, "access_token", "tok-for-test", raising=False)
    assert m._lan_mode() is True, "没进局域网模式 ⇒ 这条测试就白测了 ✗"

    url = f"/api/v1/tasks/{task_id}/tts-audio/tts_abcd_00.mp3"
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r_no = c.get(url)                                      # = 浏览器 <audio> 的真实处境 ✓
        r_yes = c.get(url + "?token=tok-for-test")             # = authedUrl 补过之后 ✓
    assert r_no.status_code == 401, f"不带密码竟然能取到？（{r_no.status_code}）——那这次就不是这个原因 ✓"
    assert r_yes.status_code == 200, f"带对了密码还取不到 ⇒ 另有毛病 ✗（{r_yes.status_code}）"


def test_authed_url_helper_still_appends_token():
    """`authedUrl` 是补密码那一步 ✓ —— 它自己坏了，朗读会**再次**哑掉 ✓。"""
    assert "export function authedUrl(" in _API, "统一补密码的函数不见了 ✗"
    assert "token=${encodeURIComponent(tok)}" in _API, "补密码的写法变了（不再是 ?token=）✗"
