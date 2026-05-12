from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

from config import settings


def _send_sync(to: str, subject: str, body: str, bcc: str | None = None) -> str:
    if not settings.smtp_host or not settings.smtp_from:
        raise RuntimeError("SMTP_HOST and SMTP_FROM must be configured before sending email.")

    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    recipients = [addr.strip() for addr in to.split(",") if addr.strip()]

    bcc_value = bcc or settings.smtp_default_bcc
    if bcc_value:
        recipients.extend(addr.strip() for addr in bcc_value.split(",") if addr.strip())

    msg.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        if settings.smtp_use_tls:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(msg, to_addrs=recipients)
    return f"Email sent to {to}"


async def send(to: str, subject: str, body: str, bcc: str | None = None) -> str:
    return await asyncio.to_thread(_send_sync, to, subject, body, bcc)
