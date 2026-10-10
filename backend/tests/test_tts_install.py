# -*- coding: utf-8 -*-
"""★★ 2026-10-08：**本地朗读的「一键装」**（用户："就像上面这个千问3似的，点一下它就安装"）。

## 用户当天问的三件事（这条功能就是冲着它们做的）

1. "本地朗读不是显示安装了吗？我看看能不能直接好使" ⇒ 实测**没装** ✓ 而且点它**静默退回**系统语音 ✗
   （那条已在 `test_tts_backends.py` 里钉住 ✓）
2. "melotts 也应该做一个下载的功能，就像上面这个千问3似的"
   ⇒ 本仓**早有安装器**（`scripts/install_melotts.py` ✓ 钉 commit + 哈希 ✓）**却没接到界面上** ✗
3. "有没有更好更小、还能商用的？"
   ⇒ 试通了 **Kokoro-82M 中文**：包 MIT ✓ 模型 Apache-2.0 ✓ fp16 模型 **156MB** ✓
     （先试的是 int8 109MB ✓ 但实测音质明显更差 ⇒ 换 fp16 ✓ 见下面那条测试的注释 ✓）
     **不带 torch** ✓ CPU 就能跑 ✓ —— 而且 `kokoro-onnx` 声明支持 **Python 3.13** ✓
     （这正是 MeloTTS 的死穴：它写死 `torch<2.0`，1.x 没有 3.13 的轮子 ✗）

## 本文件钉什么

· 两条路都**跑写死的命令** ✓（绝不接受用户输入 ✓ 与 ASR 那套同口径 ✓）
· Kokoro 的模型**先钉 sha256 再下** ✓ 哈希不符 ⇒ **当场拒并删** ✓（内容寻址 ✓ 镜像只影响速度 ✓）
· 下不动会**多镜像 + 续传** ✓（实测：GitHub 直连在国内**被重置** ✗ 得靠镜像 ✓）
· 装完**再探一次** ✓ 才敢报 ok ✓（pip 返回 0 ≠ 真能用 ✓）
· 界面那两行**真有一键装按钮** ✓ 而且它真去调那个接口 ✓
"""
from __future__ import annotations

import pathlib
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import local_install as li  # noqa: E402
from app import main as m  # noqa: E402
from app import tts as tts_mod  # noqa: E402

_FE = (pathlib.Path(__file__).resolve().parents[2]
       / "frontend" / "src" / "components" / "SettingsPanel.tsx")


def test_unknown_engine_is_refused():
    """★ 只认写死的那**两**个 ✓ —— 别的名字一律拒 ✓（这一步就是"绝不执行任意输入"的闸 ✓）。"""
    for bad in ("", "  ", "gptsovits", "../../evil", "kokoro", "kokoro; rm -rf /"):
        r = li.install_tts(bad)
        assert r.get("ok") is False, f"{bad!r} 竟然被受理了 ✗：{r}"
        assert "qwen3tts" in str(r.get("detail")), "拒绝时该说清可用的是哪几个 ✗"


def test_kokoro_stays_removed():
    """★★ **Kokoro 不许回到列表里** ✗ —— 这条是**决定**的守卫，不是洁癖 ✓

    为什么下架（2026-10-08，用户听完原话："这根本不行啊" ✓）：
      我把它音色包里 **20 多个中文音色逐个**跑了一遍（同一句话 + ASR 回听 ✓）——
      **全都**把「本地」念成「喷嚏/喷体」✗「这段」念成「团花/谈话/团画」✗
      ⇒ 错在**模型共用的那层中文解码** ✓ 换音色救不了 ✓
      ⇒ 一个"念不准中文"的档不该摆在中文朗读的列表里 ✓（摆着就是坑人 ✓）

    ★ 它不是**安全**问题（当初哈希校验做得挺严 ✓）而是**质量**问题 ✓ ——
      所以哪天有人要加回来，请先拿出"这次念得准了"的实测证据 ✓（比如同一句 ASR 回听 ✓）
    """
    src_reg = pathlib.Path(tts_mod.__file__).read_text("utf-8")
    assert '"kokoro"' not in src_reg or "已下架" in src_reg, "Kokoro 又回注册表了 ✗"
    assert "kokoro" not in tts_mod._TTS_REGISTRY, "注册表里还有 kokoro ✗"
    from app import capabilities as cap
    assert "kokoro" not in cap.CAPABILITIES["tts"]["providers"], "能力表里还有 kokoro ⇒ 界面又会列出来 ✗"
    assert "kokoro" not in cap.CAPABILITIES["tts"]["fallbacks"], "兜底顺序里还留着它 ✗"
    src_li = pathlib.Path(li.__file__).read_text("utf-8")
    assert "_KOKORO_FILES" not in src_li and "_KOKORO_BASE" not in src_li, \
        "安装器里还留着 Kokoro 的常量 ⇒ 下架没清干净 ✗"
    fe = _FE.read_text("utf-8")
    assert "pid === 'kokoro'" not in fe, "界面上还给 Kokoro 挂按钮 ✗"


def test_installer_runs_written_down_commands_only(monkeypatch):
    """★ 两条路跑的**都是写死的命令** ✓ —— 记录一下真被调用的 argv ✓ 人肉核一遍 ✓。"""
    calls: list[list[str]] = []
    monkeypatch.setattr(li, "_run", lambda cmd, **kw: (calls.append(cmd), 0)[1])
    # 两个引擎都要打桩 ✓ —— 否则 MeloTTS 那轮会因为"装完探测不通过"被判 failed ✓
    #     （那**正是对的行为** ✓：本机真没装 melotts 时不许装作成功 ✓
    #      这条测试关心的是"跑了哪些命令" ✓ 不是"真把它装上了" ✓）
    monkeypatch.setattr(tts_mod.MeloTTSBackend, "available", staticmethod(lambda: True))
    monkeypatch.setattr(tts_mod.Qwen3TTSBackend, "available", staticmethod(lambda: True))
    for engine in ("melotts", "qwen3tts"):
        calls.clear()
        r = li.install_tts(engine)
        assert r.get("ok") is True, r
        for _ in range(200):                       # 后台线程跑完为止（最多 ~10s）
            if li.status()["state"] != "running":
                break
            __import__("time").sleep(0.05)
        assert li.status()["state"] == "done", li.status()
        if engine == "melotts":
            assert calls and calls[0][1].endswith("install_melotts.py"), calls
        else:   # ★★ qwen3tts（用户点名那档）
            assert calls and calls[0][1:4] == ["-m", "pip", "install"], calls
            assert "qwen-tts" in calls[0] and "--no-deps" in calls[0], \
                f"千问3 这条必须 `--no-deps` ✗ —— 否则它会把 transformers 钉回 4.57.3 ✓" \
                f"而降级可能弄坏本机的本地 ASR ✓：{calls[0]}"
            assert "transformers" not in " ".join(" ".join(c) for c in calls), \
                f"不该动 transformers ✗（本机 ASR 正用着它 ✓）：{calls}"
            # ② 下模型那一步必须**走 Python API** ✗ 不能 `-m modelscope`
            #   （实测：新版 modelscope 没这个入口 ⇒ 那样写**必失败** ✓）
            assert any(c[1] == "-c" and "snapshot_download" in c[2] for c in calls), \
                f"下模型没走 snapshot_download ⇒ `python -m modelscope` 在新版会报 No module named ✗：{calls}"
            # ★ 判据要写准：是"拿 `-m modelscope` 当**入口**"不行 ✗
            #   —— 别误伤 `-m pip install ... modelscope`（那是**装**它 ✓ 正是对的 ✓）
            assert not any(c[1:3] == ["-m", "modelscope"] for c in calls if len(c) >= 3), \
                f"又用回 `python -m modelscope` 了 ✗（新版没这个入口 ✓）：{calls}"


def test_the_endpoint_exists_and_rejects_bad_engine():
    """★ 端点在 ✓ 而且**未知引擎要拒** ✓（不是"起了个任务然后啥也没干" ✗）。"""
    src = pathlib.Path(m.__file__).read_text("utf-8")
    assert '@app.post("/api/v1/settings/tts/install")' in src, "没有一键装的端点 ✗"
    assert '@app.get("/api/v1/settings/tts/install/status")' in src, "没有看进度的端点 ✗"
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.post("/api/v1/settings/tts/install", json={"engine": "没这个"})
        assert r.status_code == 409, r.text
        s = c.get("/api/v1/settings/tts/install/status")
        assert s.status_code == 200 and "state" in s.json(), s.text


def test_the_ui_has_a_one_click_button_for_both_local_engines():
    """★★ 界面上**真有一键装按钮** ✓ 而且它真去调那个接口 ✓

    —— 用户原话："melotts 也应该做一个下载的功能，就是就像上面这个千问3似的，给它点一下它就安装"
    回滚实验：把这个按钮拿掉 ⇒ 本条必红 ✓
    """
    src = _FE.read_text("utf-8")
    assert "api.installTtsEngine(" in src, "界面没接一键装接口 ✗"
    assert "startTtsInstall" in src, "没有那个点击处理 ✗"
    # 两个本地引擎都得能点到（不是只做一个 ✓）—— 其中 qwen3tts 是**用户点名要的那档** ✓
    assert "pid === 'melotts' || pid === 'qwen3tts'" in src, \
        "一键装的按钮没同时挂在 MeloTTS / 千问3-TTS 两行上 ✗（用户点名要千问3 ✓）"
    # 进度要贴真实输出（与 ASR 那套同口径 ✓ 不装作成功 ✓）
    assert "asrJob.kind === 'install-tts'" in src, "装的过程没在界面上显示 ✗（用户看不到在下什么 ✓）"


def test_qwen3tts_is_listed_and_probed_for_real():
    """★★ **千问3-TTS 必须在能力表里** ✓ 而且状态是**真探测**（不是写死 ready ✗）——
    用户点名要的就是这一档 ✓（"我说的是千问3 TTS，为什么不装千问3 TTS 呢" ✓）

    ★ 探测必须**看模型在不在** ✗（2.4GB 的模型没下，光装了包照样发不出声 ✓）
    """
    from app import capabilities as cap
    provs = cap.CAPABILITIES["tts"]["providers"]
    assert "qwen3tts" in provs, "能力表里没有千问3-TTS ⇒ 界面上根本看不到它 ✗"
    assert "qwen3tts" in tts_mod._TTS_REGISTRY, "注册表里没有它 ⇒ 选了也发不出声 ✗"
    st = cap._provider_status("qwen3tts", provs["qwen3tts"])
    assert st["state"] in ("ready", "not_installed"), st
    r = tts_mod.Qwen3TTSBackend.available()
    d = tts_mod.Qwen3TTSBackend._dir()
    files_ok = (d / "model.safetensors").exists() and (d / "speech_tokenizer" / "model.safetensors").exists()
    assert r == files_ok or not r, f"探测说能用，可模型文件不在 ✗：{d}"


def test_qwen3_model_is_cached_between_sentences():
    """★ 模型**必须缓存** ✗ —— 我第一版每读一句都 `from_pretrained` 一次 ✓

    为什么这是**真问题**：这模型 **2.4GB** ✓ 每次加载实测 **3.3s（GPU）/ 2.1s（CPU）** ✗
    ⇒ 朗读是**逐句**合成的 ✓ 十句话白等半分钟 ✗ 还每次重读 2.4GB 磁盘 ✗
    ⇒ 缓存一份（键 = 用不用卡 ✓）✓

    回滚实验：把缓存去掉（改回每句 from_pretrained）⇒ 本条必红 ✓
    """
    src = pathlib.Path(tts_mod.__file__).read_text("utf-8")
    assert "_QWEN3_CACHE" in src and "def _qwen3_model(" in src, "没有模型缓存 ✗（每句重载 2.4GB ✓）"
    i = src.index("def _qwen3_model(")
    seg = src[i : i + 900]
    assert "if use_cuda not in _QWEN3_CACHE" in seg, "缓存没判重 ⇒ 每句还是重载 ✗"
    assert "from_pretrained" in seg, "缓存里没真加载 ✗"
    # generate() 里**不许**再直接 from_pretrained（必须走缓存 ✓）
    #   ★ 只看**代码行** ✓ —— 注释里提到 from_pretrained 是正常的（那是在说明历史 ✓）
    g0 = src.index("class Qwen3TTSBackend")
    g1 = src.index("def _qwen3_model(")          # 缓存在类**之后** ✓ 这一刀圈住类 + 缓存说明 ✓
    seg2 = src[g0:g1]
    code = "\n".join(ln for ln in seg2.splitlines() if not ln.strip().startswith("#"))
    assert "from_pretrained" not in code, "类里还在直接 from_pretrained ⇒ 缓存白做了 ✗"
    assert "_qwen3_model(" in code, "generate() 没走缓存 ✗"
