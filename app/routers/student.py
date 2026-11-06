"""Student portal: dashboard, calendar, tasks & tests, classes (live / recorded) with AI notes and
the AI doubt solver, fees and profile (virtual ID card)."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from ..config import settings
from ..database import get_db
from ..models import (Attendance, Batch, DoubtMessage, Enrollment, FeePayment, Lesson, Meeting, Notification, Task,
                      TaskSubmission, Test, TestAttempt, User, Video, VideoProgress, utcnow)
from ..security import Principal, csrf_protect, require_student
from ..services import cashfree
from ..services.background import spawn
from ..services.mailer import queue_admin_alert
from ..services.moderation import mask
from ..services.notify import notify_users
from ..services.payments import new_order_id, sync_cashfree_order
from ..services.storage import SUBMISSION_EXT, UploadError, delete_file, has_file, save_image, save_upload
from ..services.testgen import grade_attempt
from ..templating import flash, format_inr, render
from .comments import comment_context
from .common import redirect, today

router = APIRouter(prefix="/student", dependencies=[Depends(require_student), Depends(csrf_protect)])
PROOF_METHODS = ("UPI", "Bank transfer", "Cash deposit", "Cheque")


async def my_batch_ids(db: AsyncSession, uid: int) -> list[int]:
    return list((await db.scalars(select(Enrollment.batch_id).join(Batch, Batch.id == Enrollment.batch_id)
                                  .where(Enrollment.user_id == uid))).all())


async def _task_for_student(db: AsyncSession, tid: int, me: Principal) -> Task:
    task = await db.scalar(select(Task).options(joinedload(Task.batch), joinedload(Task.test)).where(Task.id == tid))
    if task is None or task.batch_id not in await my_batch_ids(db, me.id):
        raise HTTPException(404, "Task not found")
    return task


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_student)):
    ids = await my_batch_ids(db, me.id)
    day = today()
    batches = (await db.scalars(select(Batch).options(joinedload(Batch.teacher)).where(Batch.id.in_(ids))
                                .order_by(Batch.start_time))).unique().all()
    opts = (joinedload(Lesson.batch), joinedload(Lesson.meeting), joinedload(Lesson.video))
    lessons = (await db.scalars(select(Lesson).options(*opts).where(Lesson.batch_id.in_(ids), Lesson.date == day,
                                                                    Lesson.status != "cancelled")
                                .order_by(Lesson.start_time))).unique().all()
    live = (await db.scalars(select(Meeting).options(joinedload(Meeting.batch))
                             .where(Meeting.batch_id.in_(ids), Meeting.status == "live"))).unique().all()
    tasks = (await db.scalars(select(Task).options(joinedload(Task.batch))
                              .where(Task.batch_id.in_(ids), Task.assign_date <= day, Task.due_date >= day)
                              .order_by(Task.due_date).limit(8))).unique().all()
    submitted = set((await db.scalars(select(TaskSubmission.task_id).where(TaskSubmission.user_id == me.id))).all())
    attempted = set((await db.scalars(select(TestAttempt.test_id).where(TestAttempt.user_id == me.id,
                                                                        TestAttempt.status != "in_progress"))).all())
    results = (await db.scalars(select(TestAttempt).options(joinedload(TestAttempt.test))
                                .where(TestAttempt.user_id == me.id, TestAttempt.status == "graded")
                                .order_by(TestAttempt.submitted_at.desc()).limit(5))).unique().all()
    attendance = dict((await db.execute(select(Attendance.status, func.count()).where(Attendance.user_id == me.id)
                                        .group_by(Attendance.status))).all())
    fees_due = await db.scalar(select(func.coalesce(func.sum(FeePayment.amount), 0)).where(
        FeePayment.user_id == me.id, FeePayment.status.in_(("pending", "overdue"))))
    notes = (await db.scalars(select(Notification).where(Notification.user_id == me.id)
                              .order_by(Notification.id.desc()).limit(6))).all()
    return render(request, "student/dashboard.html", {
        "batches": batches, "lessons": lessons, "live": live, "tasks": tasks, "submitted": submitted,
        "attempted": attempted, "results": results, "attendance": attendance, "fees_due": fees_due,
        "notifications": notes})


@router.get("/calendar", response_class=HTMLResponse)
async def calendar(request: Request):
    return render(request, "shared/calendar.html", {"title": "My calendar"})


# ------------------------------------------------------------------ tasks
@router.get("/tasks", response_class=HTMLResponse)
async def tasks(request: Request, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_student)):
    ids = await my_batch_ids(db, me.id)
    rows = (await db.scalars(select(Task).options(joinedload(Task.batch), joinedload(Task.test))
                             .where(Task.batch_id.in_(ids), Task.assign_date <= today() + dt.timedelta(days=30))
                             .order_by(Task.due_date.desc()).limit(200))).unique().all()
    subs = {s.task_id: s for s in (await db.scalars(select(TaskSubmission).where(TaskSubmission.user_id == me.id))).all()}
    attempts = {a.test_id: a for a in (await db.scalars(select(TestAttempt).where(TestAttempt.user_id == me.id))).all()}
    return render(request, "student/tasks.html", {"tasks": rows, "subs": subs, "attempts": attempts})


@router.get("/tasks/{tid}", response_class=HTMLResponse)
async def task_detail(tid: int, request: Request, db: AsyncSession = Depends(get_db),
                      me: Principal = Depends(require_student)):
    task = await _task_for_student(db, tid, me)
    sub = await db.scalar(select(TaskSubmission).where(TaskSubmission.task_id == tid, TaskSubmission.user_id == me.id))
    attempt = await db.scalar(select(TestAttempt).where(TestAttempt.test_id == task.test_id,
                                                        TestAttempt.user_id == me.id)) if task.test_id else None
    questions = await db.scalar(select(func.count()).select_from(Test).where(Test.id == task.test_id)) \
        if task.test_id else 0
    return render(request, "student/task_detail.html", {"task": task, "sub": sub, "attempt": attempt,
                                                        "has_test": bool(questions), "day": today()})


@router.post("/tasks/{tid}/submit")
async def task_submit(tid: int, request: Request, db: AsyncSession = Depends(get_db),
                      me: Principal = Depends(require_student), text: str = Form(""),
                      file: UploadFile | None = File(None)):
    task = await _task_for_student(db, tid, me)
    if task.test_id:
        raise HTTPException(400)
    if task.assign_date > today():
        flash(request, f"This task opens on {task.assign_date:%d %b}.", "warning")
        return redirect(f"/student/tasks/{tid}")
    sub = await db.scalar(select(TaskSubmission).where(TaskSubmission.task_id == tid, TaskSubmission.user_id == me.id))
    if sub is not None and sub.status == "graded":
        flash(request, "This task has already been graded.", "info")
        return redirect(f"/student/tasks/{tid}")
    if not text.strip() and not has_file(file):
        flash(request, "Write your answer or attach a file.", "danger")
        return redirect(f"/student/tasks/{tid}")
    try:
        rel = (await save_upload(file, "submissions", SUBMISSION_EXT))[0] if has_file(file) else None
    except UploadError as exc:
        flash(request, str(exc), "danger")
        return redirect(f"/student/tasks/{tid}")
    late = today() > task.due_date
    if sub is None:
        sub = TaskSubmission(task_id=tid, user_id=me.id)
        db.add(sub)
    elif rel:
        delete_file(sub.file_path)
    sub.text = mask(text.strip()) or sub.text
    sub.file_path = rel or sub.file_path
    sub.is_late, sub.submitted_at, sub.status = late, utcnow(), "submitted"
    if task.batch.teacher_id:
        await notify_users(db, [task.batch.teacher_id], f"New submission: {task.title}", me.name,
                           f"/staff/batches/{task.batch_id}", "fa-inbox")
    try:
        await db.commit()
    except IntegrityError:  # double-click race: the first submission wins
        await db.rollback()
    flash(request, "Submitted! ✅" + (" (marked late)" if late else ""), "success")
    return redirect(f"/student/tasks/{tid}")


# ------------------------------------------------------------------ tests
@router.post("/tests/{test_id}/start")
async def test_start(test_id: int, request: Request, db: AsyncSession = Depends(get_db),
                     me: Principal = Depends(require_student)):
    task = await db.scalar(select(Task).options(joinedload(Task.test)).where(Task.test_id == test_id))
    if task is None or task.batch_id not in await my_batch_ids(db, me.id) or task.test.status != "published":
        raise HTTPException(404)
    existing = await db.scalar(select(TestAttempt).where(TestAttempt.test_id == test_id, TestAttempt.user_id == me.id))
    if existing is not None:
        return redirect(f"/student/attempts/{existing.id}")
    day = today()
    if not task.assign_date <= day <= task.due_date:
        flash(request, "This test is not open right now." if day < task.assign_date else "The deadline has passed.",
              "warning")
        return redirect(f"/student/tasks/{task.id}")
    attempt = TestAttempt(test_id=test_id, user_id=me.id, task_id=task.id,
                          deadline_at=utcnow() + dt.timedelta(minutes=task.test.duration_minutes), answers={})
    db.add(attempt)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        attempt = await db.scalar(select(TestAttempt).where(TestAttempt.test_id == test_id,
                                                            TestAttempt.user_id == me.id))
    return redirect(f"/student/attempts/{attempt.id}")


async def _attempt(db: AsyncSession, aid: int, me: Principal) -> TestAttempt:
    attempt = await db.scalar(select(TestAttempt).options(joinedload(TestAttempt.test).selectinload(Test.questions))
                              .where(TestAttempt.id == aid, TestAttempt.user_id == me.id))
    if attempt is None:
        raise HTTPException(404)
    return attempt


def _expired(attempt: TestAttempt) -> bool:
    return utcnow() > attempt.deadline_at + dt.timedelta(seconds=settings.TEST_GRACE_SECONDS)


async def _finalize(db: AsyncSession, attempt: TestAttempt) -> None:
    if attempt.status != "in_progress":
        return
    attempt.status, attempt.submitted_at = "submitted", min(utcnow(), attempt.deadline_at + dt.timedelta(
        seconds=settings.TEST_GRACE_SECONDS))
    await db.commit()
    spawn(grade_attempt(attempt.id), name=f"grade:{attempt.id}")


@router.get("/attempts/{aid}", response_class=HTMLResponse)
async def attempt_page(aid: int, request: Request, db: AsyncSession = Depends(get_db),
                       me: Principal = Depends(require_student)):
    attempt = await _attempt(db, aid, me)
    if attempt.status == "in_progress" and _expired(attempt):
        await _finalize(db, attempt)
    test = attempt.test
    if attempt.status == "in_progress":
        questions = [{"id": q.id, "kind": q.kind, "text": q.text, "options": q.options or [],
                      "marks": float(q.marks)} for q in test.questions]
        remaining = max(0, int((attempt.deadline_at - utcnow()).total_seconds()))
        return render(request, "student/test_take.html", {"attempt": attempt, "test": test, "questions": questions,
                                                          "remaining": remaining})
    return render(request, "student/test_result.html", {"attempt": attempt, "test": test})


class AnswersIn(BaseModel):
    answers: dict[str, str] = {}


def _clean_answers(attempt: TestAttempt, answers: dict[str, str]) -> dict[str, str]:
    valid = {str(q.id) for q in attempt.test.questions}
    return {k: str(v)[:3000] for k, v in answers.items() if k in valid}


@router.post("/attempts/{aid}/save")
async def attempt_save(aid: int, payload: AnswersIn, db: AsyncSession = Depends(get_db),
                       me: Principal = Depends(require_student)):
    attempt = await _attempt(db, aid, me)
    if attempt.status != "in_progress":
        return JSONResponse({"ok": False, "status": attempt.status}, status_code=409)
    if _expired(attempt):
        await _finalize(db, attempt)
        return JSONResponse({"ok": False, "status": "submitted", "expired": True}, status_code=409)
    attempt.answers = {**(attempt.answers or {}), **_clean_answers(attempt, payload.answers)}
    await db.commit()
    return {"ok": True, "saved": len(attempt.answers)}


@router.post("/attempts/{aid}/submit")
async def attempt_submit(aid: int, payload: AnswersIn, db: AsyncSession = Depends(get_db),
                         me: Principal = Depends(require_student)):
    attempt = await _attempt(db, aid, me)
    if attempt.status == "in_progress":
        if not _expired(attempt):
            attempt.answers = {**(attempt.answers or {}), **_clean_answers(attempt, payload.answers)}
        await _finalize(db, attempt)
    return {"ok": True, "redirect": f"/student/attempts/{aid}"}


@router.get("/attempts/{aid}/status")
async def attempt_status(aid: int, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_student)):
    attempt = await db.scalar(select(TestAttempt).where(TestAttempt.id == aid, TestAttempt.user_id == me.id))
    if attempt is None:
        raise HTTPException(404)
    return {"status": attempt.status}


# ------------------------------------------------------------------ classes, AI tutor
@router.get("/classes", response_class=HTMLResponse)
async def classes(request: Request, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_student)):
    ids = await my_batch_ids(db, me.id)
    day = today()
    opts = (joinedload(Lesson.batch), joinedload(Lesson.meeting), joinedload(Lesson.video))
    upcoming = (await db.scalars(select(Lesson).options(*opts).where(Lesson.batch_id.in_(ids), Lesson.date >= day,
                                                                     Lesson.status != "cancelled")
                                 .order_by(Lesson.date, Lesson.start_time).limit(30))).unique().all()
    past = (await db.scalars(select(Lesson).options(*opts).where(Lesson.batch_id.in_(ids), Lesson.date < day)
                             .order_by(Lesson.date.desc()).limit(40))).unique().all()
    marks = {a.lesson_id: a.status for a in (await db.scalars(select(Attendance).where(Attendance.user_id == me.id))).all()}
    progress = {p.video_id: p for p in (await db.scalars(select(VideoProgress).where(VideoProgress.user_id == me.id))).all()}
    return render(request, "student/classes.html", {"upcoming": upcoming, "past": past, "marks": marks,
                                                    "progress": progress})


@router.get("/lessons/{lid}", response_class=HTMLResponse)
async def lesson(lid: int, request: Request, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_student)):
    item = await db.scalar(select(Lesson).options(joinedload(Lesson.batch), joinedload(Lesson.meeting),
                                                  joinedload(Lesson.video)).where(Lesson.id == lid))
    if item is None or item.batch_id not in await my_batch_ids(db, me.id):
        raise HTTPException(404)
    mark = await db.scalar(select(Attendance).where(Attendance.lesson_id == lid, Attendance.user_id == me.id))
    progress = await db.scalar(select(VideoProgress).where(VideoProgress.user_id == me.id,
                                                           VideoProgress.video_id == item.video_id)) \
        if item.video_id else None
    history = (await db.scalars(select(DoubtMessage).where(DoubtMessage.user_id == me.id,
                                                           DoubtMessage.lesson_id == lid)
                                .order_by(DoubtMessage.id.desc()).limit(20))).all()
    return render(request, "student/lesson.html", {"lesson": item, "mark": mark, "progress": progress,
                                                   "history": list(reversed(history)),
                                                   "threshold": int(settings.VIDEO_ATTENDANCE_RATIO * 100),
                                                   **await comment_context(db, me, item.video)})


@router.get("/tutor", response_class=HTMLResponse)
async def tutor(request: Request, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_student),
                batch: int = 0):
    ids = await my_batch_ids(db, me.id)
    batches = (await db.scalars(select(Batch).where(Batch.id.in_(ids)).order_by(Batch.name))).all()
    selected = next((b for b in batches if b.id == batch), batches[0] if batches else None)
    history = []
    if selected:
        history = (await db.scalars(select(DoubtMessage).where(DoubtMessage.user_id == me.id,
                                                               DoubtMessage.batch_id == selected.id,
                                                               DoubtMessage.lesson_id.is_(None))
                                    .order_by(DoubtMessage.id.desc()).limit(30))).all()
    return render(request, "student/tutor.html", {"batches": batches, "selected": selected,
                                                  "history": list(reversed(history))})


# ------------------------------------------------------------------ fees & profile
@router.get("/fees", response_class=HTMLResponse)
async def fees(request: Request, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_student)):
    rows = (await db.scalars(select(FeePayment).options(joinedload(FeePayment.batch))
                             .where(FeePayment.user_id == me.id).order_by(FeePayment.period.desc()))).unique().all()
    due = sum(float(f.amount) for f in rows if f.status in ("pending", "overdue"))
    paid = sum(float(f.amount) for f in rows if f.status == "paid")
    verifying = sum(float(f.amount) for f in rows if f.status == "verifying")
    return render(request, "student/fees.html", {"fees": rows, "due": due, "paid": paid, "verifying": verifying,
                                                 "cashfree": settings.cashfree_pg_active,
                                                 "methods": list(PROOF_METHODS)})


@router.get("/fees/statement", response_class=HTMLResponse)
async def statement(request: Request, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_student)):
    rows = (await db.scalars(select(FeePayment).options(joinedload(FeePayment.batch))
                             .where(FeePayment.user_id == me.id)
                             .order_by(FeePayment.period, FeePayment.id))).unique().all()
    return render(request, "finance/statement.html", {"fees": rows, "member": me.user})


async def _own_due_fee(db: AsyncSession, fid: int, me: Principal) -> FeePayment:
    fee = await db.scalar(select(FeePayment).where(FeePayment.id == fid, FeePayment.user_id == me.id))
    if fee is None:
        raise HTTPException(404)
    return fee


@router.post("/fees/{fid}/pay-online", response_class=HTMLResponse)
async def pay_online(fid: int, request: Request, db: AsyncSession = Depends(get_db),
                     me: Principal = Depends(require_student)):
    if not settings.cashfree_pg_active:
        raise HTTPException(404)
    fee = await _own_due_fee(db, fid, me)
    if not fee.is_due:
        flash(request, "This fee is not due.", "info")
        return redirect("/student/fees")
    user = me.user
    session_id = None
    try:
        if fee.gateway_order_id:  # reuse a still-active order (avoids duplicate payments)
            existing = await cashfree.get_order(fee.gateway_order_id)
            if existing.get("order_status") == "PAID":
                await sync_cashfree_order(db, fee.gateway_order_id)
                flash(request, "This fee was already paid online. ✅", "success")
                return redirect("/student/fees")
            if existing.get("order_status") == "ACTIVE":
                session_id = existing.get("payment_session_id")
        if not session_id:
            order_id = new_order_id(fee)
            order = await cashfree.create_order(
                order_id=order_id, amount=fee.amount,
                customer={"customer_id": f"STU{user.id}", "customer_name": user.full_name[:100],
                          "customer_email": user.email, "customer_phone": cashfree.phone10(user.phone or
                                                                                          user.parent_phone)},
                return_url=f"{settings.BASE_URL}/student/fees/cashfree/return?order_id={order_id}",
                notify_url=f"{settings.BASE_URL}/webhooks/cashfree", note=f"{fee.title} {fee.period}")
            fee.gateway, fee.gateway_order_id = "cashfree", order_id
            await db.commit()
            session_id = order.get("payment_session_id")
    except cashfree.CashfreeError as exc:
        flash(request, f"Online payment is unavailable right now ({exc}). Please try again later or pay at the "
                       "office.", "warning")
        return redirect("/student/fees")
    if not session_id:
        flash(request, "Cashfree did not return a payment session. Please try again.", "danger")
        return redirect("/student/fees")
    return render(request, "student/pay_redirect.html", {"session_id": session_id, "fee": fee,
                                                         "mode": "production" if settings.CASHFREE_ENV == "production"
                                                         else "sandbox"})


@router.get("/fees/cashfree/return")
async def cashfree_return(request: Request, order_id: str, db: AsyncSession = Depends(get_db),
                          me: Principal = Depends(require_student)):
    fee = await db.scalar(select(FeePayment).where(FeePayment.gateway_order_id == order_id,
                                                   FeePayment.user_id == me.id))
    if fee is None:
        raise HTTPException(404)
    try:
        fee = await sync_cashfree_order(db, order_id)
    except cashfree.CashfreeError as exc:
        flash(request, f"We couldn't confirm the payment yet ({exc}). It will update automatically once "
                       "Cashfree confirms it.", "warning")
        return redirect("/student/fees")
    if fee is not None and fee.status == "paid":
        flash(request, f"Payment successful! Receipt {fee.receipt_no} was emailed to you. 🎉", "success")
    else:
        flash(request, "The payment was not completed. You can try again.", "warning")
    return redirect("/student/fees")


@router.post("/fees/{fid}/proof")
async def submit_proof(fid: int, request: Request, db: AsyncSession = Depends(get_db),
                       me: Principal = Depends(require_student), method: str = Form("UPI"),
                       reference: str = Form(""), paid_on: str = Form(""), screenshot: UploadFile | None = File(None)):
    """Only used when Cashfree is NOT set up: the payment screenshot is mandatory proof."""
    if settings.cashfree_pg_active:
        flash(request, "Please pay online — proofs are not needed when online payment is available.", "info")
        return redirect("/student/fees")
    fee = await _own_due_fee(db, fid, me)
    if fee.status not in ("pending", "overdue", "verifying"):
        flash(request, "This fee is not awaiting payment.", "info")
        return redirect("/student/fees")
    if method not in PROOF_METHODS:  # the form offers a fixed list; anything else is tampering
        flash(request, "Please choose how you paid.", "danger")
        return redirect("/student/fees")
    if not has_file(screenshot):
        flash(request, "Please attach the payment screenshot — it is required as proof.", "danger")
        return redirect("/student/fees")
    try:
        rel = await save_image(screenshot, "receipts", 1800)
        day = dt.date.fromisoformat(paid_on) if paid_on else today()
    except (UploadError, ValueError) as exc:
        flash(request, str(exc) if isinstance(exc, UploadError) else "Invalid payment date.", "danger")
        return redirect("/student/fees")
    delete_file(fee.proof_path)
    fee.proof_path, fee.proof_reference = rel, reference.strip()[:80] or None
    fee.method, fee.paid_on, fee.proof_submitted_at = method[:20], day, utcnow()
    fee.status, fee.review_note = "verifying", None
    await db.commit()
    queue_admin_alert(f"Payment proof to verify: {me.name} — {format_inr(fee.amount)}", "payment_proof.html",
                      category="fees", student=me.name, fee_title=fee.title, period=fee.period,
                      amount=format_inr(fee.amount), method=fee.method, reference=fee.proof_reference or "—",
                      link=f"{settings.BASE_URL}/admin/fees?status=verifying")
    flash(request, "Thank you! Your payment proof was submitted. The office will verify it and email your receipt.",
          "success")
    return redirect("/student/fees")


@router.get("/fees/{fid}/receipt", response_class=HTMLResponse)
async def receipt(fid: int, request: Request, db: AsyncSession = Depends(get_db),
                  me: Principal = Depends(require_student)):
    fee = await db.scalar(select(FeePayment).options(joinedload(FeePayment.user), joinedload(FeePayment.batch))
                          .where(FeePayment.id == fid, FeePayment.user_id == me.id, FeePayment.status == "paid"))
    if fee is None:
        raise HTTPException(404)
    return render(request, "finance/receipt.html", {"fee": fee})


@router.get("/profile", response_class=HTMLResponse)
async def profile(request: Request, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_student)):
    user = await db.scalar(select(User).options(selectinload(User.enrollments).joinedload(Enrollment.batch),
                                                selectinload(User.health_records)).where(User.id == me.id))
    return render(request, "shared/profile.html", {"member": user})


__all__ = ["router", "Video"]
