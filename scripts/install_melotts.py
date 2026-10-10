"""MeloTTS 中文语音包安装器（K5 供应链加固版，scripts/安装中文语音包.bat 的实质逻辑）。

旧版三大洞（供应链 RCE 面）：
  1. `pip install git+https://githubproxy.cc/...` —— 经第三方代理拉代码并执行其 setup.py，
     代理被控/投毒即本机任意代码执行；
  2. NLTK zip 无哈希校验、无完整性校验；
  3. zipfile.extractall 无路径校验（zip-slip：成员写 ../ 可逃逸解压目录）。

本版对策（内容寻址：哈希对了，来源是谁都无所谓）：
  · MeloTTS 钉死 commit + 钉死 tarball sha256（2026-10-04 自官方 codeload 实测）；
  · NLTK 六个包全部钉死 sha256（jsdelivr 是 nltk_data 仓库的 CDN，三个节点内容一致）；
  · 任何一步哈希不符 → 拒绝安装并非零退出；
  · 解压前 ZipFile.testzip 完整性校验 + 逐成员路径穿越检查；
  · 上游可配置：AGENT_SHELL_TTS_MIRROR 可覆盖镜像前缀（哈希不变，安全性不变）。

用法：
  python scripts/install_melotts.py            # 真安装
  python scripts/install_melotts.py --dry-run  # 只打印计划与钉死值，不下载不安装
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

# ---------- 钉死值（版本 + 内容哈希） ----------

MELOTTS_COMMIT = "209145371cff8fc3bd60d7be902ea69cbdb7965a"
MELOTTS_TARBALL_SHA256 = "ceb9a1a636522fee6489c8496ef5b6b9b3b8c1e8441f402eea00d8a17fbe52b8"

# (子目录, 文件名, sha256)
NLTK_PACKAGES = [
    ("corpora", "cmudict.zip", "d07cca47fd72ad32ea9d8ad1219f85301eeaf4568f8b6b73747506a71fb5afd6"),
    ("corpora", "words.zip", "54ed02917d6771dcc3e8141218960d020947f7f2ccfd9ac9b320979349746015"),
    ("taggers", "averaged_perceptron_tagger.zip", "e1f13cf2532daadfd6f3bc481a49859f0b8ea6432ccdcd83e6a49a5f19008de9"),
    ("taggers", "averaged_perceptron_tagger_eng.zip", "6025f530624335c67d6547d44757b357b4e79bae030a0383e9887a92c1718f0b"),
    ("tokenizers", "punkt.zip", "51c3078994aeaf650bfc8e028be4fb42b4a0d177d41c012b6a983979653660ec"),
    ("tokenizers", "punkt_tab.zip", "e57f64187974277726a3417ca6f181ec5403676c717672eef6a748a7b20e0106"),
]

_UA = {"User-Agent": "Mozilla/5.0 (AgentShell-TTS-Installer)"}

# ---------- 下载体积上限（K5 第 2 处：哈希校验【之前】的磁盘防线） ----------
# 二十六轮第 7 批第 2 处：旧版只有 EOF 才停（while True: read/write），
# 无 content-length 校验、无字节上限 —— 被控/投毒的镜像（含用户可配的
# AGENT_SHELL_TTS_MIRROR）可在【哈希校验之前】写满磁盘。实测假流：
# 1 GiB 全部写入（1,073,741,824 字节）才因"哈希不符"停手，全程 0.24s。
# 上限按真实制品定（2026-10-04 实测官方源）：
#   MeloTTS tarball（codeload，5,873,911 B，无 Content-Length 头）
#   NLTK 六包同量级（cmudict/punkt_tab 各数 MB）
# ⇒ 32 MiB 已 >5× 冗余；取 256 MiB 是"再放宽一个量级也不至于写满盘"的折中。
# 判据用【已读字节数】，不依赖 content-length（无 CL 的 chunked 响应同样受限）。
MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024
# content-length 预检留 8 MiB 余量：传输/内容编码（gzip 等）下头里的长度
# 可能比解码后略小，预检只当"早退优化"，真正的闸是边读边计的硬上限。
_MAX_DECLARED_SLACK_BYTES = 8 * 1024 * 1024


class InstallRefused(RuntimeError):
    """哈希/完整性/体积不符，拒绝安装（fail-closed）。"""


def _mirror_prefixes() -> list[str]:
    """NLTK 数据源（内容寻址，镜像只影响速度不影响安全）。"""
    custom = os.environ.get("AGENT_SHELL_TTS_MIRROR", "").strip().rstrip("/")
    out = [custom] if custom else []
    out += [
        "https://cdn.jsdelivr.net/gh/nltk/nltk_data@gh-pages/packages",
        "https://gcore.jsdelivr.net/gh/nltk/nltk_data@gh-pages/packages",
        "https://testingcf.jsdelivr.net/gh/nltk/nltk_data@gh-pages/packages",
    ]
    return out


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_verified(urls: list[str], dest: Path, expect_sha256: str) -> Path:
    """按顺序尝试多个源下载，任一源成功后校验 sha256——

    ★ 哈希不符不试下一个源、直接拒绝：同一文件名拿到不同内容本身就是攻击信号，
    '换个源再试'会把投毒面放大成'哪个源能过用哪个'。

    二十六轮第 7 批第 2 处（K5 下载体积上限）：边读边计数，超过 MAX_DOWNLOAD_BYTES
    立即中断 + 删制品 + InstallRefused——绝不等 EOF、绝不写完再判。content-length
    存在时先做一次预检（早退，省流量），无 content-length 的响应同样受硬上限约束。
    """
    last_err: Exception | None = None
    for url in urls:
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=300) as r:
                declared = r.headers.get("Content-Length") if getattr(r, "headers", None) else None
                if declared is not None:
                    try:
                        declared_n = int(str(declared).strip())
                    except (TypeError, ValueError):
                        declared_n = 0
                    if declared_n > MAX_DOWNLOAD_BYTES + _MAX_DECLARED_SLACK_BYTES:
                        raise InstallRefused(
                            f"制品体积超限（源声明 {declared_n} 字节 > 上限 "
                            f"{MAX_DOWNLOAD_BYTES} 字节），拒绝下载：{url}"
                        )
                total = 0
                with open(dest, "wb") as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_DOWNLOAD_BYTES:
                            raise InstallRefused(
                                f"制品体积超限（已读 {total} 字节 > 上限 {MAX_DOWNLOAD_BYTES} 字节），"
                                f"已中断下载：{url}"
                            )
                        f.write(chunk)
            got = sha256_file(dest)
            if got != expect_sha256:
                raise InstallRefused(
                    f"哈希不符，拒绝安装：{url}\n  期望 {expect_sha256}\n  实际 {got}\n"
                    "  （源被投毒或钉死值过期——请核对后更新脚本钉死值，绝不跳过校验）"
                )
            return dest
        except InstallRefused:
            # 体积超限/哈希不符：本函数契约是"return 时 dest 才存在"——
            # 任何拒绝路径都不得留半截制品在磁盘上（旧版只在哈希分支删）
            dest.unlink(missing_ok=True)
            raise
        except Exception as e:  # 网络类失败才换源（半截制品同样删掉再换）
            dest.unlink(missing_ok=True)
            last_err = e
            continue
    raise InstallRefused(f"全部源均下载失败（最后错误：{last_err}）")


def safe_extract_zip(zpath: Path, dest_dir: Path) -> None:
    """解压前置两道闸：完整性（testzip）+ 路径穿越（逐成员 resolve 后必须在 dest 内）。

    testzip/读取阶段的 zlib 异常同样收编为 InstallRefused（fail-closed 且报错可读）。
    """
    dest_root = dest_dir.resolve()
    with zipfile.ZipFile(zpath) as zf:
        try:
            bad = zf.testzip()
        except Exception as e:
            raise InstallRefused(f"zip 完整性校验异常（{type(e).__name__}），拒绝解压：{zpath.name}") from e
        if bad is not None:
            raise InstallRefused(f"zip 完整性校验失败（损坏成员：{bad}），拒绝解压：{zpath.name}")
        for info in zf.infolist():
            target = (dest_root / info.filename).resolve()
            if target != dest_root and dest_root not in target.parents:
                raise InstallRefused(
                    f"zip 含路径穿越成员（{info.filename}），疑似 zip-slip 攻击，拒绝解压：{zpath.name}"
                )
        zf.extractall(dest_root)


def install_melotts(work: Path, py: str) -> None:
    tarball = work / "melotts.tar.gz"
    urls = [
        f"https://codeload.github.com/myshell-ai/MeloTTS/tar.gz/{MELOTTS_COMMIT}",
    ]
    mirror = os.environ.get("AGENT_SHELL_TTS_GIT_MIRROR", "").strip().rstrip("/")
    if mirror:  # 自定义 git 镜像（如代理）——内容寻址，哈希不符照样拒绝
        urls.insert(0, f"{mirror}/{MELOTTS_COMMIT}")
    print(f"[1/3] MeloTTS @ {MELOTTS_COMMIT[:12]}（钉死 commit + sha256）…", flush=True)
    download_verified(urls, tarball, MELOTTS_TARBALL_SHA256)
    print("      哈希校验通过，安装…", flush=True)
    subprocess.run([py, "-m", "pip", "install", str(tarball), "--no-deps"], check=True)


def install_nltk_data() -> None:
    base = Path.home() / "nltk_data"
    prefixes = _mirror_prefixes()
    print("[3/3] NLTK data（六包钉死 sha256 + zip 两道闸）…", flush=True)
    for sub, name, sha in NLTK_PACKAGES:
        dest_dir = base / sub
        marker = dest_dir / name.replace(".zip", "")
        if marker.exists():
            print(f"      跳过（已存在）：{name}", flush=True)
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as td:
            zpath = download_verified([f"{p}/{sub}/{name}" for p in prefixes], Path(td) / name, sha)
            safe_extract_zip(zpath, dest_dir)
        print(f"      OK：{name}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="MeloTTS 中文语音包安装（供应链加固版）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划与钉死值，不下载不安装")
    ap.add_argument("--python", default=sys.executable, help="目标 venv 的 python（pip 装到它下面）")
    args = ap.parse_args()

    print("LanternLogic Agent · MeloTTS 安装器（钉版本 + 钉哈希 + zip 两道闸）")
    print(f"  MeloTTS commit : {MELOTTS_COMMIT}")
    print(f"  tarball sha256 : {MELOTTS_TARBALL_SHA256}")
    for sub, name, sha in NLTK_PACKAGES:
        print(f"  nltk {name:38s} {sha[:16]}…")
    if args.dry_run:
        print("dry-run：未下载、未安装。")
        return

    py = args.python
    subprocess.run([py, "-m", "pip", "install", "edge-tts", "pyttsx3"], check=True)
    with tempfile.TemporaryDirectory() as td:
        install_melotts(Path(td), py)
    print("[2/3] 依赖（走 pip 已配索引）…", flush=True)
    subprocess.run([py, "-m", "pip", "install", "torchaudio", "cn2an", "pypinyin", "unidecode",
                    "num2words", "eng_to_ipa", "mecab-python3", "unidic-lite", "pykakasi",
                    "fugashi", "g2p_en", "nltk", "anyascii", "jamo", "gruut[zh]", "cached_path"],
                   check=True)
    subprocess.run([py, "-m", "pip", "uninstall", "unidic", "-y"], check=False)
    install_nltk_data()
    print("完成！重启 LanternLogic Agent 后 TTS backend 可选 melotts。")


if __name__ == "__main__":
    main()
