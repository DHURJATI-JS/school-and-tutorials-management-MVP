"""Staff portal: teachers see their batches (with health / special-needs notes), host live classes,
review AI insights and student doubts; every staff member (teachers, cleaners, coordinators, …)
can download their salary slips."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from ..constants import SUPERVISOR_ROLES, TEACHING_ROLES
from ..database import get_db
from ..models import (Attendance, Batch, DoubtMessage, Enrollment, Lesson, Meeting, Notification, PerformanceAlert,
                      SalaryPayment, Test, TestAttempt, User)
from ..security import Principal, csrf_protect, require_staff
from ..services.meetings import new_code
from ..services.notify import notify_users
from ..templating import flash, render
from .common import redirect, today

router = APIRouter(prefix="/staff", dependencies=[Depends(require_staff), Depends(csrf_protect)])


async def my_batches(db: AsyncSession, me: Principal) -> list[Batch]:
    if me.staff_role in SUPERVISOR_ROLES:
        stmt = select(Batch).where(Batch.is_active.is_(True))
    elif me.staff_role in TEACHING_ROLES:
        stmt = select(Batch).where(Batch.teacher_id == me.id)
    else:
        return []
    return list((await db.scalars(stmt.order_by(Batch.start_time))).all())


async def _my_batch(db: AsyncSession, me: Principal, bid: int) -> Batch:
    if bid not in {b.id for b in await my_batches(db, me)}:
        raise HTTPException(403, "This batch is not assigned to you.")
    return await db.get(Batch, bid)


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_staff)):
    batches = await my_batches(db, me)
    ids = [b.id for b in batches]
    day = today()
    hosting = select(Meeting.lesson_id).where(Meeting.host_user_id == me.id, Meeting.lesson_id.is_not(None))
    lessons = (await db.scalars(select(Lesson).options(joinedload(Lesson.batch), joinedload(Lesson.meeting),
                                                       joinedload(Lesson.video))
                                .where(or_(Lesson.batch_id.in_(ids), Lesson.id.in_(hosting)),  # + substitute classes
                                       Lesson.date >= day, Lesson.date <= day + dt.timedelta(days=2),
                                       Lesson.status == "scheduled")
                                .order_by(Lesson.date, Lesson.start_time))).unique().all()
    alerts = (await db.scalars(select(PerformanceAlert).options(joinedload(PerformanceAlert.user),
                                                                joinedload(PerformanceAlert.batch))
                               .where(PerformanceAlert.batch_id.in_(ids), PerformanceAlert.resolved.is_(False))
                               .order_by(PerformanceAlert.id.desc()).limit(6))).unique().all()
    doubts = int(await db.scalar(select(func.count()).select_from(DoubtMessage).where(
        DoubtMessage.batch_id.in_(ids), DoubtMessage.needs_teacher.is_(True))) or 0)
    sizes = dict((await db.execute(select(Enrollment.batch_id, func.count()).where(Enrollment.batch_id.in_(ids))
                                   .group_by(Enrollment.batch_id))).all())
    salary = await db.scalar(select(SalaryPayment).where(SalaryPayment.user_id == me.id)
                             .order_by(SalaryPayment.paid_on.desc()).limit(1))
    notes = (await db.scalars(select(Notification).where(Notification.user_id == me.id)
                              .order_by(Notification.id.desc()).limit(6))).all()
    return render(request, "staff/dashboard.html", {"batches": batches, "lessons": lessons, "alerts": alerts,
                                                    "doubts": doubts, "sizes": sizes, "salary": salary,
                                                    "notifications": notes})


@router.get("/batches/{bid}", response_class=HTMLResponse)
async def batch(bid: int, request: Request, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_staff)):
    await _my_batch(db, me, bid)
    item = await db.scalar(select(Batch).options(
        selectinload(Batch.enrollments).joinedload(Enrollment.user).selectinload(User.health_records))
        .where(Batch.id == bid))
    students = sorted((e.user for e in item.enrollments if e.user.status == "active"), key=lambda u: u.full_name)
    att = {r[0]: (r[1], r[2]) for r in (await db.execute(
        select(Attendance.user_id, func.count().filter(Attendance.status == "present"), func.count())
        .where(Attendance.batch_id == bid).group_by(Attendance.user_id))).all()}
    scores = dict((await db.execute(
        select(TestAttempt.user_id, func.avg(TestAttempt.score * 100 / TestAttempt.max_score))
        .join(Test, Test.id == TestAttempt.test_id)
        .where(Test.batch_id == bid, TestAttempt.status == "graded", TestAttempt.max_score > 0)
        .group_by(TestAttempt.user_id))).all())
    lessons = (await db.scalars(select(Lesson).options(joinedload(Lesson.meeting), joinedload(Lesson.video))
                                .where(Lesson.batch_id == bid, Lesson.date >= today() - dt.timedelta(days=14))
                                .order_by(Lesson.date, Lesson.start_time).limit(20))).unique().all()
    return render(request, "staff/batch.html", {"batch": item, "students": students, "att": att, "scores": scores,
                                                "lessons": lessons})


@router.post("/lessons/{lid}/start")
async def start_lesson(lid: int, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_staff)):
    lesson = await db.scalar(select(Lesson).options(joinedload(Lesson.meeting)).where(Lesson.id == lid))
    if lesson is None:
        raise HTTPException(404)
    meeting = lesson.meeting
    if meeting is None or meeting.host_user_id != me.id:  # the admin may have made me the (substitute) host
        await _my_batch(db, me, lesson.batch_id)
    if meeting is None:
        meeting = Meeting(code=new_code(), kind="class", title=lesson.title, batch_id=lesson.batch_id,
                          lesson_id=lesson.id, host_name=me.name, host_user_id=me.id,
                          scheduled_start=lesson.starts_at())
        db.add(meeting)
        await db.commit()
    return redirect(f"/meet/{meeting.code}")


@router.get("/calendar", response_class=HTMLResponse)
async def calendar(request: Request):
    return render(request, "shared/calendar.html", {"title": "Teaching calendar"})


@router.get("/salary", response_class=HTMLResponse)
async def salary(request: Request, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_staff)):
    rows = (await db.scalars(select(SalaryPayment).where(SalaryPayment.user_id == me.id)
                             .order_by(SalaryPayment.period.desc()))).all()
    return render(request, "staff/salary.html", {"payments": rows})


@router.get("/salary/{sid}/slip", response_class=HTMLResponse)
async def salary_slip(sid: int, request: Request, db: AsyncSession = Depends(get_db),
                      me: Principal = Depends(require_staff)):
    payment = await db.scalar(select(SalaryPayment).options(joinedload(SalaryPayment.user))
                              .where(SalaryPayment.id == sid, SalaryPayment.user_id == me.id,
                                     SalaryPayment.status == "paid"))
    if payment is None:
        raise HTTPException(404)
    return render(request, "finance/salary_slip.html", {"payment": payment})


@router.get("/insights", response_class=HTMLResponse)
async def insights(request: Request, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_staff)):
    ids = [b.id for b in await my_batches(db, me)]
    alerts = (await db.scalars(select(PerformanceAlert).options(joinedload(PerformanceAlert.user),
                                                                joinedload(PerformanceAlert.batch))
                               .where(PerformanceAlert.batch_id.in_(ids)).order_by(PerformanceAlert.id.desc())
                               .limit(100))).unique().all()
    return render(request, "staff/insights.html", {"alerts": alerts})


@router.post("/insights/{aid}/resolve")
async def resolve(aid: int, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_staff)):
    alert = await db.get(PerformanceAlert, aid)
    if alert is None:
        raise HTTPException(404)
    await _my_batch(db, me, alert.batch_id or 0)
    alert.resolved = not alert.resolved
    await db.commit()
    return redirect("/staff/insights")


@router.get("/doubts", response_class=HTMLResponse)
async def doubts(request: Request, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_staff)):
    ids = [b.id for b in await my_batches(db, me)]
    rows = (await db.scalars(select(DoubtMessage).options(joinedload(DoubtMessage.user))
                             .where(DoubtMessage.batch_id.in_(ids))
                             .order_by(DoubtMessage.needs_teacher.desc(), DoubtMessage.id.desc()).limit(100))).unique().all()
    return render(request, "staff/doubts.html", {"doubts": rows})


@router.post("/doubts/{did}/answer")
async def answer(did: int, request: Request, db: AsyncSession = Depends(get_db),
                 me: Principal = Depends(require_staff), text: str = Form(...)):
    doubt = await db.get(DoubtMessage, did)
    if doubt is None:
        raise HTTPException(404)
    await _my_batch(db, me, doubt.batch_id or 0)
    if not text.strip():
        flash(request, "Write an answer first.", "danger")
        return redirect("/staff/doubts")
    doubt.answer = f"{doubt.answer}\n\n---\n**{me.name} (teacher):** {text.strip()}".strip()
    doubt.answered_by, doubt.needs_teacher = "teacher", False
    link = f"/student/lessons/{doubt.lesson_id}" if doubt.lesson_id else f"/student/tutor?batch={doubt.batch_id}"
    await notify_users(db, [doubt.user_id], "Your teacher answered your doubt", doubt.question[:120], link,
                       "fa-chalkboard-user")
    await db.commit()
    flash(request, "Answer sent to the student.", "success")
    return redirect("/staff/doubts")


@router.get("/profile", response_class=HTMLResponse)
async def profile(request: Request, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_staff)):
    user = await db.scalar(select(User).options(selectinload(User.enrollments).joinedload(Enrollment.batch),
                                                selectinload(User.health_records)).where(User.id == me.id))
    return render(request, "shared/profile.html", {"member": user})
