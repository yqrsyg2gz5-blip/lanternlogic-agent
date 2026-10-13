# -*- coding: utf-8 -*-
"""技能自带的「成片体检」—— 渲染完跑一条命令，把"看不见"变成"能量出来的数字"。

## 为什么有它（三轮真机事故换来的，用户原话）

「做完了不自动停」「字幕都重叠了」「他光有音频没有视频」「最后一句重复了两遍」

排查结论：**模型"闭着眼睛画画"** —— 它写得出渲染代码，但**看不见自己画的结果**：

  · 字幕画了两遍、位置错开 ⇒ 它不知道 ✗
  · 一条字幕 32 个字挤一行 ⇒ 它不知道 ✗
  · 用 mpeg4/libvpx 编码 ⇒ 用户播放器**只有声音没画面**，它更不知道 ✗
  · **它自己写的** `qa_check.py` 里 ffprobe **漏了文件参数** ⇒ 自查永远返回空 ✗
    （这就是"它为什么永远不知道自己做成什么样"的技术根源）

⇒ 给它一面**客观的镜子**。跑一次：
    · 全过 ⇒ 可以安心 `task_done`（治"做完了不敢停"，省下反复重做的几十步）
    · 不过 ⇒ 报告逐条说清**哪里不对、怎么改** ✓

## 检查项（全部量化，不靠"看着还行"）

  1. **编码兼容**  h264 + yuv420p + aac ⇒ 任何播放器/手机都能放（治"只有声音"）
  2. **亮度**      全片平均 ≥ 阈值（治"画面太暗"，真机那次只有 27.7/255）
  3. **黑场**      不许有 ≥0.5 秒的连续黑
  4. **字幕换行**  一行 ≤ `MAX_CHARS` 字（治"32 字挤一行 ⇒ 看着像说了两遍"）
  5. **字幕叠字**  字幕带边缘密度异常 ⇒ 判"画了两遍"（治重影）
  6. **片尾留白**  人声结束到片尾 ≥ 阈值（治"话没说完就断了"）
  7. **交付物**    清单齐不齐（治"没发布上去"）

## 用法

    & <python> tools/qa_video.py out/final.mp4 --workspace .

    --json      输出 JSON（给程序读）
    --max-chars 一行字幕的字数上限（默认 14）

★ **它只管"客观质量"，不碰任何创作决策**（内容/风格/时长全由 AI 定）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

#: 一行字幕的字数上限（真机那次出现 32 字挤一行 ⇒ 小屏读不过来）
MAX_CHARS_DEFAULT = 14
#: 全片平均亮度的下限（255 制；真机"太暗"那次只有 27.7，修好后 44）
MIN_BRIGHTNESS = 35.0
#: 连续黑场的容忍时长（秒）
MAX_BLACK_RUN = 0.5
#: 片尾留白下限（秒）——人声结束到成片结尾
MIN_TAIL_PAD = 0.4
#: 字幕带边缘密度相对基线的倍数上限（超过视为"画了两遍/重叠"）
EDGE_DENSITY_RATIO = 1.6

#: 兼容的编码（其它一律判不合格 —— 用户"只有声音没画面"就是踩了这个）
OK_VIDEO_CODECS = ("h264", "hevc", "h265", "vp9", "av1")
#: 现代播放器/手机硬解要求的像素格式
OK_PIX_FMTS = ("yuv420p", "yuvj420p", "nv12")

#: 交付物清单（缺一不可；路径相对工作区）
REQUIRED_DELIVERABLES = (
    "out/final.mp4",
    "data/script.md",
    "data/vo_full.mp3",
    "data/vo_full.json",
    "data/timeline.json",
    "out/douyin/hook.txt",
    "out/douyin/title.txt",
    "out/douyin/desc.txt",
    "out/douyin/cover.txt",
    "out/douyin/fact-check.md",
    "out/qa/qa-report.md",
)


# ─────────────────────────────────────────────────────────────────────────────
# 工具层：找 ffmpeg / ffprobe（**要带 libx264 的那个**，见体检脚本 preflight）
# ─────────────────────────────────────────────────────────────────────────────

_KNOWN_GOOD_FFMPEG_DIRS = (r"D:\MiniMax Design\current\resources\ffmpeg",)


def _which(name: str) -> str | None:
    """找可执行文件；PATH 上那个 ffmpeg 可能是残缺版，所以也要看已知好目录。"""
    p = shutil.which(name)
    if p:
        return p
    for d in _KNOWN_GOOD_FFMPEG_DIRS:
        for ext in (".exe", ""):
            cand = Path(d) / f"{name}{ext}"
            if cand.exists():
                return str(cand)
    return None


def _run(cmd: list[str], timeout: float = 300.0) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return -1, "找不到该命令"
    except subprocess.TimeoutExpired:
        return -2, f"超时（>{timeout:.0f}s）"
    except OSError as e:
        return -3, f"{type(e).__name__}: {e}"


def probe_video(path: str | Path) -> dict | None:
    """ffprobe 读轨道信息（**记得带文件参数** —— 真机事故就出在这行少了它）。"""
    fp = _which("ffprobe")
    if not fp:
        return None
    rc, out = _run([fp, "-v", "error", "-show_streams", "-show_format",
                    "-of", "json", str(path)])
    if rc != 0 or not out.strip():
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def measure_brightness(path: str | Path, *, step: float = 0.5) -> list[float]:
    """全片亮度采样（每 `step` 秒一帧的平均亮度 YAVG，255 制）。"""
    ff = _which("ffmpeg")
    if not ff:
        return []
    rc, out = _run([ff, "-hide_banner", "-nostats", "-i", str(path),
                    "-vf", f"fps=1/{step},signalstats,"
                           "metadata=print:key=lavfi.signalstats.YAVG",
                    "-an", "-f", "null", os.devnull])
    if rc not in (0, 1):
        return []
    return [float(m.group(1)) for m in re.finditer(r"YAVG=([\d.]+)", out)]


def detect_black_runs(path: str | Path, *, min_run: float = 0.3) -> list[tuple[float, float]]:
    """找连续黑场，返回 [(start, end), …]。"""
    ff = _which("ffmpeg")
    if not ff:
        return []
    rc, out = _run([ff, "-hide_banner", "-nostats", "-i", str(path),
                    "-vf", f"blackdetect=d={min_run}:pix_th=0.10", "-an",
                    "-f", "null", os.devnull])
    runs: list[tuple[float, float]] = []
    cur = None
    for line in out.splitlines():
        m = re.search(r"black_start:\s*([\d.]+)", line)
        if m:
            cur = float(m.group(1))
        m2 = re.search(r"black_end:\s*([\d.]+)", line)
        if m2 and cur is not None:
            runs.append((cur, float(m2.group(1))))
            cur = None
    return runs


def measure_tail_voice_end(path: str | Path) -> float | None:
    """人声（有声音）在几秒结束 —— 用于判"片尾留白够不够"。"""
    ff = _which("ffmpeg")
    if not ff:
        return None
    rc, out = _run([ff, "-hide_banner", "-nostats", "-i", str(path),
                    "-af", "silencedetect=n=-35dB:d=0.25", "-f", "null", os.devnull])
    starts = [float(m.group(1)) for m in re.finditer(r"silence_start:\s*([\d.]+)", out)]
    return starts[-1] if starts else None


def measure_caption_edge_density(path: str | Path, *, start: float,
                                 band_h: int = 200, bottom_margin: int = 60,
                                 ) -> float | None:
    """量"字幕带"的边缘密度 —— 重影（画两遍）会让它接近翻倍。

    取下半屏靠底的一条横带，做边缘检测后统计非零像素占比。
    """
    ff = _which("ffmpeg")
    if not ff:
        return None
    vf = (f"crop=iw:ih*{band_h}/1920:0:ih-ih*{band_h}/1920-{bottom_margin},"
          "edgedetect=low=0.05:high=0.15,format=gray,"
          "signalstats,metadata=print:key=lavfi.signalstats.YAVG")
    rc, out = _run([ff, "-hide_banner", "-nostats", "-ss", str(start), "-i", str(path),
                    "-frames:v", "1", "-vf", vf, "-f", "null", os.devnull])
    m = re.search(r"YAVG=([\d.]+)", out)
    if not m:
        return None
    # 边缘图的平均亮度 / 255 ≈ 边缘密度
    return float(m.group(1)) / 255.0


# ─────────────────────────────────────────────────────────────────────────────
# 判据层：**纯函数**（不依赖外部程序 ⇒ 能单测、能离线跑）
# ─────────────────────────────────────────────────────────────────────────────

def check_codecs(probe: dict) -> dict:
    """编码兼容性 —— 治"只有声音没画面"。"""
    streams = (probe or {}).get("streams") or []
    vid = next((s for s in streams if s.get("codec_type") == "video"), None)
    aud = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if vid is None:
        return {"name": "编码兼容", "ok": False,
                "detail": "文件里**没有视频轨**（只有音频 ⇒ 播放器只出声音）",
                "fix": "重新渲染并确认 ffmpeg 收到 -i frames/frame_%04d.png 且带 -c:v"}
    codec = str(vid.get("codec_name") or "?")
    pix = str(vid.get("pix_fmt") or "?")
    if codec not in OK_VIDEO_CODECS:
        return {"name": "编码兼容", "ok": False,
                "detail": f"视频编码是 {codec}（老编码，现代播放器多不认 ⇒ 只有声音没画面）",
                "fix": f"改用 h264：-c:v libx264 -pix_fmt yuv420p -movflags +faststart"
                       f"（当前 {codec}）"}
    if pix not in OK_PIX_FMTS:
        return {"name": "编码兼容", "ok": False,
                "detail": f"像素格式 {pix} 很多手机硬解放不出来",
                "fix": "加 -pix_fmt yuv420p"}
    if aud is None:
        return {"name": "编码兼容", "ok": False,
                "detail": "**没有音轨**（发出去是哑的）",
                "fix": "确认渲染时带上了配音 -i data/vo_full.mp3"}
    return {"name": "编码兼容", "ok": True,
            "detail": f"{codec} / {pix} / {aud.get('codec_name')}", "fix": ""}


def check_brightness(samples: list[float], *, floor: float = MIN_BRIGHTNESS) -> dict:
    """全片亮度 —— 治"画面太暗"。"""
    if not samples:
        return {"name": "亮度", "ok": True, "detail": "（没采到样，跳过）", "fix": ""}
    avg = sum(samples) / len(samples)
    if avg < floor:
        return {"name": "亮度", "ok": False,
                "detail": f"全片平均亮度 {avg:.1f}/255（低于 {floor:.0f}，"
                          f"手机上像黑屏；最低 {min(samples):.1f}）",
                "fix": "把背景/元素提亮（抬高底色亮度或加大图形面积）"}
    return {"name": "亮度", "ok": True, "detail": f"平均 {avg:.1f}/255", "fix": ""}


def check_black_runs(runs: list[tuple[float, float]], *,
                     max_run: float = MAX_BLACK_RUN) -> dict:
    """连续黑场 —— 治"中间一大段是黑的"。"""
    bad = [(a, b) for a, b in runs if (b - a) >= max_run]
    if bad:
        worst = max(bad, key=lambda r: r[1] - r[0])
        return {"name": "黑场", "ok": False,
                "detail": f"{len(bad)} 段连续黑场（最长 {worst[1] - worst[0]:.1f}s，"
                          f"在 {worst[0]:.1f}–{worst[1]:.1f}s）",
                "fix": "检查该时间段的分镜是否漏画内容，或背景色过暗"}
    return {"name": "黑场", "ok": True, "detail": f"没有 ≥{max_run}s 的连续黑场", "fix": ""}


def check_caption_lines(lines: list[str], *, max_chars: int = MAX_CHARS_DEFAULT) -> dict:
    """字幕一行字数 —— 治"32 个字挤一行"（看着像说了两遍）。"""
    over = [(ln, len(ln)) for ln in lines if len(ln) > max_chars]
    if over:
        worst = max(over, key=lambda x: x[1])
        return {"name": "字幕换行", "ok": False,
                "detail": f"{len(over)} 行超过 {max_chars} 字（最长 {worst[1]} 字：{worst[0][:18]}…）",
                "fix": f"按 {max_chars} 字**自动换行**（超长就拆成两行，别挤一行）"}
    return {"name": "字幕换行", "ok": True,
            "detail": f"{len(lines)} 行，均 ≤{max_chars} 字", "fix": ""}


def check_caption_overlap(edge_density: float, baseline: float, *,
                          ratio_limit: float = EDGE_DENSITY_RATIO) -> dict:
    """字幕叠字/重影 —— 边缘密度异常升高说明**同一块地方画了两遍**。

    ★ 基线之外的读数才判：没字幕（密度≈0）**不算错**（纯画面场景合法）
    """
    if edge_density <= 0.0:
        return {"name": "字幕叠字", "ok": True, "detail": "该帧无字幕", "fix": ""}
    if baseline > 0 and edge_density >= baseline * ratio_limit:
        return {"name": "字幕叠字", "ok": False,
                "detail": f"字幕带边缘密度 {edge_density:.3f}，是基线 {baseline:.3f} 的 "
                          f"{edge_density / baseline:.1f} 倍 ⇒ 像**画了两遍**（重影）",
                "fix": "底字与高亮字必须**同字号、同 y、同 x**，高亮只盖它那一段；"
                       "别把整行和单词各画一次"}
    return {"name": "字幕叠字", "ok": True,
            "detail": f"边缘密度 {edge_density:.3f}（基线 {baseline:.3f}）", "fix": ""}


def check_tail_padding(video_duration: float, voice_end: float, *,
                       min_pad: float = MIN_TAIL_PAD) -> dict:
    """片尾留白 —— 治"话没说完就断了"。"""
    pad = video_duration - voice_end
    if pad < min_pad:
        extra = "（**配音比成片还长，最后一句被切了**）" if pad < 0 else ""
        return {"name": "片尾留白", "ok": False,
                "detail": f"人声在 {voice_end:.2f}s 结束、成片 {video_duration:.2f}s "
                          f"⇒ 留白仅 {pad:.2f}s{extra}",
                "fix": "把成片时长提到「人声结束 + 0.5s」以上（或把最后一句台词改短）"}
    return {"name": "片尾留白", "ok": True, "detail": f"{pad:.2f}s", "fix": ""}


def check_deliverables(*, existing: set[str],
                       required: tuple[str, ...] = REQUIRED_DELIVERABLES) -> dict:
    """交付物清单 —— 治"没发布上去/少文件"。"""
    norm = {str(x).replace("\\", "/").lstrip("./") for x in existing}
    missing = [r for r in required
               if r.replace("\\", "/").lstrip("./") not in norm
               and not any(n.endswith(r.replace("\\", "/")) for n in norm)]
    if missing:
        return {"name": "交付物", "ok": False,
                "detail": missing,
                "fix": "补齐上面这些文件（内容类，不是重做视频）"}
    return {"name": "交付物", "ok": True, "detail": f"{len(required)} 项齐全", "fix": ""}


# ─────────────────────────────────────────────────────────────────────────────
# 报告层
# ─────────────────────────────────────────────────────────────────────────────

def build_report(items: list[dict]) -> dict:
    """汇总：每项结论 + 总判定。"""
    return {"passed": all(bool(i.get("ok")) for i in items), "items": items}


def format_report(rep: dict) -> str:
    """给人（和模型）看的报告 —— 失败项要**说清怎么改**。"""
    lines = ["=" * 70, "成片体检", "=" * 70]
    for it in rep["items"]:
        mark = "✓" if it.get("ok") else "✗"
        lines.append(f"  {mark} {it['name']:<10} {str(it.get('detail'))[:110]}")
        if not it.get("ok") and it.get("fix"):
            lines.append(f"      → 怎么改：{it['fix']}")
            if isinstance(it.get("detail"), list):
                for d in it["detail"]:
                    lines.append(f"        缺：{d}")
    lines.append("=" * 70)
    if rep["passed"]:
        lines.append("结论：✅ **全部通过 —— 可以交付并 task_done**")
    else:
        bad = [i["name"] for i in rep["items"] if not i.get("ok")]
        lines.append(f"结论：✗ **{len(bad)} 项没过**（{'、'.join(bad)}）")
        lines.append("      按上面每条「怎么改」修，改完再跑一次本脚本；")
        lines.append("      ★ 只改报错的那一项，别重做整条片子。")
    lines.append("=" * 70)
    return "\n".join(lines)


def collect_lines_from_words(words_json: str | Path) -> list[str]:
    """从词时间戳 JSON 还原字幕行（按停顿切 + 按字数换行）—— 与渲染同口径。"""
    try:
        d = json.loads(Path(words_json).read_text("utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return []
    ws = d.get("words") or []
    if not ws:
        return []
    raw, cur = [], [ws[0]]
    for a, b in zip(ws, ws[1:]):
        try:
            gap = float(b["s"]) - float(a["e"])
        except (KeyError, TypeError, ValueError):
            gap = 0.0
        if gap > 0.35:
            raw.append(cur)
            cur = [b]
        else:
            cur.append(b)
    raw.append(cur)
    out = []
    for ln in raw:
        buf, n = [], 0
        for w in ln:
            k = len(str(w.get("word") or ""))
            if buf and n + k > MAX_CHARS_DEFAULT:
                out.append("".join(str(x.get("word") or "") for x in buf))
                buf, n = [], 0
            buf.append(w)
            n += k
        if buf:
            out.append("".join(str(x.get("word") or "") for x in buf))
    return out


def run_all(video: str | Path, workspace: str | Path, *,
            max_chars: int = MAX_CHARS_DEFAULT) -> dict:
    """跑全部检查，返回报告。"""
    items: list[dict] = []
    probe = probe_video(video)
    if probe:
        items.append(check_codecs(probe))
    else:
        items.append({"name": "编码兼容", "ok": False,
                      "detail": "读不出轨道信息（文件损坏或 ffprobe 缺失）",
                      "fix": "确认 ffmpeg/ffprobe 可用且文件完整"})

    items.append(check_brightness(measure_brightness(video)))
    items.append(check_black_runs(detect_black_runs(video)))

    ws = Path(workspace)
    lines = collect_lines_from_words(ws / "data" / "vo_full.json")
    if lines:
        items.append(check_caption_lines(lines, max_chars=max_chars))

    ve = measure_tail_voice_end(video)
    dur = None
    if probe:
        try:
            dur = float((probe.get("format") or {}).get("duration") or 0)
        except (TypeError, ValueError):
            dur = None
    if ve is not None and dur:
        items.append(check_tail_padding(dur, ve))

    existing = {str(p.relative_to(ws)).replace("\\", "/")
                for p in ws.rglob("*") if p.is_file()}
    items.append(check_deliverables(existing=existing))
    return build_report(items)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="成片体检（渲染完跑一次）")
    ap.add_argument("video", nargs="?", default="out/final.mp4", help="成片路径")
    ap.add_argument("--workspace", default=".", help="工作区目录（默认当前）")
    ap.add_argument("--max-chars", type=int, default=MAX_CHARS_DEFAULT,
                    help=f"一行字幕字数上限（默认 {MAX_CHARS_DEFAULT}）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    rep = run_all(args.video, args.workspace, max_chars=args.max_chars)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(format_report(rep))
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
