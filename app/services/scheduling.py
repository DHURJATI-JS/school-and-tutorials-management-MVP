"""Batch scheduling rules.

* every batch lasts at least MIN_BATCH_MINUTES (1 hour)
* at most MAX_BATCHES (12) active batches
* the cumulative batch time on any weekday is at most MAX_DAILY_HOURS (12 hours)
* a student cannot be enrolled in two batches that overlap in time
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..constants import WEEKDAY_SHORT
from ..models import Batch, Enrollment


def minutes(t: dt.time) -> int:
    return t.hour * 60 + t.minute


async def validate_batch(db: AsyncSession, *, days: list[int], start: dt.time, end: dt.time, is_active: bool,
                         exclude_id: int | None = None) -> list[str]:
    errors: list[str] = []
    if not days:
        errors.append("Pick at least one day of the week.")
    duration = minutes(end) - minutes(start)
    if duration <= 0:
        errors.append("The end time must be after the start time.")
    elif duration < settings.MIN_BATCH_MINUTES:
        errors.append(f"Each batch must be at least {settings.MIN_BATCH_MINUTES // 60} hour long "
                      f"(this one is {duration} minutes).")
    if not is_active or errors:
        return errors
    stmt = select(Batch).where(Batch.is_active.is_(True))
    if exclude_id:
        stmt = stmt.where(Batch.id != exclude_id)
    others = list((await db.scalars(stmt)).all())
    if len(others) >= settings.MAX_BATCHES:
        errors.append(f"You already have {len(others)} active batches — the maximum is {settings.MAX_BATCHES}. "
                      "Deactivate one first.")
    limit = int(settings.MAX_DAILY_HOURS * 60)
    for day in sorted(set(days)):
        total = duration + sum(b.duration_minutes for b in others if day in (b.days or []))
        if total > limit:
            errors.append(f"{WEEKDAY_SHORT[day]}: batches would total {total / 60:.1f} h — the daily cumulative "
                          f"limit is {settings.MAX_DAILY_HOURS:g} h.")
    return errors


def _overlap(a: Batch, b: Batch) -> bool:
    if not set(a.days or []) & set(b.days or []):
        return False
    return minutes(a.start_time) < minutes(b.end_time) and minutes(b.start_time) < minutes(a.end_time)


def enrollment_errors(batches: list[Batch], new_batch_ids: set[int] | list[int]) -> list[str]:
    """One student's final set of batches: no time overlap involving a new batch, and the daily cap. No database
    access, so many students can be checked against batches loaded once (no N+1)."""
    errors: list[str] = []
    for i, a in enumerate(batches):
        for b in batches[i + 1:]:
            if (a.id in new_batch_ids or b.id in new_batch_ids) and _overlap(a, b):
                errors.append(f"“{a.name}” and “{b.name}” overlap in time.")
    limit = int(settings.MAX_DAILY_HOURS * 60)
    for day in range(7):
        total = sum(b.duration_minutes for b in batches if day in (b.days or []))
        if total > limit:
            errors.append(f"{WEEKDAY_SHORT[day]}: the student's batches would total {total / 60:.1f} h "
                          f"(limit {settings.MAX_DAILY_HOURS:g} h).")
    return errors


async def validate_enrollment(db: AsyncSession, final_batch_ids: list[int], new_batch_ids: list[int]) -> list[str]:
    """Check the student's final set of batches: no time overlaps (involving a new batch) and the daily cap."""
    all_ids = set(final_batch_ids) | set(new_batch_ids)
    if not all_ids:
        return []
    batches = list((await db.scalars(select(Batch).where(Batch.id.in_(all_ids)))).all())
    return enrollment_errors(batches, new_batch_ids)


async def batch_ids_by_user(db: AsyncSession, user_ids: list[int]) -> dict[int, set[int]]:
    """{student id: their batch ids} for many students in one query."""
    out: dict[int, set[int]] = {uid: set() for uid in user_ids}
    if user_ids:
        rows = await db.execute(select(Enrollment.user_id, Enrollment.batch_id).where(Enrollment.user_id.in_(user_ids)))
        for uid, bid in rows.all():
            out[uid].add(bid)
    return out


async def batches_by_id(db: AsyncSession, ids: set[int]) -> dict[int, Batch]:
    if not ids:
        return {}
    return {b.id: b for b in (await db.scalars(select(Batch).where(Batch.id.in_(ids)))).all()}
