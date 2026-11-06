"""Small in-process caches refreshed by a background loop: IP bans and site branding."""
from __future__ import annotations

import datetime as dt
import logging
import re
from typing import Any

from sqlalchemy import select

from .config import settings
from .database import SessionLocal

log = logging.getLogger("classnest.cache")
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


class BanCache:
    def __init__(self) -> None:
        self._bans: dict[str, tuple[str, dt.datetime | None]] = {}

    def lookup(self, ip: str) -> tuple[str, dt.datetime | None] | None:
        ban = self._bans.get(ip)
        if ban and ban[1] is not None and ban[1] < dt.datetime.now(dt.timezone.utc):
            return None
        return ban

    def add(self, ip: str, reason: str, expires_at: dt.datetime | None) -> None:
        self._bans[ip] = (reason, expires_at)

    def remove(self, ip: str) -> None:
        self._bans.pop(ip, None)

    async def refresh(self) -> None:
        from .models import IPBan

        async with SessionLocal() as db:
            rows = (await db.execute(select(IPBan.ip, IPBan.reason, IPBan.expires_at))).all()
        self._bans = {r.ip: (r.reason, r.expires_at) for r in rows}


class SiteCache:
    """Branding / contact settings editable from the admin panel (falls back to .env)."""

    DEFAULTS: dict[str, Any] = {
        "site_name": settings.APP_NAME,
        "tagline": settings.APP_TAGLINE,
        "primary_color": "#4f46e5",
        "accent_color": "#06b6d4",
        "logo": None,
        "footer_text": "Built for small schools & tuition centres. A student-grade project — see Terms.",
        "contact_email": "",
        "contact_whatsapp": "",
        "contact_phone": "",
        "contact_address": "",
        "facebook": "",
        "instagram": "",
        "youtube": "",
        "linkedin": "",
    }

    def __init__(self) -> None:
        self.branding: dict[str, Any] = dict(self.DEFAULTS)
        self.footer_pages: list[dict[str, str]] = []

    @property
    def data(self) -> dict[str, Any]:
        b = self.branding
        whatsapp = b.get("contact_whatsapp") or settings.CONTACT_WHATSAPP
        return {
            **b,
            "primary_color": b.get("primary_color") if HEX_COLOR.match(str(b.get("primary_color"))) else "#4f46e5",
            "accent_color": b.get("accent_color") if HEX_COLOR.match(str(b.get("accent_color"))) else "#06b6d4",
            "logo_url": f"/media/site/{b['logo']}" if b.get("logo") else None,
            "contact_email": b.get("contact_email") or settings.CONTACT_EMAIL,
            "contact_whatsapp": whatsapp,
            "whatsapp_link": "https://wa.me/" + re.sub(r"\D", "", whatsapp),
            "contact_phone": b.get("contact_phone") or settings.CONTACT_PHONE,
            "contact_address": b.get("contact_address") or settings.CONTACT_ADDRESS,
        }

    async def refresh(self) -> None:
        from .models import Page, SiteSetting

        async with SessionLocal() as db:
            row = await db.get(SiteSetting, "branding")
            pages = (await db.execute(
                select(Page.slug, Page.title).where(Page.is_published.is_(True), Page.show_in_footer.is_(True))
                .order_by(Page.id))).all()
        self.branding = {**self.DEFAULTS, **(row.value if row else {})}
        self.footer_pages = [{"slug": p.slug, "title": p.title} for p in pages]


ban_cache = BanCache()
site_cache = SiteCache()
