# -*- coding: utf-8 -*-
"""拼接视频的编码器回退（用户 2026-10-10 实测引出来的）。

现场（都是实跑量出来的，不是推测）：
    · 本机那份 ffmpeg（GPT-SoVITS 自带 n4.3.2）编译时 `--disable-libx264 --disable-libx265`
      ⇒ `Unknown encoder 'libx264'` ⇒ **写死 libx264 的 video_join 整个用不了** ✗
    · 同一个 ffmpeg 的 `mpeg4` 好用 ✓（实测产出 6133 字节的 mp4）
    · `h264_nvenc` 在这台机器上起不来 ✓（ffmpeg 4.3 的 nvenc 头太老，认不了 5070 Ti；
      原话 `Cannot get the preset configuration: unsupported param` / `Error initializing output stream`）

判据（本文件盯三条）：
    ① 能用的优先（libx264 在就用它）✓
    ② **任何一个编码器失败就换下一个** ✓（不是一句"拼接失败"就完 ✗）
    ③ 全都不行时，报错里要**逐个列出试过什么** ✓ 而且回报里写清最后用了哪个编码器 ✓
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def _executor():
    from app.executors.local import LocalExecutor
    return LocalExecutor(SimpleNamespace(
        type="local", workspace_root=".", allowed_dirs=[], timeout_seconds=60,
        sandbox="off", shell=None, cfg_shell="", search_url="", searxng_url="",
        browser_channel="msedge", comfyui_url="http://127.0.0.1:9",
        image_checkpoint="x", sandbox_image="python:3.12-slim",
        sandbox_network="none", sandbox_memory="512m",
    ))


class _FakeProc:
    def __init__(self, rc: int, out: bytes):
        self.returncode = rc
        self._out = out

    async def communicate(self):
        return self._out, None

    def kill(self):                      # 超时分支会调它
        pass


def _install_fake_ffmpeg(monkeypatch, behaviour: dict) -> list:
    """behavior = {编码器: (returncode, 输出)}；ffprobe 单独放行（探时长不阻塞交付 ✓）。"""
    import app.executors.local as L
    seen: list[str] = []

    async def fake_exec(*args, **kwargs):
        if args and args[0] == "ffprobe":
            return _FakeProc(0, b"12.5")
        enc = args[args.index("-c:v") + 1] if "-c:v" in args else "?"
        seen.append(enc)
        rc, out = behaviour.get(enc, (1, f"Unknown encoder '{enc}'".encode()))
        return _FakeProc(rc, out)

    monkeypatch.setattr(L.asyncio, "create_subprocess_exec", fake_exec)
    return seen


async def test_join_falls_back_when_libx264_is_missing(monkeypatch, tmp_path):
    """★ 核心：libx264 没有 ⇒ 必须自动换到**下一个能用的**（本机就是这种机器）。"""
    for n in ("a.mp4", "b.mp4"):
        (tmp_path / n).write_bytes(b"x")
    seen = _install_fake_ffmpeg(monkeypatch, {
        "libx264": (1, b"Unknown encoder 'libx264'"),
        "h264_nvenc": (1, b"Error initializing output stream"),
        "h264_qsv": (1, b"Error initializing output stream"),
        "h264_amf": (1, b"Error initializing output stream"),
        "h264_mf": (1, b"Error initializing output stream"),
        "mpeg4": (0, b"ok"),
    })
    msg = await _executor()._video_join(tmp_path, ["a.mp4", "b.mp4"], "out.mp4")
    assert seen[-1] == "mpeg4", f"没退到兜底编码器：{seen}"
    assert "mpeg4" in msg, f"回报里没写用了哪个编码器：{msg}"


async def test_join_uses_libx264_first_when_available(monkeypatch, tmp_path):
    """反面：libx264 能用就**先用它** ✓（不许一上来就退到画质更差的兜底）。"""
    (tmp_path / "a.mp4").write_bytes(b"x")
    seen = _install_fake_ffmpeg(monkeypatch, {"libx264": (0, b"ok")})
    msg = await _executor()._video_join(tmp_path, ["a.mp4"], "out.mp4")
    assert seen == ["libx264"], f"能用 libx264 却先试了别的：{seen}"
    assert "libx264" in msg


async def test_join_lists_every_encoder_when_all_fail(monkeypatch, tmp_path):
    """全都不行时：报错要**逐个列出试过什么**（而不是干说一句"拼接失败"✗）。"""
    (tmp_path / "a.mp4").write_bytes(b"x")
    _install_fake_ffmpeg(monkeypatch, {})          # 所有编码器一律失败
    with pytest.raises(RuntimeError) as e:
        await _executor()._video_join(tmp_path, ["a.mp4"], "out.mp4")
    msg = str(e.value)
    assert "libx264" in msg and "mpeg4" in msg, f"没把试过的编码器列出来：{msg}"
    assert "试过的编码器都不行" in msg, msg
