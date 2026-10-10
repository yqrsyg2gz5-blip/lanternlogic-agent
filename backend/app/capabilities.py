"""★ Phase 2 ④：统一的「能力槽 + 提供者」框架（一处声明，处处一致）。

要解决什么（用户原话："万能插槽"）：
  今天"某个能力能用哪些提供者"这件事散在**四个地方**，各写各的、还会互相漂移：
    · 对话 → `app/providers.py::_REGISTRY`（10 个键，含别名）
    · 出视频 → `app/main.py::_VIDEO_PROVIDERS`（模块级集合）
    · 出图 → `app/config.py::ImageCfg.provider`（默认 dashscope=云端）
    · ASR/TTS → `app/asr.py` / `app/tts.py` 各自的实现分支
  这正是 A5 那类问题（三处硬编码不一致）的同一形态，只不过换成了"能力"这个维度。
  更麻烦的是：**没有任何一处能回答"我现在这套到底能不能出声/出图/出视频"** ——
  用户只能在报错里逐个发现。

本模块做什么：
  · `CAPABILITIES`：唯一的声明表。每个能力有标签、当前选择（从 config 读）、
    候选提供者（含 kind=local/cloud/mock、需不需要 Key、可选模型、一句话说明）、
    **降级链**（fallbacks：首选坏了往哪退，终点必须是本地或 mock）。
  · `status()`：据此算出**每个能力此刻的真实状态**（ready / needs_key / not_installed），
    Key 只报"设没设"，**绝不回传值**。
  · `recommend_local_tier()`：按机器（显存/内存）推荐本地模型档位（0.6B / 1.7B / 量化），
    给"本地模型分档 + 机器体检推荐档位"这条需求一个可测的落点。
"""
from __future__ import annotations

import os
from typing import Any

# ── 本地模型分档（ASR 用）：显存/内存 → 推荐档位 ────────────────────
# ★ 2026-10-06 改准：千问3-ASR 官方**只发了两个规格**（0.6B / 1.7B ✓ Apache-2.0 ✓）
#   —— 原来还列了个 "1.7b-quant" ✗ 官方没有这个型号 ✓ 写着等于骗人 ✓ 删掉 ✓。
#   显存依据（官方）：0.6B 约 2GB / 1.7B 约 4GB ✓
#   实测量化精度差异（官方基准）：中文会议 6.88 vs 5.88 ✓ 只差约 1 个点 ✓
#   ⇒ 拿不准就推荐 0.6B ✓（快得多、显存友好、中文够用 ✓）
LOCAL_TIERS: tuple[dict[str, Any], ...] = (
    {"id": "0.6b", "label": "0.6B（最快，显存约 2G 就能跑，中文够用）", "min_vram_gb": 2, "min_ram_gb": 8},
    {"id": "1.7b", "label": "1.7B（中文最准，需要 4G+ 显存）", "min_vram_gb": 4, "min_ram_gb": 16},
)


def recommend_local_tier(vram_gb: float = 0.0, ram_gb: float = 0.0) -> dict[str, Any]:
    """按机器配置推荐本地模型档位（纯函数，便于测试与"机器体检"复用）。

    规则（保守优先，宁可推荐小档也别让用户跑不动）：
      显存 >= 4G 且 内存 >= 16G → 1.7B（中文最准 ✓）
      其余（含纯 CPU、显存未知）→ 0.6B（快、省显存、中文够用 ✓）
    """
    if vram_gb >= 4 and ram_gb >= 16:
        pick = "1.7b"
    else:
        pick = "0.6b"
    tier = next(t for t in LOCAL_TIERS if t["id"] == pick)
    return {"tier": pick, "label": tier["label"], "vram_gb": vram_gb, "ram_gb": ram_gb,
            "reason": ("显存够（≥4G）⇒ 上 1.7B，中文最准" if pick == "1.7b" else
                       "按 0.6B 起步（最快、省显存、中文只差约 1 个点）")}


def _probe_local_asr() -> tuple[str, str]:
    """本地 ASR（**千问3-ASR**）到底装没装 —— **真探测**，而且探的必须是**真正会用的那个包** ✓。

    ★★ 2026-10-06 修的一段历史（用户实测问出来的 ✗✗）：
      这里原来探的是 **`faster_whisper`** ✗ —— 那是 **OpenAI Whisper** 的社区实现 ✓
      跟"千问3-ASR"**没关系** ✗，而且当时的代码**根本没有加载任何模型的实现** ✗
      ⇒ 用户照提示装了 `faster-whisper` 之后 ✓ **界面会显示"✅ 可用"** ✓ 而一点就报错 ✗✓
      —— 这正是本项目最想避免的"界面骗人" ✓。

      ⇒ 现在探 `qwen_asr` ✓（真接上之后它就是**实际会 import 的那个包** ✓），
        并且在提示里写清"首次会自动下模型 / 显存要多少 / 想快点装 vLLM" ✓。
    """
    import importlib.util
    if importlib.util.find_spec("qwen_asr") is None:
        return "not_installed", (
            "还没安装（千问3-ASR，Apache-2.0 可商用，中文方言与歌声都支持）—— "
            "装法：pip install -U qwen-asr；首次用会自动下模型（0.6B 约 2G 显存 / 1.7B 约 4G）"
        )
    return "ready", "已安装 qwen-asr ✓ 首次转写会自动下载模型（0.6B 约 2G 显存 / 1.7B 约 4G）"


def _probe_faster_whisper() -> tuple[str, str]:
    """（保留旧名字，指向新探针 ✓ —— 免得别处还在调用它 ✗）"""
    return _probe_local_asr()


def _probe_kb_embedder(pid: str):
    """★ 2026-10-07：知识库**向量档位**的真探测 ✓（本地那档要真查库装没装 ✓）。

    背景：知识库原来**只有云端一条路**（阿里百炼）✗ —— 用户看到界面写着"需要阿里云百炼 Key"
    就问"**为什么要用阿里百炼这个呢？我没明白**"✓ 并明确要求走本地 ✓✓。
    ⇒ 现在两档并列 ✓ 而且**绝不能"本地不可用就悄悄用云端"** ✗（那等于背叛用户的信任 ✓）。
    """
    def _probe() -> tuple[str, str]:
        if pid == "local":
            from . import embed_local
            d = embed_local.describe()
            if d["available"]:
                return "ready", (f"已装 {d['backend']} ✓ {d['size']} ✓ {d['offline']}"
                                 f"（默认模型 {d['default_model']}）")
            return "not_installed", ("还没装 —— 装法：pip install -U fastembed"
                                     "（轻，不需要 torch）；首次用会自动下模型（约 100MB）"
                                     "◆ 选本地的好处：完全离线 ✓ 免费 ✓ 资料不出本机 ✓")
        # 云端（阿里百炼）
        key = os.environ.get("DASHSCOPE_API_KEY", "")
        if key:
            return "ready", "已设 DASHSCOPE_API_KEY ✓（⚠️ 建库时会把资料片段发到阿里云端）"
        return "needs_key", ("没设 DASHSCOPE_API_KEY（阿里云百炼，与通义同账号）"
                             "◆ 也可以**改用本地**（离线 ✓ 免费 ✓ 资料不出本机 ✓）")
    return _probe


def _probe_tts(pid: str):
    """★ 2026-10-06：语音合成每个后端**真探测** ✓ 并且把"怎么装"一起报出来 ✓。

    为什么（用户实测问的 ✗）：原来界面上只写"可用/未安装" ✓
    **该装什么包、多大、能不能离线，一个字都没有** ✗ ⇒ 用户只能自己猜 `pip install` 什么 ✓。
    现在把 `tts.HINTS` 里那句话直接带进状态说明 ✓ —— 用户在界面上**当场看到怎么办** ✓。

    返回 (state, detail)：state 用 `ready` / `not_installed` ✓（与全表口径一致 ✓）。
    """
    def _probe() -> tuple[str, str]:
        from .tts import HINTS, _TTS_REGISTRY
        cls = _TTS_REGISTRY.get(pid)
        if cls is None:
            return "not_installed", f"未知后端：{pid}"
        try:
            ok = bool(cls.available())
        except Exception:                                   # noqa: BLE001
            ok = False
        hint = HINTS.get(pid, {})
        tail = f"{hint.get('size', '')} ✓ {hint.get('offline', '')}".strip(" ✓")
        if ok:
            return "ready", f"已安装 ✓ {tail}"
        return "not_installed", f"未安装 —— {hint.get('install', '（未写安装方式）')}（{tail}）"
    return _probe


def _detect_hardware() -> tuple[float, float, str]:
    """尽力探测显存/内存（→ GB）。探测不到就返回 0/0 —— **宁可推荐小档，不许瞎猜大档**。

    显存走 `nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits`（NVIDIA 自带）；
    内存走 Windows 的 GlobalMemoryStatusEx（标准库 ctypes，不引第三方依赖）。
    """
    vram = ram = 0.0
    how = []
    try:
        import subprocess
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.total",
                            "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=6)
        if r.returncode == 0 and r.stdout.strip():
            vram = round(float(r.stdout.strip().splitlines()[0]) / 1024, 1)
            how.append("nvidia-smi")
    except Exception:
        pass
    try:
        import ctypes

        class _MemStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        st = _MemStatus()
        st.dwLength = ctypes.sizeof(_MemStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):   # type: ignore[attr-defined]
            ram = round(st.ullTotalPhys / (1024 ** 3), 1)
            how.append("GlobalMemoryStatusEx")
    except Exception:
        pass
    return vram, ram, "+".join(how) or "探测不到"


def local_recommendation() -> dict[str, Any]:
    """这台机器该用哪个本地档位（给"机器体检推荐档位"一条可测的落点）。"""
    vram, ram, how = _detect_hardware()
    rec = recommend_local_tier(vram, ram)
    rec["detected"] = bool(vram or ram)
    rec["method"] = how
    if not rec["detected"]:
        rec["reason"] = "探测不到显卡/内存，按最保守的 0.6B 起步"
    return rec


# ── 能力槽声明表 ────────────────────────────────────────────────────────
# kind: cloud=要 Key 的云服务 ｜ local=本机跑 ｜ mock=演示/离线占位
# models: 该提供者可选的模型（空 = 由用户自由填）
CAPABILITIES: dict[str, dict[str, Any]] = {
    "chat": {
        "label": "对话模型（大脑）",
        "providers": {
            "mimo": {"label": "小米 MiMo", "kind": "cloud", "key_env": "XIAOMI_MIMO_API_KEY",
                     "models": ["mimo-v2.6-flash", "mimo-v2.5", "mimo-v2.5-pro"]},
            "deepseek": {"label": "DeepSeek", "kind": "cloud", "key_env": "DEEPSEEK_API_KEY",
                         "models": ["deepseek-chat", "deepseek-reasoner"]},
            "qwen": {"label": "通义千问", "kind": "cloud", "key_env": "DASHSCOPE_API_KEY",
                     "models": ["qwen-max", "qwen-plus", "qwen-turbo"]},
            "glm": {"label": "智谱 GLM", "kind": "cloud", "key_env": "GLM_API_KEY",
                    "models": ["glm-4-plus", "glm-4-flash"]},
            "kimi": {"label": "Kimi", "kind": "cloud", "key_env": "MOONSHOT_API_KEY",
                     "models": ["moonshot-v1-8k", "moonshot-v1-32k"]},
            "claude": {"label": "Claude", "kind": "cloud", "key_env": "ANTHROPIC_API_KEY",
                       "models": ["claude-sonnet-4-5", "claude-opus-4-1"]},
            "ollama": {"label": "本地模型（Ollama）", "kind": "local", "key_env": None,
                       "models": ["qwen3:0.6b", "qwen3:1.7b", "qwen3:1.7b-q4_K_M"]},
            # ★ 2026-10-10（用户点名要接的本地模型 ✓）：
            #   Bonsai 2 27B —— llama.cpp 起在 127.0.0.1:8080（桌面「Bonsai2 本地模型」✓）
            #   kind=local（无需 Key ✓）· 64K 上下文 · 带视觉
            #   ★ 没放进 fallbacks ✗：它要占约 9.5GB 显存、与 ComfyUI 抢卡 ✓
            #     当"自动降级终点"会变成"一失败就偷偷占满显存"✗ —— 要不要退到它由用户决定 ✓
            "bonsai": {"label": "本地 Bonsai 2 27B（llama.cpp）", "kind": "local", "key_env": None,
                       "models": ["bonsai-2-27b"]},
            "mock": {"label": "Mock（演示/离线）", "kind": "mock", "key_env": None, "models": ["mock"]},
        },
        "fallbacks": ["ollama", "mock"],
    },
    "image": {
        "label": "出图",
        "providers": {
            "dashscope": {"label": "通义万相（云端）", "kind": "cloud", "key_env": "DASHSCOPE_API_KEY",
                          "models": ["wanx2.1-t2i-turbo", "wanx2.1-t2i-plus"]},
            "comfyui": {"label": "本地 ComfyUI", "kind": "local", "key_env": None, "models": [],
                        # ★ 2026-10-07：**真探** ComfyUI 在不在跑 ✓（原来靠 kind=local 直接报"可用"✗
                        #   见 `_probe_comfyui` 的注释 ✓）
                        "probe": lambda: _probe_comfyui()},
        },
        "fallbacks": ["comfyui"],
    },
    "video": {
        "label": "出视频",
        "providers": {
            "minimax": {"label": "MiniMax 海螺", "kind": "cloud", "key_env": "MINIMAX_API_KEY", "models": []},
            "wan": {"label": "通义万相视频", "kind": "cloud", "key_env": "DASHSCOPE_API_KEY", "models": []},
            "seedance": {"label": "豆包 Seedance", "kind": "cloud", "key_env": "ARK_API_KEY", "models": []},
            "kling": {"label": "可灵", "kind": "cloud", "key_env": "KLING_API_KEY", "models": []},
            "comfyui": {"label": "本地 ComfyUI（Wan 2.1）", "kind": "local", "key_env": None, "models": [],
                        # ★ 2026-10-10：**这一格漏了探针** ✗ ⇒ 落回 `kind=local` 的
                        #   「本机运行，不需要 Key」+ ready ✗，而同一张表里出图那格却如实写着
                        #   「ComfyUI 没在运行」✗（用户实测抓出来的"一张表自己打架"✓）
                        "probe": lambda: _probe_comfyui(what="本地出视频")},
        },
        "fallbacks": ["comfyui"],
    },
    "asr": {
        "label": "语音转文字（听）",
        "providers": {
            # ★ 2026-10-04 修正：这一格我第一版写的是 local_qwen3 + dashscope，**是错的** ——
            #   代码里 `app/asr.py` 一直走的是云端 MiMo（mimo-v2.5-asr，OpenAI input_audio 格式）。
            #   这正说明"声明表必须对着代码写"，也说明要有一条锚点把两者钉在一起
            #   （见 tests/test_asr_two_tier.py::test_capability_table_matches_code）。
            "mimo": {"label": "云端 MiMo（有 Key 就能用，零下载）", "kind": "cloud",
                     "key_env": "XIAOMI_MIMO_API_KEY", "models": ["mimo-v2.5-asr"]},
            "local_qwen3": {"label": "本地 千问3-ASR（离线，数据不出本机）", "kind": "local",
                            "key_env": None, "models": ["qwen3-asr-0.6b", "qwen3-asr-1.7b"],
                            "probe": _probe_faster_whisper},
        },
        "fallbacks": ["local_qwen3"],
    },
    "kb": {
        "label": "知识库向量（找得到靠它）",
        "providers": {
            "local": {"label": "本地（离线 ✓ 免费 ✓ 资料不出本机）", "kind": "local",
                      "key_env": None, "models": [], "probe": _probe_kb_embedder("local")},
            "dashscope": {"label": "阿里百炼（云端 ✓ 免下载 ✗ 资料会外传）", "kind": "cloud",
                          "key_env": "DASHSCOPE_API_KEY", "models": [],
                          "probe": _probe_kb_embedder("dashscope")},
        },
        # ★ 兜底顺序：**本地优先** ✓（用户 2026-10-07 拍板 ✓）
        "fallbacks": ["local"],
    },
    "tts": {
        "label": "语音合成（说）",
        "providers": {
            "edge": {"label": "Edge TTS（在线，免费，免装）", "kind": "local", "key_env": None,
                     "models": [], "probe": _probe_tts("edge")},
            "qwen3tts": {"label": "千问3-TTS（本地，中文最准，Apache-2.0 可商用）", "kind": "local", "key_env": None,
                         "models": [], "probe": _probe_tts("qwen3tts"),
                         # ★ 2026-10-08：**音色表**（界面上的下拉直接用它 ✓ 实测拿到的 9 个 ✓）
                         #   `zh` 标出中文音色 ✓（免得用户挑到日韩音色去念中文 ✓）
                         "voices": [{"id": v, "zh": v in ("vivian", "serena", "uncle_fu", "dylan", "eric")}
                                    for v in ("vivian", "serena", "uncle_fu", "dylan", "eric",
                                              "ryan", "aiden", "ono_anna", "sohee")]},
            "melotts": {"label": "MeloTTS（本地，MIT 可商用）", "kind": "local", "key_env": None,
                        "models": [], "probe": _probe_tts("melotts")},
            "pyttsx3": {"label": "pyttsx3（用系统自带语音）", "kind": "local", "key_env": None,
                        "models": [], "probe": _probe_tts("pyttsx3")},
        },
        # ★ 兜底顺序：免装的排前面 ✓（这样"该选谁"不用用户自己猜 ✓）
        #   ★ 2026-10-08：qwen3tts 排在最前（中文词最准 ✓ 实测同一句 ASR 回听与 edge 同级 ✓）
        #   ★ Kokoro 已**下架** ✗（中文系统性错音 ✓ 20+ 音色全一样 ✓ 实测 ✓）—— 不进这张表 ✓
        "fallbacks": ["edge", "qwen3tts", "pyttsx3", "melotts"],
    },
}


def _probe_comfyui(url: str = "", what: str = "本地出图") -> tuple[str, str]:
    """★ 2026-10-07（体检⑥ 引出来的）：**本地出图这一档原来根本不探** ✗。

    ★ 2026-10-10（用户实测抓到的）：**出视频那一档也漏了探针** ✗ ——
      同一张能力表里，出图如实写着「ComfyUI 没在运行」✓ 而视频写着
      「本机运行，不需要 Key」+ ready ✗（那是 `kind=local` 的默认话术 ✓）
      ⇒ **一张表自己打架** ✓ 用户当场问："我本地都有，为什么用不了" ✓
      ⇒ 现在两档都挂这个探针 ✓ 文案按 `what` 走（本地出图 / 本地出视频）✓

    现场：这一格写的是 `kind == "local"` ⇒ `_provider_status` 直接给
    **"本机运行，不需要 Key" + ready** ✗ —— 那是**废话** ✓ 它没回答唯一重要的问题：
    **ComfyUI 到底在不在跑** ✓。

    ⇒ 后果：ComfyUI 没开的时候，能力总览照样写"可用" ✓ 用户点出图 ⇒ 卡住/报连接错 ✗
      —— 正是本项目一直在收拾的那类"**界面骗人**"✓（ASR 那档栽过一次一模一样的 ✓）。

    探法：给 ComfyUI 的 `/system_stats` 发一个小请求（本机回环、毫秒级 ✓ 不花钱 ✓）。
    如实三态：
      · 在跑      ⇒ ready + **把它报的显卡/显存写出来**（用户一眼知道是谁在画 ✓）
      · 没在跑    ⇒ not_installed + **怎么开**（而不是一句"本机运行"✓）
      · 地址没配  ⇒ not_installed + 说清配哪个字段
    """
    import json as _json
    import urllib.request

    base = (url or "").rstrip("/")
    if not base:
        try:
            from .config import load_config
            c = load_config()
            base = str(getattr(c.executor, "comfyui_url", "") or "http://127.0.0.1:8189").rstrip("/")
        except Exception:                                   # noqa: BLE001
            base = "http://127.0.0.1:8189"
    try:
        with urllib.request.urlopen(f"{base}/system_stats", timeout=2.5) as r:
            d = _json.loads(r.read().decode("utf-8", "replace"))
        dev = ((d.get("devices") or [{}])[0]) or {}
        name = str(dev.get("name") or "?").split(":")[0].strip()
        vram = dev.get("vram_free")
        extra = f"，{name}" if name and name != "?" else ""
        if isinstance(vram, (int, float)) and vram > 0:
            extra += f"（空闲显存 {vram / 1024 ** 3:.1f} GB）"
        return "ready", f"ComfyUI 正在跑（{base}）{extra}"
    except Exception:                                       # noqa: BLE001
        return "not_installed", (
            f"ComfyUI 没在运行（{base} 连不上）——{what}**要先把 ComfyUI 起起来** ✓；"
            "不想自己起就把引擎切回云端（要 Key、按量计费）"
        )


def _current_provider(cap: str, cfg: Any) -> str:
    """从 config 里读这个能力"现在选了谁"（各处字段名不同，集中在这里翻译）。"""
    if cap == "chat":
        return str(getattr(cfg.model, "provider", "") or "")
    if cap == "image":
        return str(getattr(cfg.image, "provider", "") or "")
    if cap == "video":
        return str(getattr(cfg.video, "provider", "") or "")
    if cap == "tts":
        # ★★ 2026-10-06（用户实测问出来的 ✗✗）：这里**原来硬写着 `return "melotts"`** ✗ ——
        #   而实际生效的后端是接口层默认的 `edge` ✓ ⇒ **能力总览表跟真实行为不一致** ✓
        #   （用户看到的"三处口径打架"里，这就是最硬的那一处 ✓）。
        #   ⇒ 现在从**配置**读 ✓（`config.tts.backend` ✓ 界面上可切 ✓）✓
        return str(getattr(getattr(cfg, "tts", None), "backend", "") or "edge")
    if cap == "kb":
        # ★ 2026-10-07：知识库向量档位（原来这张表里**根本没有"知识库"这一行** ✗——
        #   用户翻遍能力总览也看不出"知识库靠什么找得到"✓ 更看不出"资料会不会外传"✗）。
        #   现在从配置读 ✓（`config.kb.embedder` ✓ 默认 local ✓ 界面上可切 ✓）
        return str(getattr(getattr(cfg, "kb", None), "embedder", "") or "local")
    if cap == "asr":
        # ★ 从配置读（以前这里写死 local_qwen3 —— 而代码其实走云端 MiMo，表与实现不一致）
        return str(getattr(getattr(cfg, "asr", None), "provider", "") or "mimo")
    return ""


def _provider_status(pid: str, spec: dict[str, Any]) -> dict[str, Any]:
    """单个提供者的此刻状态。**只报 Key 设没设，绝不回传 Key 值。**"""
    # ★ 2026-10-08：**声明表里的静态信息要原样带出去** ✓（例如 TTS 的 `voices` 音色表 ✓）
    #   —— 以前这里只回 state/detail ✗ ⇒ 界面拿不到音色表 ⇒ 做不了下拉 ✓
    extra = {k: v for k, v in spec.items() if k not in ("probe", "key_env", "kind", "label", "models")}

    def _with(d: dict[str, Any]) -> dict[str, Any]:
        return {**d, **extra}

    probe = spec.get("probe")
    if callable(probe):
        state, detail = probe()
        return _with({"state": state, "detail": detail})
    env = spec.get("key_env")
    if spec["kind"] == "local":
        return _with({"state": "ready", "detail": "本机运行，不需要 Key"})
    if spec["kind"] == "mock":
        return _with({"state": "ready", "detail": "离线占位"})
    if not env:
        return _with({"state": "unknown", "detail": "没有配置 Key 环境变量名"})
    set_ = bool(os.environ.get(env))
    # ★ 2026-10-07（体检⑥ 引出来的第二处）：**别把话说满** ✗ ——
    #   `kind == "cloud"` 的那几档，此前一律写「XXX_API_KEY 已设置」+ **ready** ✓
    #   而"有 Key"离"能用"还差着：**额度用完 / 没开通 / 欠费** 全都会在真调用时 403 ✗
    #   （本机的阿里百炼正是这个状态 ✓ 万相出视频已经实测 403 过 ✓）
    #   ⇒ 措辞改成"**Key 已设置（能不能真用要真调用才知道）**"✓
    #     状态仍给 ready ✓（本地探不出云端额度的真实状态 ✓ **不假装探到了** ✓ 也不吓唬人 ✓）。
    if set_ and spec.get("kind") == "cloud":
        return {"state": "ready",
                "detail": f"{env} 已设置（有 Key ≠ 一定能用：额度用完/没开通都会在真调用时报错）"}
    return {"state": "ready" if set_ else "needs_key",
            "detail": f"{env} 已设置" if set_ else f"还没设置环境变量 {env}"}


def fallback_hint(cfg: Any, cap: str = "chat", error: str = "") -> str:
    """某个能力的提供者**当前用不了**时，给用户三条可照做的路（由声明表生成）。

    为什么要有它：以前的报错是干巴巴一句
        「模型提供者不可用（provider=mimo）：ValueError: model.base_url 未配置…」
    用户知道坏了、不知道**往哪走**。而"往哪走"这件事声明表里已经写全了
    （降级链 + 每个提供者的 Key 环境变量与中文标签）——所以由这里统一生成，
    各处不许再手写一遍（手写就会像 A5 那样三处漂移）。

    三条路固定是这个次序（越靠前越推荐）：
      ① 修首选：填 Key（并说明"可写进用户级环境变量，重启后仍有效"）
      ② 退本地：降级链里第一个"本机就能跑"的（不用 Key、数据不出本机）
      ③ 先演示：mock（离线占位，能跑通流程但不会真的思考）
    """
    spec = CAPABILITIES.get(cap)
    if not spec:
        return error or f"能力 {cap} 未声明"
    provs = spec["providers"]
    cur = _current_provider(cap, cfg)
    cur_spec = provs.get(cur, {})
    st = status(cfg)["capabilities"].get(cap, {})
    lines = [error] if error else []
    lines.append(f"「{spec['label']}」现在用不了（当前：{cur or '未选择'}）。三条路：")

    # ① 修首选
    env = cur_spec.get("key_env")
    if env:
        lines.append(f"  ① 填 Key：设置 → 模型设置 → 粘贴 Key（写进环境变量 {env}，"
                     f"可勾选『同时写入用户级环境变量』——重启后仍然有效）")
    else:
        lines.append("  ① 修配置：设置 → 模型设置 → 检查模型名 / API 地址")

    # ② 退本地（降级链里第一个 local）
    local = next((f for f in spec.get("fallbacks", []) if provs.get(f, {}).get("kind") == "local"), None)
    if local:
        lst = st.get("providers", {}).get(local, {})
        suffix = "，本机已就绪" if lst.get("state") == "ready" else ""
        lines.append(f"  ② 换本地：切到「{provs[local]['label']}」{suffix}"
                     "——不用 Key、数据不出本机")

    # ③ 先演示
    mock = next((f for f in spec.get("fallbacks", []) if provs.get(f, {}).get("kind") == "mock"), None)
    if mock:
        lines.append(f"  ③ 先用演示：切到「{provs[mock]['label']}」——能跑通流程，但不会真的思考")
    lines.append("（设置改完立刻生效；改的是当前进程用的配置，重启后仍从 config.json 读）")
    return "\n".join(lines)

def status(cfg: Any) -> dict[str, Any]:
    """把声明表 + 当前配置 + 环境，算成"每个能力此刻的真实状态"。

    返回结构（给界面/诊断用）：
      {"capabilities": {cap: {label, current, current_state, providers: {...},
                              fallbacks: [...], next_available: str|None}}, ...}
    `next_available` = 降级链里第一个可用者（首选不通时用户该往哪退）。
    """
    out: dict[str, Any] = {}
    for cap, spec in CAPABILITIES.items():
        cur = _current_provider(cap, cfg)
        provs: dict[str, Any] = {}
        for pid, pspec in spec["providers"].items():
            st = _provider_status(pid, pspec)
            # ★ 只把**可序列化**的字段发出去：`probe` 是函数、`key_env` 只留名字
            #   （第一版把 probe 也塞进去了 ⇒ FastAPI 响应校验直接 500，本班实测）。
            pub = {k: v for k, v in pspec.items() if k not in ("key_env", "probe")}
            provs[pid] = {**pub, **st, "key_env": pspec.get("key_env")}
        cur_state = provs.get(cur, {}).get("state", "unknown" if cur else "unset")
        nxt = next((f for f in spec.get("fallbacks", []) if provs.get(f, {}).get("state") == "ready"), None)
        out[cap] = {"label": spec["label"], "current": cur, "current_state": cur_state,
                    "providers": provs, "fallbacks": list(spec.get("fallbacks", [])),
                    "next_available": nxt}
    return {"capabilities": out, "local_tiers": list(LOCAL_TIERS),
            "local_recommendation": local_recommendation()}
