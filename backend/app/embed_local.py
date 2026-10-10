# -*- coding: utf-8 -*-
"""本地向量化（embedding）—— 2026-10-07 用户拍板："**我做这个知识库本地的**" ✓

## 为什么要有它（用户原话）

> "入库后 Agent 回答时会自动检索引用（带出处）。**需要阿里云百炼 Key** 做向量化"
> —— "**这个为什么要用阿里百炼这个呢？我没明白**"
> —— "**我做这个知识库本地的，没想到弄什么通义千问的了**"

★ 原来的设计（写这段时的想法 ✓ 现在要改 ✗）：
  知识库一开始**只接了阿里 DashScope** 的 `text-embedding-v3` ✓
  理由只是"你既然有通义 Key ✓ 就顺手复用了 ✓ 不用再装东西" ✓
  —— 但这条**与整个项目的定位自相矛盾** ✗✓：
  这个项目讲的是"**本地优先、数据不出本机**"✓ 而知识库**恰恰把你的资料传出去**✗✗。

⇒ 现在做成 **两档并列** ✓（跟语音"听/说"完全同一个思路 ✓）：
    · **local**（默认 ✓）：本地模型 ✓ 完全离线 ✓ 免费 ✓ **数据不出本机** ✓
    · **dashscope**：云端 ✓ 免下载 ✓ 但内容会传到阿里 ✗ 且按量计费

★ 一条铁规矩（与 ASR 同款 ✓）：**绝不静默回退** ✗
  选了本地就是用本地 ✓ —— 本地模型没装/加载失败 ✓ 就**如实报错 + 给装法** ✓
  绝**不偷偷把资料发到云端** ✗（那正是用户选本地要避免的事 ✓）。

★ 依赖选型：优先 **fastembed**（ONNX ✓ 几十 MB ✓ **不需要 torch** ✓ 装得快 ✓）
  退而求其次用 **sentence-transformers**（重 ✓ 但生态最标准 ✓；本机因 ASR 已装 torch ✓）
  两个都没有 ⇒ 报错并给一键装法 ✓。
"""
from __future__ import annotations

import os
import threading
from typing import Any

# 默认本地模型：中文小模型，约 100MB 量级，512 维（够用且快 ✓）
DEFAULT_LOCAL_MODEL = "BAAI/bge-small-zh-v1.5"

INSTALL_HINT = (
    "装本地向量库（离线、免费、数据不出本机）：\n"
    "  1) 推荐（轻，不需要 torch）：pip install -U fastembed\n"
    "  2) 或者（重，但生态标准）：pip install -U sentence-transformers\n"
    "  3) 首次使用时会**自动下载向量模型**（约 100MB 量级，一次性 ✓ 之后永久离线 ✓）\n"
    f"  4) 默认模型：{DEFAULT_LOCAL_MODEL}（可在设置里改）"
)

_LOCK = threading.Lock()
_MODEL: Any = None
_MODEL_ID: str = ""


def _fastembed_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("fastembed") is not None


def _st_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("sentence_transformers") is not None


def available() -> bool:
    """本地向量**能不能用**（真探测：两个库有一个就行 ✓ —— 模型下次载时才下 ✓）。"""
    return _fastembed_available() or _st_available()


def backend_name() -> str:
    """当前会用哪个实现（给界面显示 ✓ 让用户知道自己在跑什么 ✓）。"""
    if _fastembed_available():
        return "fastembed"
    if _st_available():
        return "sentence-transformers"
    return ""


def _load(model_id: str) -> Any:
    """载入（并进程内缓存）本地向量模型 ✓ —— 加载一次几百毫秒到几秒 ✓ 不能每问一次都载 ✗。"""
    global _MODEL, _MODEL_ID
    with _LOCK:
        if _MODEL is not None and _MODEL_ID == model_id:
            return _MODEL
        if not available():
            raise RuntimeError("本地向量库还没装 —— " + INSTALL_HINT)
        # ★ 国内下载慢时，用户可以自己设 HF_ENDPOINT 指向镜像 ✓
        #   （**不替用户默认设置** ✗ —— 那等于把资料交给一个第三方镜像 ✓ 得用户自己决定 ✓）
        try:
            if _fastembed_available():
                from fastembed import TextEmbedding
                _MODEL = ("fastembed", TextEmbedding(model_name=model_id))
            else:
                from sentence_transformers import SentenceTransformer
                _MODEL = ("st", SentenceTransformer(model_id))
        except Exception as e:                              # noqa: BLE001
            hint = ""
            if not os.environ.get("HF_ENDPOINT"):
                hint = ("\n（首次要下载模型 ✓ 国内网络慢的话，可自行设置环境变量 "
                        "HF_ENDPOINT=https://hf-mirror.com 走镜像 —— 是否信任该镜像由你决定 ✓）")
            raise RuntimeError(
                f"本地向量模型加载失败（{model_id}）：{type(e).__name__}: {str(e)[:200]}{hint}"
                "\n" + INSTALL_HINT) from e
        _MODEL_ID = model_id
        return _MODEL


def embed(texts: list[str], model_id: str = "") -> list[list[float]]:
    """把一批文本变成向量 ✓（本地 ✓ 不联网 ✓）。

    ⚠️ **返回条数必须与输入条数一致** ✓ —— 调用方是按顺序 zip 配对的 ✓
       少一条就会让"文本 ↔ 向量"整体错位 ⇒ 检索张冠李戴 ✗（知识库那边已加校验 ✓ 这里也兜一道 ✓）。
    """
    if not texts:
        return []
    kind, model = _load(model_id or DEFAULT_LOCAL_MODEL)
    if kind == "fastembed":
        vecs = [list(map(float, v)) for v in model.embed(texts)]
    else:
        arr = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        vecs = [list(map(float, v)) for v in arr]
    if len(vecs) != len(texts):
        raise RuntimeError(
            f"本地向量化返回条数不对：发出 {len(texts)} 条，回来 {len(vecs)} 条 —— 拒绝继续"
            "（否则文本与向量会整体错位，检索结果将张冠李戴）。"
        )
    return vecs


def describe() -> dict[str, Any]:
    """给界面/能力表用的一份说明 ✓。"""
    return {
        "available": available(),
        "backend": backend_name(),
        "default_model": DEFAULT_LOCAL_MODEL,
        "install": INSTALL_HINT,
        "size": "模型约 100MB 量级（首次下载，之后永久离线）",
        "offline": "完全离线 ✓ 免费 ✓ 数据不出本机 ✓",
    }
