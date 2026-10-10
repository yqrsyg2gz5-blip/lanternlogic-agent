"""语音包安装器供应链加固回归（K5）——钉哈希 / 哈希不符拒装 / zip 两道闸。

全部离线：下载用 file:// 协议与 monkeypatch，不发真实网络请求（规矩：不实际安装语音包）。
"""
from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import install_melotts as im  # noqa: E402


def _mk_file(p: Path, content: bytes) -> str:
    p.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


# ---------- sha256_file ----------

def test_sha256_file(tmp_path):
    sha = _mk_file(tmp_path / "a.bin", b"hello-k5")
    assert im.sha256_file(tmp_path / "a.bin") == sha


# ---------- download_verified ----------

def test_download_ok_when_hash_matches(tmp_path):
    src = tmp_path / "src.zip"
    sha = _mk_file(src, b"payload-bytes")
    dest = tmp_path / "out.zip"
    im.download_verified([src.as_uri()], dest, sha)
    assert dest.read_bytes() == b"payload-bytes"


def test_download_refuses_on_hash_mismatch(tmp_path):
    src = tmp_path / "src.zip"
    _mk_file(src, b"payload-bytes")
    dest = tmp_path / "out.zip"
    with pytest.raises(im.InstallRefused, match="哈希不符"):
        im.download_verified([src.as_uri()], dest, "0" * 64)
    assert not dest.exists(), "哈希不符的制品必须删除，不得留在磁盘"


def test_download_refuses_to_try_next_source_after_mismatch(tmp_path):
    """★ 安全语义：第一个源哈希不符必须【当场拒】，不能'换个源再试'
    （否则投毒面放大成'哪个源能过用哪个'）。"""
    calls = []

    class FakeResp:
        def __init__(self):
            self._reads = 0
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n=-1):
            self._reads += 1
            return b"bad" if self._reads == 1 else b""

    def fake_urlopen(req, timeout=0):
        calls.append(str(req.full_url if hasattr(req, "full_url") else req))
        return FakeResp()

    import urllib.request
    real = urllib.request.urlopen
    im.urllib.request.urlopen = fake_urlopen
    try:
        with pytest.raises(im.InstallRefused):
            im.download_verified(["http://evil/x.zip", "http://good/x.zip"],
                                 tmp_path / "o.zip", "f" * 64)
    finally:
        im.urllib.request.urlopen = real
    assert len(calls) == 1, f"哈希不符后不得尝试后续源，实际调用了 {calls}"


# ---------- K5 第 2 处：下载体积上限（哈希校验【之前】的磁盘防线） ----------
# 二十六轮第 7 批第 2 处现状（本班实测，原始输出）：旧实现
#   while True: chunk = r.read(1<<20); if not chunk: break; f.write(chunk)
# 只有 EOF 才停 —— 假流给 1GiB，【完整写入 1,073,741,824 字节】才因"哈希不符"停手，
# 全程 0.24s；被控/投毒的镜像（含用户可配的 AGENT_SHELL_TTS_MIRROR）可在哈希
# 校验之前写满磁盘。下面两组锚点：无 CL 的 chunked 流必须【在中途中断】，
# 有 CL 的超限声明必须【预检早退】（一个字节都不读）。

class _EndlessStream:
    """无 content-length 的假流：总数受控（不制造真 1GiB 内存）。"""

    def __init__(self, total: int):
        self.total = total
        self.sent = 0
        self.headers: dict[str, str] = {}   # 无 Content-Length ⇒ chunked 形态

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        if self.sent >= self.total:
            return b""
        take = min(n if n and n > 0 else 1 << 20, self.total - self.sent)
        self.sent += take
        return b"A" * take


class _DeclaredStream(_EndlessStream):
    """带 Content-Length 声明的假流（声明值可与实际不同）。"""

    def __init__(self, total: int, declared: int):
        super().__init__(total)
        self.headers = {"Content-Length": str(declared)}


class _CountingOpen:
    """open() 替身，但**真落盘**（用真实 tmp_path）：只额外记"写了多少字节"。

    为什么要记账：旧实现与修复版【都】会抛 InstallRefused（旧=哈希不符，
    新=体积超限），判据只能是"写入量"与"服务端被读了多少"。
    """

    def __init__(self, real_open, sink: list[int]):
        self._open = real_open
        self._sink = sink

    def __call__(self, *a, **k):
        return _CountingFile(self._open(*a, **k), self._sink)


class _CountingFile:
    def __init__(self, f, sink: list[int]):
        self._f = f
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return self._f.__exit__(*a)

    def write(self, b):
        if self._sink:
            self._sink[0] += len(b)
        return self._f.write(b)

    def read(self, n=-1):
        return self._f.read(n)

    def __iter__(self):
        return iter(self._f)


def test_download_refuses_and_deletes_when_stream_exceeds_cap(tmp_path, monkeypatch):
    """★ 核心锚点：无 content-length 的 chunked 流超限 → 在【上限处】中断 + 删制品。

    判据是"写入字节数 + 服务端被读了多少"，不是"抛了什么异常"——旧实现同样会抛
    （哈希不符），但它是在【写完 1GiB 之后】才抛，只有这两个量能区分修复/回滚。
    """
    monkeypatch.setattr(im, "MAX_DOWNLOAD_BYTES", 3 << 20)  # 3 MiB，免写 256MB
    stream = _EndlessStream(12 << 20)                       # 假流给 12 MiB
    dest = tmp_path / "poisoned.zip"
    written = [0]
    real_open = open
    monkeypatch.setattr("builtins.open", _CountingOpen(real_open, written))
    monkeypatch.setattr(im.urllib.request, "urlopen", lambda *a, **k: stream)

    with pytest.raises(im.InstallRefused, match="体积超限"):
        im.download_verified(["http://poisoned.mirror/x.zip"], dest, "f" * 64)

    assert written[0] <= (3 << 20), f"必须在上限处中断，实际写入 {written[0]} 字节"
    assert written[0] > 0, "写入量记账失效（锚点会假绿）——须真走 write 路径"
    assert stream.sent <= (3 << 20) + (1 << 20), \
        f"服务端不应被读完：已发出 {stream.sent} 字节（回滚态 = 12 MiB 全发）"
    assert not dest.exists(), "超限制品必须删除，不得留在磁盘（半截文件也不许）"


def test_download_precheck_rejects_oversized_content_length(tmp_path, monkeypatch):
    """有 Content-Length 且远超上限 → 预检早退：一个字节都不读、制品不落地。"""
    monkeypatch.setattr(im, "MAX_DOWNLOAD_BYTES", 3 << 20)
    over = (3 << 20) + im._MAX_DECLARED_SLACK_BYTES + (1 << 20)
    stream = _DeclaredStream(over, over)
    dest = tmp_path / "declared-big.zip"
    monkeypatch.setattr(im.urllib.request, "urlopen", lambda *a, **k: stream)
    with pytest.raises(im.InstallRefused, match="体积超限"):
        im.download_verified(["http://poisoned.mirror/x.zip"], dest, "f" * 64)
    assert stream.sent == 0, f"预检拒绝时不该读正文，实际读了 {stream.sent} 字节"
    assert not dest.exists(), "预检拒绝后不得留制品"


def test_download_normal_size_still_passes(tmp_path, monkeypatch):
    """反回归哨兵：上限内的正常文件仍必须通过（防"一律拒绝"式过度拦截）。"""
    monkeypatch.setattr(im, "MAX_DOWNLOAD_BYTES", 3 << 20)
    payload = b"B" * (2 << 20)                     # 2 MiB < 3 MiB 上限
    src = tmp_path / "src.zip"
    sha = _mk_file(src, payload)
    dest = tmp_path / "out.zip"
    im.download_verified([src.as_uri()], dest, sha)
    assert dest.read_bytes() == payload


def test_max_download_bytes_sane_for_real_artifacts():
    """上限必须 ≥ 真实制品（MeloTTS tarball 实测 5,873,911 B，2026-10-04 官方源），
    且仍是"写不满盘"的量级——防后续手滑调成 1KB（误伤）或 1TB（等于没上限）。"""
    assert (5 << 20) <= im.MAX_DOWNLOAD_BYTES <= (1 << 30), \
        f"上限不合理：{im.MAX_DOWNLOAD_BYTES}"


def test_download_all_sources_failed(tmp_path):
    # file:// 不存在路径立即失败（不碰代理/网络，判据口径：防沙箱代理 hang）
    with pytest.raises(im.InstallRefused, match="全部源均下载失败"):
        im.download_verified([(tmp_path / "none1.zip").as_uri(), (tmp_path / "none2.zip").as_uri()],
                             tmp_path / "o.zip", "f" * 64)


# ---------- safe_extract_zip ----------

def _mk_zip(p: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return p


def test_extract_normal_zip(tmp_path):
    z = _mk_zip(tmp_path / "ok.zip", {"sub/f1.txt": b"aaa", "f2.txt": b"bbb"})
    dest = tmp_path / "out"
    dest.mkdir()
    im.safe_extract_zip(z, dest)
    assert (dest / "sub" / "f1.txt").read_bytes() == b"aaa"


def test_extract_refuses_zip_slip(tmp_path):
    z = _mk_zip(tmp_path / "evil.zip", {"../evil.txt": b"pwned", "ok.txt": b"x"})
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(im.InstallRefused, match="路径穿越"):
        im.safe_extract_zip(z, dest)
    assert not (tmp_path / "evil.txt").exists(), "穿越成员绝不允许落盘"


def test_extract_refuses_absolute_member(tmp_path):
    z = _mk_zip(tmp_path / "abs.zip", {"ok.txt": b"x"})
    # 手工构造一个绝对路径成员
    with zipfile.ZipFile(z, "a") as zf:
        zf.writestr("C:/Windows/Temp/evil-k5.txt", b"pwned")
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(im.InstallRefused, match="路径穿越"):
        im.safe_extract_zip(z, dest)


def test_extract_refuses_corrupted_zip(tmp_path):
    z = _mk_zip(tmp_path / "corrupt.zip", {"data.txt": b"A" * 5000})
    raw = bytearray(z.read_bytes())
    raw[40] ^= 0xFF  # 翻转压缩数据中段一个字节（局部头 30B + 文件名 8B 之后）
    z.write_bytes(bytes(raw))
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises((im.InstallRefused, zipfile.BadZipFile)):
        im.safe_extract_zip(z, dest)


# ---------- dry-run 与 bat 接线 ----------

def test_dry_run_downloads_nothing(capsys, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("dry-run 不得发起下载/安装")
    monkeypatch.setattr(im, "download_verified", _boom)
    monkeypatch.setattr(im.subprocess, "run", _boom)
    monkeypatch.setattr(sys, "argv", ["install_melotts.py", "--dry-run"])
    im.main()
    out = capsys.readouterr().out
    assert "dry-run" in out and im.MELOTTS_COMMIT in out


def test_bat_wrapper_points_to_python_installer():
    """接线锚点：.bat 必须是薄封装且调用 install_melotts.py（实质逻辑在 py 里可测试）。"""
    bat = (Path(__file__).resolve().parents[2] / "scripts" / "安装中文语音包.bat").read_text("utf-8")
    assert "install_melotts.py" in bat
    assert "githubproxy" not in bat, "第三方代理必须彻底移除"
    assert "git+https" not in bat, "git+ 直连/代理拉代码形态必须移除"


def test_no_proxy_urls_left_in_scripts():
    """同类变体扫描锚点：scripts/ 与根 .bat 不得再出现第三方代理拉代码形态。"""
    root = Path(__file__).resolve().parents[2]
    for f in list((root / "scripts").glob("*.bat")) + [root / "install.bat", root / "start.bat"]:
        text = f.read_text("utf-8", errors="replace")
        for bad in ("githubproxy", "ghproxy", "gitclone.com", "hub.fastgit", "kkgithub"):
            assert bad not in text, f"{f.name} 仍含第三方代理 {bad}"
