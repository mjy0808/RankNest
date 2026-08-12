from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage


def send_report(subject: str, html_body: str, text_body: str) -> tuple[bool, str]:
    recipients = [value.strip() for value in os.getenv("EMAIL_TO", "").split(",") if value.strip()]
    if not recipients:
        return False, "EMAIL_TO 未配置，已跳过邮件发送"

    host = os.getenv("SMTP_HOST", "")
    username = os.getenv("SMTP_USERNAME", "")
    password = os.getenv("SMTP_PASSWORD", "")
    sender = os.getenv("EMAIL_FROM", username)
    if not host or not sender:
        return False, "SMTP_HOST 或 EMAIL_FROM 未配置，已跳过邮件发送"

    port = int(os.getenv("SMTP_PORT") or "465")
    security = (os.getenv("SMTP_SECURITY") or "ssl").lower()
    if security not in {"ssl", "starttls", "none"}:
        return False, "SMTP_SECURITY 必须是 ssl、starttls 或 none"
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    if security == "ssl":
        smtp: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        smtp = smtplib.SMTP(host, port, timeout=30)
    try:
        if security == "starttls":
            smtp.starttls()
        if username:
            smtp.login(username, password)
        smtp.send_message(message)
    finally:
        smtp.quit()
    return True, f"邮件已发送至 {len(recipients)} 个收件地址"
