"""Students & staff: registration (photo, date of birth, contact details and ID proof are mandatory),
virtual ID cards, credentials, removals with a stated reason, reinstatement and health records."""
from __future__ import annotations

import datetime as dt
import math
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from ...cache import site_cache
from ...config import settings
from ...constants import REMOVAL_TEMPLATES, STAFF_ROLES
from ...database import get_db
from ...models import (Attendance, Batch, Enrollment, FeePayment, HealthRecord, ModerationStrike, PerformanceAlert,
                       RemovalRecord, SalaryPayment, TestAttempt, User, utcnow)
from ...security import Principal, ahash_password, client_ip, generate_password, require_admin
from ...services.accounts import ban_account, delete_account, disconnect_user, restore_account, unban_account
from ...services.mailer import queue_email
from ...services.people import issue_id_card, make_id_code, send_onboarding, unique_username
from ...services.scheduling import validate_enrollment
from ...services.storage import DOC_EXT, UploadError, delete_file, has_file, save_image, save_upload
from ...templating import flash, render
from ..common import FormError, check_email, check_phone, ints, parse_date, parse_money, redirect, today, truthy

router = APIRouter()
PAGE_SIZE = 25
USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,60}$")
IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
UPI_RE = re.compile(r"^[\w.\-]{2,}@[A-Za-z][A-Za-z0-9]{1,}$")


async def all_batches(db: AsyncSession) -> list[Batch]:
    return list((await db.scalars(select(Batch).order_by(Batch.is_active.desc(), Batch.name))).all())


def parse_member_form(form, role: str) -> dict:
    def g(key: str) -> str:
        return str(form.get(key) or "").strip()

    full_name = g("full_name")
    if len(full_name) < 2:
        raise FormError("The full name is required.")
    dob = parse_date(g("dob"), "date of birth")
    age = (today() - dob).days / 365.25
    if not 2.5 <= age <= 100:
        raise FormError("Please check the date of birth.")
    if not g("id_proof_type") or not g("id_proof_number"):
        raise FormError("The ID proof type and number are mandatory.")
    username = g("username").lower()
    if username and not USERNAME_RE.match(username):
        raise FormError("Usernames may contain lowercase letters, numbers, dots, dashes and underscores (3-60).")
    password = g("password")
    if password and len(password) < 8:
        raise FormError("Passwords must be at least 8 characters (leave blank to auto-generate).")
    data = dict(full_name=full_name[:120], email=check_email(g("email")), phone=check_phone(g("phone")), dob=dob,
                gender=g("gender") or None, address=g("address") or None, id_proof_type=g("id_proof_type")[:40],
                id_proof_number=g("id_proof_number")[:60], username=username, password=password)
    if role == "student":
        if not g("class_level"):
            raise FormError("The student's class is required.")
        if not g("parent_name"):
            raise FormError("The parent / guardian name is required.")
        data.update(class_level=g("class_level")[:40], school_name=g("school_name") or None,
                    parent_name=g("parent_name")[:120], parent_relation=g("parent_relation") or None,
                    parent_email=check_email(g("parent_email"), "parent email", required=False),
                    parent_phone=check_phone(g("parent_phone"), "parent phone number"),
                    special_needs=truthy(g("special_needs")), special_needs_notes=g("special_needs_notes") or None)
    else:
        staff_role = g("staff_role")
        if staff_role not in STAFF_ROLES:
            raise FormError("Please choose the staff role.")
        account, ifsc, upi = g("bank_account_number").replace(" ", ""), g("bank_ifsc").upper(), g("upi_id")
        if account and not re.fullmatch(r"\d{6,18}", account):
            raise FormError("The bank account number should be 6-18 digits.")
        if ifsc and not IFSC_RE.match(ifsc):
            raise FormError("The IFSC code doesn't look valid (e.g. HDFC0001234).")
        if upi and not UPI_RE.match(upi):
            raise FormError("The UPI ID doesn't look valid (e.g. name@okbank).")
        data.update(staff_role=staff_role, designation=g("designation") or STAFF_ROLES[staff_role],
                    qualification=g("qualification") or None,
                    monthly_salary=parse_money(g("monthly_salary"), "monthly salary", allow_zero=True)
                    if g("monthly_salary") else None,
                    joining_date=parse_date(g("joining_date"), "joining date", required=False) or today(),
                    bank_account_name=g("bank_account_name")[:120] or None, bank_account_number=account or None,
                    bank_ifsc=ifsc or None, upi_id=upi[:100] or None)
    return data


def batch_ids_from(form) -> list[int]:
    return sorted(set(ints(form.getlist("batch_ids"))))


async def batch_names(db: AsyncSession, ids: list[int]) -> list[str]:
    if not ids:
        return []
    return list((await db.scalars(select(Batch.name).where(Batch.id.in_(ids)).order_by(Batch.name))).all())


# ------------------------------------------------------------------ lists
@router.get("/students", response_class=HTMLResponse)
async def students(request: Request, db: AsyncSession = Depends(get_db), q: str = "", batch: int = 0,
                   status: str = "active", page: int = 1):
    return await _list(request, db, "student", q, batch, status, page, "")


@router.get("/staff", response_class=HTMLResponse)
async def staff(request: Request, db: AsyncSession = Depends(get_db), q: str = "", status: str = "active",
                page: int = 1, staff_role: str = ""):
    return await _list(request, db, "staff", q, 0, status, page, staff_role)


async def _list(request: Request, db: AsyncSession, role: str, q: str, batch: int, status: str, page: int,
                staff_role: str):
    stmt = select(User).where(User.role == role)
    if status in ("active", "removed", "banned", "deleted"):
        stmt = stmt.where(User.status == status)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(User.full_name.ilike(like), User.email.ilike(like), User.username.ilike(like),
                              User.id_code.ilike(like), User.phone.ilike(like), User.parent_name.ilike(like)))
    if batch:
        stmt = stmt.where(User.id.in_(select(Enrollment.user_id).where(Enrollment.batch_id == batch)))
    if staff_role:
        stmt = stmt.where(User.staff_role == staff_role)
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    page = max(1, page)
    people = (await db.scalars(
        stmt.options(selectinload(User.enrollments).joinedload(Enrollment.batch), selectinload(User.health_records))
        .order_by(User.full_name).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE))).all()
    taught = {}
    if role == "staff" and people:
        taught = dict((await db.execute(select(Batch.teacher_id, func.count()).where(
            Batch.teacher_id.in_([p.id for p in people])).group_by(Batch.teacher_id))).all())
    return render(request, "admin/people/list.html", {
        "people": people, "role": role, "total": total, "page": page, "pages": max(1, math.ceil(total / PAGE_SIZE)),
        "q": q, "batch": batch, "status": status, "staff_role": staff_role, "taught": taught,
        "batches": await all_batches(db)})


# ------------------------------------------------------------------ create / edit
@router.get("/members/new", response_class=HTMLResponse)
async def member_new(request: Request, role: str = "student", db: AsyncSession = Depends(get_db)):
    if role not in ("student", "staff"):
        raise HTTPException(404)
    return render(request, "admin/people/form.html", {"member": None, "role": role, "values": {}, "enrolled": [],
                                                       "batches": await all_batches(db)})


@router.post("/members/new")
async def member_create(request: Request, db: AsyncSession = Depends(get_db),
                        admin: Principal = Depends(require_admin), photo: UploadFile | None = File(None),
                        id_proof: UploadFile | None = File(None)):
    form = await request.form()
    role = str(form.get("role") or "student")
    if role not in ("student", "staff"):
        raise HTTPException(400)
    values = {k: v for k, v in form.items() if isinstance(v, str) and k not in ("password", "csrf_token", "_once")}
    ids = batch_ids_from(form) if role == "student" else []
    saved: list[str] = []
    try:
        data = parse_member_form(form, role)
        if not has_file(photo):
            raise FormError("A photo is mandatory for registration.")
        if not has_file(id_proof):
            raise FormError("An ID proof document (image or PDF) is mandatory.")
        errors = await validate_enrollment(db, ids, ids)
        if errors:
            raise FormError(" ".join(errors))
        username = data.pop("username") or await unique_username(db, data["full_name"])
        if await db.scalar(select(User.id).where(func.lower(User.username) == username)):
            raise FormError(f"The username “{username}” is already taken.")
        password = data.pop("password") or generate_password()
        photo_rel = await save_image(photo, "photos", 900)
        saved.append(photo_rel)
        proof_rel, _ = await save_upload(id_proof, "idproofs", DOC_EXT)
        saved.append(proof_rel)
    except (FormError, UploadError) as exc:
        for rel in saved:
            delete_file(rel)
        flash(request, str(exc), "danger")
        return render(request, "admin/people/form.html", {"member": None, "role": role, "values": values,
                                                           "enrolled": ids, "batches": await all_batches(db)},
                      status_code=400)
    user = User(role=role, username=username, password_hash=await ahash_password(password), photo_path=photo_rel,
                id_proof_path=proof_rel, created_by=admin.username, **data)
    db.add(user)
    await db.flush()
    user.id_code = make_id_code(user)
    for bid in ids:
        db.add(Enrollment(user_id=user.id, batch_id=bid))
    condition = str(form.get("health_condition") or "").strip()
    if role == "student" and condition:
        db.add(HealthRecord(user_id=user.id, condition=condition[:160],
                            severity=str(form.get("health_severity") or "low"),
                            notes=str(form.get("health_notes") or "").strip() or None,
                            medication=str(form.get("health_medication") or "").strip() or None,
                            added_by=admin.username))
    await db.commit()
    names = await batch_names(db, ids)
    card = await issue_id_card(user, names)
    await db.commit()
    send_onboarding(user, password, names, card, is_new=True)
    flash(request, f"{user.full_name} was registered as {user.id_code}. A thank-you note, the virtual ID card and "
                   f"the confidential login details were emailed to {user.email}.", "success")
    return redirect(f"/admin/members/{user.id}")


@router.get("/members/{uid}/edit", response_class=HTMLResponse)
async def member_edit(uid: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await db.scalar(select(User).options(selectinload(User.enrollments)).where(User.id == uid))
    if user is None:
        raise HTTPException(404)
    return render(request, "admin/people/form.html", {
        "member": user, "role": user.role, "values": {}, "enrolled": [e.batch_id for e in user.enrollments],
        "batches": await all_batches(db)})


@router.post("/members/{uid}/edit")
async def member_update(uid: int, request: Request, db: AsyncSession = Depends(get_db),
                        photo: UploadFile | None = File(None), id_proof: UploadFile | None = File(None)):
    user = await db.scalar(select(User).options(selectinload(User.enrollments)).where(User.id == uid))
    if user is None:
        raise HTTPException(404)
    form = await request.form()
    old_ids = {e.batch_id for e in user.enrollments}
    new_ids = set(batch_ids_from(form)) if user.is_student else old_ids
    try:
        data = parse_member_form(form, user.role)
        username = data.pop("username")
        data.pop("password")
        if username and username != user.username:
            if await db.scalar(select(User.id).where(func.lower(User.username) == username, User.id != uid)):
                raise FormError(f"The username “{username}” is already taken.")
            user.username = username
        errors = await validate_enrollment(db, sorted(new_ids), sorted(new_ids - old_ids))
        if errors:
            raise FormError(" ".join(errors))
        if has_file(photo):
            new_photo = await save_image(photo, "photos", 900)
            delete_file(user.photo_path)
            user.photo_path = new_photo
        if has_file(id_proof):
            new_proof, _ = await save_upload(id_proof, "idproofs", DOC_EXT)
            delete_file(user.id_proof_path)
            user.id_proof_path = new_proof
    except (FormError, UploadError) as exc:
        flash(request, str(exc), "danger")
        return redirect(f"/admin/members/{uid}/edit")
    card_changed = has_file(photo) or data["full_name"] != user.full_name or new_ids != old_ids
    for key, value in data.items():
        setattr(user, key, value)
    if new_ids != old_ids:
        removed = old_ids - new_ids
        if removed:
            await db.execute(delete(Enrollment).where(Enrollment.user_id == uid, Enrollment.batch_id.in_(removed)))
        for bid in new_ids - old_ids:
            db.add(Enrollment(user_id=uid, batch_id=bid))
    await db.commit()
    if card_changed:
        await issue_id_card(user, await batch_names(db, sorted(new_ids)))
        await db.commit()
    flash(request, "Profile updated." + (" The ID card was regenerated." if card_changed else ""), "success")
    return redirect(f"/admin/members/{uid}")


# ------------------------------------------------------------------ detail
@router.get("/members/{uid}", response_class=HTMLResponse)
async def member_detail(uid: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await db.scalar(select(User).options(
        selectinload(User.enrollments).joinedload(Enrollment.batch), selectinload(User.health_records))
        .where(User.id == uid))
    if user is None:
        raise HTTPException(404)
    ctx: dict = {"member": user, "templates": REMOVAL_TEMPLATES}
    if user.is_student:
        ctx["attendance"] = dict((await db.execute(select(Attendance.status, func.count()).where(
            Attendance.user_id == uid).group_by(Attendance.status))).all())
        ctx["attempts"] = (await db.scalars(select(TestAttempt).options(joinedload(TestAttempt.test))
                                            .where(TestAttempt.user_id == uid).order_by(TestAttempt.id.desc())
                                            .limit(10))).all()
        ctx["fees"] = (await db.scalars(select(FeePayment).options(joinedload(FeePayment.batch))
                                        .where(FeePayment.user_id == uid).order_by(FeePayment.period.desc())
                                        .limit(12))).all()
        ctx["alerts"] = (await db.scalars(select(PerformanceAlert).options(joinedload(PerformanceAlert.batch))
                                          .where(PerformanceAlert.user_id == uid)
                                          .order_by(PerformanceAlert.id.desc()).limit(5))).all()
    else:
        ctx["taught"] = (await db.scalars(select(Batch).where(Batch.teacher_id == uid).order_by(Batch.name))).all()
        ctx["salaries"] = (await db.scalars(select(SalaryPayment).where(SalaryPayment.user_id == uid)
                                            .order_by(SalaryPayment.period.desc()).limit(12))).all()
    ctx["removals"] = (await db.scalars(select(RemovalRecord).where(RemovalRecord.user_id == uid)
                                        .order_by(RemovalRecord.id.desc()))).all()
    ctx["strikes"] = int(await db.scalar(select(func.count()).select_from(ModerationStrike)
                                         .where(ModerationStrike.user_id == uid)) or 0)
    ctx["removal_fill"] = {key: {"reason": t["reason"],
                                 "email_body": default_removal_body(user.full_name, t["reason"] or "{reason}")}
                           for key, t in REMOVAL_TEMPLATES.items()}
    return render(request, "admin/people/detail.html", ctx)


def default_removal_body(name: str, reason: str) -> str:
    site = site_cache.data
    return (f"Dear {name},\n\nThis is to inform you that your access to {site['site_name']} has been withdrawn with "
            f"immediate effect.\n\nReason: {reason}\n\nIf you believe this decision was made in error, or you "
            f"would like to discuss it, please reply to this email or contact the office at "
            f"{site['contact_email']}.\n\nRegards,\nAdministration, {site['site_name']}")


@router.post("/members/{uid}/remove")
async def member_remove(uid: int, request: Request, db: AsyncSession = Depends(get_db),
                        admin: Principal = Depends(require_admin), template_key: str = Form("custom"),
                        reason: str = Form(""), email_subject: str = Form(""), email_body: str = Form(""),
                        notify: str = Form("")):
    user = await db.get(User, uid)
    if user is None:
        raise HTTPException(404)
    reason = reason.strip()
    if len(reason) < 5:
        flash(request, "Please state the reason for removing this person — it is mandatory.", "danger")
        return redirect(f"/admin/members/{uid}")
    template_key = template_key if template_key in REMOVAL_TEMPLATES else "custom"
    subject = email_subject.strip()[:200] or f"Notice regarding your access to {site_cache.data['site_name']}"
    body = email_body.strip().replace("{reason}", reason) or default_removal_body(user.full_name, reason)
    user.status, user.status_reason, user.status_changed_at = "removed", reason, utcnow()
    db.add(RemovalRecord(user_id=uid, action="removed", template_key=template_key, reason=reason,
                         email_subject=subject, email_body=body, actor=admin.username))
    await db.commit()
    await disconnect_user(uid, "Your access has been removed by the administrator.")  # incl. friends' chats
    if truthy(notify):
        cc = [user.parent_email] if user.is_student and user.parent_email else None
        queue_email(user.email, subject, "removal.html", cc=cc, category="removal", body=body, name=user.full_name)
    flash(request, f"{user.full_name} was removed." + (" The reason was emailed to them." if truthy(notify) else ""),
          "success")
    return redirect(f"/admin/members/{uid}")


@router.post("/members/{uid}/reinstate")
async def member_reinstate(uid: int, request: Request, db: AsyncSession = Depends(get_db),
                           admin: Principal = Depends(require_admin), reason: str = Form(""),
                           notify: str = Form("")):
    user = await db.get(User, uid)
    if user is None:
        raise HTTPException(404)
    if user.status != "removed":  # banned / deleted accounts have their own way back (unban / restore)
        flash(request, "Use Unban or Restore for this account.", "warning")
        return redirect(f"/admin/members/{uid}")
    user.status, user.status_reason, user.status_changed_at = "active", None, utcnow()
    db.add(RemovalRecord(user_id=uid, action="reinstated", reason=reason.strip() or "Access restored",
                         actor=admin.username))
    await db.commit()
    if truthy(notify):
        queue_email(user.email, f"Welcome back to {site_cache.data['site_name']}", "reinstated.html",
                    category="removal", name=user.full_name, reason=reason.strip())
    flash(request, f"{user.full_name} has been reinstated.", "success")
    return redirect(f"/admin/members/{uid}")


async def _member(db: AsyncSession, uid: int) -> User:
    user = await db.get(User, uid)
    if user is None:
        raise HTTPException(404)
    return user


@router.post("/members/{uid}/ban")
async def member_ban(uid: int, request: Request, db: AsyncSession = Depends(get_db),
                     admin: Principal = Depends(require_admin), reason: str = Form(""), block_ip: str = Form(""),
                     notify: str = Form("")):
    user = await _member(db, uid)
    if len(reason.strip()) < 5:
        flash(request, "Please state the reason for the ban — it is shown to the person when they try to sign in.",
              "danger")
        return redirect(f"/admin/members/{uid}")
    if user.status == "deleted":
        flash(request, "This account is deleted — restore it first if you want to ban it instead.", "warning")
        return redirect(f"/admin/members/{uid}")
    blocked = await ban_account(db, user, reason=reason.strip()[:500], actor=admin.username,
                                block_ip=truthy(block_ip), admin_ip=client_ip(request), notify=truthy(notify))
    flash(request, f"{user.full_name} is banned — signed out everywhere and shown as banned."
          + (f" Their network address {blocked} is blocked too." if blocked else ""), "success")
    return redirect(f"/admin/members/{uid}")


@router.post("/members/{uid}/unban")
async def member_unban(uid: int, request: Request, db: AsyncSession = Depends(get_db),
                       admin: Principal = Depends(require_admin), reason: str = Form(""), notify: str = Form("")):
    user = await _member(db, uid)
    if user.status != "banned":
        return redirect(f"/admin/members/{uid}")
    lifted = await unban_account(db, user, note=reason.strip()[:500], actor=admin.username, notify=truthy(notify))
    flash(request, f"{user.full_name} is no longer banned."
          + (f" {lifted} network block(s) linked to them were lifted." if lifted else ""), "success")
    return redirect(f"/admin/members/{uid}")


@router.post("/members/{uid}/delete")
async def member_delete(uid: int, request: Request, db: AsyncSession = Depends(get_db),
                        admin: Principal = Depends(require_admin), reason: str = Form(""), notify: str = Form("")):
    user = await _member(db, uid)
    if len(reason.strip()) < 5:
        flash(request, "Please state why the account is being deleted.", "danger")
        return redirect(f"/admin/members/{uid}")
    if user.status == "deleted":
        return redirect(f"/admin/members/{uid}")
    purge_on = await delete_account(db, user, reason=reason.strip()[:500], actor=admin.username, notify=truthy(notify))
    flash(request, f"{user.full_name}'s account is deleted and dormant. Everything is removed for good on "
                   f"{purge_on.astimezone(settings.tz):%d %b %Y} unless you restore it before then.", "success")
    return redirect(f"/admin/members/{uid}")


@router.post("/members/{uid}/restore")
async def member_restore(uid: int, request: Request, db: AsyncSession = Depends(get_db),
                         admin: Principal = Depends(require_admin), reason: str = Form(""), notify: str = Form("")):
    user = await _member(db, uid)
    if user.status != "deleted":
        return redirect(f"/admin/members/{uid}")
    await restore_account(db, user, note=reason.strip()[:500], actor=admin.username, notify=truthy(notify))
    flash(request, f"{user.full_name}'s account is restored with all its data.", "success")
    return redirect(f"/admin/members/{uid}")


@router.post("/members/{uid}/reset-password")
async def member_reset_password(uid: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await db.scalar(select(User).options(selectinload(User.enrollments)).where(User.id == uid))
    if user is None:
        raise HTTPException(404)
    password = generate_password()
    user.password_hash = await ahash_password(password)
    await db.commit()
    card = await issue_id_card(user, await batch_names(db, [e.batch_id for e in user.enrollments]))
    await db.commit()
    send_onboarding(user, password, [], card, is_new=False)
    flash(request, f"A new confidential password was emailed to {user.email}.", "success")
    return redirect(f"/admin/members/{uid}")


@router.post("/members/{uid}/resend-card")
async def member_resend_card(uid: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await db.scalar(select(User).options(selectinload(User.enrollments)).where(User.id == uid))
    if user is None:
        raise HTTPException(404)
    names = await batch_names(db, [e.batch_id for e in user.enrollments])
    card = await issue_id_card(user, names)
    await db.commit()
    send_onboarding(user, None, names, card, is_new=False)
    flash(request, f"The ID card was regenerated and emailed to {user.email}.", "success")
    return redirect(f"/admin/members/{uid}")


@router.post("/members/{uid}/health")
async def health_add(uid: int, request: Request, db: AsyncSession = Depends(get_db),
                     admin: Principal = Depends(require_admin), condition: str = Form(...),
                     severity: str = Form("low"), notes: str = Form(""), medication: str = Form(""),
                     emergency_contact: str = Form("")):
    if await db.get(User, uid) is None:
        raise HTTPException(404)
    if not condition.strip():
        flash(request, "Please describe the health condition.", "danger")
        return redirect(f"/admin/members/{uid}")
    db.add(HealthRecord(user_id=uid, condition=condition.strip()[:160],
                        severity=severity if severity in ("low", "medium", "high") else "low",
                        notes=notes.strip() or None, medication=medication.strip()[:255] or None,
                        emergency_contact=emergency_contact.strip()[:120] or None, added_by=admin.username))
    await db.commit()
    flash(request, "Health record added. Teachers of this student's batches can now see it.", "success")
    return redirect(f"/admin/members/{uid}#health")


@router.post("/health/{rid}/delete")
async def health_delete(rid: int, request: Request, db: AsyncSession = Depends(get_db)):
    record = await db.get(HealthRecord, rid)
    if record is None:
        raise HTTPException(404)
    uid = record.user_id
    await db.delete(record)
    await db.commit()
    flash(request, "Health record removed.", "success")
    return redirect(f"/admin/members/{uid}#health")


__all__ = ["router", "all_batches", "dt"]
