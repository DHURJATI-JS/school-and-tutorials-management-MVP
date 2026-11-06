"""Member (student / staff) onboarding: usernames, ID codes, virtual ID cards and welcome emails."""
from __future__ import annotations

import datetime as dt
import re
import secrets
import unicodedata

from anyio import to_thread
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..cache import site_cache
from ..config import settings
from ..models import User
from .idcard import render_id_card
from .mailer import Attachment, InlineImage, queue_email
from .storage import abs_path, delete_file, save_bytes


async def unique_username(db: AsyncSession, full_name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", full_name).encode("ascii", "ignore").decode().lower()
    base = re.sub(r"[^a-z0-9]+", ".", ascii_name).strip(".")[:40] or "user"
    taken = set((await db.scalars(select(User.username).where(User.username.like(f"{base}%")))).all())
    if base not in taken:
        return base
    for i in range(2, 1000):
        if f"{base}{i}" not in taken:
            return f"{base}{i}"
    return f"{base}{secrets.randbelow(10 ** 6)}"


def make_id_code(user: User) -> str:
    return f"{'STU' if user.is_student else 'STF'}-{user.id:06d}"


def valid_till() -> str:
    today = dt.date.today()
    year = today.year + (1 if today.month > 3 else 0)
    return f"31 Mar {year}"


def card_details(user: User, batch_names: list[str]) -> list[tuple[str, str]]:
    if user.is_student:
        return [("Date of birth", user.dob.strftime("%d %b %Y")), ("Phone", user.phone),
                ("Batches", ", ".join(batch_names) or "—"), ("Valid till", valid_till())]
    joined = user.joining_date.strftime("%d %b %Y") if user.joining_date else "—"
    return [("Designation", user.designation or user.role_label), ("Phone", user.phone), ("Joined", joined),
            ("Valid till", valid_till())]


async def issue_id_card(user: User, batch_names: list[str]) -> bytes:
    """Render + store the ID card PNG (sets user.idcard_path; caller commits)."""
    site = site_cache.data
    png = await to_thread.run_sync(lambda: render_id_card(
        name=user.full_name, id_code=user.id_code or "", role_line=user.role_label,
        details=card_details(user, batch_names), photo_path=abs_path(user.photo_path),
        site_name=site["site_name"], primary=site["primary_color"], accent=site["accent_color"],
        footer=f"Property of {site['site_name']} • {site['contact_phone']}"))
    old = user.idcard_path
    user.idcard_path = save_bytes(png, "idcards", ".png")
    delete_file(old)
    return png


def member_context(user: User) -> dict:
    return {"name": user.full_name, "first_name": user.full_name.split()[0], "username": user.username,
            "email": user.email, "id_code": user.id_code, "role_label": user.role_label,
            "is_student": user.is_student, "special_needs": user.special_needs,
            "parent_name": user.parent_name}


def send_onboarding(user: User, password: str | None, batch_names: list[str], card_png: bytes, *,
                    is_new: bool = True) -> None:
    """Thank-you email (new joiners, parent in CC) + confidential credentials email with the ID card.
    password=None re-sends the card only (the password is unchanged and never stored in plain text)."""
    site = site_cache.data["site_name"]
    ctx = member_context(user)
    if is_new:
        cc = [user.parent_email] if user.is_student and user.parent_email else None
        queue_email(user.email, f"Thank you for joining {site}! 🎉", "welcome.html", cc=cc, category="welcome",
                    member=ctx, batches=batch_names)
    queue_email(user.email, f"Your {site} ID card & login details — CONFIDENTIAL", "credentials.html",
                category="credentials", member=ctx, password=password, login_url=f"{settings.BASE_URL}/login",
                is_new=is_new, inline=[InlineImage("idcard", card_png)],
                attachments=[Attachment(f"{user.id_code}-id-card.png", card_png, "image/png")])
