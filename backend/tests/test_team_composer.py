"""团队可试性批（语音 + 成员一键选择）的锚点。

用户原话：「团队里面的就是我选择一个团队里面的说话方式啊，就输入框能不能像正常那种又有语音这样的，
然后还有那个选择人物能不能有个选项啊？就不用 out 或者可直接选项也行。」

现状核对（改前）：
  · **@ 自动补全早就有**（打 `@` 出候选、上下键选、回车插入）—— 但要先知道名字才打得出来
  · **团队输入框没有语音** ✗（首页与任务内都有，团队那份漏了）
所以这一批补的是：**语音输入**（复用唯一实现）+ **成员一键选择**（不用记名字）。
"""
from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SRC = _ROOT / "frontend" / "src"
_TV = (_SRC / "components" / "TeamView.tsx").read_text("utf-8")
_CSS = (_SRC / "styles.css").read_text("utf-8")
_VERIFY = (_ROOT / "frontend" / "scripts" / "verify_team_composer.mjs").read_text("utf-8")


def _code_only(src: str) -> str:
    out = []
    for line in src.splitlines():
        s = line.lstrip()
        if s.startswith("//") or s.startswith("*") or s.startswith("/*"):
            continue
        out.append(line.split("//")[0])
    return "\n".join(out)


def test_team_composer_uses_the_shared_voice_implementation():
    """★ 不许再出现"第三份录音实现"（首页那份漏修过一次，教训写进 test_voice_input.py）。"""
    assert "useVoiceDraft(" in _TV, "团队输入框没接语音"
    assert "new MediaRecorder" not in _TV, "团队里又自己写了一份录音 ✗"
    assert 'aria-label="语音输入"' in _TV, "语音按钮缺稳定的定位标记（验证脚本靠它）"
    assert "voice.toggle()" in _TV, "按钮没接到切换上"


def test_member_picker_exists_and_is_hidden_in_broadcast():
    """广播模式全员都收到，插 @ 没意义 ⇒ 那个按钮不该出现。

    ★ 整行匹配 `{pickOpen && (`：只查 `className="member-pick"` 的话，
    把面板包进 `{false && pickOpen && (` 这种"写了但点不出来"的变体照样通过
    （本班回滚组实测过 —— 这是本会话第四次栽在"锚点太宽"上）。
    """
    assert 'className="member-pick"' in _TV, "没有成员一键选择面板"
    assert "\n                        {pickOpen && (" in _TV, "面板被条件关掉了（点了不出名单）"
    assert "{false && pickOpen" not in _TV, "面板恒不渲染"
    assert "curGroup.mode !== 'broadcast'" in _TV, "@ 按钮没按模式开关"
    assert "@' + e.name" in _TV or "@${e.name}" in _TV, "点成员没把 @名字 拼进输入框"


def test_member_picker_styles_exist():
    for k in (".member-pick {", ".member-pick-item"):
        assert k in _CSS, f"样式缺 {k}"


def test_leader_dispatch_has_real_test_coverage_now():
    """★ 用户问"你测没测试过这些"——组长拆解那条主路此前零覆盖，现在有 7 个锚点。"""
    t = (_ROOT / "backend" / "tests" / "test_team_leader_dispatch.py").read_text("utf-8")
    for k in ("test_leader_mode_dispatches_real_tasks_to_each_member", "test_at_mention_bypasses_the_leader",
              "test_broadcast_mode_sends_the_same_task_to_everyone", "test_unparseable_plan_says_so_instead_of_silently_dropping",
              "test_one_member_failure_does_not_kill_the_others"):
        assert k in t, f"组长/协作路缺锚点：{k}"


def test_verification_covers_voice_and_picker():
    assert "语音按钮" in _VERIFY, "没验证团队语音按钮"
    assert "member-pick-item" in _VERIFY, "没验证成员面板"
    assert "点一下就把 @名字 填进输入框" in _VERIFY, "没验证「点一下就填」"
    assert "先点开这个群" in _VERIFY or "点开这个群" in _VERIFY, \
        "没先打开群就找按钮（本班就是这么假红一次的：按钮要在打开群之后才渲染）"
