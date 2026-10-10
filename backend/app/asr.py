"""语音转文字（ASR）—— 两档并列（Phase 2 ⑤）：

  ① **云端 MiMo**（`mimo-v2.5-asr`，OpenAI `input_audio` 格式）—— 有 Key 就能用、零下载。
     网关规则：请求不得带 text 部分（提示词由网关注入）—— 实测得出的协议细节。
  ② **本地千问3-ASR**（离线，不联网、数据不出本机）—— **2026-10-06 真接上了** ✓
     走官方 `qwen-asr` 包（Apache-2.0 ✓ 模型 `Qwen/Qwen3-ASR-0.6B` / `1.7B` ✓）。

★★ 2026-10-06 修的一段历史（用户实测问出来的 ✗✗）：
  在此之前，本地那档**只有"诚实报没装"的占位** ✓，而安装指引里写的却是
  **`pip install faster-whisper`** ✗ —— 那是 **OpenAI Whisper** 的社区实现 ✓ 跟千问没关系 ✗；
  更糟的是**没有任何加载模型的代码** ✗ ⇒ 用户照提示装完 **faster-whisper** 后，
  能力探针（探的就是 `faster_whisper`）会显示 **"✅ 可用"** ✓ 而一点就报错 ✗✓
  —— 正是本项目一直在避免的那种"界面骗人" ✓。

  ⇒ 现在：探针探 `qwen_asr` ✓ 安装指引写官方包 ✓ 而且**真实现了转写** ✓。
  档位也从 `0.6b / 1.7b / 1.7b-quant` 收敛成 **`0.6b / 1.7b`** ✓
  （官方只发这两个规格 ✓ 没有 "quant" 这个型号 ✗ 写着等于骗人 ✓）。

★ 为什么不默认用本地：首次要下**几百 MB 到几 GB** 的模型 ✓，
  而且没 GPU 时较慢 ✓ —— 所以默认仍是"云端 MiMo（有 Key 就能用、零下载）" ✓，
  本地档给"要把数据留在本机 / 想省钱"的人 ✓（选它之前界面上会说清代价 ✓）。
"""
from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path

import httpx

from . import retry        # ★ 2026-10-07 第 4 项：统一的限流/瞬时故障退避 ✓

# 本地档位（千问3-ASR 开源版）—— 与 capabilities 的档位 id 对齐 ✓
# ★ 只留官方真实存在的两个规格 ✓（0.6B 约 2GB 显存 / 1.7B 约 4GB ✓）
LOCAL_TIERS = ("0.6b", "1.7b")
LOCAL_MODELS = {
    "0.6b": "Qwen/Qwen3-ASR-0.6B",
    "1.7b": "Qwen/Qwen3-ASR-1.7B",
}
LOCAL_INSTALL_HINT = (
    "装本地 ASR（千问3-ASR，离线、数据不出本机）：\n"
    "  1) 装依赖：backend\\.venv\\Scripts\\python.exe -m pip install -U qwen-asr\n"
    "     （想更快可装 vLLM 后端：pip install -U \"qwen-asr[vllm]\"）\n"
    "  2) 模型**首次使用时会自动下载**（0.6B 约 2GB 显存 / 1.7B 约 4GB 显存；\n"
    "     显存不够就用 0.6B —— 实测中文只差约 1 个百分点，但快很多）；\n"
    "     想先下好也行：modelscope download --model Qwen/Qwen3-ASR-0.6B --local_dir ./Qwen3-ASR-0.6B\n"
    "  3) 装完把 asr.provider 改成 local_qwen3（或直接跟我说「装本地 ASR」，我来做）"
)
_CLOUD_HINT = (
    "另一条路：装本地 ASR（离线、不联网），见设置 → 模型语音里的「本地档位」。"
)


class ASRUnavailable(RuntimeError):
    """语音输入不可用。**消息必须可照做**（说清往哪走），调用方会原样显示给用户。"""


def _provider(cfg: object | None = None) -> str:
    if cfg is not None:
        return str(getattr(getattr(cfg, "asr", None), "provider", "") or "")
    return os.environ.get("AGENT_SHELL_ASR_PROVIDER", "") or "mimo"


def _model(cfg: object | None = None) -> str:
    if cfg is not None:
        return str(getattr(getattr(cfg, "asr", None), "model", "") or "mimo-v2.5-asr")
    return os.environ.get("AGENT_SHELL_ASR_MODEL", "") or "mimo-v2.5-asr"


def _key_env(cfg: object | None = None) -> str:
    if cfg is not None:
        return str(getattr(getattr(cfg, "asr", None), "api_key_env", "") or "XIAOMI_MIMO_API_KEY")
    return os.environ.get("AGENT_SHELL_ASR_KEY_ENV", "") or "XIAOMI_MIMO_API_KEY"


def sniff_audio_format(audio_path: Path) -> str:
    """按**文件字节**判断格式（wav / mp3 / 其它），不信任文件名。

    ★ 为什么必须嗅探（2026-10-05 用户报"语音输入根本不好使"的真凶）：
      前端上传时把文件名写死成 `draft.webm`（不管里面其实是 wav），
      后端照后缀当成 webm ⇒ 网关回 `input_audio.format must be one of: wav, mp3. Got: webm`。
      **文件名是调用方说了算的东西**，格式判断得看内容。

    识别：`RIFF....WAVE` → wav；`ID3` 或 `0xFFEx/0xFFFx` 帧同步 → mp3；其余按后缀（可能是 m4a/ogg）。
    """
    try:
        head = audio_path.read_bytes()[:16]
    except OSError:
        head = b""
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:3] == b"ID3" or (len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "mp3"
    # 浏览器常见的几种：识别出来是为了**明确报错**（网关只收 wav/mp3），
    # 而不是照后缀猜成 wav 再把一坨 webm 发给网关
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if head[:4] == b"OggS":
        return "ogg"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return "m4a"
    return audio_path.suffix.lower().lstrip(".") or "mp3"


async def _cloud_mimo(audio_path: Path, key: str, model: str) -> str:
    ext = sniff_audio_format(audio_path)
    # 网关只收 wav/mp3：别的格式在这儿就说清楚，别让它回一句难懂的 400
    if ext not in ("wav", "mp3"):
        raise ASRUnavailable(
            f"音频格式是 {ext}，而云端网关只收 wav/mp3 —— 本机没有 ffmpeg 可供转码。"
            "请在设置页把语音输入换成浏览器内置录音（已默认），或安装 ffmpeg 后重试。"
        )
    b64 = base64.b64encode(audio_path.read_bytes()).decode()
    async with httpx.AsyncClient(timeout=60) as client:
        # ★ 2026-10-07（第 4 项）：**限流要退避** ✓ ——
        #   原来撞上 429 直接抛 ✓ 而语音这档最冤：**你得把那段话重说一遍** ✗
        #   （一次瞬时限流 = 用户白说一次 ✓）
        #   退避到上限仍不通 ⇒ `UpstreamBusy`，消息里**能照着做** ✓
        try:
            r = await retry.request_with_backoff(
                client, "POST", "https://api.xiaomimimo.com/v1/chat/completions",
                what="语音识别", attempts=3,
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": [
                        {"type": "input_audio", "input_audio": {"data": b64, "format": ext}},
                    ]}],
                },
                headers={"Authorization": "Bearer " + key},
            )
        except retry.UpstreamBusy as e:
            raise ASRUnavailable(f"{e}\n（着急就直接打字 ✓ 或到设置里换成**本地 ASR**（离线、不怕限流）✓）") from e
        if r.status_code >= 400:
            raise ASRUnavailable(
                f"ASR 上游错误 {r.status_code}：{r.text[:120]}\n"
                f"（可重试；一直失败就换本地 ASR 或直接打字。{_CLOUD_HINT}）")
    try:
        return str(r.json()["choices"][0]["message"].get("content") or "").strip()
    except Exception as e:  # 上游格式变了也要说人话
        raise ASRUnavailable(
            f"ASR 返回的格式看不懂（{type(e).__name__}）——可能服务商改了接口。{_CLOUD_HINT}") from e


# ═══ 本地千问3-ASR（2026-10-06 真接上）═══
#
# 官方用法（照抄 PyPI `qwen-asr` 的 Quick Inference ✓ 不自己编 API ✗）：
#   from qwen_asr import Qwen3ASRModel
#   model = Qwen3ASRModel.from_pretrained("Qwen/Qwen3-ASR-1.7B", dtype=torch.bfloat16, device_map="cuda:0")
#   results = model.transcribe(audio="xx.wav", language=None)
#   results[0].language / results[0].text
#
# ★ 模型**进程内缓存** ✓：加载一次要几十秒到几分钟 ✓，每次转写都重载等于不可用 ✗。
_MODEL_CACHE: dict[str, object] = {}


def local_tier_of(cfg: object | None = None) -> str:
    """当前选的本地档位（0.6b / 1.7b）。认不出来就用 0.6b ✓（更省显存、先能用 ✓）。"""
    raw = ""
    if cfg is not None:
        raw = str(getattr(getattr(cfg, "asr", None), "local_tier", "") or "")
    raw = (raw or os.environ.get("AGENT_SHELL_ASR_TIER", "") or "0.6b").strip().lower()
    return raw if raw in LOCAL_MODELS else "0.6b"


def _load_local_model(tier: str) -> object:
    """加载（或取缓存里的）本地模型 ✓ —— 失败一律转成**可照做**的提示 ✓。"""
    model_id = LOCAL_MODELS[tier]
    if model_id in _MODEL_CACHE:
        return _MODEL_CACHE[model_id]
    try:
        import torch                                    # noqa: PLC0415
        from qwen_asr import Qwen3ASRModel              # noqa: PLC0415
    except Exception as e:                              # noqa: BLE001
        raise ASRUnavailable(
            f"本地 ASR 的依赖还没装好（{type(e).__name__}: {str(e)[:120]}）。\n"
            + LOCAL_INSTALL_HINT) from e
    try:
        # 有 GPU 就用 GPU（bf16 ✓）；没有就 CPU（慢，但**能用** ✓ 数据仍然不出本机 ✓）
        use_cuda = bool(getattr(torch, "cuda", None) and torch.cuda.is_available())
        kwargs: dict[str, object] = {"max_new_tokens": 1024}
        if use_cuda:
            kwargs.update({"dtype": getattr(torch, "bfloat16", None), "device_map": "cuda:0"})
        model = Qwen3ASRModel.from_pretrained(model_id, **kwargs)
    except Exception as e:                              # noqa: BLE001
        raise ASRUnavailable(
            f"本地模型加载失败（{model_id}）：{type(e).__name__}: {str(e)[:160]}\n"
            "（首次会**自动下载模型**，请确认网络与磁盘空间；显存不够就换 0.6B 档）\n"
            + LOCAL_INSTALL_HINT) from e
    _MODEL_CACHE[model_id] = model
    return model


def _local_transcribe_sync(audio_path: Path, tier: str) -> str:
    model = _load_local_model(tier)
    results = model.transcribe(audio=str(audio_path), language=None)   # 自动识别语种 ✓
    if not results:
        raise ASRUnavailable("本地 ASR 没返回结果（音频可能是空的或格式不支持）")
    text = str(getattr(results[0], "text", "") or "").strip()
    if not text:
        raise ASRUnavailable("本地 ASR 返回了空文本（可能没人说话，或音频太短）")
    return text


async def transcribe(audio_path: Path, api_key: str | None = None, cfg: object | None = None) -> str:
    """音频文件 → 文字。按 `cfg.asr.provider` 选档（缺省 = 云端 MiMo）。"""
    prov = _provider(cfg)
    if prov in ("", "mimo", "cloud_mimo"):
        env = _key_env(cfg)
        key = api_key or os.environ.get(env, "")
        if not key:
            raise ASRUnavailable(
                f"未设置 {env} —— 云端语音输入不可用。两条路：\n"
                f"  ① 填 Key：设置 → 模型设置 → 粘贴 Key（可勾选写入用户级环境变量）\n"
                f"  ② {_CLOUD_HINT}")
        return await _cloud_mimo(audio_path, key, _model(cfg))

    if prov in ("local_qwen3", "local", "qwen3"):
        # ★★ 2026-10-06：**真跑本地模型** ✓（此前这里无条件抛"还没装" ✗ 装什么都没用 ✓）。
        #   模型加载是**同步阻塞**的（几十秒起）⇒ 丢进线程池 ✓ 不然整个后端会卡住 ✗。
        #   依然**不偷偷回退云端** ✓：用户选本地就是为了数据不出本机 ✓
        #   （真出问题就如实报错 + 给怎么修 ✓，绝不"静默上传" ✗）。
        tier = local_tier_of(cfg)
        return await asyncio.to_thread(_local_transcribe_sync, audio_path, tier)

    raise ASRUnavailable(
        f"未知的 ASR 档位：{prov!r}（可用：mimo=云端 / local_qwen3=本地）\n" + LOCAL_INSTALL_HINT)
