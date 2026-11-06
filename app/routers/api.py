"""JSON / streaming API: calendar feed, video watch tracking (auto attendance), notifications and the
AI doubt solver (RAG over the class's notes, streamed; graceful fallback when the AI is offline)."""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import math

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from ..cache import site_cache
from ..config import settings
from ..constants import SUPERVISOR_ROLES, TEACHING_ROLES
from ..database import SessionLocal, get_db
from ..models import (PTM, Attendance, Batch, DoubtMessage, Enrollment, Lesson, Meeting, MeetingMessage, Notification,
                      PTMInvite, Task, Video, VideoProgress, utcnow)
from ..security import Principal, client_ip, csrf_protect, limiter, require_member, require_student, require_user
from ..services import rag
from ..services.academic_calendar import day_info, holidays_between
from ..services.ai import GUARD, AIDeclined, AIUnavailable, ai, tidy_answer, tone_for
from ..services.moderation import mask, moderate
from ..services.notify import notify_users
from ..services.ocr import OCRUnavailable, ocr_image
from ..services.storage import UploadError, decode_frame, save_bytes
from ..templating import markdown_html, render

router = APIRouter(dependencies=[Depends(csrf_protect)])


async def scope_batch_ids(db: AsyncSession, p: Principal) -> list[int] | None:
    """None = all batches (admin / supervisors)."""
    if p.kind == "admin" or (p.kind == "staff" and p.staff_role in SUPERVISOR_ROLES):
        return None
    if p.kind == "student":
        return list((await db.scalars(select(Enrollment.batch_id).where(Enrollment.user_id == p.id))).all())
    if p.kind == "staff" and p.staff_role in TEACHING_ROLES:
        return list((await db.scalars(select(Batch.id).where(Batch.teacher_id == p.id))).all())
    return []


@router.get("/api/calendar/events")
async def calendar_events(start: str, end: str, db: AsyncSession = Depends(get_db),
                          me: Principal = Depends(require_user)):
    try:
        first, last = dt.date.fromisoformat(start[:10]), dt.date.fromisoformat(end[:10])
    except ValueError as exc:
        raise HTTPException(400) from exc
    if (last - first).days > 400:
        raise HTTPException(400, "Range too large")
    events: list[dict] = []
    for h in await holidays_between(db, first, last):
        cls = "fc-vacation" if h.kind == "vacation" else "fc-holiday"
        span = {"start": h.start_date.isoformat(), "end": (h.end_date + dt.timedelta(days=1)).isoformat()}
        events.append({**span, "display": "background", "classNames": [cls]})
        label = ("🏖️ " if h.kind == "vacation" else "🎉 ") + h.title + (" · holiday homework allowed"
                                                                        if h.allow_homework else "")
        events.append({**span, "title": label, "allDay": True, "classNames": [cls + "-label"],
                       "extendedProps": {"type": h.kind}})
    if settings.weekly_off:
        events.append({"daysOfWeek": [(d + 1) % 7 for d in sorted(settings.weekly_off)], "display": "background",
                       "classNames": ["fc-weekly-off"]})
    ids = await scope_batch_ids(db, me)
    lesson_stmt = (select(Lesson).options(joinedload(Lesson.batch), joinedload(Lesson.meeting))
                   .where(Lesson.date.between(first, last), Lesson.status != "cancelled"))
    task_stmt = select(Task).options(joinedload(Task.batch)).where(Task.assign_date <= last, Task.due_date >= first)
    if ids is not None:
        lesson_stmt = lesson_stmt.where(Lesson.batch_id.in_(ids or [0]))
        task_stmt = task_stmt.where(Task.batch_id.in_(ids or [0]))
    for lesson in (await db.scalars(lesson_stmt)).unique().all():
        live = lesson.video_id is None
        url = {"admin": f"/admin/lessons/{lesson.id}", "student": f"/student/lessons/{lesson.id}"}.get(
            me.kind, f"/staff/batches/{lesson.batch_id}")
        events.append({"title": ("🔴 " if live else "▶️ ") + lesson.title, "url": url,
                       "start": f"{lesson.date.isoformat()}T{lesson.start_time.isoformat()}",
                       "end": f"{lesson.date.isoformat()}T{lesson.end_time.isoformat()}",
                       "backgroundColor": lesson.batch.color, "borderColor": lesson.batch.color,
                       "extendedProps": {"type": "class", "batch": lesson.batch.name,
                                         "mode": "Live class" if live else "Recorded video"}})
    for task in (await db.scalars(task_stmt)).unique().all():
        url = {"admin": f"/admin/tasks/{task.id}", "student": f"/student/tasks/{task.id}"}.get(
            me.kind, f"/staff/batches/{task.batch_id}")
        icon = "🧪 " if task.kind == "test" else ("🏖️ " if task.is_holiday_homework else "📝 ")
        events.append({"title": icon + task.title, "url": url, "allDay": True,
                       "start": task.assign_date.isoformat(),
                       "end": (task.due_date + dt.timedelta(days=1)).isoformat(),
                       "backgroundColor": task.batch.color, "borderColor": task.batch.color,
                       "classNames": ["fc-task"],
                       "extendedProps": {"type": "task", "batch": task.batch.name,
                                         "due": task.due_date.strftime("%d %b")}})
    ptm_stmt = select(PTM).where(PTM.scheduled_at >= dt.datetime.combine(first, dt.time(), tzinfo=settings.tz),
                                 PTM.scheduled_at <= dt.datetime.combine(last, dt.time(23, 59), tzinfo=settings.tz),
                                 PTM.status != "cancelled")
    if me.kind != "admin":
        ptm_stmt = ptm_stmt.where(PTM.id.in_(select(PTMInvite.ptm_id).where(PTMInvite.user_id == me.id)))
    for ptm in (await db.scalars(ptm_stmt)).all():
        events.append({"title": "👪 " + ptm.title, "start": ptm.scheduled_at.isoformat(),
                       "end": (ptm.scheduled_at + dt.timedelta(minutes=ptm.duration_minutes)).isoformat(),
                       "backgroundColor": "#0ea5e9", "borderColor": "#0ea5e9",
                       "url": f"/admin/ptm/{ptm.id}" if me.is_admin else None,
                       "extendedProps": {"type": "ptm"}})
    return events


@router.get("/api/day-info")
async def api_day_info(date: str, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_user)):
    try:
        info = await day_info(db, dt.date.fromisoformat(date))
    except ValueError as exc:
        raise HTTPException(400) from exc
    return {"date": date, "kind": info.kind, "label": info.label, "allow_homework": info.allow_homework,
            "blocked": info.is_blocked}


# ------------------------------------------------------------------ video watch tracking
class ProgressIn(BaseModel):
    intervals: list[list[float]] = Field(default_factory=list, max_length=200)
    position: float = 0
    duration: float | None = None


def _to_intervals(seconds: set[int]) -> list[list[int]]:
    out: list[list[int]] = []
    for s in sorted(seconds):
        if out and s == out[-1][1]:
            out[-1][1] = s + 1
        else:
            out.append([s, s + 1])
    return out


@router.post("/api/videos/{vid}/progress")
async def video_progress(vid: int, payload: ProgressIn, db: AsyncSession = Depends(get_db),
                         me: Principal = Depends(require_student)):
    video = await db.get(Video, vid)
    batch_ids = list((await db.scalars(select(Enrollment.batch_id).where(Enrollment.user_id == me.id))).all())
    if video is None or video.batch_id not in batch_ids:
        raise HTTPException(404)
    # The length must come from the server (upload probe / admin form / meeting). Trusting the student's player
    # let one student set it for EVERYONE (e.g. 30 s for a 1-hour video = attendance for all after 27 s).
    duration = video.duration_seconds or 0
    auto = duration > 0
    if not auto:  # unknown length: track progress for display only; the teacher marks attendance
        reported = payload.duration if payload.duration and math.isfinite(payload.duration) else 0
        duration = int(min(max(reported, 0), 6 * 3600))
    progress = await db.scalar(select(VideoProgress).where(VideoProgress.user_id == me.id,
                                                           VideoProgress.video_id == vid))
    if progress is None:
        progress = VideoProgress(user_id=me.id, video_id=vid, intervals=[], watched_seconds=0)
        db.add(progress)
    now = utcnow()
    covered: set[int] = set()
    for start, end in progress.intervals or []:
        covered.update(range(int(start), int(end)))
    # anti-cheat: credit at most ~2.5x the real time elapsed since the last heartbeat (+ slack)
    elapsed = (now - progress.last_heartbeat_at).total_seconds() if progress.last_heartbeat_at else 20
    budget = int(min(600, elapsed * 2.5 + 15))
    limit = duration or 6 * 3600
    for pair in payload.intervals:
        if len(pair) != 2 or not all(math.isfinite(x) for x in pair):
            continue
        start, end = max(0, int(pair[0])), min(limit, int(math.ceil(pair[1])))
        for sec in range(start, end):
            if budget <= 0:
                break
            if sec not in covered:
                covered.add(sec)
                budget -= 1
    progress.intervals = _to_intervals(covered)
    progress.watched_seconds = len(covered)
    progress.last_position = int(max(0, payload.position))
    progress.last_heartbeat_at = now
    ratio = min(1.0, progress.watched_seconds / duration) if duration else 0
    marked = False
    if auto and ratio >= settings.VIDEO_ATTENDANCE_RATIO:
        pending = (await db.execute(select(Lesson.id, Lesson.batch_id, Lesson.date).where(
            Lesson.video_id == vid, Lesson.batch_id.in_(batch_ids), ~exists().where(and_(
                Attendance.lesson_id == Lesson.id, Attendance.user_id == me.id, Attendance.status == "present"))))).all()
        for lesson_id, batch_id, day in pending:
            stmt = pg_insert(Attendance).values(user_id=me.id, batch_id=batch_id, lesson_id=lesson_id, date=day,
                                                status="present", source="video", created_at=now,
                                                detail=f"Watched {ratio:.0%} of the video")
            await db.execute(stmt.on_conflict_do_update(constraint="uq_attendance_user_lesson", set_={
                "status": "present", "source": "video", "detail": stmt.excluded.detail}))
            marked = True
        progress.attendance_marked = progress.attendance_marked or marked or not pending
    await db.commit()
    return {"watched": progress.watched_seconds, "duration": duration if auto else 0, "ratio": round(ratio, 3),
            "attendance": progress.attendance_marked, "just_marked": marked, "auto": auto,
            "threshold": settings.VIDEO_ATTENDANCE_RATIO}


# ------------------------------------------------------------------ notifications
@router.get("/api/notifications")
async def notifications(db: AsyncSession = Depends(get_db), me: Principal = Depends(require_user)):
    if me.id is None:
        return {"unread": 0, "items": []}
    unread = await db.scalar(select(func.count()).select_from(Notification).where(
        Notification.user_id == me.id, Notification.is_read.is_(False)))
    rows = (await db.scalars(select(Notification).where(Notification.user_id == me.id)
                             .order_by(Notification.id.desc()).limit(8))).all()
    return {"unread": unread, "items": [{"id": n.id, "title": n.title, "body": n.body, "link": n.link,
                                         "icon": n.icon, "read": n.is_read,
                                         "ago": n.created_at.astimezone(settings.tz).strftime("%d %b, %I:%M %p")}
                                        for n in rows]}


@router.post("/api/notifications/read-all")
async def read_all(db: AsyncSession = Depends(get_db), me: Principal = Depends(require_user)):
    if me.id:
        await db.execute(update(Notification).where(Notification.user_id == me.id, Notification.is_read.is_(False))
                         .values(is_read=True))
        await db.commit()
    return {"ok": True}


@router.get("/notifications", response_class=HTMLResponse)
async def notifications_page(request: Request, db: AsyncSession = Depends(get_db),
                             me: Principal = Depends(require_member)):
    rows = (await db.scalars(select(Notification).where(Notification.user_id == me.id)
                             .order_by(Notification.id.desc()).limit(100))).all()
    await db.execute(update(Notification).where(Notification.user_id == me.id, Notification.is_read.is_(False))
                     .values(is_read=True))
    await db.commit()
    return render(request, "shared/notifications.html", {"items": rows})


# ------------------------------------------------------------------ AI doubt solver
class AskIn(BaseModel):
    question: str = Field(..., min_length=2, max_length=1500)
    batch_id: int
    lesson_id: int | None = None
    image: str | None = Field(None, max_length=8_000_000)  # data URL of the paused video frame
    video_time: float | None = Field(None, ge=0, le=86400)


def sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def clock(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


async def frame_context(db: AsyncSession, lesson: Lesson, image: str | None,
                        video_time: float | None) -> tuple[bytes | None, str, str]:
    """Return (jpeg bytes, on-screen OCR text, transcript around the timestamp)."""
    jpeg, screen, spoken = None, "", ""
    if image:
        try:
            jpeg = await asyncio.to_thread(decode_frame, image)
        except UploadError:
            jpeg = None
        if jpeg:
            try:
                screen = (await asyncio.wait_for(ocr_image(jpeg, "frame.jpg", "image/jpeg"), 15)).strip()[:1500]
            except (OCRUnavailable, asyncio.TimeoutError):
                screen = ""
    if video_time is not None and lesson.video_id:
        video = await db.get(Video, lesson.video_id)
        meeting = await db.get(Meeting, video.meeting_id) if video and video.meeting_id else None
        if meeting is not None and meeting.started_at:
            moment = meeting.started_at + dt.timedelta(seconds=video_time)
            rows = (await db.execute(select(MeetingMessage.sender_name, MeetingMessage.text).where(
                MeetingMessage.meeting_id == meeting.id, MeetingMessage.kind == "transcript",
                MeetingMessage.created_at.between(moment - dt.timedelta(seconds=90),
                                                  moment + dt.timedelta(seconds=30)))
                .order_by(MeetingMessage.id).limit(40))).all()
            spoken = "\n".join(f"{r.sender_name}: {r.text}" for r in rows)[:2500]
    return jpeg, screen, spoken


def fallback_answer(hits: list[rag.Hit], screen: str = "", stamp: str | None = None) -> str:
    lead = []
    if screen:
        lead = [f"🖼️ Text I could read on screen at **{stamp or 'this moment'}**:", "",
                "> " + screen[:600].replace("\n", " "), ""]
    if not hits:
        return "\n".join(lead + ["🤖 The AI tutor is offline right now and I couldn't find this topic in your "
                                 "class notes. Your question has been saved and shared with your teacher, who will "
                                 "reply soon."])
    parts = lead + ["🤖 The AI tutor is offline right now, so here is what your **class notes** say:", ""]
    for hit in hits[:3]:
        excerpt = hit.content.strip().replace("\n", " ")
        parts.append(f"> **{hit.title}** — {excerpt[:420]}{'…' if len(excerpt) > 420 else ''}")
        parts.append("")
    parts.append("Your question has also been shared with your teacher. 🙂")
    return "\n".join(parts)


@router.post("/api/ai/ask")
async def ask(payload: AskIn, request: Request, db: AsyncSession = Depends(get_db),
              me: Principal = Depends(require_member)):
    batch = await db.get(Batch, payload.batch_id)
    allowed = await scope_batch_ids(db, me)
    if batch is None or (allowed is not None and batch.id not in allowed):
        raise HTTPException(403)
    if not limiter.hit(f"ask:{me.key}", 20, 600):
        return JSONResponse({"detail": "You've asked a lot of questions in a short time — take a short break "
                                       "and try again in a few minutes."}, status_code=429)
    outcome = await moderate(db, text=payload.question, principal=me, ip=client_ip(request), context="ai_tutor")
    if outcome.banned or outcome.strikes:
        async def blocked():
            yield sse({"type": "notice", "text": outcome.notice})
            yield sse({"type": "done", "answered_by": "moderation"})
        return StreamingResponse(blocked(), media_type="text/event-stream")
    question = outcome.text
    lesson = await db.get(Lesson, payload.lesson_id) if payload.lesson_id else None
    if lesson is not None and lesson.batch_id != batch.id:
        lesson = None
    sources: list[tuple[str, int]] = []
    if lesson is not None:
        meeting_id = await db.scalar(select(Meeting.id).where(Meeting.lesson_id == lesson.id))
        if meeting_id:
            sources.append(("meeting", meeting_id))
        if lesson.video_id:
            sources.append(("video", lesson.video_id))
    hits = await rag.search(db, question, batch_ids=[batch.id], class_level=batch.class_level,
                            subject=batch.subject, sources=sources or None, k=6) if sources else []
    if len(hits) < 3:
        extra = await rag.search(db, question, batch_ids=[batch.id], class_level=batch.class_level,
                                 subject=batch.subject, k=6)
        hits += [h for h in extra if h.id not in {x.id for x in hits}][: 6 - len(hits)]
    history = (await db.scalars(select(DoubtMessage).where(DoubtMessage.user_id == me.id,
                                                           DoubtMessage.batch_id == batch.id,
                                                           DoubtMessage.lesson_id == (lesson.id if lesson else None))
                                .order_by(DoubtMessage.id.desc()).limit(4))).all()
    special = bool(me.user is not None and me.user.special_needs)
    jpeg, screen, spoken, stamp = None, "", "", None
    if lesson is not None and (payload.image or payload.video_time is not None):
        jpeg, screen, spoken = await frame_context(db, lesson, payload.image, payload.video_time)
        stamp = clock(payload.video_time) if payload.video_time is not None else None
    system = (f"You are a friendly, expert {batch.subject} tutor for {batch.class_level} students at "
              f"{site_cache.data['site_name']}. {tone_for(special)} Explain clearly and step by step at the right "
              "level, in your own words (never paste the notes or their [n] headers), with a small example when "
              "useful. Prefer the class notes in <context>; if they don't cover "
              "the question, answer from general knowledge and say so in one short line. If the question is not "
              "about studies, gently steer back to learning. Keep answers under 250 words unless asked for more. "
              "When the student shares a video moment, base your answer on what is visible / being said at that "
              f"moment. Use Markdown. {GUARD}")
    messages: list[dict] = [{"role": "system", "content": system}]
    for h in reversed(history):
        messages += [{"role": "user", "content": h.question}, {"role": "assistant", "content": h.answer[:1500]}]
    moment = ""
    if stamp or jpeg:
        moment = (f"\n\nThe student paused the lesson video at {stamp or 'the current moment'}"
                  + (" and attached a screenshot of that frame." if jpeg else ".")
                  + (f"\nText visible on screen (OCR):\n<context>\n{screen}\n</context>" if screen else "")
                  + (f"\nWhat was being said around that moment:\n<context>\n{spoken}\n</context>" if spoken else ""))
    text_part = f"{rag.format_context(hits, 6000)}{moment}\n\nQuestion: {question}"
    if jpeg:
        image_url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
        messages.append({"role": "user", "content": [{"type": "text", "text": text_part},
                                                     {"type": "image_url", "image_url": {"url": image_url}}]})
    else:
        messages.append({"role": "user", "content": text_part})
    frame_rel = save_bytes(jpeg, "doubts", ".jpg") if jpeg else None
    source_list = [{"title": h.title} for h in hits[:4]]
    if stamp:
        source_list.insert(0, {"title": f"Video moment {stamp}" + (" (screenshot)" if jpeg else "")})
    notice = outcome.notice
    user_id, batch_id, lesson_id, teacher_id, name = me.id, batch.id, lesson.id if lesson else None, \
        batch.teacher_id, me.name
    video_seconds = int(payload.video_time) if payload.video_time is not None else None
    await db.commit()

    async def stream():
        answer, answered_by = "", "ai"
        yield sse({"type": "sources", "items": source_list})
        if notice:
            yield sse({"type": "notice", "text": notice})
        try:
            async for piece in ai.stream(messages, max_tokens=900, timeout=120):
                answer += piece
                yield sse({"type": "token", "text": piece})
        except AIDeclined:  # safety filter: discard any partial text; the teacher gets the question instead
            answered_by, answer = "fallback", fallback_answer(hits, screen, stamp)
        except AIUnavailable:
            if not answer:
                answered_by = "fallback"
                answer = fallback_answer(hits, screen, stamp)
            else:
                answer += "\n\n_(The AI connection dropped — this answer may be incomplete.)_"
        clean = mask(tidy_answer(answer, [h.title for h in hits], [f"Question: {question}"]))
        yield sse({"type": "final", "html": str(markdown_html(clean)), "answered_by": answered_by})
        async with SessionLocal() as session:
            session.add(DoubtMessage(user_id=user_id, batch_id=batch_id, lesson_id=lesson_id, question=question,
                                     answer=clean, sources=source_list, answered_by=answered_by,
                                     needs_teacher=answered_by == "fallback", image_path=frame_rel,
                                     video_seconds=video_seconds))
            if answered_by == "fallback" and teacher_id:
                await notify_users(session, [teacher_id], "A student's doubt needs you",
                                   f"{name}: {question[:120]}", "/staff/doubts", "fa-circle-question")
            await session.commit()
        yield sse({"type": "done", "answered_by": answered_by})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
