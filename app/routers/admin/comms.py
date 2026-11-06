"""Communication centre: broadcasts to students/parents/staff, contact directory, PTMs with RSVP, email log."""
from __future__ import annotations

import datetime as dt
import math
import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from ...cache import site_cache
from ...config import settings
from ...constants import STAFF_ROLES, TEACHING_ROLES
from ...database import get_db
from ...models import PTM, Batch, Communication, EmailLog, Enrollment, Meeting, PTMInvite, User
from ...security import Principal, require_admin
from ...services.academic_calendar import day_info
from ...services.ai import AIUnavailable, ai, tone_for
from ...services.background import spawn
from ...services.mailer import queue_bulk
from ...services.meetings import new_code
from ...services.notify import notify_users
from ...services.sms import send_sms
from ...templating import f_datetime, flash, render
from ..common import FormError, int_or, ints, parse_date, parse_time, redirect, truthy

router = APIRouter()
TEMPLATES = {
    "notice": ("Official notice", "Dear {name},\n\nPlease note the following:\n\n…\n\nThank you for your cooperation."),
    "announcement": ("Announcement", "Dear {name},\n\nWe are pleased to announce …\n\nWarm regards,"),
    "reminder": ("Gentle reminder", "Dear {name},\n\nThis is a friendly reminder that …\n\nThank you,"),
    "holiday": ("Holiday notice", "Dear {name},\n\nThe institute will remain closed on … on account of …. "
                                  "Classes resume on ….\n\nRegards,"),
}


@router.get("/comms", response_class=HTMLResponse)
async def comms(request: Request, db: AsyncSession = Depends(get_db)):
    batches = (await db.scalars(select(Batch).where(Batch.is_active.is_(True)).order_by(Batch.name))).all()
    history = (await db.scalars(select(Communication).order_by(Communication.id.desc()).limit(30))).all()
    return render(request, "admin/comms/compose.html", {"batches": batches, "history": history,
                                                         "roles": STAFF_ROLES, "templates": TEMPLATES,
                                                         "sms_enabled": settings.SMS_PROVIDER != "console"})


class PolishIn(BaseModel):
    subject: str = Field("", max_length=200)
    body: str = Field(..., max_length=6000)


@router.post("/comms/polish")
async def polish(payload: PolishIn):
    try:
        data = await ai.chat_json([
            {"role": "system", "content": "You are the communications officer of an Indian school. Rewrite the "
                                          "message in clear, polite, professional (corporate) English. Keep every "
                                          "fact, date and placeholder such as {name} exactly. " + tone_for(False)
                                          + ' Reply with JSON {"subject": str, "body": str}.'},
            {"role": "user", "content": f"Subject: {payload.subject}\n\nMessage:\n{payload.body}"},
        ], temperature=0.3, max_tokens=900, timeout=90)
        return {"ok": True, "subject": str(data.get("subject") or payload.subject)[:200],
                "body": str(data.get("body") or payload.body)[:6000]}
    except (AIUnavailable, ValueError, AttributeError) as exc:
        return {"ok": False, "error": f"AI polish unavailable ({exc}). Your text was kept as is."}


async def resolve_recipients(db: AsyncSession, form) -> dict[str, dict]:
    """Return {email: {name, phone}} for the chosen audiences (deduplicated)."""
    out: dict[str, dict] = {}

    def add(email: str | None, name: str | None, phone: str | None = None) -> None:
        if email and email.lower() not in out:
            out[email.lower()] = {"name": (name or "Parent / Guardian").strip(), "phone": phone}

    audiences = set(form.getlist("audience"))
    batch_students = ints(form.getlist("batch_students"))
    batch_parents = ints(form.getlist("batch_parents"))
    roles = [r for r in form.getlist("staff_roles") if r in STAFF_ROLES]
    student = (User.role == "student", User.status == "active")
    if "all_students" in audiences:
        for r in (await db.execute(select(User.email, User.full_name, User.phone).where(*student))).all():
            add(*r)
    if "all_parents" in audiences:
        for r in (await db.execute(select(User.parent_email, User.parent_name, User.parent_phone).where(*student))).all():
            add(*r)
    if "all_staff" in audiences:
        for r in (await db.execute(select(User.email, User.full_name, User.phone)
                                   .where(User.role == "staff", User.status == "active"))).all():
            add(*r)
    if batch_students:
        for r in (await db.execute(select(User.email, User.full_name, User.phone).join(
                Enrollment, Enrollment.user_id == User.id).where(*student, Enrollment.batch_id.in_(batch_students)))).all():
            add(*r)
    if batch_parents:
        for r in (await db.execute(select(User.parent_email, User.parent_name, User.parent_phone).join(
                Enrollment, Enrollment.user_id == User.id).where(*student, Enrollment.batch_id.in_(batch_parents)))).all():
            add(*r)
    if roles:
        for r in (await db.execute(select(User.email, User.full_name, User.phone)
                                   .where(User.role == "staff", User.status == "active",
                                          User.staff_role.in_(roles)))).all():
            add(*r)
    for email in str(form.get("extra_emails") or "").replace(";", ",").split(","):
        if "@" in email:
            add(email.strip(), "there")
    return out


async def _sms_many(numbers: list[str], text: str) -> None:
    for number in numbers[:500]:
        await send_sms(number, text)


@router.post("/comms/send")
async def send(request: Request, db: AsyncSession = Depends(get_db), admin: Principal = Depends(require_admin)):
    form = await request.form()
    subject = str(form.get("subject") or "").strip()[:200]
    body = str(form.get("body") or "").strip()[:6000]
    if not subject or len(body) < 10:
        flash(request, "Please write a subject and a message.", "danger")
        return redirect("/admin/comms")
    recipients = await resolve_recipients(db, form)
    if not recipients:
        flash(request, "No recipients matched the selected audience.", "warning")
        return redirect("/admin/comms")
    queue_bulk([(email, {"name": info["name"], "body": body.replace("{name}", info["name"]), "sender": admin.name})
                for email, info in recipients.items()], subject, "broadcast.html", "broadcast")
    channel = "email"
    if truthy(str(form.get("sms", ""))):
        channel = "email+sms"
        phones = [i["phone"] for i in recipients.values() if i.get("phone")]
        spawn(_sms_many(phones, f"{site_cache.data['site_name']}: {subject}. Details sent to your email."), "sms-bulk")
    audience = ", ".join(sorted(set(form.getlist("audience")))) or "selected batches / roles"
    db.add(Communication(channel=channel, audience=audience[:300], subject=subject, body=body,
                         recipients=len(recipients), sent_by=admin.username))
    await db.commit()
    flash(request, f"Message queued for {len(recipients)} recipient(s).", "success")
    return redirect("/admin/comms")


@router.get("/directory", response_class=HTMLResponse)
async def directory(request: Request, db: AsyncSession = Depends(get_db), q: str = "", batch: int = 0,
                    who: str = "students"):
    stmt = select(User).where(User.status == "active", User.role == ("staff" if who == "staff" else "student"))
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(User.full_name.ilike(like), User.parent_name.ilike(like), User.id_code.ilike(like)))
    if batch and who != "staff":
        stmt = stmt.where(User.id.in_(select(Enrollment.user_id).where(Enrollment.batch_id == batch)))
    people = (await db.scalars(stmt.options(selectinload(User.enrollments).joinedload(Enrollment.batch))
                               .order_by(User.full_name).limit(300))).all()
    batches = (await db.scalars(select(Batch).order_by(Batch.name))).all()
    return render(request, "admin/comms/directory.html", {"people": people, "batches": batches, "q": q,
                                                           "batch": batch, "who": who})


# ------------------------------------------------------------------ PTM
@router.get("/ptm", response_class=HTMLResponse)
async def ptms(request: Request, db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(PTM).options(joinedload(PTM.meeting)).order_by(PTM.scheduled_at.desc())
                             .limit(100))).unique().all()
    counts: dict[int, dict[str, int]] = {}
    for pid, rsvp, n in (await db.execute(select(PTMInvite.ptm_id, PTMInvite.rsvp, func.count())
                                          .group_by(PTMInvite.ptm_id, PTMInvite.rsvp))).all():
        counts.setdefault(pid, {})[rsvp] = n
    batches = (await db.scalars(select(Batch).where(Batch.is_active.is_(True)).order_by(Batch.name))).all()
    return render(request, "admin/comms/ptm_list.html", {"ptms": rows, "counts": counts, "batches": batches})


def invite_context(ptm: PTM, invite: PTMInvite, meeting: Meeting | None) -> dict:
    base = settings.BASE_URL
    return {"name": invite.invitee_name, "regarding": invite.regarding, "title": ptm.title, "agenda": ptm.agenda,
            "when": f_datetime(ptm.scheduled_at, "%A, %d %B %Y at %I:%M %p"), "duration": ptm.duration_minutes,
            "mode": ptm.mode, "location": ptm.location, "rsvp_link": f"{base}/ptm/invite/{invite.token}",
            "join_link": f"{base}/ptm/join/{invite.token}" if meeting else None,
            "invitee_type": invite.invitee_type}


@router.post("/ptm/new")
async def ptm_create(request: Request, db: AsyncSession = Depends(get_db), admin: Principal = Depends(require_admin)):
    form = await request.form()
    try:
        title = str(form.get("title") or "").strip()
        if not title:
            raise FormError("Please give the PTM a title.")
        day = parse_date(str(form.get("date")), "date")
        at = parse_time(str(form.get("time")), "time")
        info = await day_info(db, day)
        if info.kind == "holiday":
            raise FormError(f"{day:%d %b %Y} is a holiday ({info.label}). Please choose another date.")
        when = dt.datetime.combine(day, at, tzinfo=settings.tz)
        mode = str(form.get("mode") or "online")
        mode = mode if mode in ("online", "in_person", "hybrid") else "online"
        batch_ids = ints(form.getlist("batch_ids"))
        if not batch_ids:
            raise FormError("Choose at least one batch whose parents should be invited.")
        duration = max(15, min(240, int_or(form.get("duration_minutes"), 60)))
    except (FormError, ValueError) as exc:
        flash(request, str(exc), "danger")
        return redirect("/admin/ptm")
    ptm = PTM(title=title[:160], agenda=str(form.get("agenda") or "").strip() or None, scheduled_at=when,
              duration_minutes=duration, mode=mode, location=str(form.get("location") or "").strip()[:200] or None,
              batch_ids=batch_ids, include_staff=truthy(str(form.get("include_staff", ""))),
              include_students=truthy(str(form.get("include_students", ""))), created_by=admin.username)
    meeting = None
    if mode in ("online", "hybrid"):
        meeting = Meeting(code=new_code(), kind="ptm", title=f"PTM · {title}"[:160], host_name=admin.name,
                          scheduled_start=when, scheduled_minutes=duration)
        db.add(meeting)
        await db.flush()
        ptm.meeting_id = meeting.id
    db.add(ptm)
    await db.flush()
    students = (await db.scalars(select(User).join(Enrollment, Enrollment.user_id == User.id)
                                 .where(Enrollment.batch_id.in_(batch_ids), User.status == "active").distinct())).all()
    invites: list[PTMInvite] = []
    seen: set[tuple[str, str]] = set()
    for s in students:
        if s.parent_email and (s.parent_email.lower(), s.full_name) not in seen:
            seen.add((s.parent_email.lower(), s.full_name))
            invites.append(PTMInvite(ptm_id=ptm.id, user_id=s.id, invitee_name=s.parent_name or "Parent / Guardian",
                                     invitee_email=s.parent_email, invitee_type="parent", regarding=s.full_name,
                                     token=secrets.token_urlsafe(24)))
        if ptm.include_students:
            invites.append(PTMInvite(ptm_id=ptm.id, user_id=s.id, invitee_name=s.full_name, invitee_email=s.email,
                                     invitee_type="student", token=secrets.token_urlsafe(24)))
    if ptm.include_staff:
        teacher_ids = (await db.scalars(select(Batch.teacher_id).where(Batch.id.in_(batch_ids),
                                                                       Batch.teacher_id.is_not(None)))).all()
        staff = (await db.scalars(select(User).where(User.status == "active", User.role == "staff", or_(
            User.id.in_(teacher_ids), User.staff_role.in_(("principal", "vice_principal", "coordinator")))))).all()
        for u in staff:
            invites.append(PTMInvite(ptm_id=ptm.id, user_id=u.id, invitee_name=u.full_name, invitee_email=u.email,
                                     invitee_type="staff", token=secrets.token_urlsafe(24)))
    db.add_all(invites)
    member_ids = [i.user_id for i in invites if i.invitee_type in ("student", "staff")]
    await notify_users(db, member_ids, f"PTM: {ptm.title}", f_datetime(when), "/notifications", "fa-people-roof")
    await db.commit()
    queue_bulk([(i.invitee_email, invite_context(ptm, i, meeting)) for i in invites],
               f"Invitation: {ptm.title} — {f_datetime(when, '%d %b %Y, %I:%M %p')}", "ptm_invite.html", "ptm")
    flash(request, f"PTM scheduled. {len(invites)} formal invitation(s) with RSVP links are being sent.", "success")
    return redirect(f"/admin/ptm/{ptm.id}")


@router.get("/ptm/{pid}", response_class=HTMLResponse)
async def ptm_detail(pid: int, request: Request, db: AsyncSession = Depends(get_db)):
    ptm = await db.scalar(select(PTM).options(selectinload(PTM.invites), joinedload(PTM.meeting)).where(PTM.id == pid))
    if ptm is None:
        raise HTTPException(404)
    invites = sorted(ptm.invites, key=lambda i: (i.invitee_type, i.invitee_name.lower()))
    tally: dict[str, int] = {}
    for i in invites:
        tally[i.rsvp] = tally.get(i.rsvp, 0) + 1
    names = (await db.scalars(select(Batch.name).where(Batch.id.in_(ptm.batch_ids or [0])))).all()
    return render(request, "admin/comms/ptm_detail.html", {"ptm": ptm, "invites": invites, "tally": tally,
                                                            "batch_names": names})


@router.post("/ptm/{pid}/remind")
async def ptm_remind(pid: int, request: Request, db: AsyncSession = Depends(get_db)):
    ptm = await db.scalar(select(PTM).options(selectinload(PTM.invites), joinedload(PTM.meeting)).where(PTM.id == pid))
    if ptm is None:
        raise HTTPException(404)
    pending = [i for i in ptm.invites if i.rsvp == "pending"]
    queue_bulk([(i.invitee_email, {**invite_context(ptm, i, ptm.meeting), "reminder": True}) for i in pending],
               f"Reminder: {ptm.title}", "ptm_invite.html", "ptm")
    flash(request, f"Reminder sent to {len(pending)} invitee(s) who haven't replied.", "success")
    return redirect(f"/admin/ptm/{pid}")


@router.post("/ptm/{pid}/cancel")
async def ptm_cancel(pid: int, request: Request, db: AsyncSession = Depends(get_db),
                     reason: str = Form("")):
    ptm = await db.scalar(select(PTM).options(selectinload(PTM.invites), joinedload(PTM.meeting)).where(PTM.id == pid))
    if ptm is None:
        raise HTTPException(404)
    ptm.status = "cancelled"
    if ptm.meeting is not None and ptm.meeting.status == "scheduled":
        ptm.meeting.status = "cancelled"
    await db.commit()
    queue_bulk([(i.invitee_email, {"name": i.invitee_name, "body": f"We regret to inform you that “{ptm.title}” "
                                   f"scheduled for {f_datetime(ptm.scheduled_at)} has been cancelled."
                                   + (f"\n\nReason: {reason.strip()}" if reason.strip() else "")
                                   + "\n\nWe apologise for the inconvenience and will share a new date soon.",
                                   "sender": "Administration"}) for i in ptm.invites],
               f"Cancelled: {ptm.title}", "broadcast.html", "ptm")
    flash(request, "PTM cancelled and all invitees informed.", "success")
    return redirect(f"/admin/ptm/{pid}")


@router.get("/emails", response_class=HTMLResponse)
async def email_log(request: Request, db: AsyncSession = Depends(get_db), status: str = "", page: int = 1):
    stmt = select(EmailLog)
    if status:
        stmt = stmt.where(EmailLog.status == status)
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    page = max(1, page)
    rows = (await db.scalars(stmt.order_by(EmailLog.id.desc()).offset((page - 1) * 50).limit(50))).all()
    return render(request, "admin/comms/emails.html", {"emails": rows, "status": status, "page": page,
                                                        "pages": max(1, math.ceil(total / 50))})


__all__ = ["router", "TEACHING_ROLES"]
