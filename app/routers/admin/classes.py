"""Classes (lessons), the video library and live meetings.

Rule: a lesson with a pre-recorded video needs no meeting. Without a video a live meeting is
mandatory - it is scheduled automatically and the admins are warned by email (SMTP).
"""
from __future__ import annotations

import datetime as dt
import re

from anyio import to_thread
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from ...config import settings
from ...constants import TEACHING_ROLES
from ...database import get_db
from ...models import (Attendance, Batch, Lesson, Meeting, MeetingMessage, MeetingParticipant, User, Video,
                       VideoComment, utcnow)
from ...security import Principal, require_admin
from ...services import rag
from ...services.academic_calendar import validate_class_date
from ...services.background import spawn
from ...services.mailer import queue_email
from ...services.meetings import end_meeting, generate_notes, new_code, rooms
from ...services.notify import batch_student_ids, batch_students, notify_users
from ...services.storage import (VIDEO_EXT, UploadError, abs_path, delete_file, has_file, probe_mp4_duration,
                                 save_upload)
from ...templating import f_datetime, flash, render
from ..comments import comment_context
from ..common import FormError, int_or, now_local, parse_date, parse_time, redirect, today
from .academics import teaching_staff

router = APIRouter()
DURATION_RE = re.compile(r"[0-9]{1,6}(\.[0-9]{1,6})?")


async def _host_for(db: AsyncSession, batch: Batch, admin: Principal) -> tuple[str, int | None]:
    if batch.teacher_id:
        teacher = await db.get(User, batch.teacher_id)
        if teacher is not None:
            return teacher.full_name, teacher.id
    return admin.name, None


def warn_live_required(lesson: Lesson, batch: Batch, teacher_email: str | None) -> None:
    recipients = settings.admin_alert_list + ([teacher_email] if teacher_email else [])
    queue_email(recipients, f"Live class required — {batch.name}: {lesson.title}", "meeting_warning.html",
                category="meeting_warning", kind="required", lesson=lesson.title, batch=batch.name,
                when=f_datetime(lesson.starts_at()), link=f"{settings.BASE_URL}/admin/lessons/{lesson.id}")


async def index_video(db: AsyncSession, video: Video, batch: Batch) -> None:
    content = "\n\n".join(filter(None, [video.title, video.description, video.notes]))
    await rag.index_document(db, source_type="video", source_id=video.id, title=f"Video: {video.title}",
                             content=content, batch_id=batch.id, class_level=batch.class_level,
                             subject=batch.subject)


async def store_video(db: AsyncSession, admin: Principal, batch: Batch, *, title: str, description: str,
                      notes: str, upload: UploadFile | None, external_url: str, duration: str) -> Video:
    file_rel, size = None, 0
    url = external_url.strip()
    if has_file(upload):
        file_rel, size = await save_upload(upload, "videos", VIDEO_EXT, settings.MAX_VIDEO_MB)
    elif url:
        if not url.startswith("https://"):
            raise FormError("External video links must start with https://")
    else:
        raise FormError("Upload a video file or paste a direct https:// link to a video file.")
    seconds = int(float(duration)) if DURATION_RE.fullmatch((duration or "").strip()) else None  # browser sends e.g. 742.53
    if not seconds and file_rel and file_rel.lower().endswith((".mp4", ".mov", ".m4v")):
        # the admin's browser normally fills the length in; read it from the file if it didn't
        seconds = await to_thread.run_sync(probe_mp4_duration, abs_path(file_rel))
    video = Video(batch_id=batch.id, title=title.strip()[:160] or "Untitled video",
                  description=description.strip() or None, notes=notes.strip() or None, file_path=file_rel,
                  external_url=None if file_rel else url[:500], size_bytes=size,
                  mime=(upload.content_type if file_rel and upload else None), duration_seconds=seconds,
                  uploaded_by=admin.username)
    db.add(video)
    await db.flush()
    await index_video(db, video, batch)
    return video


# ------------------------------------------------------------------ lessons
@router.get("/lessons", response_class=HTMLResponse)
async def lessons(request: Request, db: AsyncSession = Depends(get_db)):
    day = today()
    opts = (joinedload(Lesson.batch), joinedload(Lesson.meeting), joinedload(Lesson.video))
    upcoming = (await db.scalars(select(Lesson).options(*opts).where(Lesson.date >= day)
                                 .order_by(Lesson.date, Lesson.start_time).limit(100))).unique().all()
    recent = (await db.scalars(select(Lesson).options(*opts).where(Lesson.date < day,
                                                                   Lesson.date >= day - dt.timedelta(days=30))
                               .order_by(Lesson.date.desc(), Lesson.start_time.desc()).limit(60))).unique().all()
    return render(request, "admin/lessons/list.html", {"upcoming": upcoming, "recent": recent})


@router.get("/lessons/new", response_class=HTMLResponse)
async def lesson_new(request: Request, db: AsyncSession = Depends(get_db), batch: int = 0):
    batches = (await db.scalars(select(Batch).where(Batch.is_active.is_(True)).order_by(Batch.name))).all()
    videos = (await db.scalars(select(Video).order_by(Video.id.desc()).limit(300))).all()
    return render(request, "admin/lessons/form.html", {"batches": batches, "videos": videos, "batch": batch})


@router.post("/lessons/new")
async def lesson_create(request: Request, db: AsyncSession = Depends(get_db),
                        admin: Principal = Depends(require_admin), video_file: UploadFile | None = File(None)):
    form = await request.form()
    try:
        batch = await db.get(Batch, int_or(form.get("batch_id"), 0))
        if batch is None:
            raise FormError("Please choose a batch.")
        title = str(form.get("title") or "").strip()
        if not title:
            raise FormError("Please enter the topic of the class.")
        day = parse_date(str(form.get("date")), "class date")
        start = parse_time(str(form.get("start_time") or batch.start_time.isoformat()), "start time")
        end = parse_time(str(form.get("end_time") or batch.end_time.isoformat()), "end time")
        if end <= start:
            raise FormError("The class must end after it starts.")
        errors = await validate_class_date(db, day)
        if errors:
            raise FormError(" ".join(errors))
        mode = str(form.get("mode") or "live")
        video = None
        if mode == "video":
            vid = int_or(form.get("video_id"), 0)
            if vid:
                video = await db.get(Video, vid)
                if video is None or video.batch_id != batch.id:
                    raise FormError("Choose a video from this batch's library.")
            else:
                video = await store_video(db, admin, batch, title=str(form.get("video_title") or title),
                                          description="", notes=str(form.get("notes") or ""), upload=video_file,
                                          external_url=str(form.get("external_url") or ""),
                                          duration=str(form.get("duration_seconds") or ""))
    except (FormError, UploadError, ValueError) as exc:
        flash(request, str(exc), "danger")
        return redirect("/admin/lessons/new")
    lesson = Lesson(batch_id=batch.id, title=title[:160], description=str(form.get("description") or "").strip()
                    or None, date=day, start_time=start, end_time=end, video_id=video.id if video else None,
                    created_by=admin.username)
    db.add(lesson)
    await db.flush()
    teacher_email = None
    if video is None:
        host_name, host_id = await _host_for(db, batch, admin)
        if host_id:
            teacher_email = (await db.get(User, host_id)).email
        minutes = (dt.datetime.combine(day, end) - dt.datetime.combine(day, start)).seconds // 60
        db.add(Meeting(code=new_code(), kind="class", title=title[:160], batch_id=batch.id, lesson_id=lesson.id,
                       host_name=host_name, host_user_id=host_id, scheduled_start=lesson.starts_at(),
                       scheduled_minutes=minutes))
        lesson.warning_sent_at = utcnow()
    ids = await batch_student_ids(db, [batch.id])
    await notify_users(db, ids, f"New class: {lesson.title}", f"{day:%a %d %b} · {start:%I:%M %p}",
                       f"/student/lessons/{lesson.id}", "fa-chalkboard-user")
    await db.commit()
    if video is None:
        warn_live_required(lesson, batch, teacher_email)
        flash(request, "No pre-recorded video is attached, so a live class is mandatory. A meeting room was "
                       "scheduled and a warning was emailed to the admins.", "warning")
    else:
        flash(request, "Class scheduled with a pre-recorded video — no live meeting is required.", "success")
    return redirect(f"/admin/lessons/{lesson.id}")


@router.get("/lessons/{lid}", response_class=HTMLResponse)
async def lesson_detail(lid: int, request: Request, db: AsyncSession = Depends(get_db),
                        admin: Principal = Depends(require_admin)):
    lesson = await db.scalar(select(Lesson).options(joinedload(Lesson.batch), joinedload(Lesson.meeting),
                                                    joinedload(Lesson.video)).where(Lesson.id == lid))
    if lesson is None:
        raise HTTPException(404)
    students = await batch_students(db, [lesson.batch_id])
    marks = {a.user_id: a for a in (await db.scalars(select(Attendance).where(Attendance.lesson_id == lid))).all()}
    videos = (await db.scalars(select(Video).where(Video.batch_id == lesson.batch_id).order_by(Video.id.desc()))).all()
    return render(request, "admin/lessons/detail.html", {"lesson": lesson, "students": students, "marks": marks,
                                                         "videos": videos, "teachers": await teaching_staff(db),
                                                         **await comment_context(db, admin, lesson.video)})


@router.post("/lessons/{lid}/edit")
async def lesson_edit(lid: int, request: Request, db: AsyncSession = Depends(get_db),
                      admin: Principal = Depends(require_admin)):
    """Reschedule one class (topic, date, time) or give its live room another host - e.g. a substitute teacher."""
    lesson = await db.scalar(select(Lesson).options(joinedload(Lesson.batch), joinedload(Lesson.meeting))
                             .where(Lesson.id == lid))
    if lesson is None:
        raise HTTPException(404)
    meeting = lesson.meeting
    if lesson.status != "scheduled" or (meeting is not None and meeting.status != "scheduled"):
        flash(request, "Only classes that haven't started yet can be changed.", "warning")
        return redirect(f"/admin/lessons/{lid}")
    form = await request.form()
    try:
        title = str(form.get("title") or "").strip()
        if not title:
            raise FormError("Please enter the topic of the class.")
        day = parse_date(str(form.get("date")), "class date")
        start, end = parse_time(str(form.get("start_time")), "start time"), parse_time(str(form.get("end_time")),
                                                                                         "end time")
        if end <= start:
            raise FormError("The class must end after it starts.")
        if day < today():
            raise FormError("Pick today or a later date.")
        errors = await validate_class_date(db, day)
        if errors:
            raise FormError(" ".join(errors))
        host = None
        if meeting is not None and int_or(form.get("host_id")):
            host = await db.get(User, int_or(form.get("host_id")))
            if host is None or host.role != "staff" or host.status != "active" or host.staff_role not in TEACHING_ROLES:
                raise FormError("The host must be an active teaching staff member.")
    except (FormError, ValueError) as exc:
        flash(request, str(exc), "danger")
        return redirect(f"/admin/lessons/{lid}")
    moved = (day, start, end) != (lesson.date, lesson.start_time, lesson.end_time)
    lesson.title, lesson.date, lesson.start_time, lesson.end_time = title[:160], day, start, end
    if moved:
        lesson.reminder_sent_at = lesson.missed_alert_sent_at = None  # remind / alert again for the new time
    changes = []
    if meeting is not None:
        meeting.title, meeting.scheduled_start = lesson.title, lesson.starts_at()
        meeting.scheduled_minutes = (dt.datetime.combine(day, end) - dt.datetime.combine(day, start)).seconds // 60
        if host is not None and host.id != meeting.host_user_id:
            meeting.host_name, meeting.host_user_id = host.full_name, host.id
            changes.append(f"hosted by {host.full_name}")
            await notify_users(db, [host.id], f"You're hosting: {lesson.title}",
                               f"{lesson.batch.name} · {day:%a %d %b} · {start:%I:%M %p}", f"/meet/{meeting.code}",
                               "fa-chalkboard-user")
    if moved:
        changes.append(f"moved to {day:%a %d %b}, {start:%I:%M %p}")
        await notify_users(db, await batch_student_ids(db, [lesson.batch_id]), f"Class rescheduled: {lesson.title}",
                           f"{day:%a %d %b} · {start:%I:%M %p}", f"/student/lessons/{lesson.id}", "fa-clock")
    await db.commit()
    flash(request, "Class updated" + (f" — {' and '.join(changes)}; " + ("students were notified." if moved else
                                                                        "the new host was notified.")
                                       if changes else "."), "success")
    return redirect(f"/admin/lessons/{lid}")


@router.post("/lessons/{lid}/attach-video")
async def lesson_attach(lid: int, request: Request, db: AsyncSession = Depends(get_db),
                        admin: Principal = Depends(require_admin), video_file: UploadFile | None = File(None)):
    lesson = await db.scalar(select(Lesson).options(joinedload(Lesson.batch)).where(Lesson.id == lid))
    if lesson is None:
        raise HTTPException(404)
    form = await request.form()
    try:
        vid = int_or(form.get("video_id"), 0)
        if vid:
            video = await db.get(Video, vid)
            if video is None or video.batch_id != lesson.batch_id:
                raise FormError("Choose a video from this batch's library.")
        else:
            video = await store_video(db, admin, lesson.batch, title=str(form.get("video_title") or lesson.title),
                                      description="", notes=str(form.get("notes") or ""), upload=video_file,
                                      external_url=str(form.get("external_url") or ""),
                                      duration=str(form.get("duration_seconds") or ""))
    except (FormError, UploadError, ValueError) as exc:
        flash(request, str(exc), "danger")
        return redirect(f"/admin/lessons/{lid}")
    lesson.video_id = video.id
    await db.commit()
    flash(request, "Video attached — a live meeting is no longer mandatory for this class.", "success")
    return redirect(f"/admin/lessons/{lid}")


@router.post("/lessons/{lid}/start")
async def lesson_start(lid: int, db: AsyncSession = Depends(get_db), admin: Principal = Depends(require_admin)):
    lesson = await db.scalar(select(Lesson).options(joinedload(Lesson.batch), joinedload(Lesson.meeting))
                             .where(Lesson.id == lid))
    if lesson is None:
        raise HTTPException(404)
    meeting = lesson.meeting
    if meeting is None:
        host_name, host_id = await _host_for(db, lesson.batch, admin)
        meeting = Meeting(code=new_code(), kind="class", title=lesson.title, batch_id=lesson.batch_id,
                          lesson_id=lesson.id, host_name=host_name, host_user_id=host_id,
                          scheduled_start=lesson.starts_at())
        db.add(meeting)
        await db.commit()
    return redirect(f"/meet/{meeting.code}")


@router.post("/lessons/{lid}/cancel")
async def lesson_cancel(lid: int, request: Request, db: AsyncSession = Depends(get_db)):
    lesson = await db.scalar(select(Lesson).options(joinedload(Lesson.meeting)).where(Lesson.id == lid))
    if lesson is None:
        raise HTTPException(404)
    lesson.status = "cancelled"
    if lesson.meeting is not None and lesson.meeting.status == "scheduled":
        lesson.meeting.status = "cancelled"
    ids = await batch_student_ids(db, [lesson.batch_id])
    await notify_users(db, ids, f"Class cancelled: {lesson.title}", f"{lesson.date:%a %d %b}", None, "fa-ban")
    await db.commit()
    flash(request, "Class cancelled and students notified.", "success")
    return redirect(f"/admin/lessons/{lid}")


@router.post("/lessons/{lid}/attendance")
async def lesson_attendance(lid: int, request: Request, db: AsyncSession = Depends(get_db),
                            admin: Principal = Depends(require_admin), user_id: int = Form(...),
                            status: str = Form("present")):
    lesson = await db.get(Lesson, lid)
    if lesson is None or status not in ("present", "absent"):
        raise HTTPException(400)
    stmt = pg_insert(Attendance).values(user_id=user_id, batch_id=lesson.batch_id, lesson_id=lid, date=lesson.date,
                                        status=status, source="manual", detail=f"Marked by {admin.username}",
                                        created_at=utcnow())
    await db.execute(stmt.on_conflict_do_update(constraint="uq_attendance_user_lesson", set_={
        "status": stmt.excluded.status, "source": "manual", "detail": stmt.excluded.detail}))
    await db.commit()
    return redirect(f"/admin/lessons/{lid}#attendance")


# ------------------------------------------------------------------ video library
@router.get("/videos", response_class=HTMLResponse)
async def videos(request: Request, db: AsyncSession = Depends(get_db), batch: int = 0):
    stmt = select(Video).options(joinedload(Video.batch)).order_by(Video.id.desc())
    if batch:
        stmt = stmt.where(Video.batch_id == batch)
    rows = (await db.scalars(stmt.limit(300))).unique().all()
    usage = dict((await db.execute(select(Lesson.video_id, func.count()).where(Lesson.video_id.is_not(None))
                                   .group_by(Lesson.video_id))).all())
    comments = dict((await db.execute(select(VideoComment.video_id, func.count())
                                      .where(VideoComment.deleted.is_(False)).group_by(VideoComment.video_id))).all())
    batches = (await db.scalars(select(Batch).order_by(Batch.name))).all()
    return render(request, "admin/videos.html", {"videos": rows, "usage": usage, "comments": comments,
                                                 "batches": batches, "batch": batch})


@router.post("/videos/upload")
async def video_upload(request: Request, db: AsyncSession = Depends(get_db), admin: Principal = Depends(require_admin),
                       batch_id: int = Form(...), title: str = Form(...), description: str = Form(""),
                       notes: str = Form(""), external_url: str = Form(""), duration_seconds: str = Form(""),
                       file: UploadFile | None = File(None)):
    batch = await db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(400)
    try:
        await store_video(db, admin, batch, title=title, description=description, notes=notes, upload=file,
                          external_url=external_url, duration=duration_seconds)
    except (FormError, UploadError) as exc:
        flash(request, str(exc), "danger")
        return redirect("/admin/videos")
    await db.commit()
    flash(request, "Video added to the library. Its notes were indexed for the AI doubt solver.", "success")
    return redirect("/admin/videos")


@router.post("/videos/{vid}/notes")
async def video_notes(vid: int, request: Request, db: AsyncSession = Depends(get_db), notes: str = Form("")):
    video = await db.scalar(select(Video).options(joinedload(Video.batch)).where(Video.id == vid))
    if video is None:
        raise HTTPException(404)
    video.notes = notes.strip() or None
    await index_video(db, video, video.batch)
    await db.commit()
    flash(request, "Notes saved and re-indexed.", "success")
    return redirect("/admin/videos")


@router.post("/videos/{vid}/delete")
async def video_delete(vid: int, request: Request, db: AsyncSession = Depends(get_db)):
    video = await db.get(Video, vid)
    if video is not None:
        await rag.remove_document(db, "video", vid)
        path, is_recording = video.file_path, video.source == "recording"
        await db.delete(video)
        await db.commit()
        if path and not is_recording:  # recordings stay with their meeting
            delete_file(path)
    flash(request, "Video deleted.", "success")
    return redirect("/admin/videos")


# ------------------------------------------------------------------ meetings
@router.get("/meetings", response_class=HTMLResponse)
async def meetings(request: Request, db: AsyncSession = Depends(get_db)):
    opts = (joinedload(Meeting.batch), joinedload(Meeting.lesson))
    live = (await db.scalars(select(Meeting).options(*opts).where(Meeting.status == "live"))).unique().all()
    scheduled = (await db.scalars(select(Meeting).options(*opts).where(Meeting.status == "scheduled")
                                  .order_by(Meeting.scheduled_start).limit(50))).unique().all()
    ended = (await db.scalars(select(Meeting).options(*opts).where(Meeting.status == "ended")
                              .order_by(Meeting.ended_at.desc()).limit(40))).unique().all()
    batches = (await db.scalars(select(Batch).where(Batch.is_active.is_(True)).order_by(Batch.name))).all()
    return render(request, "admin/meetings/list.html", {"live": live, "scheduled": scheduled, "ended": ended,
                                                        "batches": batches, "counts": {m.code: rooms.count(m.code)
                                                                                       for m in live}})


@router.post("/meetings/new")
async def meeting_new(request: Request, db: AsyncSession = Depends(get_db), admin: Principal = Depends(require_admin),
                      batch_id: int = Form(...), title: str = Form(...)):
    batch = await db.get(Batch, batch_id)
    if batch is None or not title.strip():
        flash(request, "Choose a batch and a title for the live class.", "danger")
        return redirect("/admin/meetings")
    now = now_local()
    errors = await validate_class_date(db, now.date())
    if errors:
        flash(request, " ".join(errors), "danger")
        return redirect("/admin/meetings")
    start = now.time().replace(second=0, microsecond=0)
    end = min(dt.time(23, 59), (now + dt.timedelta(minutes=60)).time().replace(second=0, microsecond=0)) \
        if now.hour < 23 else dt.time(23, 59)
    lesson = Lesson(batch_id=batch.id, title=title.strip()[:160], date=now.date(), start_time=start, end_time=end,
                    created_by=admin.username)
    db.add(lesson)
    await db.flush()
    host_name, host_id = await _host_for(db, batch, admin)
    meeting = Meeting(code=new_code(), kind="class", title=lesson.title, batch_id=batch.id, lesson_id=lesson.id,
                      host_name=host_name, host_user_id=host_id, scheduled_start=lesson.starts_at())
    db.add(meeting)
    ids = await batch_student_ids(db, [batch.id])
    await notify_users(db, ids, f"Live class starting: {lesson.title}", batch.name, f"/meet/{meeting.code}",
                       "fa-video")
    await db.commit()
    return redirect(f"/meet/{meeting.code}")


@router.get("/meetings/{mid}", response_class=HTMLResponse)
async def meeting_detail(mid: int, request: Request, db: AsyncSession = Depends(get_db)):
    meeting = await db.scalar(select(Meeting).options(joinedload(Meeting.batch), joinedload(Meeting.lesson))
                              .where(Meeting.id == mid))
    if meeting is None:
        raise HTTPException(404)
    participants = (await db.scalars(select(MeetingParticipant).where(MeetingParticipant.meeting_id == mid)
                                     .order_by(MeetingParticipant.total_seconds.desc()))).all()
    chat = (await db.scalars(select(MeetingMessage).where(MeetingMessage.meeting_id == mid,
                                                          MeetingMessage.kind == "chat")
                             .order_by(MeetingMessage.id).limit(500))).all()
    attendance = []
    if meeting.lesson_id:
        attendance = (await db.execute(select(User.full_name, Attendance.status, Attendance.detail)
                                       .join(User, User.id == Attendance.user_id)
                                       .where(Attendance.lesson_id == meeting.lesson_id)
                                       .order_by(User.full_name))).all()
    return render(request, "admin/meetings/detail.html", {"meeting": meeting, "participants": participants,
                                                          "chat": chat, "attendance": attendance})


@router.post("/meetings/{mid}/regenerate-notes")
async def meeting_regenerate(mid: int, request: Request, db: AsyncSession = Depends(get_db)):
    meeting = await db.get(Meeting, mid)
    if meeting is None or meeting.status != "ended":
        raise HTTPException(400)
    meeting.notes_status = "pending"
    await db.commit()
    spawn(generate_notes(mid), name=f"notes:{mid}")
    flash(request, "Regenerating the AI notes — refresh in a minute.", "info")
    return redirect(f"/admin/meetings/{mid}")


@router.post("/meetings/{mid}/end")
async def meeting_end(mid: int, request: Request):
    await end_meeting(mid, reason="ended by admin")
    flash(request, "Meeting ended. Attendance, recording and AI notes are being processed.", "success")
    return redirect(f"/admin/meetings/{mid}")
