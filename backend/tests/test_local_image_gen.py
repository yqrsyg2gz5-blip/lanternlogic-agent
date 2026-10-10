# -*- coding: utf-8 -*-
"""★ 第 2 项 · 体检⑥ 本地出图 —— 2026-10-07 真跑真出图，顺带修掉两处"界面骗人"。

## 体检结论（**真出了一张** ✓ 不是看代码觉得没问题 ✗）

```
prompt：一只戴工程师安全帽的橘猫，坐在电脑前写代码，办公室暖光，写实风格
路径：程序自己的路（LocalExecutor.run_tool("image_gen") ⇒ ComfyUI ✓）
结果：成功 ✓ 12.2 秒 ✓ 512×512 ✓ 304 KB ✓ 用的 Juggernaut-XL_v9.safetensors ✓
图片：agent-shell-评审\\_临时工具\\_出图样本\\gen_2691.png
```
⇒ **本地出图这条路是通的** ✓ 体检表里那一格可以从 ❌ 变 ✅ ✓

## 但真跑一趟，拽出来三个真缺口（都不在"出图能不能画"这件事上 ✗）

**① 界面上**根本切不了出图引擎** ✗
   能力表里明明写着两档（云端通义万相 / 本地 ComfyUI ✓），
   而听/说/知识库**每一档**都有「用这个」✓ —— **唯独出图要手改 config.json** ✗。

**② 那句提示指到一个不存在的地方** ✗
   「换出图提供者 ⇒ 到『模型设置』里的图片一块改」✓ —— 而模型设置里**根本没有这一项** ✗
   （本轮加端点前，后端也**没有** `/settings/image` ✓ 也就是说：那句话从头到尾是空头支票 ✓）

**③ 本地那一档的探针是**废话** ✗ —— 正是本项目一直在收拾的"界面骗人"**
   它写的是 `kind == "local"` ⇒ 直接给「**本机运行，不需要 Key** + ✅可用」✓
   —— 而它**没回答唯一重要的问题**：**ComfyUI 到底在不在跑** ✓
   ⇒ ComfyUI 没开时界面照样写"可用" ✗ 用户点出图 ⇒ 卡住/连接错 ✓
     （ASR 那档栽过一模一样的：探针探 A、实现用 B ⇒ 界面显示可用而点了报错 ✓）
   ⇒ 顺带：云端那档写「DASHSCOPE_API_KEY 已设置」+ ✅可用 ✓
     而"有 Key"离"能用"还差着（**额度用完/没开通**⇒ 真调用 403 ✓ 本机的百炼正是这个状态 ✓）
     ⇒ 措辞改成「有 Key ≠ 一定能用：额度用完/没开通都会在真调用时报错」✓
       （状态仍给 ready ✓ **不假装探到了云端额度** ✓ 也不吓唬人 ✓）
"""
from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient

from app import capabilities as C
from app import main as m

#: 体检证据存在**评审工作区**里（在仓库之外 ✓ 交接书 §1 的约定 ✓）
#: ★ 这两条测试因此**只在有那个工作区的机器上跑** ✓ —— 换个干净机器 clone 下来时**跳过** ✓
#:   （不然"开源之后别人一跑就红"✗ —— 而它红的理由跟代码毫无关系 ✓）
_REVIEW = pathlib.Path(r"D:\丹东云杉网络工作室\agent-shell-评审")
_SAMPLES = _REVIEW / "_临时工具" / "_出图样本"
_needs_review = pytest.mark.skipif(not _REVIEW.exists(), reason="没有评审工作区（换机器/开源 clone）⇒ 跳过")


# ═══ ① 切换端点：校验过再写，且**如实回报现在能不能用** ═══

def test_switching_the_image_engine_is_validated(monkeypatch):
    """★ 照 ASR/TTS/KB 的规矩：**校验过再写** ✓（不许把声明表里没有的引擎写进配置 ✗）。"""
    monkeypatch.setattr(m.cfg.image, "provider", "comfyui", raising=False)
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.post("/api/v1/settings/image", json={"provider": "随便写的引擎"})
        assert r.status_code == 422, r.text
        assert "未知的出图引擎" in r.json()["detail"], r.text
        assert m.cfg.image.provider == "comfyui", "被拒了却把配置改了 ✗"


def test_switching_reports_the_truth_about_the_new_engine(monkeypatch):
    """★ 切完要**如实说它现在能不能用** ✓ —— 与 ASR 那条同款（"已记下，但它现在还用不了"✓）。"""
    monkeypatch.setattr(m.cfg.image, "provider", "dashscope", raising=False)
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.post("/api/v1/settings/image", json={"provider": "comfyui"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["provider"] == "comfyui" and m.cfg.image.provider == "comfyui", d
        assert d["note"], "切完什么都没说 ✗"
        assert d["detail"], "没回报那一档此刻的状态 ✗"


def test_the_ui_no_longer_points_to_a_place_that_does_not_exist(monkeypatch):
    """★★ 那句"到模型设置里改"是**空头支票** ✗ —— 模型设置里根本没有这一项 ✓

    现在开关就在出图这一页 ✓ 所以那句话必须改成"就在这儿切"✓
    （回滚实验：把那句话改回去 ⇒ 本组必红 ✓）
    """
    import pathlib
    src = pathlib.Path(m.__file__).resolve().parents[2] / "frontend" / "src" / "components" / "SettingsPanel.tsx"
    txt = src.read_text("utf-8")
    assert "换出图提供者 ⇒ 到「" not in txt, "那句指到不存在的地方的提示还在 ✗"
    assert "api.setImage(" in txt, "出图这一页没有切换按钮 ✗（那用户还是只能手改配置文件 ✓）"
    assert "用这个" in txt


def test_backend_has_the_switch_endpoint():
    import pathlib
    src = pathlib.Path(m.__file__).read_text("utf-8")
    assert '@app.post("/api/v1/settings/image")' in src, "后端没有切换出图引擎的接口 ✗"
    assert "未知的出图引擎" in src, "切换时不校验 ⇒ 能写进不存在的引擎 ✗"


# ═══ ② 本地那一档的探针：必须**真探**（这正是"界面骗人"的病根 ✗）═══

def test_local_engine_probe_really_checks_whether_comfyui_is_running():
    """★★ **真探** ✓ —— 回滚实验：把 probe 拿掉（退回 `kind=local` 那句"本机运行，不需要 Key"）
    ⇒ 本组必红 ✓

    真实现场：ComfyUI 没开时，界面照样写「✅ 可用」✗ 用户点出图 ⇒ 卡住 ✓
    """
    spec = C.CAPABILITIES["image"]["providers"]["comfyui"]
    assert callable(spec.get("probe")), \
        "本地出图这一档没有探针 ✗ ⇒ 又会退回那句废话（ComfyUI 没开也说可用 ✓）"
    state, detail = spec["probe"]()
    assert state in ("ready", "not_installed"), (state, detail)
    if state == "ready":
        assert "正在跑" in detail and "http" in detail, f"探到了却没说清在哪跑 ✗：{detail}"
    else:
        assert "没在运行" in detail, detail
        # 没在跑时必须**告诉用户怎么办** ✓（而不是干说"不可用"✗）
        assert "起起来" in detail or "切回云端" in detail, f"没给出路 ✗：{detail}"


def test_video_engine_probe_is_wired_too():
    """★★ 2026-10-10（用户实测抓到的）：出**视频**那一档同样必须真探 ✓

    现场：同一张能力表里，出图如实写着「ComfyUI 没在运行」✓
    而视频写着「本机运行，不需要 Key」+ ready ✗ —— **一张表自己打架** ✓
    用户当场问："我 Docker 和 ComfyUI 本地都有，出视频为什么用不了？"
    """
    spec = C.CAPABILITIES["video"]["providers"]["comfyui"]
    assert callable(spec.get("probe")), \
        "本地出视频这一档没有探针 ✗ ⇒ 又会退回「本机运行，不需要 Key」那句假话"
    state, detail = spec["probe"]()
    assert state in ("ready", "not_installed"), (state, detail)
    if state == "ready":
        assert "正在跑" in detail and "http" in detail, f"探到了却没说清在哪跑 ✗：{detail}"
    else:
        # 文案要认得出是**视频**这一档（出图/出视频两条路不能共用一句糊话 ✗）
        assert "没在运行" in detail and "出视频" in detail, detail
        assert "起起来" in detail or "切回云端" in detail, f"没给出路 ✗：{detail}"


def test_probe_says_not_running_when_nothing_listens():
    """★ 探针要能**如实报"没在跑"** ✓（拿一个肯定没人监听的端口试 ✓ 不依赖本机状态 ✓）。"""
    state, detail = C._probe_comfyui("http://127.0.0.1:9")
    assert state == "not_installed", (state, detail)
    assert "没在运行" in detail, detail


def test_probe_reads_the_configured_url():
    """★ 地址要从**配置**读 ✓（用户在设置里改了 ComfyUI 地址 ⇒ 探针得跟着走 ✓）。"""
    import pathlib
    src = pathlib.Path(C.__file__).read_text("utf-8")
    assert 'getattr(c.executor, "comfyui_url"' in src, "探针没从配置读地址 ✗（写死就又会骗人 ✓）"


# ═══ ③ 云端那档：**别把话说满**（有 Key ≠ 能用）═══

def test_cloud_engine_does_not_promise_more_than_it_knows(monkeypatch):
    """★ 原来写「DASHSCOPE_API_KEY 已设置」+ ✅可用 ✗ —— 而额度用完/没开通都会 403 ✓
    （本机的百炼正是这个状态 ✓ 万相出视频已经实测 403 过 ✓）"""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-fake-for-test")
    spec = C.CAPABILITIES["image"]["providers"]["dashscope"]
    st = C._provider_status("dashscope", spec)
    assert st["state"] == "ready", st          # 状态仍按"Key 有没有"给 ✓（本地探不出云端额度 ✓）
    assert "≠" in st["detail"] or "不一定能用" in st["detail"], \
        f"把'有 Key'说成了'能用' ✗（额度用完后用户会一头雾水 ✓）：{st['detail']}"


def test_image_capability_still_declares_both_engines():
    """★ 两档必须都还在声明表里 ✓（一处声明处处一致 ✓）—— 别为了修探针把档位删了 ✗。"""
    provs = C.CAPABILITIES["image"]["providers"]
    assert set(provs) == {"dashscope", "comfyui"}, provs
    assert provs["comfyui"]["kind"] == "local" and provs["dashscope"]["kind"] == "cloud"


@_needs_review
def test_evidence_image_from_the_real_health_check():
    """★ 体检⑥ 的**证据**要留在盘上 ✓ —— 真出的那张图（不然"验过了"只是一句话 ✓）。"""
    imgs = sorted(_SAMPLES.glob("gen_*.png"))
    assert imgs, f"没有留下真出的图 ✗（体检⑥ 的证据应该是**一张真图** ✓）：{_SAMPLES}"
    blob = imgs[-1].read_bytes()
    assert len(blob) > 20_000, f"图太小、不像真出的 ✗：{len(blob)} 字节"
    assert blob[:8] == b"\x89PNG\r\n\x1a\n", "不是 PNG ✗"


def test_comfyui_url_is_where_the_code_looks():
    """★ 探针探的地址 = 出图真去调的地址 ✓（**探针探 A、实现连 B** 正是 ASR 那次栽的坑 ✗）。"""
    import pathlib
    impl = pathlib.Path(m.__file__).resolve().parent / "executors" / "local.py"
    txt = impl.read_text("utf-8")
    assert "self.comfyui_url" in txt and 'f"{self.comfyui_url}/prompt"' in txt, \
        "出图实现没连配置里那个地址 ✗"
    assert 'getattr(cfg, "comfyui_url"' in txt, "出图实现没从配置读地址 ✗"


@_needs_review
def test_health_check_verdict_is_written_down():
    """★ 体检结论要**落成文字** ✓（散落的口头结论会丢 ✓ 用户要的是能打开看的东西 ✓）。"""
    rep = _REVIEW / "第2项-体检6-本地出图-2026-10-07.md"
    assert rep.exists(), "体检⑥ 的报告没写 ✗"
    txt = rep.read_text("utf-8")
    assert "12.2" in txt or "12" in txt, "没写耗时（用户要的是具体数字 ✓）"
    assert "gen_2691.png" in txt or "出图样本" in txt, "没给证据图片的路径 ✗"
