# -*- coding: utf-8 -*-
"""★ 成片体检（`qa_video.py`）的验收测试。

## 为什么有这个脚本（三轮真机事故，用户原话）

「做完了不自动停」「字幕都重叠了」「他光有音频没有视频」「最后一句重复了两遍」

排查结论：**模型"闭着眼睛画画"** —— 它写得出渲染代码，但**看不见自己画的结果**：

  · 字幕画了两遍、位置错开（重影）⇒ 它不知道 ✗
  · 一条字幕 32 个字挤一行 ⇒ 它不知道 ✗
  · 编码用 mpeg4/libvpx 装进 mp4 ⇒ 用户播放器"只有声音没画面"，它更不知道 ✗
  · 它**自己写的** `qa_check.py` 里 ffprobe **漏了文件参数** ⇒ 自查永远返回空 ⇒ 永远发现不了 ✗

⇒ 给它一面**客观的镜子**：跑一条命令，把"看不见"变成"能量出来的数字"。
   通过 ⇒ 它可以安心按完成（治"做完了不敢停"）；不通过 ⇒ 报告说清哪里不对 ✓

## 本文件钉什么

1. **能抓出真实事故**：mpeg4 编码、字幕叠字、一行太长、片尾没留白、交付物缺失
2. **不许误报**（对着我们已经确认合格的那条成片跑，必须全绿）
3. **判据是纯函数**（能单测、不依赖有没有 ffmpeg 装在哪）
4. **输出要说人话**（每条失败都给出"怎么改"），因为读它的是模型
"""
from __future__ import annotations

import importlib.util
import pathlib
import types

import pytest

SKILL = pathlib.Path(__file__).resolve().parents[2] / "backend" / "skills" / "卡点视频"
QA_PATH = SKILL / "tools" / "qa_video.py"


def _load() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("skill_qa_video", QA_PATH)
    assert spec and spec.loader, f"加载不了 {QA_PATH}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_qa_script_ships_with_the_skill():
    """脚本必须随技能包分发（否则模型拿不到它）。"""
    assert QA_PATH.exists(), f"技能里没有成片体检脚本：{QA_PATH} ✗"


# ─────────────────────────────────────────────────────────────────────────────
# 一、编码兼容（用户"只有声音没画面"的直接原因）
# ─────────────────────────────────────────────────────────────────────────────

def test_accepts_h264_aac():
    """h264 + aac + yuv420p = 全世界都能放 ✓"""
    mod = _load()
    r = mod.check_codecs({
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p",
             "width": 1080, "height": 1920},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"duration": "32.0"},
    })
    assert r["ok"] is True, r


def test_rejects_mpeg4_video_codec():
    """★ mpeg4（MPEG-4 Part 2）⇒ 现代播放器不认 ⇒ 用户看到"只有声音" ✗"""
    mod = _load()
    r = mod.check_codecs({
        "streams": [
            {"codec_type": "video", "codec_name": "mpeg4", "pix_fmt": "yuv420p"},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"duration": "32.0"},
    })
    assert r["ok"] is False
    assert "mpeg4" in r["detail"] or "h264" in r["detail"], "没说清该换成什么 ✗"
    assert "h264" in r["fix"], "没给出修法（改编码）✗"


def test_rejects_non_yuv420p_pixfmt():
    """非 yuv420p（如 yuv444p）很多手机硬解放不出来。"""
    mod = _load()
    r = mod.check_codecs({
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv444p"},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"duration": "32.0"},
    })
    assert r["ok"] is False
    assert "yuv420p" in (r["detail"] + r["fix"]), "没提 yuv420p ✗"


def test_rejects_no_audio_track():
    """没有音轨 ⇒ 发出去是哑的。"""
    mod = _load()
    r = mod.check_codecs({
        "streams": [{"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p"}],
        "format": {"duration": "32.0"},
    })
    assert r["ok"] is False
    assert "音" in r["detail"] or "audio" in r["detail"].lower()


def test_rejects_no_video_track():
    """★ 只有音轨 ⇒ 正是用户说的"只有音频没有视频" ✗"""
    mod = _load()
    r = mod.check_codecs({
        "streams": [{"codec_type": "audio", "codec_name": "aac"}],
        "format": {"duration": "32.0"},
    })
    assert r["ok"] is False
    assert "视频" in r["detail"] or "video" in r["detail"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# 二、亮度 / 黑场（用户"画面太暗"那次的量化版）
# ─────────────────────────────────────────────────────────────────────────────

def test_brightness_pass_and_fail():
    mod = _load()
    okr = mod.check_brightness([44.0, 45.2, 43.8, 46.0])
    assert okr["ok"] is True, okr
    bad = mod.check_brightness([27.7] * 10)          # 真机那次的数字
    assert bad["ok"] is False, "全片 27.7 太暗却没报 ✗"
    assert "亮度" in bad["detail"]


def test_black_frames_detected():
    """连续黑场（真机有 6.5 秒连续黑）要报出来。"""
    mod = _load()
    r = mod.check_black_runs([(1.5, 8.0), (25.0, 29.9)])
    assert r["ok"] is False
    assert "黑" in r["detail"]
    assert mod.check_black_runs([])["ok"] is True


# ─────────────────────────────────────────────────────────────────────────────
# 三、字幕（用户"字幕重叠"+"看起来像说了两遍"）
# ─────────────────────────────────────────────────────────────────────────────

def test_line_too_long_is_rejected():
    """★ 一行 32 个字（真机那次）必须报错 —— 小屏读不过来，还会与下行挤在一起。"""
    mod = _load()
    long_line = "输出1 万token光生成阶段约20 秒上下文100 万token最大生成约128k"
    r = mod.check_caption_lines([long_line], max_chars=14)
    assert r["ok"] is False, f"一行 {len(long_line)} 字却没报 ✗"
    assert "换行" in r["fix"] or "14" in r["fix"], "没给修法 ✗"
    assert mod.check_caption_lines(["你眨一次眼它写完一段"], max_chars=14)["ok"] is True


def test_caption_band_overlap_is_rejected():
    """★ 字幕带边缘密度异常 ⇒ 判为"画了两遍/重叠"（真机重影就是这个）。

    判据是纯函数：输入一次"边缘密度"读数，输出结论。
    """
    mod = _load()
    # 正常一行的边缘密度（同一行字，单次绘制）
    normal = mod.check_caption_overlap(edge_density=0.06, baseline=0.06)
    assert normal["ok"] is True, normal
    # 重影：密度接近两倍
    bad = mod.check_caption_overlap(edge_density=0.13, baseline=0.06)
    assert bad["ok"] is False, "边缘密度翻倍（重影）却没报 ✗"
    assert "叠" in bad["detail"] or "重" in bad["detail"]


def test_caption_band_absent_is_not_an_error_by_itself():
    """某些场景没有字幕是合法的（如纯画面落款）—— 不许因为"没字幕"就判失败。"""
    mod = _load()
    r = mod.check_caption_overlap(edge_density=0.0, baseline=0.06)
    assert r["ok"] is True, "没字幕被判失败 ⇒ 会误伤纯画面场景 ✗"


# ─────────────────────────────────────────────────────────────────────────────
# 四、片尾留白（用户"话没说完就断了"）
# ─────────────────────────────────────────────────────────────────────────────

def test_tail_padding_pass_and_fail():
    mod = _load()
    okr = mod.check_tail_padding(video_duration=32.07, voice_end=31.47, min_pad=0.4)
    assert okr["ok"] is True, okr
    bad = mod.check_tail_padding(video_duration=30.12, voice_end=31.0, min_pad=0.4)
    assert bad["ok"] is False, "配音比成片还长（话被切断）却没报 ✗"
    assert "切" in bad["detail"] or "留白" in bad["detail"]


# ─────────────────────────────────────────────────────────────────────────────
# 五、交付物清单（用户"他也没发布上去"）
# ─────────────────────────────────────────────────────────────────────────────

def test_missing_deliverables_are_listed():
    mod = _load()
    r = mod.check_deliverables(existing={"out/final.mp4", "data/script.md"})
    assert r["ok"] is False
    missing = " ".join(r["detail"])
    for need in ("timeline.json", "hook.txt", "qa-report.md"):
        assert need in missing, f"缺的 {need} 没列出来 ✗"


def test_all_deliverables_present():
    mod = _load()
    r = mod.check_deliverables(existing=set(mod.REQUIRED_DELIVERABLES))
    assert r["ok"] is True, r


# ─────────────────────────────────────────────────────────────────────────────
# 六、总报告的形状（模型读的就是它）
# ─────────────────────────────────────────────────────────────────────────────

def test_report_shape_and_verdict():
    """报告要有：每项结论 + 总结论 + 失败项各自的"怎么改"。"""
    mod = _load()
    rep = mod.build_report([
        {"name": "编码", "ok": True, "detail": "h264", "fix": ""},
        {"name": "字幕", "ok": False, "detail": "一行 32 字", "fix": "按 14 字换行"},
    ])
    assert rep["passed"] is False
    assert len(rep["items"]) == 2
    txt = mod.format_report(rep)
    assert "编码" in txt and "字幕" in txt
    assert "按 14 字换行" in txt, "失败项的修法没出现在报告里 ✗"
    assert "✗" in txt, "没有一眼可见的失败标记 ✗"


def test_report_all_pass():
    mod = _load()
    rep = mod.build_report([{"name": "编码", "ok": True, "detail": "h264", "fix": ""}])
    assert rep["passed"] is True
    assert "通过" in mod.format_report(rep)


# ─────────────────────────────────────────────────────────────────────────────
# 七、真实成片回归（拿已经人工确认合格的那条片子跑）
# ─────────────────────────────────────────────────────────────────────────────

def test_skill_md_tells_the_agent_to_run_qa_before_done():
    """★ SKILL.md 必须把「成片体检」写成**收尾必做** —— 否则模型不知道自己做得对不对。

    这条是给**后来人**的护栏：体检脚本很容易被当成"可选工具"。
    而真机证据是：模型在一条命令上绕了 37 次，**因为没有任何客观反馈** ✗
    """
    md = (SKILL / "SKILL.md").read_text("utf-8", errors="replace")
    assert "qa_video.py" in md, "SKILL.md 里没提 qa_video.py ⇒ agent 不会跑它 ✗"
    assert "机器体检" in md or "成片体检" in md, "SKILL.md 里没有这一步 ✗"
    # 必须写在**第 8 步（渲完自检）里**，而且排在人工细查（8.1）之前
    i_qa = md.find("qa_video.py")
    i_step8 = md.find("第 8 步")
    i_81 = md.find("| 8.1 |")
    assert 0 < i_step8 < i_qa, "成片体检没写在第 8 步里 ✗"
    assert i_81 < 0 or i_qa < i_81, "机器体检排在了 8.1 人工细查之后（应该先跑它）✗"


def test_real_fixed_video_passes_codec_and_brightness():
    """★ 回归：对着我们**已确认合格**的那条成片跑编码+亮度检查，必须全绿。

    ★ 同时也钉住"不许误报" —— 体检脚本最容易犯的错就是把好的判成坏的 ✗
    没有现成成片时跳过（该面无法验证）。
    """
    vid = pathlib.Path(r"D:\丹东云杉网络工作室\agent-shell-评审\_产出-成片"
                       r"\成片-v2-字幕修复-1080x1920.mp4")
    if not vid.exists():
        pytest.skip("本机没有那条成片 —— 该面无法验证 ✓")
    mod = _load()
    probe = mod.probe_video(vid)
    if not probe:
        pytest.skip("ffprobe 不可用 —— 该面无法验证 ✓")
    assert mod.check_codecs(probe)["ok"] is True, "已确认合格的成片被判编码不合格 ✗"
    yavg = mod.measure_brightness(vid)
    if yavg:
        assert mod.check_brightness(yavg)["ok"] is True, f"亮度被判不合格：{yavg[:3]} ✗"
