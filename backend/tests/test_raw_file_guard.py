"""产物预览的安全护栏 —— P2-7。

**修复前的两个真实问题**（第一轮评估发现，一直未修）：
1. 越界访问 `/files/raw` 时 `PermissionError` 无人接 → 冒泡成 **500**，而不是干脆的 403；
2. `FileResponse` 按扩展名猜 Content-Type → **Agent 生成的 HTML 以同源方式直接渲染**，
   里面的 `<script>` 拿到完整 API 权限（读任务、发命令、改配置）
   —— **本地存储型 XSS**：只要 Agent 被外部内容诱导写出一个恶意 HTML 就够了。

**修法**：可安全渲染的类型内联（带 `nosniff` + `CSP: sandbox`），其余强制下载。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import _raw_response, app

CLIENT: TestClient | None = None  # fixture 内 global 赋值


@pytest.fixture(scope="module", autouse=True)
def _lifespan_client():
    """审计 §7.5：裸 TestClient 不触发 lifespan——看门狗/自动化/群回流装配路径
    完全没进测试。改为 module 级 with 上下文，启动/关闭钩子真实执行。"""
    global CLIENT
    with TestClient(app, base_url="http://127.0.0.1:8642") as c:
        CLIENT = c
        yield


def _mk(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_text("<script>fetch('/api/v1/settings')</script>", encoding="utf-8")
    return p


# ---------------- 越界必须是 403，不是 500 ----------------


def test_out_of_workspace_raw_access_is_403_not_500():
    """★ 回归：旧实现这里返回 500（未处理的 PermissionError）。"""
    r = CLIENT.get(
        "/api/v1/tasks/task_20260930_p2t7/files/raw",
        params={"path": "../../../../../Windows/win.ini"},
    )
    assert r.status_code == 403, f"越界应当 403，实际 {r.status_code}"
    assert "越界" in r.json()["detail"]


# ---------------- HTML 必须被 CSP sandbox 关进笼子 ----------------


def test_html_preview_is_sandboxed_and_nosniff(tmp_path):
    resp = _raw_response(_mk(tmp_path, "index.html"))
    headers = {k.lower(): v for k, v in resp.headers.items()}
    assert headers.get("content-security-policy") == "sandbox", "HTML 预览必须带 CSP sandbox"
    assert headers.get("x-content-type-options") == "nosniff"
    assert "attachment" not in headers.get("content-disposition", ""), "HTML 仍应能内联预览"


def test_svg_is_also_sandboxed(tmp_path):
    """SVG 里也能塞 <script>，同样要关进 sandbox。"""
    resp = _raw_response(_mk(tmp_path, "logo.svg"))
    headers = {k.lower(): v for k, v in resp.headers.items()}
    assert headers.get("content-security-policy") == "sandbox"


def test_images_stay_inline(tmp_path):
    resp = _raw_response(_mk(tmp_path, "shot.png"))
    headers = {k.lower(): v for k, v in resp.headers.items()}
    assert headers.get("x-content-type-options") == "nosniff"
    assert "attachment" not in headers.get("content-disposition", "")


# ---------------- 可执行/未知类型必须强制下载 ----------------


def test_script_and_binary_types_are_forced_to_download(tmp_path):
    for name in ("evil.js", "evil.mjs", "payload.exe", "run.bat", "x.ps1", "archive.zip", "noext"):
        resp = _raw_response(_mk(tmp_path, name))
        headers = {k.lower(): v for k, v in resp.headers.items()}
        assert "attachment" in headers.get("content-disposition", ""), f"{name} 必须强制下载"
        assert headers.get("content-type", "").startswith("application/octet-stream"), name


def test_every_response_carries_nosniff(tmp_path):
    for name in ("a.html", "a.png", "a.js", "a.unknownext"):
        resp = _raw_response(_mk(tmp_path, name))
        headers = {k.lower(): v for k, v in resp.headers.items()}
        assert headers.get("x-content-type-options") == "nosniff", f"{name} 缺少 nosniff"
