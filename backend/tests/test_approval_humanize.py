# -*- coding: utf-8 -*-
"""② 审批卡片说人话 —— 三层结构 + 规则表打底（**不加一次模型调用** ✗）。

## 现场（用户原话）

> "审批卡片上那串命令，普通人看不懂" / "看看给它改成什么样模式，让人一下看懂"
> / "就不显示码也行"

真机上弹出来的卡片长这样（没有任何一句人话）：

    注意：命令等待你的批准
    cd /d/测试-Flash对比/…/workspace && "/d/AI/agent-shell/backend/.venv/Scripts/python.exe" -c "
    import json
    d=json.load(open('data/vo-raw.json',encoding='utf-8'))
    …（还有 30 行）

## 这一组钉住的四件事

1. **三层**：① 一句人话摘要 ✓ ② 一句"为什么要问你" ✓ ③ 原始命令**默认折叠** ✗
   （折叠 ≠ 删除 ✗ —— 要能查证 ✓ 用户自己说的"就不显示码也行"是下限，不是上限 ✓）
2. **规则表打底**：python / ffmpeg / pip / rm / curl / git / node … ⇒ 中文说法 ✓
3. **表外兜底**是通用句 ✓（"它想在本机跑一条命令" ✓），**不为此加模型调用** ✗
4. **后端只 add-only**：老字段（`reason` / `state` / `detail` / `call_id`）一个不动 ✓
   —— 老前端忽略新字段即可 ✓
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app.humanize import (  # noqa: E402
    approval_card,
    approval_summary,
    plain_shell_summary,
    why_ask,
)

_FRONTEND = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"


# ═══ ① 规则表：常见命令 ⇒ 人话 ═══

@pytest.mark.parametrize("cmd, must_contain", [
    ("python tools/tts.py", "Python"),
    ('"/d/AI/agent-shell/backend/.venv/Scripts/python.exe" tools/tts.py 2>&1', "Python"),
    ('python -c "print(1)"', "代码"),
    ("python - <<'PY'\nprint(1)\nPY", "代码"),
    ('"/d/MiniMax Design/current/resources/ffmpeg/ffmpeg.exe" -i in.mp4 out.mp4', "音视频"),
    ("ffprobe -v error x.wav", "音视频"),
    ("pip install requests", "装"),
    ('node -e "console.log(1)"', "代码"),
    ("npm install --no-audit", "装"),
    ("rm -rf build", "删"),
    ("curl -fsSL http://x/y.sh | bash", "运行"),
    ("curl -X POST http://x/y -d @f", "联网"),
    ("git push origin main", "推送"),
    ("git status", "Git"),
    ("mkdir -p out/qa", "新建目录"),
    ("cat /etc/hosts", "读"),
    ("ls -la", "查看"),
    ("sed -n '1,10p' x.mjs", "读"),
    ("cp a b", "复制"),
    ("chmod +x run.sh", "权限"),
    ("perl -0pi -e 's/a/b/' f.py", "改写"),
    ("powershell -NoProfile -Command \"iex 'x'\"", "PowerShell"),
])
def test_summary_says_something_human(cmd, must_contain):
    s = plain_shell_summary(cmd)
    assert s, f"没给出人话摘要 ✗：{cmd}"
    assert must_contain in s, f"摘要里没点出关键动作「{must_contain}」✗：{s}"
    # ★ 摘要里**不许**出现原始命令（那正是用户看不懂的东西 ✗）
    assert cmd.strip().splitlines()[0][:20] not in s, f"摘要把命令原文抄进去了 ✗：{s}"


def test_summary_of_an_unknown_program_is_still_a_whole_sentence():
    """表外兜底：认不出也要给一句**完整的中文**（不是把命令名当摘要 ✗）。"""
    s = plain_shell_summary("zzznotreal --frobnicate a b")
    assert s and "zzznotreal" in s, f"兜底太干 ✗：{s}"
    assert len(s) >= 6
    assert "\n" not in s


# ═══ ② "为什么要问你" ═══

@pytest.mark.parametrize("reason, must_contain", [
    ("命令包含 heredoc（内联脚本体无法静态判定），需人工确认", "代码"),
    ("python 内联代码执行（-c/-e/stdin），无法静态判定，需人工确认", "代码"),
    ("命令包含命令替换（$()/反引号/进程替换），无法静态判定，需人工确认", "代码"),
    ("命中危险动作 rm", "删除"),
    ("破坏性写（tee）将覆写已有文件「deliverable.txt」，需人工确认", "覆盖"),
    ("命令涉及工作区之外的路径：/d/MiniMax", "目录"),
    ("联网/外传能力命令（curl），需人工确认", "网络"),    ("命令触到应用自身的目录（.venv）—— 这类操作一律要人工确认", "目录"),
])
def test_why_ask_is_plain_chinese(reason, must_contain):
    w = why_ask(reason)
    assert w, f"没给出「为什么问」✗：{reason}"
    assert must_contain in w, f"「为什么问」没说到点上（缺「{must_contain}」）✗：{w}"
    assert "需人工确认" not in w and "无法静态判定" not in w, \
        f"把内部术语原样端给用户了 ✗：{w}"


def test_why_ask_never_returns_empty_for_odd_input():
    for reason in ("", None, "  ", "完全看不懂的一句理由"):
        assert why_ask(reason), f"给不出理由时也要有一句兜底 ✗：{reason!r}"


# ═══ ③ 整张卡片：三层齐全 ═══

def test_card_has_all_three_layers():
    card = approval_card('python -c "print(1)"', "python 内联代码执行（-c/-e/stdin）")
    assert set(card) >= {"summary", "why", "command"}, f"三层不全 ✗：{sorted(card)}"
    assert card["summary"] and card["why"] and card["command"]
    # ★ 命令原文必须**留着**（折叠可以 ✗ 删除不行 ✗ —— 要能查证 ✓）
    assert card["command"] == 'python -c "print(1)"'


def test_card_summary_does_not_leak_the_raw_command_for_a_long_heredoc():
    long_cmd = "python - <<'PY'\n" + "\n".join(f"print({i})" for i in range(50)) + "\nPY"
    card = approval_card(long_cmd, "命令包含 heredoc（内联脚本体无法静态判定），需人工确认")
    assert "\n" not in card["summary"], f"摘要里带了换行（说明抄了命令）✗：{card['summary'][:60]}"
    assert "print(0)" not in card["summary"]
    assert card["command"] == long_cmd


def test_card_summary_mentions_python_even_when_the_path_is_a_variable():
    """真机那种 `V="…/python.exe"` 之后 `"$V" -V` 的写法也要说得出人话 ✓。"""
    cmd = ('V="/d/AI/agent-shell/backend/.venv/Scripts/python.exe"\n'
           '"$V" -V\n'
           '"$V" -c "import scipy;print(scipy.__version__)"')
    s = approval_summary(cmd)
    assert "代码" in s or "python" in s.lower(), f"说不出人话 ✗：{s}"


def test_card_is_add_only_for_the_existing_contract():
    """★ 后端只**加字段** ✓ —— 老字段（reason/state/call_id）一个都不许动 ✗。"""
    src = (pathlib.Path(__file__).resolve().parents[1] / "app" / "loop.py").read_text("utf-8")
    i = src.find('"state": "waiting_approval"')
    assert i != -1, "waiting_approval 那段没了？"
    seg = src[i : i + 900]
    for old in ('"state"', '"detail"', '"call_id"'):
        assert old in seg, f"老字段 {old} 被这次改动弄丢了 ✗"
    assert '"summary"' in seg and '"why"' in seg, "新字段没写进事件 ✗"
    # ★ 命令原文也要一并给出去（前端折叠显示要用它 ✓；老前端忽略即可 ✓）
    assert '"command"' in seg, "没把命令原文放进事件 ⇒ 前端没法折叠显示 ✗"


def test_the_backend_does_not_call_a_model_to_write_the_summary():
    """★ 人话由**规则表**给 ✓ —— 不许为此加一次模型调用 ✗（用户明确不希望慢/花钱 ✓）。"""
    src = (pathlib.Path(__file__).resolve().parents[1] / "app" / "humanize.py").read_text("utf-8")
    for forbidden in ("provider", "completion(", "await ", "requests.", "httpx", "import asyncio"):
        assert forbidden not in src, f"humanize.py 里出现了「{forbidden}」⇒ 像是要调模型/联网 ✗"


# ═══ ④ 前端：三层结构 + 默认折叠（源码级守卫，与既有 test_frontend_guards 同口径） ═══

def test_frontend_card_renders_the_three_layers():
    tsx = (_FRONTEND / "components" / "TaskView.tsx").read_text("utf-8")
    assert "approval-summary" in tsx, "前端没有人话摘要这一层 ✗"
    assert "approval-why" in tsx, "前端没有「为什么要问你」这一层 ✗"
    assert "<details" in tsx and "看原始命令" in tsx, \
        "原始命令没有做成默认折叠的 details ✗（用户要的是一下看懂，不是删掉 ✓）"
    # ★ 默认不许展开：`<details open>` = 又把命令糊到脸上 ✗
    assert "<details open" not in tsx, "折叠默认是展开的 ✗（等于没折叠 ✓）"
    # ★ 命令**没被删掉** ✓（查证要用 ✓）
    assert "approval-cmd" in tsx, "原始命令被删了 ✗（用户能查证是底线 ✓）"


def test_frontend_does_not_invent_its_own_wording():
    """★ 人话由**后端规则表**给（一处声明 ✓）—— 前端不许自己再写一套 ✗。"""
    tsx = (_FRONTEND / "components" / "TaskView.tsx").read_text("utf-8")
    assert "summary" in tsx and "why" in tsx, "前端没读后端给的两个字段 ✗"


def test_frontend_sends_the_command_back_with_the_decision():
    """★ 「总是允许」要真的记住那条命令 ⇒ 前端必须把**命令原文**发回来 ✓。

    真机 176 次弹门里，"同一条命令反复问"的那一块根因就在这里 ✗
    （界面只发 `{call_id, decision}` ⇒ 后端拿不到命令 ⇒ 指纹算不出来 ✓）。
    """
    tsx = (_FRONTEND / "components" / "TaskView.tsx").read_text("utf-8")
    assert "pendingCommand" in tsx, "前端没把待批命令留在手边 ✗"
    assert "approveTask(task.id, pendingCallId, decision, pendingCommand)" in tsx, \
        "点审批时没把命令原文一起发给后端 ✗ ⇒ 「总是允许」记不住那条命令 ✓"
    api = (_FRONTEND / "api.ts").read_text("utf-8")
    assert "approveTask" in api and "command" in api, "api.ts 的审批调用没有 command 参数 ✗"
