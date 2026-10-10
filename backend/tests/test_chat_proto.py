"""Phase 3 ⑦ 对话界面**原型**（`?proto=chat`）的锚点。

原型是"给你看过再拍板"的东西，所以它必须满足三条纪律，缺一条就会变成技术债：
  ① **可完全回退**：只由一个查询参数控制；删掉 ChatProto.tsx + App.tsx 那几行即可
  ② **不碰业务**：自身不调任何接口；且原型模式下 App 连任务列表都不拉
     （这样评审时不必先起后端，也不会"原型偷偷依赖接口"）
  ③ **能迁移**：假数据用**真实事件形状**（types.ts 里的 MessagePayload/ActionPayload/
     ObservationPayload/PlanStep），将来换成真事件流不用重写视图

外加一条：样式统一 `cp-` 前缀，不和现有类名打架（否则真界面会被原型样式污染）。
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "frontend" / "src"
PROTO = (SRC / "components" / "ChatProto.tsx").read_text("utf-8")
APP = (SRC / "App.tsx").read_text("utf-8")
CSS = (SRC / "styles.css").read_text("utf-8")
VERIFY = (ROOT / "frontend" / "scripts" / "verify_chat_proto.mjs").read_text("utf-8")


def test_proto_is_gated_by_a_query_param():
    """① 可回退：一个查询参数说了算。"""
    assert "proto" in APP and "'chat'" in APP, "App 里没有 ?proto=chat 的开关"
    assert "ChatProto" in APP, "开关没有接到原型组件上"
    assert "window.location.search" in APP, "不是从地址栏读开关（那样没法直接打开）"


def test_proto_sends_no_business_requests():
    """② 不碰业务：组件自身零接口调用 + 原型模式下 App 不拉数据。"""
    assert "api." not in PROTO, "原型组件调了接口 —— 它应该只吃本地假数据"
    assert "fetch(" not in PROTO, "原型组件发了 fetch"
    assert APP.count("if (proto) return") >= 2, \
        "原型模式下 App 仍在拉业务数据（任务列表/鉴权）—— 评审时就得先起后端"


def test_proto_reuses_real_event_shapes():
    """③ 能迁移：假数据用真实事件形状，将来换真数据不重写视图。"""
    for t in ("MessagePayload", "ActionPayload", "ObservationPayload", "PlanStep"):
        assert t in PROTO, f"原型没用真实类型 {t}（那它就没法直接迁移到真界面）"


def test_proto_css_is_namespaced():
    """样式必须 cp- 前缀 —— 不然会和真界面类名互相污染。"""
    assert ".cp-wrap" in CSS and ".cp-tool" in CSS and ".cp-composer" in CSS, "原型样式没进 styles.css"
    # 反向：原型用到的类都应带 cp- 前缀（抽几个关键结构检查）
    for cls in ("cp-turn", "cp-bubble-user", "cp-assistant", "cp-tool-head", "cp-steps"):
        assert cls in PROTO and f".{cls}" in CSS, f"{cls} 没有配套样式（界面会散架）"


def test_verify_script_checks_geometry_not_just_classnames():
    """验证脚本要看**几何**（贴边对齐）而不是只看类名 —— 否则样式散架也照样"通过"。"""
    assert "getBoundingClientRect" in VERIFY, "没做几何验证"
    assert "proto=chat" in VERIFY, "没验证开关本身"
    assert "不调任何业务接口" in VERIFY, "没验证「原型不偷偷依赖后端」"


# ═══ 迁移到真界面（Phase 3 ⑦ 第二刀）：工具调用卡片 + 助手署名 ═══

EVENT_ITEM = (SRC / "components" / "EventItem.tsx").read_text("utf-8")
TASKVIEW = (SRC / "components" / "TaskView.tsx").read_text("utf-8")
CARDS_VERIFY = (ROOT / "frontend" / "scripts" / "verify_chat_cards.mjs").read_text("utf-8")


def test_tool_calls_render_as_cards_with_icons():
    assert "TOOL_ICON" in EVENT_ITEM, "工具图标表没了（卡片会退回成裸行）"
    assert 'className="tool-icon"' in EVENT_ITEM, "动作行没渲染图标"
    assert "toolIcon(p.tool)" in EVENT_ITEM, "图标没按工具名取"


def test_card_look_is_joined_action_plus_observation():
    """动作无下边框、结果无上边框、结果带左侧色条 —— 三件套缺一就不像一张卡。"""
    assert ".ev-action {" in CSS and "border-bottom: none" in CSS, "动作没做成卡头"
    assert ".ev-obs" in CSS and "border-top: none" in CSS, "结果没接成卡身"
    assert ".ev-obs.obs-ok" in CSS and ".ev-obs.obs-fail" in CSS, "成功/失败没有色条区分"


def test_memo_structure_is_preserved():
    """★ 性能红线：本迁移**只动呈现**，不许破坏 EventItem 的 memo
    （622 次工具调用的任务实测卡顿主因就是重渲染）。"""
    assert "export const EventItem = memo(EventItemImpl)" in EVENT_ITEM, "memo 被去掉了 —— 长任务会卡"
    assert "call_id" in EVENT_ITEM, "动作/结果还是各自渲染（没被配对）—— 配对照样会破坏 memo"


def test_assistant_signature_is_consistent():
    """助手消息与流式态都该有「Agent」（用户要"知道是谁在说话"）。"""
    assert "msg-who" in EVENT_ITEM, "完成的助手消息没有署名"
    assert "msg-who" in TASKVIEW and "msg-live" in TASKVIEW, "流式态没有署名/生成中标记"


def test_real_view_verification_is_not_fake_data():
    """迁移后的验证必须打**真任务真事件**（原型那套假数据证明不了真界面）。"""
    assert "/api/v1/tasks" in CARDS_VERIFY, "没用真任务"
    assert "ev-obs" in CARDS_VERIFY, "没验证结果卡片"
    assert "getComputedStyle" in CARDS_VERIFY, "没做样式层面的验证（只看类名会假绿）"



# ═══ 折叠小结（「这一趟做了什么」）—— 2026-10-04 改版：用户否掉了切段，要的是这个 ═══
# 用户原话："上面照旧问一个回一个；底下有个折叠，相当于给你一个总结，不用再往上翻"

DIGEST = (SRC / "components" / "TaskDigest.tsx").read_text("utf-8")
DIGEST_VERIFY = (ROOT / "frontend" / "scripts" / "verify_task_digest.mjs").read_text("utf-8")


def test_digest_block_exists_and_is_event_derived():
    assert "export function TaskDigest" in DIGEST, "小结组件没了"
    for sec in ("你提的要求", "过程", "产出与来源", "它最后说的"):
        assert sec in DIGEST, f"小结缺少「{sec}」这一段"
    assert "api." not in DIGEST and "fetch(" not in DIGEST, \
        "小结不该调接口 —— 它只从本地事件推导，瞬时且不花 token"


def test_digest_is_mounted_at_the_bottom_of_the_stream():
    assert "<TaskDigest" in TASKVIEW, "小结没挂到真界面上"
    assert TASKVIEW.index("<TaskDigest") > TASKVIEW.index("streamingText &&"), \
        "小结没钉在对话流最底下（那样就还得往上翻）"


def test_splitting_was_reverted():
    assert "splitParagraphs" not in EVENT_ITEM and "msg-stack" not in EVENT_ITEM, \
        "助手消息又被切段了（用户明确说不对：要一问一答）"
    assert "splitStreaming" not in TASKVIEW, "流式草稿纸又被切段了"
    assert not (SRC / "lib" / "paragraphs.ts").exists(), "切段实现该删掉"


def test_digest_verification_covers_the_users_points():
    assert "默认收起" in DIGEST_VERIFY, "没验证默认收起"
    assert "钉在最底下" in DIGEST_VERIFY, "没验证位置（这条是这个功能的立身之本）"
    assert "你提的要求" in DIGEST_VERIFY, "没验证小结里能看到之前说过啥"
    assert "一问一答" in DIGEST_VERIFY, "没验证上面对话流没被切碎"
# ═══ 应用层去重（用户："上面那条重复的回复"）—— 2026-10-04 ═══
# 实测同一句 61 字在那个任务里出现 4 次：#7 工具输出 → #12 模型原样贴回 → #16 task_done 参数
# → #18 交付语。治的是**应用层那两份重复**（卡片抄全文 + 最终回复再发一遍），
# 不碰模型行为（用户让它"原样贴回来"时它照贴，只是重复的那层界面自动收起来）。

DEDUPE = (SRC / "lib" / "dedupe.ts").read_text("utf-8")
DEDUPE_VERIFY = (ROOT / "frontend" / "scripts" / "verify_dedupe.mjs").read_text("utf-8")


def test_dedupe_rule_is_conservative():
    assert "export function isDuplicateOfEarlier" in DEDUPE, "去重函数没了"
    assert "DUP_MIN_LEN = 40" in DEDUPE, "短文本不该参与判定（短句撞车太正常）"
    assert "DUP_RATIO = 0.85" in DEDUPE, "高覆盖阈值被改松了？"
    assert "DUP_RATIO_LOW = 0.6" in DEDUPE and "DUP_UNMATCHED_MAX = 30" in DEDUPE, \
        "双阈值（低覆盖+新增极少也算重复）没了"
    # 算法必须是"最长游程"：滑窗会误收（接缝被盖过去）、不重叠分块会漏判（对偏移过敏）
    assert "matchedChars" in DEDUPE and "SEED = 16" in DEDUPE, "算法不是「最长游程」了"


def test_dedupe_is_wired_without_breaking_memo():
    assert "isDuplicateOfEarlier" in TASKVIEW, "没接去重"
    assert "duplicate={dupIds.has(e.id)}" in TASKVIEW, "算出来的重复标记没传给事件条目"
    assert "useMemo" in TASKVIEW, "重复判定没 memo（长任务会反复算）"
    # 传进去的必须是"每个事件稳定的布尔"，不能是每次都变的 Set —— 否则 memo 失效
    assert "duplicate?: boolean" in EVENT_ITEM, "EventItem 的重复参数类型不对（会破坏 memo）"


def test_duplicate_reply_collapses_but_keeps_full_text():
    assert "dup-row" in EVENT_ITEM, "重复回复没有收起的行"
    assert "同上一步结果" in EVENT_ITEM, "收起后没说清是什么"
    assert "setShowDup(true)" in EVENT_ITEM, "点不开就丢数据了（必须能展开看全文）"
    assert ".dup-row" in CSS, "收起行没有样式"


def test_task_done_card_is_preview_only():
    """task_done 的 message 就是最终交付语，紧接着会被发成正式回复 ⇒ 卡片别再抄全文。"""
    assert "isDone" in EVENT_ITEM and "'交付：'" in EVENT_ITEM, "task_done 卡片还在抄全文"
    assert "isDone ? 20 : 120" in EVENT_ITEM, "预览长度没区分（应当更短）"


def test_digest_last_section_is_a_pointer_not_a_copy():
    """用户点出"总账和上面重复" ⇒ 那一节改成一行指引 + 点一下跳过去。"""
    assert "digest-jump" in DIGEST, "总账还在抄最后一条回复的全文"
    assert "点这里跳过去" in DIGEST, "没有跳转指引"
    assert "digest-last" not in DIGEST, "旧的全文块没删掉"


def test_dedupe_verification_covers_both_directions():
    assert "正常长回复照常完整显示" in DEDUPE_VERIFY, "只验了该收的收了，没验不该收的没收"
    assert "点开能看到全文" in DEDUPE_VERIFY, "没验不丢数据"
# ═══ 去重第二刀：拼接型重复（用户 2026-10-05 圈出来的那类）═══
# 用户截图里那条回复 = 上面两条回复的拼接。对**任何单独一条**覆盖率都只有 ~50% ⇒ 逐条比全漏。
# 修法：把"之前说过的所有内容（含助手自己的回复）"拼成一份语料再比覆盖率。

UNIT = (ROOT / "frontend" / "scripts" / "test_dedupe.mjs").read_text("utf-8")


def test_corpus_includes_assistant_history_and_is_joined():
    assert "buildCorpus" in DEDUPE, "没有语料拼接（逐条比会漏掉拼接型重复）"
    assert "seen.push(text)" in TASKVIEW, "助手自己的历史回复没进语料（拼接型的原料就是它们）"
    assert "isDuplicateOfEarlier(text, [buildCorpus(seen)])" in TASKVIEW, \
        "还是逐条比 —— 拼接型重复会漏判"
    assert "limit = 20000" in DEDUPE, "语料没有上限（超长任务会拖慢）"


def test_unit_test_pins_both_directions_of_the_rule():
    """规则必须有**单元测试**：手头任务里未必有那种重复的样本，靠浏览器测不全。"""
    assert "esbuild" in UNIT, "单元测试没真跑 TS（应当用 esbuild 编译后 import）"
    assert "拼成语料后必须判为重复" in UNIT, "没钉住拼接型（本次修复的核心）"
    assert "真正的新内容不许判为重复" in UNIT, "没钉住反向（不许误收）"
    assert "覆盖率只有一半" in UNIT, "没把当初为什么漏判记进测试"
    assert "短文本不参与判定" in UNIT, "没钉住短文本豁免"