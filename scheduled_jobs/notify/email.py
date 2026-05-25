# -*- coding: utf-8 -*-
"""定时任务结果邮件通知（环境变量 ALPHA_NOTIFY_*）。"""
from __future__ import annotations

import os
import smtplib
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Iterable

from loguru import logger

DATE_FMT_DB = "%Y-%m-%d"


def _split_recipients(raw: str) -> list[str]:
    return [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]


def notify_configured() -> bool:
    required = (
        "ALPHA_NOTIFY_SMTP_HOST",
        "ALPHA_NOTIFY_USER",
        "ALPHA_NOTIFY_PASS",
        "ALPHA_NOTIFY_TO",
    )
    return all(os.environ.get(k, "").strip() for k in required)


def send_task_email(title: str, content: str) -> bool:
    """发送纯文本通知；未配置时打日志并返回 False。"""
    if not notify_configured():
        logger.warning("未配置 ALPHA_NOTIFY_*，跳过邮件发送")
        return False

    host = os.environ["ALPHA_NOTIFY_SMTP_HOST"].strip()
    port = int(os.environ.get("ALPHA_NOTIFY_SMTP_PORT", "465"))
    user = os.environ["ALPHA_NOTIFY_USER"].strip()
    password = os.environ["ALPHA_NOTIFY_PASS"]
    receivers: Iterable[str] = _split_recipients(os.environ["ALPHA_NOTIFY_TO"])

    msg = MIMEMultipart()
    msg["Subject"] = Header(title, "utf-8")
    msg["From"] = user
    msg["To"] = ";".join(receivers)
    msg.attach(MIMEText(content, "plain", "utf-8"))

    try:
        with smtplib.SMTP_SSL(host, port, timeout=60) as smtp:
            smtp.login(user, password)
            smtp.sendmail(user, list(receivers), msg.as_string())
        logger.info("邮件发送成功：{}", title)
        return True
    except smtplib.SMTPException as e:
        logger.error("邮件发送失败：{}", e)
        return False
