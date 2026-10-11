"""B10（审计台账 P2）：**出网面**（Slack / 邮件的任务完成通知）每个字段都必须打码。

现状（本批开工实测，%TEMP% 假 store + 假渠道；原始输出见 agent-shell-评审/B10-接口面扫描.log）：
    LEAK 出网通道 slack.text      命中=1
    LEAK 出网通道 email.subject   命中=1
    LEAK 出网通道 email.body      命中=1
    命中明细（原文）：
      【LanternLogic Agent】任务完成：迁移密钥 sk-title-9f3a2b7c8d1e4f5a
      好的，我看到 [已隐藏-疑似密钥]
  ⇒ **同一条消息里"正文打了码、标题是明文"**：main.py 的复审 P1 只给 summary
    加了 `_redact_text`，`task_title` 直接拼进 Slack 正文、**邮件标题**、邮件正文。
    而 task_title = 用户首条消息的前 30 字（用户把 Key 贴在开头就整个进标题，
    重命名端点还能写到 100 字）⇒ 真·出网泄漏。

为什么修在 notify.py 而不是调用点：出网面是本项目自己定的"唯一出网通道"口径
（main.py:381 原注释），打码收拢到 `notify_task_done` 这一个咽喉点 ⇒ 将来新增
渠道（企业微信/钉钉/Webhook…）自动被覆盖，不会再出现"漏了一个字段/一个渠道"。

本文件三组锚点：
  ① 纯函数级：三个出网字段（Slack 正文 / 邮件标题 / 邮件正文）都不得有明文
  ② 反假绿：标题的非密部分必须仍在（不是"整条删掉"式的通过）
  ③ ★ 接线级：真调 main._notify_done（真调用点 + 真 store 摘要路径）——
     只测纯函数证明不了"调用点真的会把原始 title 送进来"
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import main as m
from app import notify as nt
from app.schemas import EventEnvelope

TID = "task_20261004_b10a"
SEC_TITLE = "sk-title-9f3a2b7c8d1e4f5a"                  # \bsk-[A-Za-z0-9_-]{6,}
SEC_MSG = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"    # \bghp_[A-Za-z0-9]{20,}
SECRETS = (SEC_TITLE, SEC_MSG)
TITLE = f"迁移密钥 {SEC_TITLE}"
SUMMARY = f"已经处理好，密钥 {SEC_MSG} 已轮换"

NOTIFY_CFG = {
    "slack_webhook": "https://hooks.slack.com/services/T000/B000/XXXX",
    "email": {"smtp_host": "smtp.qq.com", "smtp_port": 465, "smtp_user": "you@qq.com",
              "smtp_pass_env": "SMTP_PASS_ENV_NAME", "to": ["someone@example.com"]},
}


@pytest.fixture()
def sent(monkeypatch):
    """把两个真渠道换成捕获器——绝不真的发 Slack/邮件。"""
    box: dict[str, str] = {}

    def fake_slack(webhook, text):
        box["slack.text"] = text
        return True, "HTTP 200"

    def fake_email(email_cfg, subject, body):
        box["email.subject"] = subject
        box["email.body"] = body
        return True, "已发送"

    monkeypatch.setattr(nt, "send_slack", fake_slack)
    monkeypatch.setattr(nt, "send_email", fake_email)
    return box


def _assert_no_plaintext(box: dict[str, str], where: str) -> None:
    assert box, f"{where}：一个出网字段都没捕获到——测试没打中目标面"
    leaks = [(k, v) for k, v in box.items() if any(s in v for s in SECRETS)]
    assert not leaks, (
        f"{where}：出网内容出现明文密钥：\n"
        + "\n".join(f"  {k} = {v[:160]}" for k, v in leaks)
    )


# ═══ ① 纯函数级：三个字段全部打码 ═══

def test_notify_task_done_redacts_every_outbound_field(sent):
    nt.notify_task_done(NOTIFY_CFG, TITLE, "done", SUMMARY)
    _assert_no_plaintext(sent, "notify_task_done")
    # 每个字段都真的被发过（防"某渠道没走"导致假绿）
    assert "slack.text" in sent and "email.subject" in sent and "email.body" in sent, sent.keys()
    # 三处都必须是"打码"，不是"把字段删空"
    for k in ("slack.text", "email.subject", "email.body"):
        assert "[已隐藏" in sent[k], f"{k} 里没有打码标记（像是把内容删了）：{sent[k]!r}"


def test_notify_keeps_non_secret_content(sent):
    """反假绿对照：标题/摘要的非密部分必须原样可见——告知能力不能被"打码"吃掉。"""
    nt.notify_task_done(NOTIFY_CFG, TITLE, "done", SUMMARY)
    assert "迁移密钥" in sent["slack.text"], sent["slack.text"]
    assert "迁移密钥" in sent["email.subject"], sent["email.subject"]
    assert "已经处理好" in sent["email.body"], sent["email.body"]


def test_notify_no_config_sends_nothing(sent):
    assert nt.notify_task_done(None, TITLE, "done", SUMMARY) == []
    assert nt.notify_task_done({}, TITLE, "done", SUMMARY) == []
    assert sent == {}


def test_notify_redaction_is_idempotent(sent):
    """幂等：调用点已经打过码的摘要再走一遍本函数，不得被二次破坏成乱码。

    （main.py 的 _notify_done 历史上就打过一次；打码实现换了会导致
      "打码标记本身被打码"这类回归，故钉住。）
    """
    once = nt.redact_text(TITLE) if hasattr(nt, "redact_text") else None
    from app.redact import redact_text
    twice = redact_text(redact_text(TITLE))
    assert twice == redact_text(TITLE), f"redact_text 不幂等：{once!r} / {twice!r}"
    nt.notify_task_done(NOTIFY_CFG, redact_text(TITLE), "done", redact_text(SUMMARY))
    _assert_no_plaintext(sent, "notify_task_done（已打码输入再走一遍）")


# ═══ ③ 接线级：真调用点 main._notify_done ═══

def _assistant_event(text: str) -> EventEnvelope:
    return EventEnvelope(id="evt_000009", seq=9, task_id=TID, type="message", version=1,
                         ts="2026-10-04T00:00:00Z", payload={"role": "assistant", "text": text})


def test_wiring_main_notify_done_redacts_title(monkeypatch, sent):
    """★ 接线锚点：真调 `main._notify_done`——它把**未打码的 task.title** 传进来，
    打码必须发生在 notify.py 这一层（出网咽喉点）。"""
    monkeypatch.setattr(m, "store", SimpleNamespace(read_events=lambda tid: [_assistant_event(SUMMARY)]))
    monkeypatch.setattr(m.cfg, "notify", SimpleNamespace(
        slack_webhook=NOTIFY_CFG["slack_webhook"], email=NOTIFY_CFG["email"],
        model_dump=lambda: NOTIFY_CFG,
    ))
    m._notify_done(TITLE, TID, SimpleNamespace(task=SimpleNamespace(status="done")))
    _assert_no_plaintext(sent, "main._notify_done 接线")
    assert "迁移密钥" in sent["slack.text"], "接线级反假绿：标题非密部分丢了"
    assert "已经处理好" in sent["slack.text"], "接线级反假绿：摘要没进通知"
