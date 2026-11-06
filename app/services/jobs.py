"""Background scheduler. A Postgres advisory lock makes sure only one worker runs the jobs."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import joinedload

from ..cache import ban_cache, site_cache
from ..config import settings
from ..database import SessionLocal, engine
from ..models import FeePayment, IPBan, Lesson, LoginEvent, OTPCode, SiteSetting, User, utcnow
from ..security import limiter
from ..templating import f_datetime
from .mailer import queue_email
from .payments import poll_processing_payouts
from .performance import analyze_all

log = logging.getLogger("classnest.jobs")
LOCK_KEY = 724_001


async def lesson_watch() -> None:
    """A live class is mandatory when a lesson has no pre-recorded video: remind & warn the admins."""
    now = utcnow()
    today = now.astimezone(settings.tz).date()
    async with SessionLocal() as db:
        lessons = (await db.scalars(
            select(Lesson).options(joinedload(Lesson.batch), joinedload(Lesson.meeting))
            .where(Lesson.status == "scheduled", Lesson.video_id.is_(None),
                   Lesson.date.between(today - dt.timedelta(days=1), today + dt.timedelta(days=1))))).unique().all()
        teacher_ids = {ls.batch.teacher_id for ls in lessons if ls.batch and ls.batch.teacher_id}
        teachers = {u.id: u.email for u in (await db.scalars(select(User).where(User.id.in_(teacher_ids)))).all()} \
            if teacher_ids else {}
        for lesson in lessons:
            starts = lesson.starts_at()
            meeting = lesson.meeting
            held = meeting is not None and meeting.status in ("live", "ended")
            if held:
                continue
            ctx = {"lesson": lesson.title, "batch": lesson.batch.name, "when": f_datetime(starts),
                   "link": f"{settings.BASE_URL}/admin/lessons/{lesson.id}"}
            recipients = settings.admin_alert_list + ([teachers[lesson.batch.teacher_id]]
                                                      if lesson.batch.teacher_id in teachers else [])
            if lesson.reminder_sent_at is None and now < starts <= now + dt.timedelta(hours=24):
                queue_email(recipients, f"Reminder: live class required — {lesson.batch.name}", "meeting_warning.html",
                            category="meeting_warning", kind="reminder", **ctx)
                lesson.reminder_sent_at = now
            if lesson.missed_alert_sent_at is None and now > starts + dt.timedelta(minutes=15):
                queue_email(recipients, f"⚠️ Live class not started — {lesson.batch.name}", "meeting_warning.html",
                            category="meeting_warning", kind="missed", **ctx)
                lesson.missed_alert_sent_at = now
        await db.commit()


async def fee_overdue() -> None:
    today = dt.datetime.now(settings.tz).date()
    async with SessionLocal() as db:
        await db.execute(update(FeePayment).where(FeePayment.status == "pending", FeePayment.due_date < today)
                         .values(status="overdue"))
        await db.commit()


async def housekeeping() -> None:
    now = utcnow()
    async with SessionLocal() as db:
        await db.execute(delete(OTPCode).where(OTPCode.expires_at < now - dt.timedelta(days=1)))
        expired = await db.execute(delete(IPBan).where(IPBan.expires_at.is_not(None), IPBan.expires_at < now))
        await db.execute(delete(LoginEvent).where(LoginEvent.created_at < now - dt.timedelta(days=180)))
        await db.commit()
    if expired.rowcount:
        await ban_cache.refresh()


async def account_purge() -> None:
    """Deleted accounts are dormant for DELETED_RETENTION_DAYS, then removed for good."""
    from .accounts import purge_due

    async with SessionLocal() as db:
        await purge_due(db)


async def maybe_performance(force: bool = False) -> int:
    async with SessionLocal() as db:
        state = await db.get(SiteSetting, "jobs")
        last = (state.value or {}).get("last_performance") if state else None
        due = force or not last or (utcnow() - dt.datetime.fromisoformat(last)) >= dt.timedelta(
            hours=settings.PERFORMANCE_CHECK_HOURS)
        if not due:
            return 0
        created = await analyze_all(db)
        state = await db.get(SiteSetting, "jobs")
        value = dict(state.value or {}) if state else {}
        value["last_performance"] = utcnow().isoformat()
        if state:
            state.value = value
        else:
            db.add(SiteSetting(key="jobs", value=value))
        await db.commit()
        return created


async def run_jobs_once(force_performance: bool = False) -> None:
    async with engine.connect() as lock_conn:
        if not await lock_conn.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}):
            return
        try:
            for job in (lesson_watch, fee_overdue, housekeeping, account_purge, poll_processing_payouts):
                try:
                    await job()
                except Exception:
                    log.exception("Job %s failed", job.__name__)
            try:
                await maybe_performance(force_performance)
            except Exception:
                log.exception("Performance analysis failed")
        finally:
            await lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})


async def scheduler_loop() -> None:
    await asyncio.sleep(20)
    while True:
        try:
            await run_jobs_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Scheduler iteration failed")
        await asyncio.sleep(settings.JOBS_INTERVAL_SECONDS)


async def cache_loop() -> None:
    while True:
        await asyncio.sleep(30)
        try:
            await ban_cache.refresh()
            await site_cache.refresh()
            limiter.cleanup()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("Cache refresh failed", exc_info=True)
