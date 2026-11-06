"""Batches, holiday calendar and task-based scheduling."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from ...cache import HEX_COLOR
from ...config import settings
from ...constants import TASK_KINDS, TEACHING_ROLES
from ...database import get_db
from ...models import (Batch, Enrollment, Holiday, Lesson, Task, TaskSubmission, Test, TestAttempt, User, utcnow)
from ...security import Principal, require_admin
from ...services.academic_calendar import validate_task_dates
from ...services.mailer import queue_bulk
from ...services.notify import batch_student_ids, batch_students, notify_users
from ...services.scheduling import batch_ids_by_user, batches_by_id, enrollment_errors, validate_batch
from ...services.storage import SUBMISSION_EXT, UploadError, has_file, save_upload
from ...templating import flash, render
from ..common import FormError, int_or, ints, parse_date, parse_money, parse_time, redirect, today, truthy

router = APIRouter()


async def teaching_staff(db: AsyncSession) -> list[User]:
    return list((await db.scalars(select(User).where(User.role == "staff", User.status == "active",
                                                     User.staff_role.in_(TEACHING_ROLES))
                                  .order_by(User.full_name))).all())


# ------------------------------------------------------------------ batches
@router.get("/batches", response_class=HTMLResponse)
async def batches(request: Request, db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(Batch).options(joinedload(Batch.teacher))
                             .order_by(Batch.is_active.desc(), Batch.start_time))).unique().all()
    counts = dict((await db.execute(select(Enrollment.batch_id, func.count()).group_by(Enrollment.batch_id))).all())
    load = [0] * 7
    for b in rows:
        if b.is_active:
            for d in b.days or []:
                load[d] += b.duration_minutes
    return render(request, "admin/batches/list.html", {
        "batches": rows, "counts": counts, "load": load, "limit": int(settings.MAX_DAILY_HOURS * 60),
        "active": sum(1 for b in rows if b.is_active)})


@router.get("/batches/new", response_class=HTMLResponse)
async def batch_new(request: Request, db: AsyncSession = Depends(get_db)):
    return render(request, "admin/batches/form.html", {"batch": None, "teachers": await teaching_staff(db)})


@router.get("/batches/{bid}/edit", response_class=HTMLResponse)
async def batch_edit(bid: int, request: Request, db: AsyncSession = Depends(get_db)):
    batch = await db.get(Batch, bid)
    if batch is None:
        raise HTTPException(404)
    return render(request, "admin/batches/form.html", {"batch": batch, "teachers": await teaching_staff(db)})


async def timetable_clashes(db: AsyncSession, batch: Batch) -> list[str]:
    """New days / times must not give the batch's students overlapping classes or too many hours a day - the rule
    enrolment enforces. The edited, unsaved batch is the object in the session, so the check sees its new times.
    Three queries however many students are enrolled."""
    students = dict((await db.execute(select(User.id, User.full_name).join(Enrollment, Enrollment.user_id == User.id)
                                      .where(Enrollment.batch_id == batch.id))).all())
    current = await batch_ids_by_user(db, list(students))
    batches = await batches_by_id(db, {b for ids in current.values() for b in ids})
    problems = []
    for uid, name in sorted(students.items(), key=lambda kv: kv[1]):
        errors = enrollment_errors([batches[i] for i in current[uid] if i in batches], {batch.id})
        if errors:
            problems.append(f"{name}: {' '.join(errors)}")
    return problems


async def follow_timetable(db: AsyncSession, batch: Batch, admin: Principal, old_teacher_id: int | None,
                           old_start: dt.time, old_end: dt.time) -> tuple[int, int, str]:
    """Upcoming classes scheduled at the old batch time move to the new time, and live rooms hosted by the old
    teacher get the new one. Classes given their own time or host (a substitute), or already started, stay."""
    lessons = (await db.scalars(select(Lesson).options(joinedload(Lesson.meeting)).where(
        Lesson.batch_id == batch.id, Lesson.status == "scheduled", Lesson.date >= today()))).unique().all()
    teacher = await db.get(User, batch.teacher_id) if batch.teacher_id else None
    host_name, host_id = (teacher.full_name, teacher.id) if teacher else (admin.name, None)
    new_time = (batch.start_time, batch.end_time) != (old_start, old_end)
    moved = rehosted = 0
    for lesson in lessons:
        meeting = lesson.meeting
        if meeting is not None and meeting.status != "scheduled":
            continue
        if new_time and (lesson.start_time, lesson.end_time) == (old_start, old_end):
            lesson.start_time, lesson.end_time = batch.start_time, batch.end_time
            lesson.reminder_sent_at = lesson.missed_alert_sent_at = None  # remind / alert again for the new time
            if meeting is not None:
                meeting.scheduled_start, meeting.scheduled_minutes = lesson.starts_at(), batch.duration_minutes
            moved += 1
        if batch.teacher_id != old_teacher_id and meeting is not None and meeting.host_user_id == old_teacher_id:
            meeting.host_name, meeting.host_user_id = host_name, host_id
            rehosted += 1
    return moved, rehosted, host_name


@router.post("/batches/new")
@router.post("/batches/{bid}/edit")
async def batch_save(request: Request, bid: int = 0, db: AsyncSession = Depends(get_db),
                     admin: Principal = Depends(require_admin)):
    form = await request.form()
    batch = await db.get(Batch, bid) if bid else None
    if bid and batch is None:
        raise HTTPException(404)
    try:
        name = str(form.get("name") or "").strip()
        class_level = str(form.get("class_level") or "").strip()
        subject = str(form.get("subject") or "").strip()
        if not (name and class_level and subject):
            raise FormError("Name, class and subject are required.")
        days = sorted({d for d in ints(form.getlist("days")) if 0 <= d <= 6})
        start, end = parse_time(str(form.get("start_time")), "start time"), parse_time(str(form.get("end_time")),
                                                                                         "end time")
        is_active = truthy(str(form.get("is_active", "")))
        color = str(form.get("color") or "#6366f1")
        teacher_id = int_or(form.get("teacher_id"))
        fee = parse_money(str(form.get("monthly_fee") or "0"), "monthly fee", allow_zero=True)
        errors = await validate_batch(db, days=days, start=start, end=end, is_active=is_active, exclude_id=bid or None)
        if errors:
            raise FormError(" ".join(errors))
        if await db.scalar(select(Batch.id).where(func.lower(Batch.name) == name.lower(), Batch.id != bid)):
            raise FormError(f"A batch called “{name}” already exists.")
        if teacher_id and teacher_id not in {t.id for t in await teaching_staff(db)}:
            raise FormError("The selected teacher is not an active teaching staff member.")
    except FormError as exc:
        flash(request, str(exc), "danger")
        return redirect(f"/admin/batches/{bid}/edit" if bid else "/admin/batches/new")
    old = None if batch is None else (batch.teacher_id, batch.start_time, batch.end_time, sorted(batch.days or []))
    if batch is None:
        batch = Batch()
        db.add(batch)
    batch.name, batch.class_level, batch.subject = name[:80], class_level[:40], subject[:80]
    batch.description = str(form.get("description") or "").strip() or None
    batch.color = color if HEX_COLOR.match(color) else "#6366f1"
    batch.days, batch.start_time, batch.end_time = days, start, end
    batch.teacher_id, batch.monthly_fee, batch.is_active = teacher_id, fee, is_active
    note = ""
    if old is not None:
        if (start, end, days) != (old[1], old[2], old[3]):
            clashes = await timetable_clashes(db, batch)
            if clashes:
                await db.rollback()
                more = f" (+{len(clashes) - 3} more)" if len(clashes) > 3 else ""
                flash(request, "Not saved — with the new days/times these enrolled students would have clashing "
                               "classes: " + "; ".join(clashes[:3]) + more, "danger")
                return redirect(f"/admin/batches/{bid}/edit")
        moved, rehosted, host = await follow_timetable(db, batch, admin, old[0], old[1], old[2])
        if moved:
            note += f" {moved} upcoming class(es) moved to {batch.time_label}."
            await notify_users(db, await batch_student_ids(db, [batch.id]), f"New class time: {batch.name}",
                               f"{batch.days_label} · {batch.time_label} — your upcoming classes have moved",
                               "/student/calendar", "fa-clock")
        if rehosted:
            note += f" {rehosted} upcoming live class(es) are now hosted by {host}."
        if teacher_id and teacher_id != old[0]:
            await notify_users(db, [teacher_id], f"You're now teaching {batch.name}",
                               f"{batch.days_label} · {batch.time_label}", "/staff", "fa-chalkboard-user")
    await db.commit()
    flash(request, f"Batch “{batch.name}” saved.{note}", "success")
    return redirect(f"/admin/batches/{batch.id}")


@router.get("/batches/{bid}", response_class=HTMLResponse)
async def batch_detail(bid: int, request: Request, db: AsyncSession = Depends(get_db)):
    batch = await db.scalar(select(Batch).options(
        joinedload(Batch.teacher),
        selectinload(Batch.enrollments).joinedload(Enrollment.user).selectinload(User.health_records))
        .where(Batch.id == bid))
    if batch is None:
        raise HTTPException(404)
    enrolled_ids = select(Enrollment.user_id).where(Enrollment.batch_id == bid)
    available = (await db.scalars(select(User).where(User.role == "student", User.status == "active",
                                                     User.id.not_in(enrolled_ids))
                                  .order_by(User.full_name).limit(500))).all()
    lessons = (await db.scalars(select(Lesson).options(joinedload(Lesson.meeting), joinedload(Lesson.video))
                                .where(Lesson.batch_id == bid, Lesson.date >= today() - dt.timedelta(days=7))
                                .order_by(Lesson.date, Lesson.start_time).limit(12))).unique().all()
    tasks = (await db.scalars(select(Task).where(Task.batch_id == bid).order_by(Task.due_date.desc()).limit(10))).all()
    tests = (await db.scalars(select(Test).where(Test.batch_id == bid).order_by(Test.id.desc()).limit(10))).all()
    students = sorted((e.user for e in batch.enrollments), key=lambda u: u.full_name)
    return render(request, "admin/batches/detail.html", {"batch": batch, "students": students,
                                                         "available": available, "lessons": lessons,
                                                         "tasks": tasks, "tests": tests})


@router.post("/batches/{bid}/enroll")
async def batch_enroll(bid: int, request: Request, db: AsyncSession = Depends(get_db)):
    batch = await db.get(Batch, bid)
    if batch is None:
        raise HTTPException(404)
    form = await request.form()
    ids = sorted(set(ints(form.getlist("user_ids"))))
    added, problems = [], []
    users = {u.id: u for u in (await db.scalars(select(User).where(User.id.in_(ids), User.role == "student"))).all()}
    current = await batch_ids_by_user(db, list(users))  # 3 queries for any number of students (was 2 per student)
    batches = await batches_by_id(db, {bid} | {b for mine in current.values() for b in mine})
    for uid in ids:
        if uid not in users:
            continue
        if bid in current[uid]:
            continue
        errors = enrollment_errors([batches[i] for i in current[uid] | {bid} if i in batches], {bid})
        if errors:
            problems.append(f"{users[uid].full_name}: {' '.join(errors)}")
            continue
        db.add(Enrollment(user_id=uid, batch_id=bid))
        added.append(uid)
    await notify_users(db, added, f"You've been added to {batch.name}", f"{batch.days_label} · {batch.time_label}",
                       "/student", "fa-people-group")
    await db.commit()
    if added:
        flash(request, f"Added {len(added)} student(s) to {batch.name}.", "success")
    for problem in problems[:5]:
        flash(request, problem, "warning")
    return redirect(f"/admin/batches/{bid}")


@router.post("/batches/{bid}/unenroll/{uid}")
async def batch_unenroll(bid: int, uid: int, request: Request, db: AsyncSession = Depends(get_db)):
    await db.execute(delete(Enrollment).where(Enrollment.batch_id == bid, Enrollment.user_id == uid))
    await db.commit()
    flash(request, "Student removed from the batch.", "success")
    return redirect(f"/admin/batches/{bid}")


@router.post("/batches/{bid}/delete")
async def batch_delete(bid: int, request: Request, db: AsyncSession = Depends(get_db)):
    batch = await db.get(Batch, bid)
    if batch is None:
        raise HTTPException(404)
    has_history = await db.scalar(select(func.count()).select_from(Lesson).where(Lesson.batch_id == bid)) or \
        await db.scalar(select(func.count()).select_from(Test).where(Test.batch_id == bid))
    if has_history:
        flash(request, "This batch has classes or tests on record — deactivate it instead of deleting it.",
              "warning")
        return redirect(f"/admin/batches/{bid}")
    await db.delete(batch)
    await db.commit()
    flash(request, "Batch deleted.", "success")
    return redirect("/admin/batches")


# ------------------------------------------------------------------ holiday calendar
@router.get("/calendar", response_class=HTMLResponse)
async def calendar(request: Request, db: AsyncSession = Depends(get_db)):
    holidays = (await db.scalars(select(Holiday).where(Holiday.end_date >= today() - dt.timedelta(days=60))
                                 .order_by(Holiday.start_date))).all()
    return render(request, "admin/calendar.html", {"holidays": holidays, "weekly_off": sorted(settings.weekly_off)})


@router.post("/holidays")
async def holiday_create(request: Request, db: AsyncSession = Depends(get_db),
                         admin: Principal = Depends(require_admin), title: str = Form(...),
                         start_date: str = Form(...), end_date: str = Form(""), kind: str = Form("holiday"),
                         allow_homework: str = Form(""), description: str = Form("")):
    try:
        start = parse_date(start_date, "start date")
        end = parse_date(end_date, "end date", required=False) or start
        if end < start:
            raise FormError("The end date can't be before the start date.")
        if (end - start).days > 120:
            raise FormError("A single holiday entry can span at most 120 days.")
        if not title.strip():
            raise FormError("Please give the holiday a name.")
    except FormError as exc:
        flash(request, str(exc), "danger")
        return redirect("/admin/calendar")
    kind = kind if kind in ("holiday", "vacation") else "holiday"
    homework = kind == "vacation" and truthy(allow_homework)
    db.add(Holiday(title=title.strip()[:120], start_date=start, end_date=end, kind=kind, allow_homework=homework,
                   description=description.strip() or None, created_by=admin.username))
    clash = select(func.count()).select_from(Task).where(or_(Task.assign_date.between(start, end),
                                                             Task.due_date.between(start, end)))
    if homework:
        clash = clash.where(Task.is_holiday_homework.is_(False))
    conflicts = int(await db.scalar(clash) or 0)
    await db.commit()
    flash(request, f"“{title.strip()}” added to the calendar.", "success")
    if conflicts:
        flash(request, f"{conflicts} existing task(s) have an assignment date or deadline inside this "
                       f"{kind} — please review them on the Tasks page.", "warning")
    return redirect("/admin/calendar")


@router.post("/holidays/{hid}/delete")
async def holiday_delete(hid: int, request: Request, db: AsyncSession = Depends(get_db)):
    holiday = await db.get(Holiday, hid)
    if holiday is not None:
        await db.delete(holiday)
        await db.commit()
    flash(request, "Holiday removed.", "success")
    return redirect("/admin/calendar")


@router.post("/holidays/seed")
async def holiday_seed(request: Request, db: AsyncSession = Depends(get_db),
                       admin: Principal = Depends(require_admin), year: int = Form(...)):
    if not 2000 <= year <= 2100:
        raise HTTPException(400)
    presets = [
        ("Republic Day", dt.date(year, 1, 26), dt.date(year, 1, 26), "holiday", False),
        ("Independence Day", dt.date(year, 8, 15), dt.date(year, 8, 15), "holiday", False),
        ("Gandhi Jayanti", dt.date(year, 10, 2), dt.date(year, 10, 2), "holiday", False),
        ("Christmas", dt.date(year, 12, 25), dt.date(year, 12, 25), "holiday", False),
        ("Summer vacation", dt.date(year, 5, 1), dt.date(year, 5, 31), "vacation", True),
    ]
    existing = {(h.title, h.start_date) for h in (await db.scalars(
        select(Holiday).where(Holiday.start_date.between(dt.date(year, 1, 1), dt.date(year, 12, 31))))).all()}
    added = 0
    for title, start, end, kind, homework in presets:
        if (title, start) not in existing:
            db.add(Holiday(title=title, start_date=start, end_date=end, kind=kind, allow_homework=homework,
                           description="Added from the national holiday preset", created_by=admin.username))
            added += 1
    await db.commit()
    flash(request, f"Added {added} holiday(s) for {year}. Summer vacation (May) allows holiday homework. "
                   "Add festival holidays (Diwali, Holi, Eid …) for your state as needed.", "success")
    return redirect("/admin/calendar")


# ------------------------------------------------------------------ tasks
@router.get("/tasks", response_class=HTMLResponse)
async def tasks(request: Request, db: AsyncSession = Depends(get_db), batch: int = 0):
    stmt = select(Task).options(joinedload(Task.batch)).order_by(Task.due_date.desc())
    if batch:
        stmt = stmt.where(Task.batch_id == batch)
    rows = (await db.scalars(stmt.limit(200))).unique().all()
    submissions = dict((await db.execute(select(TaskSubmission.task_id, func.count())
                                         .group_by(TaskSubmission.task_id))).all())
    attempts = dict((await db.execute(select(TestAttempt.test_id, func.count())
                                      .where(TestAttempt.status != "in_progress")
                                      .group_by(TestAttempt.test_id))).all())
    sizes = dict((await db.execute(select(Enrollment.batch_id, func.count()).group_by(Enrollment.batch_id))).all())
    all_b = (await db.scalars(select(Batch).order_by(Batch.name))).all()
    return render(request, "admin/tasks/list.html", {"tasks": rows, "submissions": submissions,
                                                     "attempts": attempts, "sizes": sizes, "batches": all_b,
                                                     "batch": batch})


@router.get("/tasks/new", response_class=HTMLResponse)
async def task_new(request: Request, db: AsyncSession = Depends(get_db), batch: int = 0):
    all_b = (await db.scalars(select(Batch).where(Batch.is_active.is_(True)).order_by(Batch.name))).all()
    return render(request, "admin/tasks/form.html", {"batches": all_b, "batch": batch, "values": {}})


@router.post("/tasks/new")
async def task_create(request: Request, db: AsyncSession = Depends(get_db), admin: Principal = Depends(require_admin),
                      attachment: UploadFile | None = File(None)):
    form = await request.form()
    values = {k: v for k, v in form.items() if isinstance(v, str)}
    try:
        batch = await db.get(Batch, int_or(form.get("batch_id"), 0))
        if batch is None:
            raise FormError("Please choose a batch.")
        title = str(form.get("title") or "").strip()
        if not title:
            raise FormError("Please give the task a title.")
        kind = str(form.get("kind") or "assignment")
        kind = kind if kind in TASK_KINDS and kind != "test" else "assignment"
        assign = parse_date(str(form.get("assign_date")), "assignment date")
        due = parse_date(str(form.get("due_date")), "deadline")
        holiday_hw = truthy(str(form.get("is_holiday_homework", "")))
        errors = await validate_task_dates(db, assign, due, holiday_hw)
        if errors:
            raise FormError(" ".join(errors))
        marks = parse_money(str(form.get("max_marks")), "maximum marks") if form.get("max_marks") else None
        attachment_rel = None
        if has_file(attachment):
            attachment_rel, _ = await save_upload(attachment, "tasks", SUBMISSION_EXT)
    except (FormError, UploadError, ValueError) as exc:
        flash(request, str(exc), "danger")
        all_b = (await db.scalars(select(Batch).where(Batch.is_active.is_(True)).order_by(Batch.name))).all()
        return render(request, "admin/tasks/form.html", {"batches": all_b, "batch": 0, "values": values},
                      status_code=400)
    task = Task(batch_id=batch.id, kind=kind, title=title[:160],
                description=str(form.get("description") or "").strip() or None, assign_date=assign, due_date=due,
                is_holiday_homework=holiday_hw, max_marks=marks, attachment_path=attachment_rel,
                created_by=admin.username)
    db.add(task)
    await db.flush()
    label = "Holiday homework" if holiday_hw else TASK_KINDS[kind]
    student_ids = await batch_student_ids(db, [batch.id])
    await notify_users(db, student_ids, f"New {label.lower()}: {task.title}", f"Due {due:%d %b %Y}",
                       f"/student/tasks/{task.id}", "fa-list-check")
    await db.commit()
    if truthy(str(form.get("notify_email", ""))):
        people = await batch_students(db, [batch.id])
        queue_bulk([(u.email, {"name": u.full_name.split()[0], "task": task.title, "kind": label,
                               "due": f"{due:%A, %d %B %Y}", "batch": batch.name,
                               "link": f"{settings.BASE_URL}/student/tasks/{task.id}"}) for u in people],
                   f"New {label.lower()} for {batch.name}: {task.title}", "task_assigned.html", "task")
    flash(request, f"{label} “{task.title}” assigned to {batch.name} ({len(student_ids)} students).", "success")
    return redirect(f"/admin/tasks/{task.id}")


@router.get("/tasks/{tid}", response_class=HTMLResponse)
async def task_detail(tid: int, request: Request, db: AsyncSession = Depends(get_db)):
    task = await db.scalar(select(Task).options(joinedload(Task.batch)).where(Task.id == tid))
    if task is None:
        raise HTTPException(404)
    if task.test_id:
        return redirect(f"/admin/tests/{task.test_id}/results")
    students = await batch_students(db, [task.batch_id])
    subs = {s.user_id: s for s in (await db.scalars(select(TaskSubmission).where(TaskSubmission.task_id == tid))).all()}
    return render(request, "admin/tasks/detail.html", {"task": task, "students": students, "subs": subs})


@router.post("/tasks/{tid}/grade/{sid}")
async def task_grade(tid: int, sid: int, request: Request, db: AsyncSession = Depends(get_db),
                     marks: str = Form(""), feedback: str = Form("")):
    sub = await db.get(TaskSubmission, sid)
    task = await db.get(Task, tid)
    if sub is None or task is None or sub.task_id != tid:
        raise HTTPException(404)
    try:
        sub.marks = parse_money(marks, "marks", allow_zero=True) if marks else None
    except FormError as exc:
        flash(request, str(exc), "danger")
        return redirect(f"/admin/tasks/{tid}")
    if task.max_marks is not None and sub.marks is not None and sub.marks > task.max_marks:
        sub.marks = Decimal(task.max_marks)
    sub.feedback, sub.status, sub.graded_at = feedback.strip() or None, "graded", utcnow()
    await notify_users(db, [sub.user_id], f"“{task.title}” was graded", feedback.strip()[:120] or None,
                       f"/student/tasks/{tid}", "fa-circle-check")
    await db.commit()
    flash(request, "Submission graded.", "success")
    return redirect(f"/admin/tasks/{tid}")


@router.post("/tasks/{tid}/delete")
async def task_delete(tid: int, request: Request, db: AsyncSession = Depends(get_db)):
    task = await db.get(Task, tid)
    if task is not None:
        await db.delete(task)
        await db.commit()
    flash(request, "Task deleted.", "success")
    return redirect("/admin/tasks")
