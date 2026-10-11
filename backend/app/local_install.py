# -*- coding: utf-8 -*-
"""本地能力「一键装 + 一键下模型」—— 2026-10-07。

## 为什么要有它（用户原话）

> "我点击下载这个本地千问3 ASR，我点击的话它是不是就能下载，下载就能用了"
> "我想要的就是**一键能安装**，然后还能**使用**这种的"

在此之前 ✗：界面上那个按钮叫「用这个」✓ **只切配置** ✓
—— 依赖没装、模型没下 ✓ ⇒ 点完**一说话就报错** ✓（虽然报错是可照做的 ✓ 但用户要的是"能用" ✗）。

## 名字为什么从 `asr_install` 改成 `local_install`（2026-10-07 夜）

本来只服务语音 ASR ✓ 现在**知识库的本地向量**也要走同一套 ✓（用户："我做这个知识库本地的"）
—— 两处需求一模一样 ✓（写死的命令 ✓ 真实输出 ✓ 装完再探一次 ✓ 同一时刻只跑一个 ✓）
⇒ 通用化 ✓ 免得把同一套逻辑抄两遍 ✓（抄两遍就会养出两套脾气 ✗）。

## 三条设计红线（都是本项目一贯的规矩 ✓）

1. **绝不执行用户输入的任意命令** ✗ —— 这里跑的是**写死的几条**：
   `python -m pip install -U qwen-asr modelscope` ✓ / `python -m pip install -U fastembed` ✓
   / `python -m modelscope download --model Qwen/Qwen3-ASR-<档>…` ✓
   （用 `sys.executable` ✓ 保证装进**本项目的 venv** ✓ 而不是别处的 Python ✓）
2. **不装作成功** ✗ —— 真实输出一行行留着 ✓ 失败就把最后几行亮出来 ✓
   装完还要**再探一次**（`find_spec`）确认真的能 import ✓ 才报 ok ✓
3. **不偷偷下大文件** ✗ —— 下模型是**用户点**才做 ✓ 而且**显示已下多少** ✓
   （ASR 模型 1–4GB ✓ 向量模型约 100MB ✓ 用户有权先知道 ✓）

## 为什么"进度"用**目录体积**算，而不解析进度条

进度条是 tty 艺术 ✗（`\r` 刷新、宽度、编码花样多 ✓）解析它很脆 ✗。
改成：**定时量一下目标目录已占多少字节** ✓ —— 稳 ✓ 而且能顺带给出"下了多少 MB" ✓
（官方 CLI 的输出仍原样保留在 `lines` 里 ✓ 想看细节的能看 ✓）。
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import threading
import time
from typing import Any

# 只认这两个档（与 asr.LOCAL_MODELS 对齐 ✓）
_TIERS = {"0.6b": "Qwen/Qwen3-ASR-0.6B", "1.7b": "Qwen/Qwen3-ASR-1.7B"}
MAX_LINES = 60          # 日志留最后多少行（够看失败原因 ✓ 又不至于把内存撑大 ✓）

_LOCK = threading.Lock()
_JOB: dict[str, Any] = {"kind": "", "state": "idle", "lines": [], "started": 0.0,
                        "target": "", "error": "", "bytes": 0}


def _log(line: str) -> None:
    with _LOCK:
        _JOB["lines"] = (list(_JOB.get("lines") or []) + [line.rstrip()])[-MAX_LINES:]


def _dir_bytes(p: pathlib.Path) -> int:
    try:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except Exception:                                       # noqa: BLE001
        return 0


def model_cache_dir(tier: str) -> pathlib.Path:
    """模型下到哪儿 ✓ —— 放进 `backend/data/asr_models/<档>` ✓（跟其它数据一个地方 ✓ 好清理 ✓）。"""
    root = pathlib.Path(__file__).resolve().parent.parent / "data" / "asr_models" / tier
    return root


def _run(cmd: list[str], *, cwd: pathlib.Path | None = None, watch: pathlib.Path | None = None) -> int:
    """跑一条**写死**的命令 ✓ 输出实时进日志 ✓ 边跑边量目录体积 ✓。"""
    _log("$ " + " ".join(cmd))
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        proc = subprocess.Popen(  # noqa: S603 —— 参数是写死的 ✓ 不经过 shell ✓
            cmd, cwd=str(cwd) if cwd else None, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", bufsize=1,
        )
    except Exception as e:                                  # noqa: BLE001
        _log(f"✗ 起不来：{type(e).__name__}: {e}")
        return 127
    assert proc.stdout is not None
    while True:
        line = proc.stdout.readline()
        if line:
            _log(line)
            continue
        if proc.poll() is not None:
            break
        if watch is not None:                               # 边跑边报"已下多少"
            with _LOCK:
                _JOB["bytes"] = _dir_bytes(watch)
        time.sleep(0.2)
    if watch is not None:
        with _LOCK:
            _JOB["bytes"] = _dir_bytes(watch)
    return int(proc.returncode or 0)


def _job_running() -> bool:
    with _LOCK:
        return _JOB.get("state") == "running"


def _start(kind: str, target: str, fn) -> dict[str, Any]:
    """起一个后台任务（**同一时刻只允许一个** ✓ —— 两条 pip 撞在一起会互相毁掉 ✓）。"""
    if _job_running():
        return {"ok": False, "detail": "已经有一个安装/下载在跑了，等它结束再说 ✓"}
    with _LOCK:
        _JOB.update({"kind": kind, "state": "running", "lines": [], "started": time.time(),
                     "target": target, "error": "", "bytes": 0})
    threading.Thread(target=fn, daemon=True).start()
    return {"ok": True, "kind": kind, "target": target}


def install_deps() -> dict[str, Any]:
    """一键装依赖：`<本项目的 python> -m pip install -U qwen-asr modelscope` ✓（写死 ✓ 不走 shell ✓）。

    ★ 为什么**连 `modelscope` 一起装**（2026-10-07 实测补的 ✗→✓）：
      只装 `qwen-asr` 时，环境里**没有下载器** ✗ ⇒ 下模型会走**直连 HuggingFace** ✓
      —— 国内经常很慢甚至不通 ✗✓。ModelScope 是阿里的国内源 ✓ 同一批模型 ✓ 快得多 ✓。
      （装了它，"② 一键下模型"就会优先用它 ✓）
    """
    def _work() -> None:
        code = _run([sys.executable, "-m", "pip", "install", "-U", "qwen-asr", "modelscope"])
        ok = code == 0
        if ok:
            # ★ **装完必须再探一次** ✓ —— pip 返回 0 不等于真的能 import ✓（本项目不装作成功 ✓）
            import importlib.util
            ok = importlib.util.find_spec("qwen_asr") is not None
            _log("✓ 依赖装好了（已确认能 import qwen_asr）" if ok else
                 "✗ pip 说成功，但 import 不到 qwen_asr —— 可能装到了别的 Python 环境")
        else:
            _log(f"✗ pip 退出码 {code} —— 上面最后几行就是原因")
        with _LOCK:
            _JOB.update({"state": "done" if ok else "failed", "error": "" if ok else "装依赖失败"})
    return _start("install", "qwen-asr", _work)


def install_kb_deps() -> dict[str, Any]:
    """一键装**知识库本地向量**的依赖：`<本项目的 python> -m pip install -U fastembed` ✓。

    ★ 为什么选 `fastembed`（2026-10-07）：
      它是 **ONNX** 实现 ✓ 几十 MB ✓ **不需要 torch** ✓ 装得快 ✓
      —— 而 `sentence-transformers` 要拖 torch（本机因语音 ASR 已经装了 2.5GB ✗）✓
      对只想用知识库的人来说太重 ✗ ⇒ 首选轻的 ✓ 重的作为备选（`embed_local` 两个都认 ✓）。
    """
    def _work() -> None:
        code = _run([sys.executable, "-m", "pip", "install", "-U", "fastembed"])
        ok = code == 0
        if ok:
            import importlib.util
            ok = importlib.util.find_spec("fastembed") is not None
            _log("✓ 装好了（已确认能 import fastembed）—— 首次建库时会自动下载向量模型（约 100MB）"
                 if ok else
                 "✗ pip 说成功，但 import 不到 fastembed —— 可能装到了别的 Python 环境")
        else:
            _log(f"✗ pip 退出码 {code} —— 上面最后几行就是原因")
        with _LOCK:
            _JOB.update({"state": "done" if ok else "failed", "error": "" if ok else "装依赖失败"})
    return _start("install-kb", "fastembed", _work)


def pull_model(tier: str) -> dict[str, Any]:
    """一键下模型 ✓ —— 优先 ModelScope（国内快 ✓）✓ 没有就 HuggingFace ✓ 都没有就直连加载 ✓。"""
    t = (tier or "0.6b").strip().lower()
    if t not in _TIERS:
        return {"ok": False, "detail": f"未知档位：{tier}（可用：{sorted(_TIERS)}）"}
    model_id = _TIERS[t]
    dest = model_cache_dir(t)

    def _work() -> None:
        dest.mkdir(parents=True, exist_ok=True)
        code = 127
        import importlib.util
        if importlib.util.find_spec("modelscope") is not None:
            code = _run([sys.executable, "-m", "modelscope", "download",
                         "--model", model_id, "--local_dir", str(dest)], watch=dest)
        elif importlib.util.find_spec("huggingface_hub") is not None:
            code = _run([sys.executable, "-m", "huggingface_hub.commands.huggingface_cli",
                         "download", model_id, "--local-dir", str(dest)], watch=dest)
        else:
            _log("两个下载器都没有 ⇒ 改用**直接加载**的方式下（首次转写也会走这条）")
            try:
                import torch                                     # noqa: PLC0415
                from qwen_asr import Qwen3ASRModel               # noqa: PLC0415
                use_cuda = bool(getattr(torch, "cuda", None) and torch.cuda.is_available())
                kw: dict[str, Any] = {}
                if use_cuda:
                    kw = {"dtype": torch.bfloat16, "device_map": "cuda:0"}
                Qwen3ASRModel.from_pretrained(model_id, **kw)
                code = 0
            except Exception as e:                               # noqa: BLE001
                _log(f"✗ 加载式下载失败：{type(e).__name__}: {str(e)[:200]}")
                code = 1
        got = _dir_bytes(dest)
        ok = code == 0 and got > 1024 * 1024                # 至少 1MB 才算真下到东西 ✓
        _log(f"{'✓ 模型下好了' if ok else '✗ 下载没成功'}：{dest}（{got / 1048576:.1f} MB）")
        with _LOCK:
            _JOB.update({"state": "done" if ok else "failed", "bytes": got,
                         "error": "" if ok else "下载失败"})
    return _start("pull", model_id, _work)


def status() -> dict[str, Any]:
    """给界面轮询的状态 ✓（含真实输出最后几行 ✓）。"""
    with _LOCK:
        j = dict(_JOB)
    j["lines"] = list(j.get("lines") or [])
    j["elapsed"] = int(time.time() - float(j.get("started") or 0)) if j.get("started") else 0
    return j


# ═══════════════════════════════════════════════════════════════════════════
# ★★ 2026-10-08：**本地朗读的「一键装」**（用户："就像上面这个千问3似的，点一下它就安装"）
#
# 为什么必须做（用户当天问出来的三件事）：
#   ① 界面上原来只说 `pip install melotts` ✗ —— 实测**装不上**（Python 3.13：
#      PyPI 只发源码包且包里缺 requirements.txt；还写死 torch<2.0、1.x 没有 3.13 轮子 ✓）
#   ② MeloTTS **本仓早就有安装器**（`scripts/install_melotts.py` ✓ 钉 commit + 哈希 ✓）
#      —— 但**没接到界面上** ✗（"留了口子却没做成界面"的老毛病 ✓）
#   ③ 用户又问"有没有更好更小还能商用的" ⇒ 当时试了 **Kokoro-82M 中文** ✗
#      —— **后来下架了** ✓：它 20 多个中文音色**全**把「本地」念成「喷嚏」✗（实测 ✓）
#      现在只剩两档本地引擎：**千问3-TTS**（用户点名 ✓ 中文词全对 ✓）+ MeloTTS ✓
#
# 三条红线照旧（与上面 ASR 那套同口径 ✓ 不另立一套 ✗）：
#   1. **绝不执行用户输入的任意命令** ✗ —— 跑的全是**写死的**；
#   2. **不装作成功** ✗ —— 真实输出一行行留着 ✓ 装完**再探一次** ✓ 才敢报 ok；
#   3. **不偷偷下大文件** ✗ —— 是用户点才下 ✓ 而且**边下边报已下多少 MB** ✓。
# ═══════════════════════════════════════════════════════════════════════════

#: 下千问3-TTS 模型的那段（**写死的** ✓ 不接受任何用户输入 ✓）——
#: ★ 为什么不用命令行：新版 `modelscope` **没有 `python -m modelscope`** ✗
#:   （实测报 `No module named modelscope.__main__` ✓）⇒ 只能走 Python API ✓
_QWEN3_DL_SNIPPET = (
    "import sys;from modelscope import snapshot_download;"
    "snapshot_download('Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice',local_dir=sys.argv[1]);"
    "print('[ok] 模型下好了')"
)


def tts_model_dir(engine: str) -> pathlib.Path:
    """本地朗读的模型放哪儿 ✓（与 tts.Qwen3TTSBackend._dir() 必须**同一处** ✓ 不然装完不认 ✓）。"""
    return pathlib.Path(__file__).resolve().parent.parent / "data" / f"tts_{engine}"


def install_tts(engine: str) -> dict[str, Any]:
    """**一键装本地朗读** ✓ —— 两条路，都跑写死的命令 ✓。

    · `melotts`  ⇒ 跑**本仓自带的安装器**（`scripts/install_melotts.py` ✓ 钉 commit + 哈希 ✓）
    · `qwen3tts` ⇒ pip 四条 + ModelScope 下模型（**用户点名要的那档** ✓ 中文词全对 ✓ 2.4GB ✓）
    ★ Kokoro 已于 2026-10-08 **下架** ✗（20+ 个中文音色全把「本地」念成「喷嚏」✓ 实测 ✓）
    """
    eng = (engine or "").strip().lower()
    if eng not in ("melotts", "qwen3tts"):
        return {"ok": False, "detail": f"未知的本地朗读引擎：{engine}（可用：melotts / qwen3tts）"}
    dest = tts_model_dir(eng)

    def _work() -> None:
        try:
            if eng == "melotts":
                script = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "install_melotts.py"
                if not script.exists():
                    _log(f"✗ 找不到安装器：{script}")
                    raise RuntimeError("安装器不在（仓库被动过？）")
                _log("跑本仓自带的安装器（钉 commit + 哈希 + zip 两道闸 ✓）…")
                code = _run([sys.executable, str(script), "--python", sys.executable], watch=dest)
                if code != 0:
                    raise RuntimeError(f"安装器退出码 {code} —— 上面最后几行就是原因")
            else:  # qwen3tts
                _log("① 装依赖（qwen-tts **--no-deps** ✓ + torchaudio/accelerate/einops/modelscope ✓）…")
                # ★ `--no-deps`：它会把 transformers 钉到 4.57.3 ✗ —— 而本机本地 ASR 正用着 4.57.6 ✓
                #   降级可能把 ASR 弄坏 ✗ ⇒ 只装它自己，依赖我们按需补 ✓（实测这样能跑 ✓）
                code = _run([sys.executable, "-m", "pip", "install", "-U", "qwen-tts", "--no-deps"])
                if code == 0:
                    code = _run([sys.executable, "-m", "pip", "install", "-U",
                                 "torchaudio", "accelerate", "einops", "modelscope"])
                if code != 0:
                    raise RuntimeError(f"pip 退出码 {code} —— 上面最后几行就是原因")
                _log("② 下模型（约 **2.4GB** ✓ 走 ModelScope 国内直连 ✓ 边下边报进度 ✓ 可重复点：会接着下 ✓）…")
                dest.mkdir(parents=True, exist_ok=True)
                # ★ 必须走 **Python API** ✗ 不能 `-m modelscope`
                #   （新版 modelscope 没这个入口 ✓ 实测报 `No module named modelscope.__main__` ✓）
                code = _run([sys.executable, "-c", _QWEN3_DL_SNIPPET, str(dest)], watch=dest)
                if code != 0:
                    raise RuntimeError(f"下模型退出码 {code} —— 上面最后几行就是原因")
            # ★ 装完**再探一次** ✓ —— pip/脚本返回 0 不等于真能用 ✓（本项目不装作成功 ✓）
            # ★ 2026-10-08：这里用 **deep=True** ✓ —— MeloTTS 那次深探要 2 分钟 ✓
            #   但这是**后台任务** ✓ 等得起 ✓ 而常规探针（设置页/能力表）必须毫秒级 ✓
            #   （谁不支持 deep 参数就用默认探针 ✓ —— 别为了统一接口把别人也拖慢 ✗）
            from . import tts as tts_mod
            cls = tts_mod._TTS_REGISTRY.get(eng)
            if cls is None:
                ok = False
            else:
                try:
                    ok = bool(cls.available(deep=True))
                except TypeError:
                    ok = bool(cls.available())
            _log("✓ 装好了（已确认能读模型）" if ok else
                 "✗ 装完了但**探测仍然不通过** —— 可能装到了别的 Python 环境 / 模型不完整")
            with _LOCK:
                _JOB.update({"state": "done" if ok else "failed",
                             "error": "" if ok else "装完但探测不通过",
                             "bytes": _dir_bytes(dest) if dest.exists() else 0})
        except Exception as e:                                    # noqa: BLE001
            _log(f"✗ {type(e).__name__}: {str(e)[:300]}")
            with _LOCK:
                _JOB.update({"state": "failed", "error": str(e)[:200]})

    return _start("install-tts", f"{eng} → {dest}", _work)
