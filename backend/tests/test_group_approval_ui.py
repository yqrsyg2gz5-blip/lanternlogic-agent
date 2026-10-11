# -*- coding: utf-8 -*-
"""★ 第 10 项 ② 多审批汇总（界面那一块）—— 2026-10-07。

## 要解决的真问题（用户今天一个人点了 11 次审批）

一个群同时开几个任务时，审批卡片**散在 feed 里一条条刷屏** ✗：
· 他得**一个个点** ✓ · **看不出还有几条在等** ✗（点完一条才发现下面还有 ✓）

## 做法

在消息列表**上方**给一块汇总：「**还差 N 个决定**」+ 每人一行（任务/命令 ✓）+ 就地「批准 / 拒绝」✓

★ 两条纪律：
  · 计数与卡片**同源** ✓（都用 `handled` 那份已批记录 ✓ 不另立一套 ✗）
  · 批**还是走原来那个 `doApprove`** ✓ —— 不另写一条提交路径 ✗
    （否则"这类以后都别问"的确认文案 / `apBusy` 防手滑 / 失败提示 迟早两边不一致 ✓）
"""
from __future__ import annotations

import pathlib

from app import main as m

TEAM = (pathlib.Path(m.__file__).resolve().parents[2]
        / "frontend" / "src" / "components" / "TeamView.tsx")


def test_the_summary_block_exists():
    """★ 那块汇总得**真在界面上** ✓ —— 而且要在消息列表**上方**（不然还是得翻 ✓）。"""
    src = TEAM.read_text("utf-8")
    assert "个决定等你" in src, "没有「还差 N 个决定」那块汇总 ✗"
    i = src.find("个决定等你")
    j = src.find("{feed.map((m) => {")
    assert i > 0 and j > 0 and i < j, "汇总块不在消息列表**上方** ⇒ 用户还是得往下翻才看得到 ✗"


def test_it_counts_pending_and_hides_when_zero():
    """★★ **数得对 + 没有就不显示** ✓
    （一直挂个"还差 0 个决定"比没有更烦 ✗ —— 本项目一贯的"没把握就别显示"✓）"""
    src = TEAM.read_text("utf-8")
    i = src.find("个决定等你")
    seg = src[max(0, i - 1600) : i + 400]
    assert "!handled[m.approval.call_id]" in seg, \
        "没把**已经批过的**排除掉 ⇒ 数会一直不减 ✗（用户会以为没批上 ✓）"
    assert "if (!pend.length) return null" in seg, \
        "0 条时还挂着那块 ⇒ 界面永远有一条没用的提示 ✗"


def test_it_uses_the_existing_approve_path():
    """★★ 批**必须走原来那条** ✓（`doApprove` ✓）—— 不另写提交路径 ✗
    否则"这类以后都别问"的确认文案 ✓ `apBusy` 防手滑 ✓ 失败提示 ✓ 迟早两边不一致 ✓。"""
    src = TEAM.read_text("utf-8")
    i = src.find("个决定等你")
    seg = src[max(0, i - 1600) : i + 1600]
    assert "doApprove(m, 'once')" in seg, "汇总里的「批准」没走 `doApprove` ✗"
    assert "doApprove(m, 'deny')" in seg, "汇总里的「拒绝」没走 `doApprove` ✗"
    assert "api.teamApprove(" not in seg, \
        "汇总块里**自己又调了一次** teamApprove ✗ ⇒ 两条提交路径 ✓ 迟早不一致 ✓"


def test_buttons_are_busy_guarded():
    """★ 点完要**防手滑** ✓（复用原有的 `apBusy` ✓ 不另起一套状态 ✗）。"""
    src = TEAM.read_text("utf-8")
    i = src.find("个决定等你")
    seg = src[max(0, i - 1600) : i + 1600]
    assert seg.count("disabled={apBusy ===") >= 2, "批准/拒绝没接 `apBusy` ⇒ 能连点 ✗"


# ═══ ★★ A-1 收尾（2026-10-07）：主数改接**后端权威接口** ═══
#
# 收尾前的边界（我在上一轮提交信息里明说过 ✓）：
#   那块「还差 N 个决定」数的是"**这一屏 feed 里**还没批的" ✗ ⇒
#   某个审批若藏在**没加载到的更早消息**里，这一屏**数不到它** ✓（N 偏小 ✓）
#
# 后端权威接口上一轮就做好了 ✓：
#   `GET /api/v1/team/groups/{gid}/approvals` ⇒ `{count, approvals:[{task_id, call_id, title, command}]}`

_API_TS = (pathlib.Path(m.__file__).resolve().parents[2]
           / "frontend" / "src" / "api.ts")


def test_it_counts_from_the_authoritative_api():
    """★★ 主数必须**来自那个接口** ✓ —— 而不是只数"这一屏看得到的" ✗

    回滚实验：把主数改回 `fromFeed`（= 收尾前的形态 ✓）⇒ 本条必红 ✓
    """
    src = TEAM.read_text("utf-8")
    i = src.find("const pend: ")
    assert i > 0, "找不到那份待批列表 ⇒ 这条测试没在测东西 ✗"
    seg = src[i : i + 260]
    assert "pendApi" in seg and "fromFeed" in seg, (
        "主数没在「**后端权威那份**」与「这一屏 feed 那份」之间选 ✗ —— "
        "只数这一屏 ⇒ **还没加载到的更早消息里的审批数不到** ✓（用户看到的 N 偏小 ✓）")
    # 接口那三处（声明 / 真实实现 / 演示实现）都要有 ✓（少一处 tsc 就不过 ✓ 这里再钉一道 ✓）
    api = _API_TS.read_text("utf-8")
    assert "/team/groups/${gid}/approvals" in api, "api.ts 里没有这个接口 ⇒ 界面拉不到权威数 ✗"
    assert api.count("groupApprovals") >= 3, "声明 / HttpApi / MockApi 三处不齐 ⇒ tsc 过不去 ✗"


def test_it_drops_what_is_already_handled():
    """★ **已经批过的不许再显示** ✓ —— 不然那个数永远不减 ✓
    （用户刚点完「批准」✓ 数还是 1 ⇒ 他以为没批上 ✓ 会再点一次 ✓）"""
    src = TEAM.read_text("utf-8")
    i = src.find("const pend: ")
    assert i > 0
    seg = src[i : i + 400]
    assert "handled[m.approval.call_id]" in seg, \
        "最终那份待批没排除**已经批过的** ✗（数永远不减 ⇒ 用户以为没批上 ✓）"


def test_a_failed_api_call_does_not_break_the_screen():
    """★★ 接口挂了**不许把界面弄崩** ✓ —— 也不许**假装 0 条** ✗

    （群聊是用户天天用的地方 ✓ 一个只读接口 500 / 断网 / 后端正在重启 ✓
      都可能发生 ✓ 那时**退回这一屏 feed 那份**就够了 ✓ —— 宁可少数几条 ✓ 不能白屏 ✓）
    """
    src = TEAM.read_text("utf-8")
    i = src.find("const loadPend = ")
    assert i > 0, "找不到拉权威待批的那段 ⇒ 这条测试没在测东西 ✗"
    seg = src[i : i + 900]
    assert "api.groupApprovals(" in seg, "没真去拉那个接口 ✗"
    assert ".catch(" in seg, "拉失败没兜住 ⇒ 一个坏响应就能把群聊页弄崩 ✗"
    assert "setPendApi(null)" in seg, "失败后没退回 feed 那份 ✗（会一直挂着上一份 / 显示空 ✓）"
    # 换群要清 ✓（不许把 A 群的待批显示在 B 群 ✗）
    k = src.find("const tick = (): void => {", src.find("const loadPend = "))
    assert k > 0, "找不到拉 feed 那个 tick ⇒ 这条测试没在测东西 ✗"
    assert "setPendApi(null)" in src[k : k + 700], \
        "换群时没清掉上一份 ⇒ 会把 A 群的待批显示在 B 群 ✗"
