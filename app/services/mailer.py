"""Async SMTP mailer (aiosmtplib): HTML templates, inline images, attachments, retries, logging.

Emails never block a request: use ``queue_email`` (background) for user-facing flows.
"""
from __future__ import annotations

import asyncio
import html as html_lib
import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Any

import aiosmtplib

from ..cache import site_cache
from ..config import settings
from ..database import SessionLocal
from ..templating import render_string
from .background import spawn

log = logging.getLogger("classnest.mail")


@dataclass
class Attachment:
    filename: str
    data: bytes
    mime: str = "application/octet-stream"


@dataclass
class InlineImage:
    cid: str
    data: bytes
    subtype: str = "png"


def html_to_text(markup: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", markup)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</h[1-6]>|</tr>|</li>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _as_list(value: str | Sequence[str] | None) -> list[str]:
    if not value:
        return []
    items = [value] if isinstance(value, str) else list(value)
    seen: list[str] = []
    for item in items:
        item = (item or "").strip()
        if item and item.lower() not in {s.lower() for s in seen}:
            seen.append(item)
    return seen


def one_line(value: str) -> str:
    """Header values can't contain line breaks: EmailMessage raises on them (an enquiry from "Name\\r\\nBcc: …"
    made the admin alert fail silently). Collapse all whitespace instead."""
    return " ".join(str(value).split())


def build_message(to: str | Sequence[str], subject: str, html_body: str, *, cc: Sequence[str] | None = None,
                  reply_to: str | None = None, attachments: Iterable[Attachment] = (),
                  inline: Iterable[InlineImage] = ()) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((one_line(settings.SMTP_FROM_NAME or site_cache.data["site_name"]),
                              settings.SMTP_FROM_EMAIL))
    msg["To"] = ", ".join(_as_list(to))
    if cc:
        msg["Cc"] = ", ".join(_as_list(cc))
    if reply_to:
        msg["Reply-To"] = one_line(reply_to)
    msg["Subject"] = one_line(subject)[:250]
    msg["Message-ID"] = make_msgid(domain=settings.SMTP_FROM_EMAIL.split("@")[-1] or "classnest.local")
    msg.set_content(html_to_text(html_body))
    msg.add_alternative(html_body, subtype="html")
    inline = list(inline)
    if inline:
        html_part = msg.get_payload()[1]
        for img in inline:
            html_part.add_related(img.data, maintype="image", subtype=img.subtype, cid=f"<{img.cid}>")
    for att in attachments:
        maintype, _, subtype = att.mime.partition("/")
        msg.add_attachment(att.data, maintype=maintype or "application", subtype=subtype or "octet-stream",
                           filename=att.filename)
    return msg


async def _log(to: str, subject: str, category: str, status: str, error: str | None) -> None:
    from ..models import EmailLog

    try:
        async with SessionLocal() as db:
            db.add(EmailLog(to_email=to[:500], subject=subject[:300], category=category[:40], status=status,
                            error=(error or None) and error[:2000]))
            await db.commit()
    except Exception:  # logging must never break mail flow
        log.exception("Could not write email log")


def _smtp_kwargs() -> dict[str, Any]:
    return {
        "hostname": settings.smtp_host,
        "port": settings.SMTP_PORT,
        "username": settings.SMTP_USERNAME or None,
        "password": settings.SMTP_PASSWORD or None,
        "use_tls": settings.SMTP_SSL,
        "start_tls": False if settings.SMTP_SSL else settings.SMTP_STARTTLS,
        "timeout": settings.SMTP_TIMEOUT_SECONDS,
    }


async def deliver(msg: EmailMessage, category: str = "general") -> bool:
    to = ", ".join(filter(None, [msg.get("To", ""), msg.get("Cc", "")]))
    subject = str(msg.get("Subject", ""))
    if not settings.EMAIL_ENABLED or not settings.smtp_host:
        log.info("[email disabled] %s -> %s", subject, to)
        await _log(to, subject, category, "skipped", "EMAIL_ENABLED is false or SMTP_HOST empty")
        return False
    error = None
    for attempt in range(3):
        try:
            await aiosmtplib.send(msg, **_smtp_kwargs())
            await _log(to, subject, category, "sent", None)
            return True
        except Exception as exc:  # network / auth / server errors
            error = f"{exc.__class__.__name__}: {exc}"
            log.warning("SMTP send failed (attempt %s): %s", attempt + 1, error)
            await asyncio.sleep(1.5 * (attempt + 1))
    await _log(to, subject, category, "failed", error)
    return False


async def deliver_many(messages: list[EmailMessage], category: str = "bulk") -> int:
    """Send many messages over one SMTP connection. Returns the number delivered."""
    if not messages:
        return 0
    if not settings.EMAIL_ENABLED or not settings.smtp_host:
        for msg in messages:
            await _log(str(msg.get("To", "")), str(msg.get("Subject", "")), category, "skipped", "email disabled")
        return 0
    sent = 0
    try:
        async with aiosmtplib.SMTP(**_smtp_kwargs()) as smtp:
            for msg in messages:
                try:
                    await smtp.send_message(msg)
                    sent += 1
                    await _log(str(msg.get("To", "")), str(msg.get("Subject", "")), category, "sent", None)
                except Exception as exc:
                    await _log(str(msg.get("To", "")), str(msg.get("Subject", "")), category, "failed", str(exc))
    except Exception as exc:  # connection failed: fall back to one-by-one (with retries)
        log.warning("Bulk SMTP connection failed (%s); sending individually", exc)
        for msg in messages[sent:]:
            sent += await deliver(msg, category)
    return sent


async def send_email(to: str | Sequence[str], subject: str, template: str, *, category: str = "general",
                     cc: Sequence[str] | None = None, attachments: Iterable[Attachment] = (),
                     inline: Iterable[InlineImage] = (), reply_to: str | None = None, **context: Any) -> bool:
    if not _as_list(to):
        return False
    body = render_string(f"emails/{template}", subject=subject, **context)
    return await deliver(build_message(to, subject, body, cc=cc, attachments=attachments, inline=inline,
                                       reply_to=reply_to), category)


def queue_email(to: str | Sequence[str], subject: str, template: str, **kwargs: Any) -> None:
    """Fire-and-forget email (never blocks or breaks the request)."""
    if _as_list(to):
        spawn(send_email(to, subject, template, **kwargs), name=f"email:{kwargs.get('category', 'general')}")


def queue_bulk(recipients: list[tuple[str, dict[str, Any]]], subject: str, template: str,
               category: str = "bulk") -> None:
    """recipients: [(email, per-recipient context), ...]"""
    messages = []
    for email, ctx in recipients:
        if email:
            body = render_string(f"emails/{template}", subject=subject, **ctx)
            messages.append(build_message(email, subject, body))
    if messages:
        spawn(deliver_many(messages, category), name=f"bulk:{category}")


def queue_admin_alert(subject: str, template: str, **context: Any) -> None:
    queue_email(settings.admin_alert_list, subject, template, **context)
