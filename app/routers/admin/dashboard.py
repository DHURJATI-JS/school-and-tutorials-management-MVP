"""Admin dashboard: KPI tiles (one round-trip), charts and to-do lists."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from ...database import get_db
from ...security import admins
from ...models import (Attendance, Batch, ContactMessage, FeePayment, Lesson, Meeting, PerformanceAlert, Report, Test,
                       TestAttempt, User)
from ...services.ai import ai
from ...templating import render
from ..common import month_label, today

router = APIRouter()


def _count(model, *where):
    return select(func.count()).select_from(model).where(*where).scalar_subquery()


def _months_back(day: dt.date, n: int) -> list[str]:
    y, m = day.year, day.month
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    day = today()
    month_start = day.replace(day=1)
    stats = (await db.execute(select(
        _count(User, User.role == "student", User.status == "active").label("students"),
        _count(User, User.role == "staff", User.status == "active").label("staff"),
        _count(Batch, Batch.is_active.is_(True)).label("batches"),
        _count(Lesson, Lesson.date == day, Lesson.status != "cancelled").label("classes_today"),
        _count(Meeting, Meeting.status == "live").label("live"),
        select(func.coalesce(func.sum(FeePayment.amount), 0)).where(
            FeePayment.status == "paid", FeePayment.paid_on >= month_start).scalar_subquery().label("collected"),
        select(func.coalesce(func.sum(FeePayment.amount), 0)).where(
            FeePayment.status.in_(("pending", "overdue"))).scalar_subquery().label("pending"),
        _count(PerformanceAlert, PerformanceAlert.resolved.is_(False),
               PerformanceAlert.level != "info").label("alerts"),
        _count(ContactMessage, ContactMessage.is_read.is_(False)).label("inbox"),
        _count(FeePayment, FeePayment.status == "verifying").label("proofs"),
        _count(Report, Report.status == "open").label("reports"),
    ))).one()

    months = _months_back(day, 6)
    fee_rows = dict((await db.execute(
        select(func.to_char(FeePayment.paid_on, "YYYY-MM").label("m"), func.sum(FeePayment.amount))
        .where(FeePayment.status == "paid", FeePayment.paid_on >= dt.date.fromisoformat(months[0] + "-01"))
        .group_by("m"))).all())
    fee_series = [{"label": month_label(m), "value": float(fee_rows.get(m, 0) or 0)} for m in months]

    start = day - dt.timedelta(days=13)
    att_rows = {r[0]: (r[1], r[2]) for r in (await db.execute(
        select(Attendance.date, func.count().filter(Attendance.status == "present"), func.count())
        .where(Attendance.date >= start, Attendance.date <= day).group_by(Attendance.date))).all()}
    attendance_series = []
    for i in range(14):
        d = start + dt.timedelta(days=i)
        present, total = att_rows.get(d, (0, 0))
        attendance_series.append({"label": d.strftime("%d %b"),
                                  "value": round(present * 100 / total, 1) if total else None})

    score_rows = (await db.execute(
        select(Batch.name, func.avg(TestAttempt.score * 100 / TestAttempt.max_score).label("avg"))
        .join(Test, Test.id == TestAttempt.test_id).join(Batch, Batch.id == Test.batch_id)
        .where(TestAttempt.status == "graded", TestAttempt.max_score > 0)
        .group_by(Batch.name).order_by(desc("avg")).limit(12))).all()
    score_series = [{"label": r[0], "value": round(float(r[1]), 1)} for r in score_rows]

    upcoming = (await db.scalars(
        select(Lesson).options(joinedload(Lesson.batch), joinedload(Lesson.meeting), joinedload(Lesson.video))
        .where(Lesson.date >= day, Lesson.date <= day + dt.timedelta(days=7), Lesson.status == "scheduled")
        .order_by(Lesson.date, Lesson.start_time).limit(10))).unique().all()
    live = (await db.scalars(select(Meeting).options(joinedload(Meeting.batch))
                             .where(Meeting.status == "live").order_by(Meeting.started_at))).unique().all()
    alerts = (await db.scalars(
        select(PerformanceAlert).options(joinedload(PerformanceAlert.user), joinedload(PerformanceAlert.batch))
        .where(PerformanceAlert.resolved.is_(False)).order_by(PerformanceAlert.id.desc()).limit(5))).unique().all()
    inbox = (await db.scalars(select(ContactMessage).order_by(ContactMessage.id.desc()).limit(5))).all()

    return render(request, "admin/dashboard.html", {
        "stats": stats, "upcoming": upcoming, "live": live, "alerts": alerts, "inbox": inbox,
        "charts": {"fees": fee_series, "attendance": attendance_series, "scores": score_series},
        "ai_state": ai.breaker.state, "ai_error": ai.last_error,
        "weak_password": getattr(request.state.principal, "username", "") in admins.weak,
    })
