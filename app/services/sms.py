"""SMS delivery for OTPs: console (dev), Twilio, or a generic webhook (any Indian SMS gateway)."""
from __future__ import annotations

import logging
import re

import httpx

from ..config import settings

log = logging.getLogger("classnest.sms")


def normalize_phone(phone: str | None) -> str:
    if not phone:
        return ""
    raw = phone.strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+"):
        return "+" + digits
    if len(digits) == 10:
        return settings.DEFAULT_COUNTRY_CODE + digits
    if len(digits) == 12 and digits.startswith("91"):
        return "+" + digits
    return "+" + digits if digits else ""


def mask_phone(phone: str | None) -> str:
    p = normalize_phone(phone)
    return p[:3] + "•" * max(0, len(p) - 6) + p[-3:] if len(p) > 6 else p


async def send_sms(phone: str | None, message: str) -> bool:
    to = normalize_phone(phone)
    if not to:
        return False
    provider = settings.SMS_PROVIDER.strip().lower()
    try:
        if provider == "twilio" and settings.TWILIO_ACCOUNT_SID:
            url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}/Messages.json"
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, data={"To": to, "From": settings.TWILIO_FROM_NUMBER, "Body": message},
                                         auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN))
            resp.raise_for_status()
            return True
        if provider == "webhook" and settings.SMS_WEBHOOK_URL:
            headers = {"Authorization": f"Bearer {settings.SMS_WEBHOOK_TOKEN}"} if settings.SMS_WEBHOOK_TOKEN else {}
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(settings.SMS_WEBHOOK_URL, json={"to": to, "message": message},
                                         headers=headers)
            resp.raise_for_status()
            return True
    except httpx.HTTPError as exc:
        log.warning("SMS via %s failed: %s", provider, exc)
        return False
    # console provider (development): never log the full number in production
    log.warning("[SMS:%s] to %s -> %s", provider or "console", mask_phone(to) if settings.is_production else to,
                message)
    return True
