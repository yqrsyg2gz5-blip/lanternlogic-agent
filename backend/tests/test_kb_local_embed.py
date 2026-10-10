# -*- coding: utf-8 -*-
"""知识库**本地向量**的专属测试 —— 2026-10-07 用户拍板改的（✗→✓）。

## 用户原话（这就是验收标准）

> "入库后 Agent 回答时会自动检索引用（带出处）。**需要阿里云百炼 Key** 做向量化"
> —— "**这个为什么要用阿里百炼这个呢？我没明白**"
> —— "**我做这个知识库本地的，没想到弄什么通义千问的了**"

## 为什么这条改得对（不是口味问题）

这个项目讲的是"**本地优先、数据不出本机**"✓ 而原来的知识库
**恰恰把用户资料传到阿里云端** ✗✓ —— 这是**定位上的自相矛盾** ✓ 用户一眼就看出来了 ✓。

## 一条铁规矩（与 ASR 同款 ✓ 这条测试就是钉它 ✓）

**绝不静默回退** ✗ —— 选了本地就**只用本地** ✓
本地模型没装/加载失败 ⇒ **如实报错 + 给装法** ✓
**绝不允许偷偷把资料发到云端** ✗✗（那正是用户选本地要避免的事 ✓）。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import kb as K  # noqa: E402


def _cfg(embedder: str, model: str = "BAAI/bge-small-zh-v1.5"):
    return type("X", (), {"kb": type("K", (), {"embedder": embedder, "local_model": model})()})()


@pytest.fixture()
def pin(monkeypatch):
    """改档位的开关 ✓（测哪档就钉哪档 ✓）。"""
    def _pin(embedder: str, model: str = "BAAI/bge-small-zh-v1.5"):
        from app import config as C
        monkeypatch.setattr(C, "load_config", lambda *a, **kw: _cfg(embedder, model))
    return _pin


# ═══ ① 默认就是本地（用户要的 ✓）═══

def test_default_embedder_is_local():
    """**默认必须是本地** ✓ —— 用户要的就是"不弄通义那套" ✓。"""
    from app.config import KbCfg
    assert KbCfg().embedder == "local", "默认又变回云端了 ✗（用户明确要求本地 ✓）"
    assert KbCfg().local_model.startswith("BAAI/"), "默认模型不像个中文小模型 ✗"


def test_local_path_is_used_by_default(monkeypatch):
    """不带配置时走的就是本地 ✓（真调 `embed_local.embed` ✓ 而不是阿里 ✓）。"""
    from app import config as C
    monkeypatch.setattr(C, "load_config", lambda *a, **kw: _cfg("local"))
    called: list[list[str]] = []

    def fake_embed(texts, model_id=""):                      # noqa: ARG001
        called.append(texts)
        return [[1.0, 0.0] for _ in texts]

    from app import embed_local
    monkeypatch.setattr(embed_local, "embed", fake_embed)
    out = asyncio.run(K.embed_texts(["甲", "乙"]))
    assert out == [[1.0, 0.0], [1.0, 0.0]]
    assert called == [["甲", "乙"]], called


# ═══ ② 铁规矩：本地不行时**绝不许偷偷改云端** ═══

def test_local_missing_never_falls_back_to_cloud(monkeypatch, pin):
    """★★ **这条最要紧** ✓✓ —— 本地用不了 ⇒ 必须**报错** ✓ 而不是悄悄用云端 ✓。

    真事：用户选本地就是为了"资料不出本机" ✓（原话："我做这个知识库本地的"✓）
    ⇒ 若失败时偷偷发到阿里 ✓ 那就是**这个项目最不能容忍的那种行为** ✗✗
      （跟"选了本地 ASR 却偷偷上传录音"是同一类 ✓）。
    """
    pin("local")
    cloud_called: list[str] = []

    async def _spy(texts):
        cloud_called.append("cloud")
        return [[0.0, 1.0] for _ in texts]

    monkeypatch.setattr(K, "_embed_dashscope", _spy)         # 云端那条路：只要被碰一下就记下来 ✓
    from app import embed_local

    def _boom(texts, model_id=""):                           # noqa: ARG001
        raise RuntimeError("本地向量库还没装 —— " + embed_local.INSTALL_HINT)

    monkeypatch.setattr(embed_local, "embed", _boom)
    with pytest.raises(RuntimeError, match="本地向量库还没装"):
        asyncio.run(K.embed_texts(["资料"]))
    assert cloud_called == [], "本地失败却偷偷把资料发去云端了 ✗✗（这是最坏的惊喜）"


def test_local_error_message_tells_you_how_to_install(monkeypatch, pin):
    """本地缺依赖时，报错必须**可照做** ✓（装什么包、多大、能不能离线 ✓）。

    ★★ 2026-10-07 修（本班实测抓到的 ✗✗）：这条原来写的是
       "**本机确实没装** ✓ 所以走的是真实报错路径" ✗ —— 那是**把测试建在机器状态上** ✗：
         · 用户在设置页点了「一键装」（本机现在 fastembed 已装 ✓）⇒ 这条**当场变红** ✗
           （不是代码坏了 ✓ 是测试假设过期了 ✓）
         · 更糟：它调的是**真的** `embed_local.embed(["x"])` ✗ ⇒ 在**装了 fastembed 的机器上**
           会**真的去下约 100MB 的向量模型** ✗ —— 正是本仓那条"**单元测试不许碰真网络/真下载**"✗
       ⇒ 现在**显式把依赖探测钉成"没装"** ✓：走的仍是 `_load` 里**真实的**缺依赖分支
         （`if not available(): raise RuntimeError("本地向量库还没装 —— " + INSTALL_HINT)` ✓）
         既与机器状态无关 ✓ 也**一个字节都不下载** ✓。
    """
    pin("local")
    from app import embed_local
    monkeypatch.setattr(embed_local, "_MODEL", None)          # 防同进程里已缓存模型 ⇒ 提前返回 ✓
    monkeypatch.setattr(embed_local, "_MODEL_ID", "")
    monkeypatch.setattr(embed_local, "_fastembed_available", lambda: False)
    monkeypatch.setattr(embed_local, "_st_available", lambda: False)
    with pytest.raises(RuntimeError) as ei:
        embed_local.embed(["x"])
    msg = str(ei.value)
    assert "pip install" in msg and "fastembed" in msg, f"没给装法 ✗：{msg[:120]}"
    assert "离线" in msg and "不出本机" in msg, "没讲清本地这档的好处 ✗"


# ═══ ③ 云端档：没 Key 时要指回去本地那条路 ✓ ═══

def test_cloud_without_key_points_back_to_local(monkeypatch, pin):
    """选了云端但没 Key ⇒ 报错里**必须提"也可以改用本地"** ✓ ——
    不然用户只知道"得去申请个 Key"✗ 而不知道**本机就能干**✓。"""
    pin("dashscope")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    with pytest.raises(RuntimeError) as ei:
        asyncio.run(K.embed_texts(["x"]))
    msg = str(ei.value)
    assert "DASHSCOPE_API_KEY" in msg, "没说清缺哪把 Key ✗"
    assert "本地" in msg, f"没告诉用户还有本地这条路 ✗：{msg[:140]}"


# ═══ ④ 未知档位 / 条数校验 / 空输入 ═══

def test_unknown_embedder_is_rejected(pin):
    pin("随便写的档")
    with pytest.raises(RuntimeError, match="未知的知识库向量档位"):
        asyncio.run(K.embed_texts(["x"]))


def test_empty_input_short_circuits(monkeypatch, pin):
    """空输入**不该去调模型** ✓（白白加载一次模型很贵 ✓）。"""
    pin("local")
    from app import embed_local
    called: list[int] = []
    monkeypatch.setattr(embed_local, "embed",
                       lambda texts, model_id="": called.append(len(texts)) or [])
    assert asyncio.run(K.embed_texts([])) == []
    assert called == [], "空输入也去调模型了 ✗"


def test_local_count_mismatch_is_refused(monkeypatch):
    """★ 本地实现**自己也兜一道条数校验** ✓ —— 少一条就会让"文本↔向量"整体错位 ✗
    （云端那边已经钉过 ✓；本地这条是新代码 ✓ 同样要钉 ✓）。"""
    from app import embed_local

    class _FakeModel:
        def embed(self, texts):
            return [[1.0] for _ in texts[:-1]]               # 故意少一条 ✓

    monkeypatch.setattr(embed_local, "_load", lambda mid: ("fastembed", _FakeModel()))
    with pytest.raises(RuntimeError, match="条数不对"):
        embed_local.embed(["a", "b", "c"])


def test_local_describe_is_honest_about_size_and_offline():
    """给界面看的那份说明 ✓ 必须写清**多大 / 能不能离线** ✓（用户问的就是这些 ✓）。"""
    from app import embed_local
    d = embed_local.describe()
    assert d["default_model"] and "100MB" in d["size"], d
    assert "离线" in d["offline"] and "不出本机" in d["offline"], d
    assert d["install"], "没写怎么装 ✗"


# ═══ ⑤ 能力总览 / 设置接口（口径必须只有一处 ✓）═══

def test_capability_table_lists_the_kb_row_and_reads_from_config():
    """★ 能力总览里**必须有"知识库向量"这一行** ✓ 而且**从配置读当前档** ✓。

    真事：原来这张表里**压根没有知识库这一行** ✗ ——
    用户翻遍能力总览也看不出"知识库靠什么找得到"✓ 更看不出"**资料会不会外传**"✗✓
    （而他最在意的恰恰是后者 ✓）。
    """
    from app import capabilities as C
    from app.config import KbCfg, load_config
    assert "kb" in C.CAPABILITIES, "能力表里没有知识库这一行 ✗"
    assert set(C.CAPABILITIES["kb"]["providers"]) == {"local", "dashscope"}, "档位不对 ✗"
    cfg = load_config()
    assert C._current_provider("kb", cfg) == str(cfg.kb.embedder), "没从配置读 ✗"
    # 默认档必须是本地 ✓（用户拍板的 ✓）
    assert KbCfg().embedder == "local"


def test_local_probe_says_how_to_install_and_why_local():
    """本地那档的**状态说明**要能回答用户的三个问题 ✓：
    装什么 ✓ 多大 ✓ 以及**为什么值得选本地**（离线/免费/不出本机 ✓）。"""
    from app import capabilities as C
    st = C._provider_status("local", C.CAPABILITIES["kb"]["providers"]["local"])
    if st["state"] == "not_installed":
        assert "pip install" in st["detail"] and "fastembed" in st["detail"], st
    assert "不出本机" in st["detail"] or "离线" in st["detail"], st


def test_cloud_probe_warns_that_data_leaves_the_machine():
    """云端那档的说明**必须写明"资料会外传"** ✓ —— 这是用户最该知道、也最容易忽略的一句 ✓。"""
    from app import capabilities as C
    st = C._provider_status("dashscope", C.CAPABILITIES["kb"]["providers"]["dashscope"])
    assert "外传" in st["detail"] or "阿里" in st["detail"], st


def test_settings_endpoint_validates_and_never_claims_false_success():
    """切换接口：**校验过再写** ✓ + 选了没装的要**如实说用不了** ✓（照 ASR/TTS 的规矩 ✓）。"""
    import pathlib
    from app import main as m
    src = pathlib.Path(m.__file__).read_text("utf-8")
    assert '@app.post("/api/v1/settings/kb")' in src, "没有切换知识库档位的接口 ✗"
    assert "未知的知识库向量档位" in src, "切换时不校验 ⇒ 能写进不存在的档 ✗"
    assert "但它现在还用不了" in src, "选了没装的却没如实说 ✗"
    assert '@app.post("/api/v1/settings/kb/install")' in src, "没有一键装入口 ✗"
    # 一键装的命令必须是**写死的** ✓ 不接受用户输入 ✓
    from app import local_install
    s = pathlib.Path(local_install.__file__).read_text("utf-8")
    assert '"pip", "install", "-U", "fastembed"' in s, "一键装的命令不对 ✗"
    assert "shell=True" not in s, "走了 shell ⇒ 成了任意命令口子 ✗"
