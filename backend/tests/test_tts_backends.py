# -*- coding: utf-8 -*-
"""★ 2026-10-06 语音合成（说）：**能装、能切、口径只有一处** ✓

## 用户实测问出来的三件事（都得有答案 ✓）

> "这些本地的软件是不是 MeloTTS？然后 Edge……如果他这些都没有可以装吗？
>  咱不是留口子了是不是不可以装安装吗？然后包括就是他们的使用，比如说切换或者什么都都可以做到吗"

| 问 | 改之前 ✗ | 现在 ✓ |
|---|---|---|
| 有哪些后端 | 注册表里有 ✓ 但**千问3 TTS 根本不存在** ✗（那是 ASR 那边的 ✓）| 界面按注册表列 ✓ 不写没实现的 ✓ |
| 没装能装吗 | 只报"依赖未安装" ✗ **装什么包、多大、能不能离线一个字没有** ✗ | 每个后端带 `install/size/offline` ✓ 界面上直接看到 ✓ |
| 能切吗 | **不能** ✗（设置页自己写着"切换还没做" ✗）| **能切** ✓（下拉/按钮 ✓ 没装的也能先选，但**如实说发不出声** ✓）|

## 口径打架（这条最要命 ✗✗）

同一个"朗读用哪个后端"，代码里**四处各说各的** ✗：
· 关于页写 **Edge** ✓（**这个是对的** ✓ 实际跑的就是它 ✓）
· 设置页写"**固定用 MeloTTS**" ✗（过时 ✓）
· 能力总览 `_current_provider` **硬编码 `"melotts"`** ✗
· `/api/v1/settings` 返回里**也硬编码 `"melotts"`** ✗
· TTS 接口 `req.backend or "edge"` **又写死一个** ✗

⇒ 用户看到三处互相矛盾 ✓ 而"界面上到底能不能切"也没人知道 ✓。

**修法**：立 `config.tts.backend` 当**唯一真相** ✓ 五处全从它读 ✓。
下面几条测试就是钉"**界面说的 = 真会用的**" ✓ —— 这比"五处都写同一个常量"结实得多 ✓。
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import capabilities as cap  # noqa: E402
from app import main as m  # noqa: E402
from app.tts import HINTS, describe  # noqa: E402

_FE = (pathlib.Path(__file__).resolve().parents[2]
       / "frontend" / "src" / "components" / "SettingsPanel.tsx")


def test_every_backend_tells_you_how_to_install_it():
    """**每个后端都得说清"怎么装、多大、能不能离线"** ✓ —— 这就是用户问的那句话 ✓。

    改之前只有一句"依赖未安装" ✗ ⇒ 用户只能自己猜 `pip install` 什么 ✓。
    """
    for pid, h in HINTS.items():
        assert h.get("install"), f"{pid} 没写怎么装 ✗"
        assert h.get("size"), f"{pid} 没写多大 ✗"
        assert h.get("offline"), f"{pid} 没写要不要联网 ✗"
    rows = {r["id"]: r for r in describe()}
    assert set(rows) == set(HINTS), "describe() 和 HINTS 对不上 ✗"
    for pid, r in rows.items():
        assert isinstance(r["available"], bool), f"{pid} 没有真实探测 ✓"


def test_uninstalled_backend_is_honest_in_the_status_text():
    """没装的后端，状态文字里**必须带安装命令** ✓ —— 界面直接显示它 ✓ 用户当场就知道怎么办 ✓。"""
    pid = "melotts"                     # 本机就没装它 ✓（真探测 ✓）
    st = cap._provider_status(pid, cap.CAPABILITIES["tts"]["providers"][pid])
    if st["state"] == "not_installed":
        assert "pip install" in st["detail"], f"没装却没告诉怎么装 ✗：{st['detail']}"
    # 装了的也不该只说"可用" ✓ 顺带把"多大 / 要不要联网"带上 ✓
    st2 = cap._provider_status("pyttsx3", cap.CAPABILITIES["tts"]["providers"]["pyttsx3"])
    assert st2["detail"], "状态没有说明 ✗"


def test_settings_endpoint_and_capabilities_agree_with_config():
    """**三处（其实五处）口径必须一致** ✓ —— 而且都得等于**配置里那个** ✓。

    只钉"两处相等"是不够的 ✗（它们可以一起写死同一个错值 ✓ 本班就撞过这个 ✓）。
    """
    from fastapi.testclient import TestClient
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        real = c.get("/api/v1/settings").json()["tts"]["backend"]
    cfg_val = str(m.cfg.tts.backend)
    assert real == cfg_val, f"设置接口 {real} ≠ 配置 {cfg_val} ⇒ 又有人写死了 ✗"
    assert cap._current_provider("tts", m.cfg) == cfg_val, "能力总览没从配置读 ✗"


def test_switching_tts_is_validated_and_honest():
    """切换接口：**校验过再写** ✓ + 选了没装的要**如实说它用不了** ✓（照 ASR 那个口子的规矩 ✓）。"""
    src = pathlib.Path(m.__file__).read_text("utf-8")
    assert '@app.post("/api/v1/settings/tts")' in src, "没有切换 TTS 的接口 ✗"
    assert "未知的 TTS 后端" in src, "切换时不校验 ⇒ 用户能写进一个不存在的后端 ✗"
    assert "但它现在还用不了" in src, "选了没装的却没如实说 ✗"
    assert '"tts"' in src and "cfg.tts.backend" in src, "设置响应没从配置读 ✗"


def test_ui_can_actually_switch_and_shows_the_install_hint():
    """界面：**能切** ✓ 而且把"怎么装"摆出来 ✓（改之前只有一句"切换还没做" ✗）。"""
    src = _FE.read_text("utf-8")
    assert "switchTts" in src, "界面上没有切换的入口 ✗"
    assert "切换 TTS 后端还没做" not in src, "还留着那句'切换还没做' ✗（现在做了 ✓）"
    assert "p.detail" in src, "没把后端的安装说明显示出来 ✗（用户要的就是这句话 ✓）"


def test_no_stale_voice_or_tool_counts_in_the_about_card():
    """**过时数字一律钉住** ✗：工具数 21→22 ✓ 团队说两套→**五套** ✓ 沙箱要标"默认关" ✓。

    这几条都是"关于页对不上"里被点名的 ✓ —— 而它们**机器一秒就能查** ✓。
    """
    src = _FE.read_text("utf-8")
    assert "21 个" not in src, "关于页还写着 21 个工具 ✗（实际 22 ✓）"
    assert "五套模式" in src or "接力 / 开会" in src, "团队模式没写全（是五套 ✓）"
    assert "默认关" in src, "沙箱没标'默认关'（实际默认 off ✓）"


# ═══ ★★ 2026-10-08（用户当场问出来的两件）═══
#
# 起因：用户问"本地朗读不是显示安装了吗？我看看能不能直接好使" ✓
# 实测三件事（都在下面钉住 ✓）：
#   ① **没装** ✓（`import melo` 失败；能力总览也如实写着 not_installed ✓）
#   ② **但点它不报错** ✗ —— 后端**静默**改用 pyttsx3（系统自带语音）念 ✓
#      铁证：同一句话分别点 melotts 与 pyttsx3，两个 wav **字节数一模一样（104796）** ✓
#      ⇒ 用户"听到声音"就以为装好了 ✓（而设置页那句"没装的后端会**如实告诉你**发不出声"
#         ——它**没做到** ✗ 界面承诺与实现不符 ✓）
#   ③ 界面给的安装命令 **`pip install melotts` 是假指路** ✗（本机 Python 3.13 装不上 ✓ 见下）


class _FakeTTS:
    """一个不发声的假后端 ✓ —— 测试只关心"**退回有没有说出来**"✓ 不真去调 SAPI ✓
    （真调 SAPI 会写音频、依赖系统语音引擎 ✓ CI 上没有它 ⇒ 测试会随环境红绿 ✗）。"""

    async def generate(self, text, output_path):
        output_path.write_bytes(b"RIFF-fake")
        return output_path


def _tts_lines(monkeypatch, asked: str, fail_for: set[str]) -> list[dict]:
    """真发一次 POST /tts（走**真端点、真 NDJSON 流** ✓），把那几行 JSON 收回来 ✓。

    `fail_for` 里的后端名 ⇒ 取后端时抛错（= 没装 / 网络挂 ✓ 与真实现同一种失败 ✓）。
    """
    import json as _json

    from fastapi.testclient import TestClient

    def fake_get(name: str = ""):
        if name in fail_for:
            raise RuntimeError(
                f"TTS 后端 {name} 不可用（依赖未安装）—— 怎么装："
                "跑本仓自带的安装器 `python scripts\\install_melotts.py`"
            )
        return _FakeTTS()

    monkeypatch.setattr(m, "get_tts_backend", fake_get)
    monkeypatch.setattr(m, "_TTS_DIR", m._DATA_DIR / "tts_test")
    tid = "task_20261008_tts1"
    if tid not in m.tasks:
        m.tasks[tid] = _TTSFakeTask(tid)
    try:
        with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
            r = c.post(f"/api/v1/tasks/{tid}/tts", json={"text": "第一句。第二句。", "backend": asked})
        assert r.status_code == 200, r.text
        return [_json.loads(ln) for ln in r.text.strip().splitlines() if ln.strip()]
    finally:
        m.tasks.pop(tid, None)


class _TTSFakeTask:
    """`_get_task` 只读这几个字段 ✓（给它一个最小的壳 ✓ 不碰真索引 ✓）。"""

    def __init__(self, tid: str):
        self.id = tid
        self.title = "朗读自检"
        self.status = "done"
        self.created_at = "2026-10-08T00:00:00Z"
        self.updated_at = "2026-10-08T00:00:00Z"
        self.project_id = None


def test_the_fallback_is_declared_not_silent(monkeypatch):
    """★★ **退回必须说出来** ✗ —— 这是用户当场撞见的那件事：

    他点 MeloTTS（没装）✓ 听到的是系统自带语音 ✓ 而**界面上一个字都没有** ✗
    ⇒ 他以为"本地朗读装好了、能用了" ✓（实际念的是另一个引擎 ✓）

    ★ 判据：每一行都得带 `used`（真用的谁）+ `asked`（你要的谁）+ `why`（为什么换了 ✓
      里面就有"怎么装"那句 ✓）✓
    回滚实验：把这三个字段去掉（= 修复前的静默态 ✓）⇒ 本条必红 ✓
    """
    lines = _tts_lines(monkeypatch, asked="melotts", fail_for={"melotts"})
    assert lines, "一句都没回来 ⇒ 这条测试没在测东西 ✗"
    for ln in lines:
        assert ln.get("url"), ln
        assert ln.get("used") == "pyttsx3", f"真用的引擎没报出来 ✗：{ln}"
        assert ln.get("asked") == "melotts", f"没报'你要的是谁' ⇒ 用户不知道自己点的是啥 ✗：{ln}"
        assert "install_melotts" in str(ln.get("why")), \
            f"没说清'为什么换了 / 怎么装' ⇒ 用户还是不知道怎么办 ✗：{ln}"


def test_no_fallback_means_no_noise(monkeypatch):
    """★ 没退回 ⇒ **一个多余字段都不许有** ✓（正常路径不许被这条提示污染 ✓）。"""
    lines = _tts_lines(monkeypatch, asked="melotts", fail_for=set())
    assert lines and all("used" not in ln for ln in lines), \
        f"后端正常出声却还挂着'退回'提示 ⇒ 界面会莫名警告 ✗：{lines}"


def test_the_melotts_hint_points_at_the_real_installer():
    """★★ **别再给假指路** ✗ —— 界面上原来只有一句 `pip install melotts` ✓
    实测（本机 venv = Python 3.13.13）**装不上**，两个硬原因：

      ① PyPI 上 melotts 0.1.1 **只有源码包、没有 wheel**，而那个源码包里
         **缺 `requirements.txt`** ⇒ pip 当场 `FileNotFoundError`（我实跑出来的 ✓）
      ② 它写死 **`torch<2.0`** ⇒ torch 1.x **没有 Python 3.13 的轮子** ⇒ 依赖无解 ✓

    ★ 而本仓**自己有一个安装器**：`scripts/install_melotts.py` ✓（钉死 commit + sha256 ✓
      装的是 GitHub tarball —— **带 requirements.txt** ✓ 依赖走最新 torchaudio ✓ 绕开那个 pin ✓）
      ⇒ 提示里必须把它给出来 ✓（不然用户按界面那句走，撞两次墙还不知道为什么 ✓）

    回滚实验：把提示改回 `pip install melotts` 那一句 ⇒ 本条必红 ✓
    """
    hint = HINTS["melotts"]["install"]
    assert "install_melotts" in hint, \
        f"没把本仓那个安装器给出来 ⇒ 用户只会去撞 PyPI 那堵墙 ✗：{hint}"
    assert "3.13" in hint or "torch<2.0" in hint, \
        f"没说清'为什么 pip 那条路走不通' ⇒ 用户会以为是自己网络问题 ✗：{hint}"
    # 安装器**真在**（提示里说的东西不能是空气 ✓）
    script = pathlib.Path(m.__file__).resolve().parents[2] / "scripts" / "install_melotts.py"
    assert script.exists(), f"提示里让人跑 {script} ⇒ 它必须存在 ✗"
