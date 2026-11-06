"""Tests: AI generation (background job), question editor, publishing as a calendar task, results."""
from __future__ import annotations

import csv
import io
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from ...config import settings
from ...constants import DIFFICULTIES, QUESTION_KINDS
from ...database import get_db
from ...models import Batch, Question, Task, Test, TestAttempt
from ...security import Principal, require_admin
from ...services.academic_calendar import validate_task_dates
from ...services.ai import ai
from ...services.background import spawn
from ...services.mailer import queue_bulk
from ...services.moderation import mask
from ...services.notify import batch_students, notify_users
from ...services.testgen import generate_for_test
from ...templating import flash, render
from ..common import FormError, csv_row, int_or, parse_date, redirect, truthy

router = APIRouter()


@router.get("/tests", response_class=HTMLResponse)
async def tests(request: Request, db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(Test).options(joinedload(Test.batch)).order_by(Test.id.desc()).limit(200))).unique().all()
    qcounts = dict((await db.execute(select(Question.test_id, func.count()).group_by(Question.test_id))).all())
    acounts = dict((await db.execute(select(TestAttempt.test_id, func.count())
                                     .where(TestAttempt.status != "in_progress").group_by(TestAttempt.test_id))).all())
    return render(request, "admin/tests/list.html", {"tests": rows, "qcounts": qcounts, "acounts": acounts})


@router.get("/tests/generate", response_class=HTMLResponse)
async def generate_form(request: Request, db: AsyncSession = Depends(get_db), batch: int = 0):
    batches = (await db.scalars(select(Batch).where(Batch.is_active.is_(True)).order_by(Batch.name))).all()
    return render(request, "admin/tests/generate.html", {"batches": batches, "batch": batch,
                                                         "difficulties": DIFFICULTIES, "ai_online": ai.online})


@router.post("/tests/generate")
async def generate(request: Request, db: AsyncSession = Depends(get_db), admin: Principal = Depends(require_admin),
                   batch_id: int = Form(...), title: str = Form(""), topic: str = Form(...),
                   difficulty: str = Form("medium"), count: int = Form(10), duration_minutes: int = Form(30),
                   use_kb: str = Form(""), extra: str = Form(""), mode: str = Form("ai")):
    form = await request.form()
    kinds = [k for k in form.getlist("kinds") if k in QUESTION_KINDS] or ["mcq"]
    batch = await db.get(Batch, batch_id)
    if batch is None or not topic.strip():
        flash(request, "Choose a batch and enter a topic.", "danger")
        return redirect("/admin/tests/generate")
    test = Test(batch_id=batch.id, title=(title.strip() or f"{batch.subject}: {topic.strip()}")[:160],
                subject=batch.subject, class_level=batch.class_level, topic=topic.strip()[:200],
                difficulty=difficulty if difficulty in DIFFICULTIES else "medium",
                duration_minutes=max(5, min(240, duration_minutes)),
                instructions="Read each question carefully. Answer all questions. The timer starts when you begin.",
                status="generating" if mode == "ai" else "draft", created_by=admin.username)
    db.add(test)
    await db.commit()
    if mode == "ai":
        spawn(generate_for_test(test.id, count=max(1, min(30, count)), kinds=kinds, use_kb=truthy(use_kb),
                                extra=extra.strip()[:600]), name=f"testgen:{test.id}")
        flash(request, "The AI is drafting your questions using the batch's class, subject and notes…", "info")
    return redirect(f"/admin/tests/{test.id}")


@router.get("/tests/{tid}/status")
async def test_status(tid: int, db: AsyncSession = Depends(get_db)):
    test = await db.get(Test, tid)
    if test is None:
        raise HTTPException(404)
    n = await db.scalar(select(func.count()).select_from(Question).where(Question.test_id == tid))
    return {"status": test.status, "questions": n, "note": test.generation_note}


@router.get("/tests/{tid}", response_class=HTMLResponse)
async def editor(tid: int, request: Request, db: AsyncSession = Depends(get_db)):
    test = await db.scalar(select(Test).options(selectinload(Test.questions), joinedload(Test.batch))
                           .where(Test.id == tid))
    if test is None:
        raise HTTPException(404)
    task = await db.scalar(select(Task).where(Task.test_id == tid).limit(1))
    attempts = int(await db.scalar(select(func.count()).select_from(TestAttempt).where(TestAttempt.test_id == tid)) or 0)
    questions = [{"id": q.id, "kind": q.kind, "text": q.text, "options": q.options or [], "answer": q.answer,
                  "explanation": q.explanation or "", "marks": float(q.marks)} for q in test.questions]
    return render(request, "admin/tests/editor.html", {"test": test, "task": task, "attempts": attempts,
                                                       "questions": questions, "kinds": QUESTION_KINDS})


class QuestionIn(BaseModel):
    kind: str
    text: str = Field(max_length=2000)
    options: list[str] = []
    answer: str = ""
    explanation: str = ""
    marks: float = 1


class TestSaveIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    instructions: str = ""
    duration_minutes: int = 30
    show_results: bool = True
    questions: list[QuestionIn]


def check_question(i: int, q: QuestionIn) -> dict:
    n = i + 1
    if q.kind not in QUESTION_KINDS:
        raise FormError(f"Question {n}: unknown type.")
    text = q.text.strip()
    if not text:
        raise FormError(f"Question {n} has no text.")
    marks = max(0.5, min(20.0, float(q.marks or 1)))
    out = {"kind": q.kind, "text": mask(text), "options": [], "answer": q.answer.strip(),
           "explanation": q.explanation.strip()[:1500] or None, "marks": Decimal(str(round(marks * 2) / 2))}
    if q.kind == "mcq":
        options = [o.strip()[:300] for o in q.options if o.strip()]
        if not 2 <= len(options) <= 6:
            raise FormError(f"Question {n}: multiple-choice questions need 2-6 options.")
        if int_or(out["answer"]) is None or int_or(out["answer"]) >= len(options):
            raise FormError(f"Question {n}: mark the correct option.")
        out["options"] = [mask(o) for o in options]
    elif q.kind == "true_false":
        if out["answer"] not in ("true", "false"):
            raise FormError(f"Question {n}: choose True or False as the answer.")
        out["options"] = ["True", "False"]
    else:
        out["answer"] = out["answer"][:2000]
    return out


@router.post("/tests/{tid}/save")
async def save(tid: int, payload: TestSaveIn, db: AsyncSession = Depends(get_db)):
    test = await db.get(Test, tid)
    if test is None:
        raise HTTPException(404)
    if test.status == "generating":
        return JSONResponse({"error": "The AI is still generating this test."}, status_code=409)
    attempts = await db.scalar(select(func.count()).select_from(TestAttempt).where(TestAttempt.test_id == tid))
    if attempts:
        return JSONResponse({"error": "Students have already attempted this test, so its questions are locked."},
                            status_code=409)
    try:
        cleaned = [check_question(i, q) for i, q in enumerate(payload.questions[:100])]
    except FormError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    await db.execute(delete(Question).where(Question.test_id == tid))
    total = Decimal("0")
    for pos, q in enumerate(cleaned):
        total += q["marks"]
        db.add(Question(test_id=tid, position=pos, **q))
    test.title = payload.title.strip()[:160]
    test.instructions = payload.instructions.strip()[:3000] or None
    test.duration_minutes = max(5, min(240, payload.duration_minutes))
    test.show_results = payload.show_results
    test.total_marks = total
    if test.status == "failed":
        test.status = "draft"
    await db.commit()
    return {"ok": True, "count": len(cleaned), "total_marks": float(total)}


@router.post("/tests/{tid}/publish")
async def publish(tid: int, request: Request, db: AsyncSession = Depends(get_db),
                  admin: Principal = Depends(require_admin), assign_date: str = Form(...), due_date: str = Form(...),
                  is_holiday_homework: str = Form(""), notify_email: str = Form("")):
    test = await db.scalar(select(Test).options(joinedload(Test.batch)).where(Test.id == tid))
    if test is None:
        raise HTTPException(404)
    n = await db.scalar(select(func.count()).select_from(Question).where(Question.test_id == tid))
    if not n:
        flash(request, "Add at least one question before publishing.", "danger")
        return redirect(f"/admin/tests/{tid}")
    if await db.scalar(select(Task.id).where(Task.test_id == tid)):
        flash(request, "This test is already published.", "info")
        return redirect(f"/admin/tests/{tid}")
    try:
        assign = parse_date(assign_date, "assignment date")
        due = parse_date(due_date, "deadline")
        hw = truthy(is_holiday_homework)
        errors = await validate_task_dates(db, assign, due, hw)
        if errors:
            raise FormError(" ".join(errors))
    except FormError as exc:
        flash(request, str(exc), "danger")
        return redirect(f"/admin/tests/{tid}#publish")
    task = Task(batch_id=test.batch_id, kind="test", title=test.title, description=test.instructions,
                assign_date=assign, due_date=due, is_holiday_homework=hw, test_id=test.id,
                max_marks=test.total_marks, created_by=admin.username)
    db.add(task)
    test.status = "published"
    await db.flush()
    students = await batch_students(db, [test.batch_id])
    await notify_users(db, [s.id for s in students], f"New test: {test.title}",
                       f"Open {assign:%d %b} – due {due:%d %b} · {test.duration_minutes} min",
                       f"/student/tasks/{task.id}", "fa-file-pen")
    await db.commit()
    if truthy(notify_email):
        queue_bulk([(s.email, {"name": s.full_name.split()[0], "task": test.title, "kind": "Test",
                               "due": f"{due:%A, %d %B %Y}", "batch": test.batch.name,
                               "link": f"{settings.BASE_URL}/student/tasks/{task.id}"}) for s in students],
                   f"New test for {test.batch.name}: {test.title}", "task_assigned.html", "task")
    flash(request, f"Published to {test.batch.name}. Students will find it on their calendar.", "success")
    return redirect(f"/admin/tests/{tid}")


@router.get("/tests/{tid}/results", response_class=HTMLResponse)
async def results(tid: int, request: Request, db: AsyncSession = Depends(get_db)):
    test = await db.scalar(select(Test).options(selectinload(Test.questions), joinedload(Test.batch))
                           .where(Test.id == tid))
    if test is None:
        raise HTTPException(404)
    attempts = (await db.scalars(select(TestAttempt).options(joinedload(TestAttempt.user))
                                 .where(TestAttempt.test_id == tid).order_by(TestAttempt.score.desc().nulls_last()))
                ).unique().all()
    attempted = {a.user_id for a in attempts}
    missing = [s for s in await batch_students(db, [test.batch_id]) if s.id not in attempted]
    stats = []
    for q in test.questions:
        answered = [a.per_question.get(str(q.id)) for a in attempts if a.per_question]
        answered = [x for x in answered if x]
        correct = sum(1 for x in answered if x.get("correct"))
        stats.append({"q": q, "answered": len(answered), "correct": correct,
                      "rate": round(correct * 100 / len(answered)) if answered else None})
    task = await db.scalar(select(Task).where(Task.test_id == tid).limit(1))
    return render(request, "admin/tests/results.html", {"test": test, "attempts": attempts, "missing": missing,
                                                        "stats": stats, "task": task})


@router.post("/attempts/{aid}/marks")
async def override_marks(aid: int, request: Request, db: AsyncSession = Depends(get_db),
                         admin: Principal = Depends(require_admin), score: str = Form(...), note: str = Form("")):
    attempt = await db.get(TestAttempt, aid)
    if attempt is None:
        raise HTTPException(404)
    try:
        value = Decimal(score)
    except Exception:
        value = Decimal("-1")
    if value < 0 or (attempt.max_score is not None and value > attempt.max_score):
        flash(request, "The score must be between 0 and the maximum marks.", "danger")
        return redirect(f"/admin/tests/{attempt.test_id}/results")
    attempt.score, attempt.status = value, "graded"
    attempt.grading_note = f"Score reviewed by {admin.username}." + (f" {note.strip()}" if note.strip() else "")
    await notify_users(db, [attempt.user_id], "Your test score was reviewed", attempt.grading_note,
                       f"/student/attempts/{aid}", "fa-user-pen")
    await db.commit()
    flash(request, "Score updated.", "success")
    return redirect(f"/admin/tests/{attempt.test_id}/results")


@router.get("/tests/{tid}/export.csv")
async def export(tid: int, db: AsyncSession = Depends(get_db)):
    test = await db.get(Test, tid)
    if test is None:
        raise HTTPException(404)
    rows = (await db.scalars(select(TestAttempt).options(joinedload(TestAttempt.user))
                             .where(TestAttempt.test_id == tid))).unique().all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["ID", "Student", "Status", "Score", "Max", "Percent", "Submitted"])
    for a in rows:
        writer.writerow(csv_row([a.user.id_code, a.user.full_name, a.status, a.score, a.max_score, a.percent,
                                 a.submitted_at.isoformat() if a.submitted_at else ""]))
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="test-{tid}-results.csv"'})


@router.post("/tests/{tid}/delete")
async def delete_test(tid: int, request: Request, db: AsyncSession = Depends(get_db)):
    test = await db.get(Test, tid)
    if test is not None:
        await db.execute(delete(Task).where(Task.test_id == tid))
        await db.delete(test)
        await db.commit()
    flash(request, "Test deleted.", "success")
    return redirect("/admin/tests")
