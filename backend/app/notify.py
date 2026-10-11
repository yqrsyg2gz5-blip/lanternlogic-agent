"""外发通知器 —— Slack / 邮件（第 41 班：第三方集成第一步）。

设计：任务交付（done/partial）时，若配置了对应渠道则推送一条摘要；
Agent 侧另有 notify 工具可在任务执行中主动发消息。

配置（config.json 顶层 notify 段）：
  "notify": {
    "slack_webhook": "https://hooks.slack.com/services/T000/B000/XXXX",   # Slack Incoming Webhook
    "email": {"smtp_host": "smtp.qq.com", "smtp_port": 465, "smtp_user": "you@qq.com",
              "smtp_pass_env": "SMTP_PASS_ENV_NAME", "to": ["someone@example.com"]}
  }
⚠️ 邮箱授权码存环境变量（与 API Key 同规矩，不落盘）。
Slack webhook URL 本身即凭据，也建议放环境变量（支持 env: 前缀引用）。
"""
from __future__ import annotations

import os
import smtplib
from email.header import Header
from email.mime.text import MIMEText
from typing import Any

import httpx


def _resolve(value: str) -> str:
    """支持 env:VARNAME 形式引用环境变量（webhook/授权码不落盘）。"""
    if value.startswith("env:"):
        return os.environ.get(value[4:], "")
    return value


def send_slack(webhook: str, text: str) -> tuple[bool, str]:
    """Slack Incoming Webhook 推送。返回 (ok, 说明)。"""
    url = _resolve(webhook)
    if not url.startswith("https://hooks.slack.com/"):
        return False, "slack_webhook 未配置或格式不对（应形如 https://hooks.slack.com/services/...）"
    try:
        r = httpx.post(url, json={"text": text[:3000]}, timeout=15)
        return (r.status_code == 200 and r.text == "ok"), f"HTTP {r.status_code}"
    except httpx.HTTPError as e:
        return False, f"{type(e).__name__}: {e}"


def send_email(email_cfg: dict[str, Any], subject: str, body: str) -> tuple[bool, str]:
    """SMTP 发送（SSL 465 / STARTTLS 587 自适应）。返回 (ok, 说明)。"""
    host = str(email_cfg.get("smtp_host") or "")
    user = str(email_cfg.get("smtp_user") or "")
    port = int(email_cfg.get("smtp_port") or 465)
    pass_env = str(email_cfg.get("smtp_pass_env") or "")
    password = os.environ.get(pass_env, "") if pass_env else _resolve(str(email_cfg.get("smtp_pass") or ""))
    to_list = list(email_cfg.get("to") or [])
    if not (host and user and password and to_list):
        return False, "邮件配置不完整（需要 smtp_host/smtp_user/smtp_pass_env/to）"
    msg = MIMEText(body[:20000], "plain", "utf-8")
    msg["Subject"] = Header(subject[:200], "utf-8")
    msg["From"] = user
    msg["To"] = ", ".join(to_list)
    try:
        if port == 465:
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=20)
        else:
            smtp = smtplib.SMTP(host, port, timeout=20)
            smtp.starttls()
        with smtp:
            smtp.login(user, password)
            smtp.sendmail(user, to_list, msg.as_string())
        return True, f"已发送至 {len(to_list)} 个收件人"
    except (smtplib.SMTPException, OSError) as e:
        return False, f"{type(e).__name__}: {e}"


def notify_task_done(notify_cfg: dict[str, Any] | None, task_title: str, status: str, summary: str) -> list[str]:
    """任务交付时按配置推送全部已配渠道。返回各渠道结果说明。

    ★ B10（审计台账）：**出网面每个字段都要打码**。此前只对 summary 打了码
      （main.py 的复审 P1），title 原样拼进 Slack 正文与**邮件标题**——
      实测（%TEMP% 假 store + 假渠道，原始输出见汇报）：
          【LanternLogic Agent】任务完成：迁移密钥 sk-title-9f3a2b7c8d1e4f5a
          好的，我看到 [已隐藏-疑似密钥]
      同一条消息里"正文打码、标题泄漏"，正是本项目反复出现的"只修了一个形态"。
      ⇒ 打码收拢到【本函数】这一个咽喉点（redact.py 同源单一实现，幂等，
        调用方已打过码也不会二次破坏），将来新增渠道自动覆盖。
    """
    if not notify_cfg:
        return []
    from .redact import redact_text

    safe_title = redact_text(str(task_title or ""))
    safe_summary = redact_text(str(summary or ""))[:500]
    text = f"【LanternLogic Agent】任务{ '完成' if status == 'done' else f'结束（{status}）' }：{safe_title}\n{safe_summary}"
    results = []
    if notify_cfg.get("slack_webhook"):
        ok, note = send_slack(str(notify_cfg["slack_webhook"]), text)
        results.append(f"Slack: {'✅' if ok else '❌'} {note}")
    ecfg = notify_cfg.get("email")
    if isinstance(ecfg, dict):
        subject = f"LanternLogic Agent 任务{'完成' if status == 'done' else '结束'}：{safe_title}"
        ok, note = send_email(ecfg, subject, text)
        results.append(f"邮件: {'✅' if ok else '❌'} {note}")
    return results
