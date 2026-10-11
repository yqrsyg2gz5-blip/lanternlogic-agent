"""语音输入（2026-10-05 用户报"根本不好使"）的锚点。

这一批的教训值得写进测试：**同一个功能在首页输入框和任务内输入框各写了一份**，
我修了后者、测的也是后者，而用户天天用的是前者 ⇒ 用户看到的是"根本没修好"。
所以锚点里两条最基本的就是：
  ① 两份实现必须共用（Hero 用 `lib/useVoiceDraft`）
  ② 两个输入框都要能被验证脚本点到（稳定选择器 `aria-label="语音输入"` + 验证脚本覆盖两处）

另外钉三个真坑（都是本班实测踩到的）：
  · 录音必须是 **WAV**（浏览器 MediaRecorder 默认 webm，而网关只收 wav/mp3；本机没有 ffmpeg）
  · 上传文件名要按真实类型（写死 draft.webm 时后端照后缀就把 wav 当 webm 拒掉）
  · `stopDraft` 必须先判 WAV 句柄再判老句柄 —— 顺序反了就直接 return（点了没反应）
"""
from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SRC = _ROOT / "frontend" / "src"
_HERO = (_SRC / "components" / "Hero.tsx").read_text("utf-8")
_TV = (_SRC / "components" / "TaskView.tsx").read_text("utf-8")
_HOOK = (_SRC / "lib" / "useVoiceDraft.ts").read_text("utf-8")
_REC = (_SRC / "lib" / "recwav.ts").read_text("utf-8")
_API = (_SRC / "api.ts").read_text("utf-8")
_ASR = (_ROOT / "backend" / "app" / "asr.py").read_text("utf-8")
_VERIFY = (_ROOT / "frontend" / "scripts" / "verify_voice_input.mjs").read_text("utf-8")


def test_hero_uses_the_shared_voice_hook():
    """★ 核心一条：首页不能再有自己那份录音实现（这正是用户报"没修好"的原因）。

    锚点用**整行**匹配：只查 `useVoiceDraft(` 的话，把首页改成
    `void useVoiceDraft({...})` + 一个假的 voice 对象也照样通过（本班回滚组实测过）。
    """
    assert "\n  const voice = useVoiceDraft({" in _HERO, "首页没真正用共用实现（写了但没用也算没修）"
    assert "new MediaRecorder" not in _HERO, "首页还留着自己那套 MediaRecorder 录音"
    assert "voiceTranscribe" not in _HERO, "首页还自己发转写请求（应当由 hook 统一负责）"


def test_both_composers_expose_a_stable_voice_selector():
    assert 'aria-label="语音输入"' in _HERO and 'aria-label="语音输入"' in _TV, \
        "两个输入框的麦克风都要有 aria-label=语音输入（标题会随状态变，验证脚本靠它定位）"
    assert "语音输入" in _HOOK, "hook 里没有语音提示文案"


def test_hotkey_exists_on_both_surfaces_and_avoids_the_ime():
    """用户点名要的快捷键：**按住右 Ctrl** 说话，松开自动转写。

    ★ 为什么不是 Ctrl+空格（第一版就是这么写的，用户实测"不好用"）：
      中文 Windows 上 **Ctrl+空格 是切换输入法的系统键** —— 被 IME 抢走，网页收不到。
      所以主键必须是 IME 不碰的：右 Ctrl（`ControlRight`）。
    """
    assert "ControlRight" in _HOOK and "ControlRight" in _TV, "两处快捷键都必须认右 Ctrl"
    assert "'ControlRight' ||" in _HOOK, "右 Ctrl 不是主判据（只在 Ctrl+空格 里附带就不算）"
    assert "keydown" in _HOOK and "keyup" in _HOOK, "快捷键不是按住/松开式"
    # 界面上要写清楚按哪个键（写上被 IME 吃掉的 Ctrl+空格 等于没写）
    assert "右 Ctrl" in _HERO and "右 Ctrl" in _TV, "界面上没告诉用户按住哪个键"


def test_recording_is_wav_so_no_ffmpeg_is_needed():
    assert "writeStr(0, 'RIFF')" in _REC and "writeStr(8, 'WAVE')" in _REC, "没写 WAV 头"
    assert "sampleRate: 16000" in _REC, "不是 16k（网关偏好）"
    assert "audio/wav" in _REC, "Blob 类型不是 audio/wav"


def test_upload_name_matches_the_real_type():
    assert "blob.type === 'audio/wav' ? 'draft.wav'" in _API, \
        "上传文件名没按真实类型（写死 webm 会让后端把 wav 当 webm）"


def test_backend_sniffs_format_instead_of_trusting_the_filename():
    assert "def sniff_audio_format(" in _ASR, "后端还是只看后缀"
    assert "RIFF" in _ASR and "WAVE" in _ASR, "嗅探没看 WAV 魔数"
    assert "网关只收 wav/mp3" in _ASR, "不支持的格式没说清楚（用户只会看到难懂的 400）"


def test_stop_path_checks_the_wav_handle_first():
    """★ 本班实测踩到：先判老句柄的话，走 WAV 时 `if (!st) return` 直接返回 ⇒ 点了没反应。

    注意：比较前必须**剔掉注释行** —— 注释里也会提到 `draftRef.current`，
    直接 index() 会被注释抢先命中（本会话第三次栽在"注释替代码蒙混过关"上）。
    """
    seg = _TV.split("const stopDraft = ")[1][:600]
    code = "\n".join(l for l in seg.splitlines() if not l.strip().startswith("//"))
    # 整行匹配：只比"谁先出现"的话，`if (false && wavRef.current)` 这种"写了但不生效"的
    # 变体照样能通过（本班回滚组实测过）
    assert "\n    if (wavRef.current) {" in code, "WAV 句柄那一支被绕过了（点了没反应）"
    assert "if (false && wavRef.current)" not in code, "WAV 句柄那一支被禁用了"
    assert code.index("wavRef.current") < code.index("draftRef.current"), \
        "stopDraft 里 WAV 句柄必须**先**判（顺序反了 = 点了没反应）"


def test_verification_covers_both_surfaces_and_the_hotkey():
    assert "首页录音转写成功" in _VERIFY and "任务内录音转写成功" in _VERIFY, \
        "验证脚本必须两个输入框都测（只测一个正是这次的教训）"
    assert "按住右 Ctrl" in _VERIFY, "没验证快捷键"
    assert "ControlRight" in _VERIFY, "验证脚本没按右 Ctrl（会用被 IME 吃掉的组合键假绿）"
    assert "aria-label=" in _VERIFY, "定位按钮的方式不稳定（标题会随录音状态变化）"
