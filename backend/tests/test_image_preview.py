"""Phase 3 ⑦ 预览批（图片灯箱 + 工作区图片鉴权）的锚点。

这一批抓到两个真问题，都要钉住：
  ① **点图片只能跳新标签页** ⇒ 加全局灯箱（对话/附件/正文/产物面板通用）
  ② ★ **工作区图片其实一直是 401**：`<img src>` 发不了 `X-Auth-Token` 头，
     而 `/files/raw` 要鉴权 ⇒ 元素在、字节没下来（"看着有图，其实是空壳"）。
     修法是给这类"浏览器自己发起的请求"补 `?token=`（后端本来就支持，SSE 就是这么连的），
     并且**一处实现（api.ts authedUrl）、两处取文件地址都调它** —— 免得又出现
     "改了一处、另一处照旧"（本会话在语音输入上刚吃过这个亏）。
"""
from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SRC = _ROOT / "frontend" / "src"
_API = (_SRC / "api.ts").read_text("utf-8")
_EV = (_SRC / "components" / "EventItem.tsx").read_text("utf-8")
_ART = (_SRC / "components" / "ArtifactPanel.tsx").read_text("utf-8")
_MD = (_SRC / "components" / "Markdown.tsx").read_text("utf-8")
_OV = (_SRC / "components" / "PreviewOverlay.tsx").read_text("utf-8")
_APP = (_SRC / "App.tsx").read_text("utf-8")
_VERIFY = (_ROOT / "frontend" / "scripts" / "verify_image_preview.mjs").read_text("utf-8")


def test_raw_file_urls_carry_the_token():
    """★ 取文件地址必须走 authedUrl（否则 <img> 401，图是空壳）。"""
    assert "export function authedUrl(" in _API, "没有统一的带 token 地址助手"
    assert "token=${encodeURIComponent(tok)}" in _API, "authedUrl 没真的把 token 拼上去"
    assert "/[?&]token=/.test(url)" in _API, "没防重复拼 token"
    # 两处取文件地址都要调它（一处实现、两处调用）
    assert "return authedUrl(" in _EV, "对话附件/正文的取文件地址没带 token"
    assert "return authedUrl(" in _ART, "产物面板的取文件地址没带 token"


def test_lightbox_is_global_and_closes_every_way():
    assert "document.addEventListener('click'" in _OV, "灯箱没走全局委托（四处图片会漏）"
    assert ", true)" in _OV.split("document.addEventListener('click'")[1][:40], \
        "没用捕获阶段（外层是链接，会先跳走）"
    assert "e.key === 'Escape'" in _OV, "Esc 关不掉"
    assert "onClick={() => setSrc(null)}" in _OV, "点遮罩关不掉"
    assert "在新标签打开" in _OV, "没有'在新标签打开'的退路"
    assert "<PreviewOverlay />" in _APP, "灯箱没挂到应用根上"


def test_all_four_image_sites_opt_in():
    assert 'className="chat-img" data-preview' in _EV, "对话附件缩略图没接灯箱"
    assert 'className="art-img" data-preview' in _ART, "产物面板的图没接灯箱"
    assert 'className="md-img" data-preview' in _MD, "正文 Markdown 图片没接灯箱"


def test_markdown_resolves_relative_image_paths_only():
    """相对路径要解析成工作区地址；http(s)/data:/绝对路径不许乱拼。"""
    assert "function isRelative(" in _EV, "没有'是不是相对路径'的判断"
    assert "resolveSrc" in _MD and "resolveSrc" in _EV, "正文图片没接解析器"
    assert 'x.startsWith("data:")' in _ART or "data:" in _ART, "产物面板的解析没排除 data:"
    for bad in ("^([a-z]+:)?\\/\\/", "^[a-zA-Z]:[\\\\/]"):
        assert bad in _EV, f"isRelative 少了一条排除：{bad}"


def test_verification_proves_bytes_arrived_not_just_dom():
    assert "naturalWidth > 0" in _VERIFY, \
        "只断言元素存在会漏掉 401 空壳 —— 必须验'字节真的下来了'"
    # 验证脚本里是**正则字面量** `/files\/raw/`（斜杠被转义），别按裸串找
    assert r"/files\/raw/" in _VERIFY, "没断言取文件走的是 /files/raw"
    assert "Esc 能关掉灯箱" in _VERIFY and "点遮罩空白处也能关" in _VERIFY, \
        "灯箱的关闭路径没验全"
