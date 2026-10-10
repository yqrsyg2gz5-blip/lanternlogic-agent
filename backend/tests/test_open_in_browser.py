# -*- coding: utf-8 -*-
"""「用系统浏览器打开」这条路 —— 为什么要有它、以及它不许破坏什么 ✓

背景（2026-10-09 用户实测 ✓）：
  交付的 index.html 在**应用预览里**点任何按钮都没反应 ✗
  根因不是页面坏 ✗ 而是预览通道**故意**加了 CSP sandbox ✓（防本地存储型 XSS ✓ 见 raw_file 注释 ✓）
  ⇒ 所以新增了 open-in-browser：交给**系统浏览器**用 file:// 打开 ✓
     · 脚本能跑 ✓（用户终于能真试 ✓）
     · 仍不是同源 ✓ ⇒ 拿不到本机 API 权限 ✓

本文件锁住三件事：
  ① 新通道**不许**绕过 _safe_path（越界必须 403 ✓）
  ② 新通道调起的是**系统打开器**，且只对**存在的文件**（不存在 ⇒ 404 ✓）
  ③ ★ 预览通道的**安全设计不许被这次改动放宽** ✗（CSP sandbox + nosniff 必须还在 ✓）
     —— 这条最重要 ✓：将来有人"顺手"为了让预览能点而删掉 CSP ✗ 必须当场变红 ✓

★ 客户端固定用 base_url=127.0.0.1 ✓（本仓惯例 ✓ 见 82 处同款 ✓）：
  接口令牌守卫只放行**回环**请求 ✓ 默认 base_url（testserver ✗）会被 403 挡掉 ✓
  ⇒ 不这么写的话，测试会**假通过**（403 被误当成"越界拦住了"✗ —— 我第一版就踩了这个 ✗）
"""
from __future__ import annotations

import pathlib

from fastapi.testclient import TestClient

from app import main as m


def _client() -> TestClient:
    return TestClient(m.app, base_url="http://127.0.0.1:8642")


def test_missing_file_is_404(tmp_path, monkeypatch):
    """文件不存在 ⇒ 404（用打桩把解析结果指到一个不存在的路径 ✓）"""
    monkeypatch.setattr(m, "_safe_path", lambda tid, rel: tmp_path / "nope.html", raising=False)
    r = _client().get("/api/v1/tasks/t1/open-in-browser", params={"path": "nope.html"})
    assert r.status_code == 404, r.text


def test_traversal_is_403(tmp_path):
    """★ 越界一律 403/404 —— 与预览通道**同一套判据** ✓（不许另开一条没护栏的路 ✗）

    ★ 这里**不复用** base_url 的坑：回环已经放行 ✓ 所以拿到的 403 只可能来自路径护栏 ✓
    """
    for bad in ("../../../windows/win.ini", "..%2f..%2fsecret.txt"):
        r = _client().get("/api/v1/tasks/t1/open-in-browser", params={"path": bad})
        assert r.status_code in (403, 404), f"{bad} 竟然没被拦住：{r.status_code} {r.text[:120]}"


def test_opens_with_the_system_opener(tmp_path, monkeypatch):
    """真调起系统打开器 ✓（打桩掉 ⇒ 测试不弹真浏览器窗口 ✓）"""
    f = tmp_path / "index.html"
    f.write_text("<html><script>1</script></html>", encoding="utf-8")
    monkeypatch.setattr(m, "_safe_path", lambda tid, rel: f, raising=False)
    called: list[str] = []
    monkeypatch.setattr(m.os, "startfile", lambda p: called.append(p), raising=False)

    r = _client().get("/api/v1/tasks/t1/open-in-browser", params={"path": "index.html"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert called == [str(f)], f"没调起系统打开器：{called}"
    assert "拿不到本机 API 权限" in body["detail"], "必须向用户说明它是安全的（非同源）✓"


def test_preview_channel_still_sandboxed():
    """★★ 最重要的一条：**预览通道的安全设计不许被放宽** ✗

    （本次改动的诱因就是"预览点不动"✗ ⇒ 最容易犯的错就是把 CSP 去掉 ✓
      ⇒ 那会让 Agent 生成的恶意 HTML 拿到完整 API 权限 ✗ 这条测试就是拦它的 ✓）
    """
    src = pathlib.Path(m.__file__).resolve().read_text(encoding="utf-8")
    assert "Content-Security-Policy" in src and "sandbox" in src, \
        "预览通道的 CSP sandbox 不见了 ✗（本地存储型 XSS 会复活）"
    assert "X-Content-Type-Options" in src and "nosniff" in src, \
        "nosniff 不见了 ✗（浏览器会开始猜类型 ✓ 危险）"
