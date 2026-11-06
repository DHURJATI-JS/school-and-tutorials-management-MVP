"""In-app notifications - written with one bulk INSERT (never one query per user)."""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Enrollment, Notification, User, utcnow


async def notify_users(db: AsyncSession, user_ids: Iterable[int | None], title: str, body: str | None = None,
                       link: str | None = None, icon: str = "fa-bell") -> int:
    ids = sorted({int(u) for u in user_ids if u})
    if not ids:
        return 0
    now = utcnow()
    await db.execute(insert(Notification), [
        {"user_id": uid, "title": title[:200], "body": body, "link": link, "icon": icon, "is_read": False,
         "created_at": now}
        for uid in ids
    ])
    return len(ids)


async def batch_student_ids(db: AsyncSession, batch_ids: Iterable[int]) -> list[int]:
    ids = list(batch_ids)
    if not ids:
        return []
    stmt = (select(Enrollment.user_id).join(User, User.id == Enrollment.user_id)
            .where(Enrollment.batch_id.in_(ids), User.status == "active").distinct())
    return list((await db.scalars(stmt)).all())


async def batch_students(db: AsyncSession, batch_ids: Iterable[int]) -> list[User]:
    ids = list(batch_ids)
    if not ids:
        return []
    stmt = (select(User).join(Enrollment, Enrollment.user_id == User.id)
            .where(Enrollment.batch_id.in_(ids), User.status == "active").distinct().order_by(User.full_name))
    return list((await db.scalars(stmt)).all())
