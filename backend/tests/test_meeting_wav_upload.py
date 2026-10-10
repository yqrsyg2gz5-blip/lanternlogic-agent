# -*- coding: utf-8 -*-
"""★★ bug B：**会议录完不显示、也不生成纪要**（用户："读了好几遍也不生成" ✗）
—— 2026-10-07 真录一段量出来的 ✓ 钉死。

## 现场（假麦克风真录 12 秒 → 原样喂给后端 ✓）

```
① 界面点 🎙 → 「会议记录中」出现 ✓（**前端是起来的** ✓）
② POST /api/v1/tasks/<id>/meeting → **201 Created**（**0.0 秒**就回来了 ✓）
   响应体：{"ok":false,"text":"","error":"转写失败：LibsndfileError:
            Error opening 'data\\tasks\\meeting_1b3243.webm': Format not recognised."}
```

## 两条根因（各有一组测试钉住）

**① 录的是 webm，而后端读不了 webm** ✗
   · `MediaRecorder` 默认产 **webm/opus** ✓
   · 后端 `_to_wav()` 要靠 **ffmpeg** 转码 ✗ —— 而**本机（干净机器）根本没装** ✓
   · 转不了它**静默退回原文件** ✗ ⇒ 本地 ASR 的 soundfile 打不开 ⇒ `Format not recognised` ✓
   · ★ 最刺眼的一点：**"语音输入（草稿）"那条路早就修过同一个坑** ✓
     （改成浏览器侧直接录 16k WAV ✓ 见 `lib/recwav.ts` 顶部注释 ✓）
     —— **会议这条路没跟上** ✗（同一个 bug 修了一条、漏了另一条 ✓）。

**② 失败了，但界面一个字都不说** ✗
   · 后端**转写失败也回 201** ✓ 只是 body 里 `ok:false` + `error` ✓
   · 前端 `await api.meetingChunk(...)` **把返回值直接丢掉** ✗（原来连 `ok` 都不看 ✓）
   ⇒ 用户看到的就是"录了、然后什么都没有" ✗✓ —— 这正是最难查的那类静默失败 ✓。

## 修法（本文件钉的四条）

1. 会议也改成**浏览器侧录 WAV** ✓（不依赖 ffmpeg ✓ 与草稿那条路同源 ✓）
2. 首段失败才退回 webm ✓ 而且**如实说**在走备用路 ✓（不许静默换路 ✗）
3. 每一段的结果**都要在界面上有回音** ✓（转到了什么 / 这段没声音 / 这段失败带原因 ✓）
4. 后端 `_to_wav` **转不了就说清楚** ✓（什么格式、缺 ffmpeg、两条怎么修 ✓），
   绝不把"注定读不了的文件"再往下传 ✗
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

from app import main as m

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SRC = _ROOT / "frontend" / "src"
_API = (_SRC / "api.ts").read_text("utf-8")
_TV = (_SRC / "components" / "TaskView.tsx").read_text("utf-8")
_REC = (_SRC / "lib" / "recwav.ts").read_text("utf-8")


# ═══ ① 会议改录 WAV（根治：不再依赖 ffmpeg）═══

def test_meeting_records_wav_in_the_browser():
    """★★ 会议那一段**必须走浏览器侧 WAV 录音** ✓ —— 这是根治 ffmpeg 依赖的那一步 ✓。

    回滚实验：把 `startWavRecording({ stream, gain: 1 })` 换回纯 `new MediaRecorder(stream)`
    ⇒ 必须变红 ✓（那就又回到"录 webm、后端读不了"的老路 ✗）。
    """
    seg = _TV.split("const meetingRecordSegment = ")[1].split("const toggleMeeting = ")[0]
    code = "\n".join(ln for ln in seg.splitlines() if not ln.strip().startswith("//"))
    assert "startWavRecording({ stream, gain: 1 })" in code, \
        "会议还在用 MediaRecorder 录 webm ✗ ⇒ 本机没 ffmpeg ⇒ 后端必然 Format not recognised"
    assert "return await w.stop();" in code, "没真去取 WAV 的字节 ✗"
    # 备用路的 webm 只允许出现在 catch 里（首段失败才退 ✓ 且要如实说出来 ✓）
    assert "console.warn('[会议] 浏览器内置 WAV 录音不可用" in code, "换路没留痕 ✗（静默换路正是这次的坑）"


def test_wav_recorder_can_reuse_the_meeting_mic_stream():
    """WAV 录音要能**复用会议那条麦克风流** ✓ —— 而且**不许把它关掉** ✗（下一段还要接着录 ✓）。"""
    assert "stream?: MediaStream" in _REC, "WAV 录音不支持外部传入麦克风流 ✗"
    assert "const own = !opts.stream;" in _REC, "没区分「这条流是不是我开的」✗"
    assert "if (own) raw.getTracks().forEach((t) => t.stop());" in _REC, \
        "复用别人的流时还去关它的轨道 ✗ ⇒ 会议第 2 段起就没声音了 ✗"


def test_upload_name_matches_the_real_type_for_meeting():
    """上传文件名要按**真实类型** ✓ —— 内容改了 WAV 而后缀还写 webm，
    后端照后缀去叫 ffmpeg ⇒ 白跑一趟 ✓（草稿那条路早就是这么修的 ✓ 见 test_voice_input ✓）。"""
    assert "const name = blob.type === 'audio/wav' ? 'chunk.wav'" in _API, \
        "会议上传的文件名没按真实类型 ✗"
    assert "fd.append('audio', blob, name);" in _API, "文件名没真的用上去 ✗"


# ═══ ② 失败不许静默：后端说清楚 + 前端显示出来 ═══

def test_hostile_format_is_refused_loudly_by_the_backend(tmp_path):
    """★ 后端 `_to_wav`：转不了就**报清楚** ✓ 不许把读不了的文件再传给 ASR ✗。

    真实现场（本班实测原话）：一个 webm 一路传到本地 ASR ⇒
      `LibsndfileError: Error opening '…webm': Format not recognised.`
    —— 这句话里**没有一个字**提示"本机缺 ffmpeg" ✗（用户只能干瞪眼 ✓）。
    """
    webm = tmp_path / "chunk.webm"
    webm.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 64)          # webm 的魔数 ✓
    with pytest.raises(RuntimeError) as ei:
        asyncio.run(m._to_wav(webm))
    msg = str(ei.value)
    assert "webm" in msg, f"没说清是哪一种格式 ✗：{msg[:160]}"
    assert "ffmpeg" in msg, f"没点出真正缺的东西 ✗：{msg[:160]}"
    assert "WAV" in msg and "录音按钮" in msg, f"没告诉用户怎么绕过 ✗：{msg[:200]}"


def test_wav_passes_straight_through(tmp_path):
    """WAV/MP3 **一个字节都不该去动** ✓（本机没 ffmpeg 也照样能用 ✓ —— 这正是修法的立足点 ✓）。"""
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 32)
    assert asyncio.run(m._to_wav(wav)) == wav


def test_format_is_judged_by_content_not_by_filename(tmp_path):
    """★ 后缀骗人：**内容**是 WAV 的文件不该被拒 ✓（与 `sniff_audio_format` 同一口径 ✓）。"""
    lying = tmp_path / "from_phone.webm"
    lying.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 32)
    assert asyncio.run(m._to_wav(lying)) == lying, "看着后缀就拒了 ⇒ 明明能读的音频用不了 ✗"


def test_meeting_endpoint_reports_the_reason_instead_of_pretending_success(tmp_path):
    """★ 接口层：转不了要**回话**（`ok:false` + 原因 ✓），不许 500 也不许假装成功 ✗。"""
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        task = c.post("/api/v1/tasks", json={"input": "会议录音测试"}).json()
        tid = task["id"]
        r = c.post(f"/api/v1/tasks/{tid}/meeting",
                   files={"audio": ("chunk.webm", b"\x1a\x45\xdf\xa3" + b"\x00" * 64, "audio/webm")})
    assert r.status_code == 201, f"接口本身不该炸 ✗（{r.status_code}）"
    body = r.json()
    assert body["ok"] is False, "转不了竟然说成功 ✗（那用户就永远查不出为什么没记录 ✓）"
    assert "ffmpeg" in body["error"], f"错误里没点出缺 ffmpeg ✗：{body['error'][:160]}"


def test_frontend_shows_every_segment_result():
    """★ 前端**必须看 `ok`** ✓ 并把三种结果都显示出来 ✓ —— 这就是"录完不显示"的那一半 ✓。"""
    seg = _TV.split("const meetingUpload = ")[1].split("const meetingRecordSegment = ")[0]
    code = "\n".join(ln for ln in seg.splitlines() if not ln.strip().startswith("//"))
    assert "const r = await api.meetingChunk(taskId, blob);" in code, \
        "返回值又被丢掉了 ✗（丢掉 = 用户什么也看不到 ✓ 这次就是这么来的 ✓）"
    assert "r.ok && r.text" in code, "转到了什么没显示 ✗"
    assert "setMeetingHint(`这一段没转成文字" in code, "失败那段没把**后端的原话**显示出来 ✗"
    assert "setMeetingHint(`这一段上传失败" in code, "网络/鉴权那一层失败没显示 ✗"
    assert "meetingHint" in _TV.split("const [meetingOn")[1][:400], "没有会议提示这个状态 ✗"


def test_meeting_stop_tells_you_what_happened():
    """★ 停下来那一刻要有交代 ✓（录进去几段 ✓ 以及"一段都没有"这件事实话实说 ✓）。"""
    seg = _TV.split("const toggleMeeting = ")[1].split("const [asideTab")[0]
    assert "已记录 ${meetingSegsRef.current} 段" in seg, "停下后没告诉用户录进去几段 ✗"
    assert "一段都没转成文字" in seg, "一段都没录到时没说清 ✗（用户正是这么被蒙在鼓里的 ✓）"


def test_mic_failure_is_not_silent():
    """★ 麦克风打不开也要说话 ✓ —— 原来 `catch {}` 一声不吭 ✗（用户只看到"点了没反应"✓）。"""
    seg = _TV.split("const toggleMeeting = ")[1].split("const [asideTab")[0]
    assert "麦克风打不开" in seg, "麦克风失败被静默吞了 ✗"


# ═══ ③ 整条链真跑一遍：WAV 进来 → 转写（假模型）→ 落进 meeting_notes.md ═══

def test_wav_chunk_is_transcribed_and_written_into_the_notes(tmp_path, monkeypatch):
    """★ 端到端（**不碰真模型** ✓）：WAV 分片上传 → 转写 → 带时间戳追加进 meeting_notes.md ✓。

    这里把 `_transcribe` 换成假的 ✓ —— 真 ASR 要加载本地模型/联网 ✗
    （本仓那条规矩：单元测试不许碰真网络/真下载 ✓ 用假对象、且假对象照真接口写 ✓）。
    """
    monkeypatch.setattr(m, "_transcribe", lambda p: _const("今天先把登录做完，明天再看支付。"))
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        tid = c.post("/api/v1/tasks", json={"input": "周会"}).json()["id"]
        wav = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00" + b"\x00" * 24
        r = c.post(f"/api/v1/tasks/{tid}/meeting",
                   files={"audio": ("chunk.wav", wav, "audio/wav")})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["ok"] is True and "登录" in body["text"], body
        # 真的落到工作区里了 ✓（这正是 Agent 后面写纪要要读的那份素材 ✓）
        notes = m.store.workspace_dir(tid) / "meeting_notes.md"
        text = notes.read_text("utf-8")
        assert "登录" in text, f"没写进 meeting_notes.md ✗：{text[:200]}"
        assert "会议记录" in text and "- [" in text, "没带表头/时间戳 ✗"
        # 读接口也拿得到 ✓（界面上"看一看录了什么"靠它 ✓）
        assert "登录" in c.get(f"/api/v1/tasks/{tid}/meeting").json()["text"]


def test_silent_chunk_is_reported_but_not_written(tmp_path, monkeypatch):
    """★ 这段没人说话 ⇒ 如实说"没听到" ✓ 而且**不往记录里塞空行** ✓。"""
    monkeypatch.setattr(m, "_transcribe", lambda p: _const("   "))
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        tid = c.post("/api/v1/tasks", json={"input": "周会2"}).json()["id"]
        wav = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00" + b"\x00" * 24
        body = c.post(f"/api/v1/tasks/{tid}/meeting",
                      files={"audio": ("chunk.wav", wav, "audio/wav")}).json()
        assert body["ok"] is True and body["text"] == "" and body.get("note"), body
        assert not (m.store.workspace_dir(tid) / "meeting_notes.md").exists(), "空内容不该落盘 ✗"


async def _const(v: str) -> str:
    return v


if __name__ == "__main__":       # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
