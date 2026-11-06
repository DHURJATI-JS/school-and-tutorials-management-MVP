"""Account lifecycle beyond "removed": ban, delete (30-day dormancy) and the automatic purge.

* **Banned** - sign-in refused with a "banned - contact the administrator" notice, every live connection closed at once,
  optionally their network address blocked too. Nothing is deleted: the account stays and shows as *Banned*.
* **Deleted** - the account goes dormant: no sign-in, hidden from other members, its messages and comments shown as
  "Deleted user". An admin can restore it for DELETED_RETENTION_DAYS (30); after that `purge_due` removes the person
  and their data for good (files included). Threads stay readable: comments others replied to become placeholders.
"""
from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterable

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ..cache import ban_cache, site_cache
from ..config import settings
from ..models import (BatchChatMessage, ChatMessage, DoubtMessage, FeePayment, IPBan, MeetingMessage,
                      MeetingParticipant, PTMInvite, RemovalRecord, SalaryPayment, TaskSubmission, User, VideoComment,
                      utcnow)
from ..security import contact_email
from .chat import hub
from .mailer import queue_admin_alert, queue_email
from .meetings import rooms
from .social import social_hub
from .storage import delete_file

log = logging.getLogger("classnest.accounts")
DELETED_NAME = "Deleted user"
REGISTRIES = (rooms, hub, social_hub)  # live classes, batch chats, friends / DMs


async def disconnect_user(user_id: int, message: str) -> None:
    """Close every live connection of one person (the pages reload into the sign-in notice)."""
    key = f"u:{user_id}"
    for registry in REGISTRIES:
        await registry.kick_where(lambda p: p.key == key, message)


async def disconnect_ip(ip: str, message: str) -> None:
    """Close every live connection from one network address (the pages reload into the block page)."""
    for registry in REGISTRIES:
        await registry.kick_where(lambda p: p.ip == ip, message)


async def statuses(db: AsyncSession, ids: Iterable[int | None]) -> dict[int, str]:
    """{user id: status} for the people in `ids` who are NOT active - one query for a whole chat history."""
    wanted = {i for i in ids if i}
    if not wanted:
        return {}
    rows = await db.execute(select(User.id, User.status).where(User.id.in_(wanted), User.status != "active"))
    return dict(rows.all())


def _record(db: AsyncSession, user: User, status: str, action: str, reason: str, actor: str, *,
            subject: str | None = None, body: str | None = None) -> None:
    user.status, user.status_reason, user.status_changed_at = status, reason or None, utcnow()
    db.add(RemovalRecord(user_id=user.id, action=action, reason=reason or action.capitalize(), actor=actor,
                         email_subject=subject, email_body=body))


def _site() -> str:
    return site_cache.data.get("site_name") or settings.APP_NAME


# ------------------------------------------------------------------ ban
async def ban_account(db: AsyncSession, user: User, *, reason: str, actor: str, block_ip: bool = False,
                      admin_ip: str | None = None, notify: bool = True) -> str | None:
    """Ban the account (and optionally its last network address). Returns the IP that was blocked, if any."""
    body = (f"Dear {user.full_name},\n\nYour {_site()} account has been banned by the administration.\n\n"
            f"Reason: {reason}\n\nIf you believe this is a mistake, contact the administrator at {contact_email()}.")
    _record(db, user, "banned", "banned", reason, actor, subject=f"Your {_site()} account has been banned", body=body)
    blocked = None
    if block_ip and user.last_ip and user.last_ip != admin_ip:
        existing = await db.scalar(select(IPBan).where(IPBan.ip == user.last_ip))
        if existing is None:
            db.add(IPBan(ip=user.last_ip, reason=reason, banned_by=actor, user_id=user.id))
        else:
            existing.reason, existing.banned_by, existing.expires_at, existing.user_id = reason, actor, None, user.id
        blocked = user.last_ip
    await db.commit()
    if blocked:
        ban_cache.add(blocked, reason, None)
        await disconnect_ip(blocked, "This network address has been blocked by the administrator.")
    await disconnect_user(user.id, "Your account has been banned by the administrator.")
    if notify:
        queue_email(user.email, f"Your {_site()} account has been banned", "removal.html", category="removal",
                    name=user.full_name, body=body)
    return blocked


async def unban_account(db: AsyncSession, user: User, *, note: str, actor: str, notify: bool = True) -> int:
    """Lift the ban - and the network blocks that were linked to this person. Returns how many IPs were unblocked."""
    _record(db, user, "active", "unbanned", note or "Ban lifted", actor)
    user.status_reason = None
    linked = (await db.scalars(select(IPBan).where(IPBan.user_id == user.id))).all()
    for ban in linked:
        await db.delete(ban)
    await db.commit()
    for ban in linked:
        ban_cache.remove(ban.ip)
    if notify:
        queue_email(user.email, f"Welcome back to {_site()}", "reinstated.html", category="removal",
                    name=user.full_name, reason=note)
    return len(linked)


# ------------------------------------------------------------------ delete -> dormant -> purge
async def delete_account(db: AsyncSession, user: User, *, reason: str, actor: str, notify: bool = True) -> dt.datetime:
    """Start the dormancy period. Returns when the account will be removed for good."""
    _record(db, user, "deleted", "deleted", reason, actor)
    await db.commit()
    await disconnect_user(user.id, "This account has been deleted.")
    purge_on = user.purge_on or utcnow() + dt.timedelta(days=settings.DELETED_RETENTION_DAYS)
    if notify:
        when = purge_on.astimezone(settings.tz).strftime("%d %B %Y")
        queue_email(user.email, f"Your {_site()} account has been deleted", "removal.html", category="removal",
                    name=user.full_name,
                    body=(f"Dear {user.full_name},\n\nYour {_site()} account has been deleted.\n\nReason: {reason}\n\n"
                          f"Your data is kept for {settings.DELETED_RETENTION_DAYS} days and permanently removed on "
                          f"{when}. If this is a mistake, contact the administrator at {contact_email()} before "
                          f"then and it can be restored."))
    return purge_on


async def restore_account(db: AsyncSession, user: User, *, note: str, actor: str, notify: bool = True) -> None:
    _record(db, user, "active", "restored", note or "Account restored", actor)
    user.status_reason = None
    await db.commit()
    if notify:
        queue_email(user.email, f"Your {_site()} account has been restored", "reinstated.html", category="removal",
                    name=user.full_name, reason=note)


async def purge_user(db: AsyncSession, user: User) -> list[str]:
    """Remove one person and everything that is theirs. Returns the files to delete once the transaction commits."""
    uid = user.id
    files = [user.photo_path, user.id_proof_path, user.idcard_path]
    for column, owner in ((TaskSubmission.file_path, TaskSubmission.user_id), (FeePayment.proof_path, FeePayment.user_id),
                          (SalaryPayment.receipt_path, SalaryPayment.user_id),
                          (DoubtMessage.image_path, DoubtMessage.user_id)):
        files += (await db.scalars(select(column).where(owner == uid, column.is_not(None)))).all()
    # their words in shared spaces (these keys are SET NULL on delete, which would leave name + text behind)
    await db.execute(delete(ChatMessage).where(ChatMessage.user_id == uid))
    await db.execute(delete(BatchChatMessage).where(BatchChatMessage.user_id == uid))
    await db.execute(delete(MeetingMessage).where(MeetingMessage.sender_key == f"u:{uid}"))
    await db.execute(delete(MeetingParticipant).where(MeetingParticipant.user_id == uid))
    await db.execute(delete(PTMInvite).where(PTMInvite.user_id == uid))
    # comments: one that others replied to becomes an empty placeholder so the thread still reads; the rest go
    reply = aliased(VideoComment)
    answered = select(reply.id).where(reply.parent_id == VideoComment.id).exists()
    await db.execute(update(VideoComment).where(VideoComment.user_id == uid, answered).values(
        body="", deleted=True, author_name=DELETED_NAME, user_id=None, video_seconds=None))
    await db.execute(delete(VideoComment).where(VideoComment.user_id == uid))
    await db.delete(user)  # cascades: enrolments, attendance, fees, salaries, submissions, attempts, friends, ...
    return [f for f in files if f]


async def purge_due(db: AsyncSession, now: dt.datetime | None = None) -> list[str]:
    """Permanently remove every account whose dormancy has run out. Returns "Name (ID)" for each one."""
    cutoff = (now or utcnow()) - dt.timedelta(days=settings.DELETED_RETENTION_DAYS)
    due = (await db.scalars(select(User).where(User.status == "deleted", User.status_changed_at <= cutoff)
                            .order_by(User.id).limit(200))).all()
    purged, files = [], []
    for user in due:
        purged.append(f"{user.full_name} ({user.id_code or user.username})")
        files += await purge_user(db, user)
    if not due:
        return []
    await db.commit()
    for rel in files:
        delete_file(rel)
    log.warning("Purged %d deleted account(s) after %d days: %s", len(purged), settings.DELETED_RETENTION_DAYS,
                ", ".join(purged))
    queue_admin_alert(f"{len(purged)} deleted account(s) permanently removed", "purge_notice.html",
                      category="accounts", people=purged, days=settings.DELETED_RETENTION_DAYS)
    return purged
