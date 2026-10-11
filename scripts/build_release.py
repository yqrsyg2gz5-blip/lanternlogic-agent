# -*- coding: utf-8 -*-
"""一键打发布包（★ 2026-10-11 · 阶段 1 的产物）。

## 打出来的东西（两种，给两类人）

  · `LanternLogic-Agent-<tag>-full.zip` —— **解压即用** ✓
      公开源码 + 构建好的前端 + **便携 Python 运行时（含依赖）** + 离线 wheels
      ⇒ 用户电脑上**什么都不用装**（不用 Python ✓ 不用 Node ✓）
  · `LanternLogic-Agent-<tag>.zip` —— 轻量版 ✓
      公开源码 + 构建好的前端 ⇒ 需要用户自己装 Python 3.11+ ✓（体积 2MB 级 ✓）

## 踩过的坑（都写在这儿，别再踩一遍 ✗）

  1. **运行时必须是官方 embeddable 版**（`python-3.12.x-embed-amd64.zip` ✓ ~11MB ✓）
     不要用 PyInstaller ✗ —— 它会把整个 venv 打进去 ✓ 体积失控 ✓（评估里点名的那条 ✓）。
  2. **`python312._pth` 要改两处** ✗：
       · `import site` 那行**默认是注释掉的** ⇒ 不改 ⇒ 装不了任何依赖 ✓
       · 要加一行 `..\\backend` ⇒ 嵌入式 Python 跑在**隔离模式** ✓
         **当前目录不在 sys.path 上** ✓ 不加这行 ⇒ `python -m app.main` 报
         `ModuleNotFoundError: app` ✓（PYTHONPATH 也没用 ✗ 隔离模式不理环境变量 ✓）
  3. **wheels 必须用"运行时自己的 pip"下** ✗：
     用开发机的 Python 下 ⇒ 拿到的是 cp313 的二进制轮子 ✓
     而运行时是 3.12 ⇒ `pydantic-core` 之类**装不上** ✓（实测踩过 ✓）
  4. **嵌入式包不带 pip** ⇒ 用官方 `get-pip.py` 装一个 ✓
  5. 依赖装进**运行时自己**（不建 venv ✓）—— 嵌入式 Python 建 venv 会多一层坑 ✓
     装完体积 ~66MB ✓ 压完 ~37MB ✓

## 用法

    python scripts/build_release.py --tag v0.1.0 --out D:\\out
    python scripts/build_release.py --tag v0.1.0 --out D:\\out --light-only   # 只出轻量版

打完会打印两个包的体积与 SHA256 ✓（发 Release 时贴出去 ✓）。
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
EMBED_URL = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
GETPIP_URL = "https://bootstrap.pypa.io/get-pip.py"
MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"

QUICKSTART_FULL = (
    "LanternLogic Agent {tag} —— 下载版（解压即用）\r\n"
    "==============================================\r\n\r\n"
    "【你需要先装什么】**什么都不用装** ✓\r\n"
    "   这个包里已经带了 Python 运行时和全部后端依赖 ✓ 连 Node.js 都不需要 ✓\r\n\r\n"
    "【怎么跑】\r\n"
    "   1. 双击  install.bat     就绪检查（几秒钟；第一次可能被安全软件扫一下）\r\n"
    "   2. 双击  start.bat       启动，会自动打开浏览器界面\r\n"
    "   3. 界面里：设置 -> 模型 -> 选一家、粘你自己的 API Key\r\n"
    "      （或者切到本地模型：Ollama / 本地 Bonsai ✓ 不花钱 ✓）\r\n\r\n"
    "【数据在哪】全在本机 backend\\data\\ 里，不上传任何地方 ✓\r\n"
    "【不想折腾？】打开后先用「演示模式」逛逛 ✓ 不用 Key、不花钱 ✓\r\n"
    "【详细说明】README.md 与 docs\\用户指南.md\r\n"
)

QUICKSTART_LIGHT = (
    "LanternLogic Agent {tag} —— 下载版（轻量）\r\n"
    "==========================================\r\n\r\n"
    "【你需要先装什么】一个：Python 3.11 或更高版本（python.org）\r\n"
    "   安装时**务必勾上** Add python.exe to PATH —— 不勾这步，后面会失败。\r\n"
    "   ★ Node.js 不需要（包里已经带了构建好的界面）。\r\n"
    "   ★ 想要「什么都不用装」的版本 ⇒ 下那个带 -full 的包。\r\n\r\n"
    "【怎么跑】\r\n"
    "   1. 双击  install.bat     装依赖（第一次几分钟；窗口别关）\r\n"
    "   2. 双击  start.bat       启动，会自动打开浏览器窗口\r\n"
    "   3. 界面里：设置 -> 模型 -> 选一家、粘你自己的 API Key\r\n\r\n"
    "【详细说明】README.md 与 docs\\用户指南.md\r\n"
)

RUNTIME_NOTE = (
    "这是随包附带的便携 Python（官方 embeddable 版）+ 本项目后端依赖。\r\n"
    "你不用管它，也别删它 —— start.bat / install.bat 会用它。\r\n"
    "想自己升级依赖：runtime\\python.exe -m pip install -r backend\\requirements.txt\r\n"
)


def sha256(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def sh(cmd: list[str], cwd: pathlib.Path | None = None) -> None:
    r = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True)
    if r.returncode != 0:
        sys.stderr.write(r.stdout.decode("utf-8", "replace")[-2000:] + "\n")
        sys.stderr.write(r.stderr.decode("utf-8", "replace")[-2000:] + "\n")
        raise SystemExit(f"命令失败（{r.returncode}）：{' '.join(map(str, cmd))}")


def download(url: str, dst: pathlib.Path) -> pathlib.Path:
    if dst.exists() and dst.stat().st_size > 1000:
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    print(f"   下载 {url.rsplit('/', 1)[-1]} …")
    urllib.request.urlretrieve(url, dst)          # noqa: S310 - 固定官方地址 ✓
    return dst


def export_tree(out: pathlib.Path) -> pathlib.Path:
    """公开源码树（不含 .git ✓ 不含 data/ ✓ 由仓里的导出脚本负责 ✓）。"""
    tree = out / "LanternLogic-Agent"
    sh([sys.executable, str(ROOT / "scripts" / "export_public.py"), "--out", str(tree)], cwd=ROOT)
    dist = ROOT / "frontend" / "dist"
    if not dist.is_dir():
        raise SystemExit("frontend/dist 不存在 —— 先跑 npm run build ✓（发布包必须带构建好的界面 ✓）")
    shutil.copytree(dist, tree / "frontend" / "dist", dirs_exist_ok=True)
    return tree


def build_runtime(cache: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """做一个带依赖的便携 Python（坑都在这儿 ✓）。返回 (runtime 目录, wheels 目录) ✓。"""
    embed = download(EMBED_URL, cache / "python-embed.zip")
    rt = cache / "runtime"
    if rt.exists():
        shutil.rmtree(rt)
    rt.mkdir(parents=True)
    with zipfile.ZipFile(embed) as z:
        z.extractall(rt)

    # ★ 坑 2：改 ._pth —— 打开 site（否则装不了依赖 ✓）+ 加 ..\backend（否则 import app 失败 ✓）
    pth = next(rt.glob("*._pth"))
    name = pth.stem                                     # 例如 python312
    pth.write_text(f"{name}.zip\n.\n..\\backend\nimport site\n", "utf-8")

    # ★ 坑 4：嵌入式包不带 pip ⇒ 官方 get-pip
    getpip = download(GETPIP_URL, cache / "get-pip.py")
    sh([str(rt / "python.exe"), str(getpip), "--no-warn-script-location", "-i", MIRROR])

    # ★ 坑 3：wheels 必须用**运行时自己的 pip** 下（否则版本对不上 ✓）
    wheels = cache / "wheels"
    if wheels.exists():
        shutil.rmtree(wheels)
    wheels.mkdir(parents=True)
    sh([str(rt / "python.exe"), "-m", "pip", "download",
        "-r", str(ROOT / "backend" / "requirements.txt"), "-d", str(wheels),
        "--only-binary=:all:", "-i", MIRROR])
    # ★ 坑 5：依赖装进运行时自己（不建 venv ✓）
    sh([str(rt / "python.exe"), "-m", "pip", "install", "--no-index",
        "--find-links", str(wheels), "-r", str(ROOT / "backend" / "requirements.txt"),
        "--no-warn-script-location"])
    sh([str(rt / "python.exe"), "-c", "import fastapi, uvicorn, httpx, pydantic, multipart, cryptography"])
    return rt, wheels          # wheels 也随包（8MB ✓ 别人依赖坏了能离线自修 ✓）


def zip_tree(tree: pathlib.Path, out_zip: pathlib.Path) -> pathlib.Path:
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(tree.rglob("*")):
            if p.is_file():
                z.write(p, (pathlib.Path(tree.name) / p.relative_to(tree)).as_posix())
    return out_zip


def main() -> int:
    ap = argparse.ArgumentParser(description="打 LanternLogic 发布包")
    ap.add_argument("--tag", default="v0.1.0", help="版本号（只影响文件名与说明 ✓）")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--light-only", action="store_true", help="只打轻量版（不下载嵌入式 Python ✓）")
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cache = out / "_build-cache"
    cache.mkdir(exist_ok=True)
    made: list[pathlib.Path] = []

    print("[1/2] 轻量版（源码 + 构建好的前端 ⇒ 需要用户自己装 Python ✓）")
    tree = export_tree(cache / "light")
    (tree / "先看我-怎么跑起来.txt").write_text(QUICKSTART_LIGHT.format(tag=args.tag), "utf-8")
    made.append(zip_tree(tree, out / f"LanternLogic-Agent-{args.tag}.zip"))

    if not args.light_only:
        print("[2/2] 完整版（+ 便携 Python 运行时 ⇒ 什么都不用装 ✓）")
        rt, wheels = build_runtime(cache)
        tree2 = export_tree(cache / "full")
        shutil.copytree(rt, tree2 / "runtime")
        shutil.copytree(wheels, tree2 / "wheels", dirs_exist_ok=True)
        (tree2 / "runtime" / "README-这是啥.txt").write_text(RUNTIME_NOTE, "utf-8")
        (tree2 / "先看我-怎么跑起来.txt").write_text(QUICKSTART_FULL.format(tag=args.tag), "utf-8")
        made.append(zip_tree(tree2, out / f"LanternLogic-Agent-{args.tag}-full.zip"))

    print("\n=== 产物 ===")
    for p in made:
        print(f"  {p.name}\n    {p.stat().st_size/1048576:.2f} MB\n    SHA256 {sha256(p)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
