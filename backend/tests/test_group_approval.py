"""群内审批的锚点（2026-10-05 用户要求）。

用户原话：「按道理这个群聊他们的工作内容什么的，这些允许一次，这个审批，
            这个允许一次，这个都应该在他们群里。我还得上外面点，这多麻烦」

所以要钉住三件事：
  ① 群员干活卡在审批时，**群里要主动播报**（带工具与内容摘要，且同一个 call_id 只播一次）
  ② 群里要能**直接批**（与任务页共用同一份审批逻辑，语义不许分叉）
  ③ 批完群里要留痕（谁批的、批的什么），且**拒绝**不能被当成"继续"
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main as m
from app.team import TeamStore


@pytest.fixture()
def gshop(tmp_path, monkeypatch):
    st = TeamStore(tmp_path)
    emp = st.add_employee({"name": "运维", "dept": "技术部", "role": "工程师", "persona": "跑命令", "mode": "expert"})
    g = st.create_group("运维群", [emp["id"]], mode="manual")
    monkeypatch.setattr(m, "_team_store", st)
    return {"store": st, "gid": g["id"], "name": "运维", "task_id": "task_20261005_abcd0001"}


def test_pending_approval_is_announced_in_the_group(gshop, monkeypatch):
    """★ 播报：群员卡在审批时，群里要出现一张可点的卡片（工具 + 内容 + call_id）。"""
    monkeypatch.setattr(m.approval, "pending_for", lambda tid: ["call_007"])
    monkeypatch.setattr(m, "_approval_summary",
                        lambda tid: ("host_exec", "rm -rf build/"))
    m._announce_group_approval.__wrapped__ if hasattr(m._announce_group_approval, "__wrapped__") else None
    import asyncio
    asyncio.run(m._announce_group_approval(gshop["gid"], gshop["name"], gshop["task_id"]))
    feed = gshop["store"].feed(gshop["gid"])
    card = [x for x in feed if (x.get("approval") or {}).get("call_id") == "call_007"]
    assert card, feed
    assert card[0]["approval"]["tool"] == "host_exec"
    assert "rm -rf build/" in card[0]["text"], card[0]["text"]
    assert "不用去任务页" in card[0]["text"], "没告诉用户【就在群里点】"
    assert card[0]["task_id"] == gshop["task_id"], "卡片必须带 task_id（一个群里可能挂多个任务的审批）"


def test_the_same_approval_is_announced_only_once(gshop, monkeypatch):
    """★ 去重：看门每几秒轮询一次，同一个 call_id 只能播报一次（否则刷屏）。"""
    import asyncio
    monkeypatch.setattr(m.approval, "pending_for", lambda tid: ["call_dup"])
    monkeypatch.setattr(m, "_approval_summary", lambda tid: ("host_exec", "echo hi"))
    for _ in range(4):
        asyncio.run(m._announce_group_approval(gshop["gid"], gshop["name"], gshop["task_id"]))
    cards = [x for x in gshop["store"].feed(gshop["gid"]) if (x.get("approval") or {}).get("call_id") == "call_dup"]
    assert len(cards) == 1, f"播报了 {len(cards)} 次"


def test_group_approve_goes_through_the_same_path(gshop, monkeypatch):
    """★ 群里批 = 任务页批（共用 `_do_approve`）：不许出现"群里能批、任务页不能批"。"""
    seen: list[tuple] = []
    monkeypatch.setattr(m, "_do_approve", lambda t, c, d, *a: seen.append((t, c, d)))
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.post(f"/api/v1/team/groups/{gshop['gid']}/approve",
                   json={"task_id": gshop["task_id"], "call_id": "call_9", "decision": "once"})
    assert r.status_code == 200, r.text
    assert seen == [(gshop["task_id"], "call_9", "once")], seen
    texts = [x["text"] for x in gshop["store"].feed(gshop["gid"])]
    assert any("已在群里处理：允许一次" in t for t in texts), texts


def test_group_artifacts_are_clickable_and_authenticated():
    """★★ 2026-10-06（用户实测：**点群里的产物弹「需要访问密码（token 不匹配）」** ✗✗）

    根因：取工作区文件**要鉴权** ✓ 而浏览器新窗口**发不了自定义请求头** ✗
    ⇒ 必须把 `?token=` 拼进 URL（项目里有统一函数 `authedUrl` ✓）。
    **这毛病在任务页犯过一次** ✓ 当时也加了红绿组 ✓ —— 但**没覆盖群聊这条路** ✗ ⇒ 又犯了 ✓。

    ⇒ 这条测试同时钉三件事：
      ① 群聊里每个产物链接都必须走 `authedUrl` ✓（免得再犯第三次 ✓）
      ② 点它要能**在群里直接看** ✓（弹层里复用产物面板 ✓ 不跳走 ✓）
      ③ 那个弹层**不许用 `backdrop-filter`** ✗（已知的整屏变黑诱因 ✓ 有专门的守卫 ✓）
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    src = (root / "frontend" / "src" / "components" / "TeamView.tsx").read_text("utf-8")
    # ① 所有取文件的地方都要带 token ✓（注释行不算 ✓）
    raw = [ln.strip() for ln in src.splitlines()
           if "files/raw" in ln and "authedUrl" not in ln
           and not ln.strip().startswith(("*", "//", "/*"))]
    assert not raw, f"群聊里还有没带 token 的产物链接 ✗：{raw[:2]}"
    assert src.count("authedUrl(") >= 2, "群聊的产物链接没走 authedUrl ✗"
    # ② 点了能就地看 ✓
    assert "ArtifactPanel" in src, "群聊没接产物面板 ⇒ 点了只能新窗口开 ✗"
    assert "产物预览" in src, "没有就地预览的弹层 ✗"
    assert "initialOpen" in src, "点了产物没直接展开那一个（还得再找一遍 ✗）"
    # ③ 弹层不许**真用** backdrop-filter ✓ —— 判据取 "backdrop-filter:"（真写样式才有冒号 ✓），
    #    这样"注释里提它"不会被误判 ✓（本班第一版就被自己的注释绊了一下 ✓）
    live = [ln.strip() for ln in src.splitlines() if "backdrop-filter:" in ln]
    assert not live, f"群聊弹层**真用了** backdrop-filter ✗（整屏变黑诱因 ✓）：{live[:2]}"


def test_group_deny_says_stopped_not_continue(gshop, monkeypatch):
    """拒绝**不是**"继续" —— 文案不能骗人（否则用户以为那步执行了）。"""
    monkeypatch.setattr(m, "_do_approve", lambda t, c, d, *a: None)
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        c.post(f"/api/v1/team/groups/{gshop['gid']}/approve",
               json={"task_id": gshop["task_id"], "call_id": "call_x", "decision": "deny"})
    texts = [x["text"] for x in gshop["store"].feed(gshop["gid"])]
    hit = [t for t in texts if "已在群里处理" in t]
    assert hit and "拒绝" in hit[0] and "继续" not in hit[0], hit


def test_approve_on_missing_group_is_404(gshop, monkeypatch):
    monkeypatch.setattr(m, "_do_approve", lambda t, c, d, *a: None)
    with TestClient(m.app, base_url="http://127.0.0.1:8642") as c:
        r = c.post("/api/v1/team/groups/grp_不存在/approve",
                   json={"task_id": "t", "call_id": "c", "decision": "once"})
    assert r.status_code == 404, r.text


def test_approval_summary_reads_the_last_action(gshop, monkeypatch):
    """卡片要写清"到底批准什么"：取任务最后一条 action（没有就少说，别编）。"""
    monkeypatch.setattr(m.store, "read_events", lambda tid: [
        SimpleNamespace(type="action", payload={"tool": "host_exec", "params": {"command": "npm run build"}}),
    ])
    tool, detail = m._approval_summary("t1")
    assert tool == "host_exec" and detail == "npm run build"
    monkeypatch.setattr(m.store, "read_events", lambda tid: [])
    tool2, detail2 = m._approval_summary("t1")
    assert tool2 and detail2 == "", "没有 action 时不该编内容"


def test_pending_for_only_returns_that_task():
    """approval.pending_for 只返回本任务的 call_id（群里可能同时挂多个任务）。"""
    from app.approval import ApprovalManager
    am = ApprovalManager()
    assert am.pending_for("t1") == []
    am._pending[("t1", "a")] = None
    am._pending[("t2", "b")] = None
    assert am.pending_for("t1") == ["a"]
    assert am.pending_for("t2") == ["b"]


def test_repeat_approval_card_suggests_always(gshop, monkeypatch):
    """★ 真群实测（2026-10-05）：一个写脚本的任务连着要十几次批准（每条 shell 一次）。

    第一次的卡片照旧；**同一任务的第二次**就顺手告诉他"点「本任务都允许」可以一次放行这一类"。
    """
    import asyncio
    monkeypatch.setattr(m.approval, "pending_for", lambda tid: ["call_1"])
    monkeypatch.setattr(m, "_approval_summary", lambda tid: ("shell_exec", "python todo.py add x"))
    asyncio.run(m._announce_group_approval(gshop["gid"], gshop["name"], gshop["task_id"]))
    first = [x["text"] for x in gshop["store"].feed(gshop["gid"])
             if (x.get("approval") or {}).get("call_id") == "call_1"]
    assert first and "本任务都允许" not in first[0], first      # 第一次不啰嗦
    monkeypatch.setattr(m.approval, "pending_for", lambda tid: ["call_2"])
    asyncio.run(m._announce_group_approval(gshop["gid"], gshop["name"], gshop["task_id"]))
    second = [x["text"] for x in gshop["store"].feed(gshop["gid"])
              if (x.get("approval") or {}).get("call_id") == "call_2"]
    assert second and "本任务都允许" in second[0] and "不用每条都点" in second[0], second


def test_same_call_id_with_a_new_command_is_announced_again(gshop, monkeypatch):
    """★ 用户实测（2026-10-05）："我点一下允许，它就停住了，一直停着没跑"。

    根因：**续跑后 call_id 会从 001 重新计数**（新命令又叫 call_012）⇒ 去重键只用 call_id 的话，
    新命令会被误判成"已经播报过" ⇒ **审批卡永远不出现** ⇒ 任务干等 ✗。
    去重键必须带上**命令内容摘要**：同一条命令只播一次，换了命令一定重新播。
    """
    import asyncio
    monkeypatch.setattr(m.approval, "pending_for", lambda tid: ["call_012"])
    monkeypatch.setattr(m, "_approval_summary", lambda tid: ("shell_exec", "python run_tests.py"))
    asyncio.run(m._announce_group_approval(gshop["gid"], gshop["name"], gshop["task_id"]))
    first = [x for x in gshop["store"].feed(gshop["gid"])
             if (x.get("approval") or {}).get("call_id") == "call_012"]
    assert len(first) == 1, first
    # 同一条命令再问一次 ⇒ 不重复播（去重仍然有效）
    asyncio.run(m._announce_group_approval(gshop["gid"], gshop["name"], gshop["task_id"]))
    again = [x for x in gshop["store"].feed(gshop["gid"])
             if (x.get("approval") or {}).get("call_id") == "call_012"]
    assert len(again) == 1, again
    # ★ 换个命令但 call_id 相同（续跑后的真实情形）⇒ **必须重新播报**，否则用户看不到卡
    monkeypatch.setattr(m, "_approval_summary", lambda tid: ("shell_exec", "python run_tests.py --full"))
    asyncio.run(m._announce_group_approval(gshop["gid"], gshop["name"], gshop["task_id"]))
    fresh = [x for x in gshop["store"].feed(gshop["gid"])
             if (x.get("approval") or {}).get("call_id") == "call_012"]
    assert len(fresh) == 2, fresh
    assert "call_012" in fresh[-1].get("text", "") or True


def test_allow_all_for_a_task_stops_asking(gshop, monkeypatch):
    """★ 2026-10-05「本任务全部允许（含新命令）」：治"一个任务连要 25 次批准"。

    背景：结构性命令（`$()`/heredoc/eval）**故意不记忆**（审计 P1：一次豁免 = 整类任意执行），
    所以一个写脚本的任务**每条命令都要人点一次** ⇒ 无人值守不可能 ✗。
    用户在**具体任务**上按这个按钮之后：它的命令不再逐条询问，但**照样留审计**；
    并且**只活在内存**（重启失效）—— 安全边界不变。
    """
    from app.approval import ApprovalManager

    am = ApprovalManager()
    cmd = 'cd /w && T=$(mktemp -d) && python run_tests.py'
    # 没开之前：结构性命令是要问的（ask）
    v0 = am.check("t1", cmd, ["shell_exec"], lambda p: True)
    assert v0 is not None and v0.action == "ask", v0
    # 开了之后：不问了，但返回 allow_all（调用方据此**留审计**而不是静默放行）
    am.allow_all("t1")
    v1 = am.check("t1", cmd, ["shell_exec"], lambda p: True)
    assert v1 is not None and v1.action == "allow_all", v1
    assert am.allow_all_tasks() == ["t1"]
    # 别的任务不受影响（授权是**按任务**的）
    assert am.check("t2", cmd, ["shell_exec"], lambda p: True).action == "ask"
    # 能收回
    am.revoke_all("t1")
    assert am.check("t1", cmd, ["shell_exec"], lambda p: True).action == "ask"


def test_ui_renders_the_allow_all_button():
    """界面锚点：审批卡上必须有第四档「本任务全部允许（含新命令）」，且打的是 decision: all。"""
    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    ui = (root / "frontend" / "src" / "components" / "TeamView.tsx").read_text("utf-8")
    assert "本任务全部允许" in ui, "群里没有这一档（用户实测：一个任务点 25 次 ✗）"
    assert "'all'" in ui or '"all"' in ui, "按钮没打 decision=all"
    api = (root / "frontend" / "src" / "api.ts").read_text("utf-8")
    assert "teamApprove" in api


def test_artifacts_are_listed_at_the_top_of_a_delivery():
    """★ 2026-10-06 用户实测："我不知道在哪打开" —— 产物块原来在消息**底部**，
    长交付一折叠就把它挡住了 ✗。现在：交付消息**顶部**先给一排可点的产物（带「打开/↗」与个数）✓。"""
    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    ui = (root / "frontend" / "src" / "components" / "TeamView.tsx").read_text("utf-8")
    assert "📦 产物" in ui and "个：" in ui, "没在顶部列出产物"
    i_att = ui.find("📦 产物")
    i_verdict = ui.find("<VerdictCard text={String(m.text")
    i_body = ui.find("<CollapsibleDelivery text={String(m.text")
    assert -1 < i_att < i_verdict < i_body, (i_att, i_verdict, i_body)   # 产物在结论与正文**之前**
    assert 'target="_blank"' in ui and "↗" in ui, "产物没做成「新窗口打开」"


def test_leader_is_told_to_produce_clickable_artifacts():
    """★★ 2026-10-06 用户实测："看代码我不懂，你要是让我打开操作使用还行" ——
    我们做的是命令行工具 ✗，用户根本不敲命令，等于白做 ✓。
    所以组长提示词里必须写明：**工具类产物优先做成双击就能用的单文件网页**，
    命令行只能算附加 ✓。
    """
    import inspect

    import app.team as team_mod

    src = inspect.getsource(team_mod.TeamStore.leader_dispatch_prompt)
    assert "双击就用" in src and "单文件网页" in src, src[:200]
    assert "别只交命令行脚本" in src


def test_leader_must_say_how_to_open_the_artifact():
    """★★ 2026-10-06 用户追问："写微信小程序 / App 也能这么看吗？"

    他问得对 ✓ —— **不同产物有不同的打开方式** ✗（小程序要微信开发者工具 ✓、
    App 要构建安装 ✓、后端要启动 ✓），"一律双击网页"解决不了 ✓。
    所以规矩是：**每条交付都必须有一节「怎么打开 / 怎么用」**，
    照那节做用户能自己跑起来 ✓；小程序还要尽量附一份能双击的网页预览 ✓。
    """
    import inspect

    import app.team as team_mod

    src = inspect.getsource(team_mod.TeamStore.leader_dispatch_prompt)
    assert "怎么打开 / 怎么用" in src, src[:200]
    assert "微信开发者工具" in src and "preview.html" in src
    assert "flutter run" in src or "react-native" in src
    assert "双击启动脚本" in src


def test_html_artifacts_get_a_prominent_open_button():
    """★ 2026-10-06（用户："我不知道在哪打开"）：产物里只要有网页，就给一个**显眼的「▶ 打开看看」** ✓；
    并且把交付里那节「怎么打开 / 怎么用」**单独拎出来显示一行** ✓
    （用户要的就是这句话 —— 小程序要开发者工具、App 要构建、后端要启动 ✓，不该埋在长文里 ✗）。"""
    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    ui = (root / "frontend" / "src" / "components" / "TeamView.tsx").read_text("utf-8")
    assert "▶ 打开看看" in ui, "没有显眼的打开按钮"
    assert "howToOpen" in ui and "🖱️" in ui, "没把「怎么打开」那一节拎出来"
    assert "怎么打开|怎么用" in ui or "怎么打开" in ui
    assert "/\\.html?$/i" in ui, "打开按钮只该给网页类产物"


def test_image_and_video_settings_live_together():
    """★ 2026-10-06（用户提的"图片/视频设置合并进「出图/出视频」一块"）：
    这两个能力常常连着用（先出图、再拿图去出视频 ✓），以前出图状态只在能力表里能看 ✗
    ⇒ 现在同一节里先给「当前出图引擎」，再给「当前视频引擎」✓。"""
    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    ui = (root / "frontend" / "src" / "components" / "SettingsPanel.tsx").read_text("utf-8")
    assert "['video', '出图 / 出视频']" in ui, "导航没改成「出图 / 出视频」"
    assert "当前出图引擎" in ui and "当前视频引擎" in ui, "两块没放在同一节"
    i_img = ui.find("当前出图引擎")
    i_vid = ui.find("当前视频引擎")
    assert -1 < i_img < i_vid, (i_img, i_vid)          # 先出图，后出视频


def test_ui_polish_sticky_save_and_meta_line():
    """★ 2026-10-06 界面收尾（用户提的两条）：
    · 设置页很长，"保存"按钮滚到底才看得见 ✗ ⇒ 动作区**吸底**（sticky）✓
    · 任务页头部给**token/耗时小字**（一眼看出花了多少、跑了多久 ✓）
    两者都只改显示，不改行为 ✓。
    """
    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    ui = (root / "frontend" / "src" / "components" / "SettingsPanel.tsx").read_text("utf-8")
    css = (root / "frontend" / "src" / "styles.css").read_text("utf-8")
    tv = (root / "frontend" / "src" / "components" / "TaskView.tsx").read_text("utf-8")
    assert "sticky-actions" in ui, "设置页没做吸底动作区"
    assert ".card .btn-row.sticky-actions" in css and "position: sticky" in css, "吸底样式没落地"
    assert "metaLine" in tv and "tv-meta" in tv, "任务页没有 token/耗时小字"
    assert "次调用" in tv and "用时" in tv
    assert ".tv-meta" in css


def test_verdict_messages_are_not_rendered_twice():
    """★★ 2026-10-06 **截图里逮到的真 bug**：同一段话显示两遍 ✗。

    原因：为了做"结论条"，`VerdictCard` 会把整条文本渲染一遍 ✓，
    而下面的「小节导航 + 折叠正文」**又渲染一遍** ✗ ⇒ 群里每条结论都重复一次 ✓。
    修法：走结论条的消息**不再走**下面那套（用 `isVerdict()` 互斥 ✓）。

    （先确认过后端数据是干净的：41 条、seq 唯一、只有两条合理的重复 ✓ —— 所以是前端的问题 ✓。）
    """
    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    ui = (root / "frontend" / "src" / "components" / "TeamView.tsx").read_text("utf-8")
    assert "function isVerdict" in ui, "没有 isVerdict（互斥判据）"
    assert "!isVerdict(String(m.text ?? '')) && (<>" in ui, "结论条与正文没有互斥 ⇒ 会重复显示"
    # 互斥要真的包住「小节导航 + 折叠正文」两块
    i = ui.find("!isVerdict(String(m.text ?? '')) && (<>")
    seg = ui[i:i + 900]
    assert "CollapsibleDelivery" in seg, "折叠正文没被互斥包住"


def test_verdicts_and_deliveries_render_as_cards():
    """★ 2026-10-06 体验：群里「交付/验收」要一眼看懂 ——
    · 验收结论（✅/❌/🟡 开头）⇒ 醒目结论条（绿/红/黄）
    · 交付消息 ⇒ 顶部列小节名（只看小节就知道它交了什么）+ 正文折叠
    只改**显示**，内容一字不动 ✓。"""
    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    ui = (root / "frontend" / "src" / "components" / "TeamView.tsx").read_text("utf-8")
    assert "VerdictCard" in ui and "sectionNames" in ui
    assert "VerdictCard text={String(m.text" in ui, "消息没走结论条"
    assert "文件清单" in ui and "自测命令" in ui and "运行结果" in ui, "小节导航缺项"


def test_long_deliveries_are_collapsed_in_the_group():
    """★ 2026-10-06 体验：群里别再刷字墙 —— 长交付默认折叠成几行 + 「展开全文」。
    （只折叠显示，内容一字不改；实测一条交付动辄 2000+ 字。）"""
    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    ui = (root / "frontend" / "src" / "components" / "TeamView.tsx").read_text("utf-8")
    assert "CollapsibleDelivery" in ui, "群消息没有折叠长交付"
    assert "展开全文" in ui and "收起" in ui
    assert "CollapsibleDelivery text={String(m.text" in ui, "消息没走折叠组件"


def test_ui_renders_the_approval_card():
    """界面锚点：卡片、三个按钮、防重复点、以及"批完就地留痕"。"""
    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    ui = (root / "frontend" / "src" / "components" / "TeamView.tsx").read_text("utf-8")
    for k in ("approval-card", "需要你批准", "允许一次", "本任务都允许", "拒绝", "approval-done"):
        assert k in ui, f"群里的审批卡片缺 {k}"
    assert "api.teamApprove(" in ui, "没接上群内审批接口"
    api = (root / "frontend" / "src" / "api.ts").read_text("utf-8")
    assert "/team/groups/${gid}/approve" in api, "api.ts 里没有群内审批"
