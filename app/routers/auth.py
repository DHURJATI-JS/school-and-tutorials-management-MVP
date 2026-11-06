"""Authentication: member password login, ID-card OCR login + OTP (email & phone), admin login, logout."""
from __future__ import annotations

import datetime as dt
import logging

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..cache import site_cache
from ..config import settings
from ..database import get_db
from ..models import LoginEvent, OTPCode, User, utcnow
from ..security import (account_notice, admins, averify_password, client_ip, csrf_protect, dummy_hash, generate_otp,
                        hash_otp, limiter, optional_principal, session_fingerprint, start_session, verify_otp)
from ..services.background import spawn
from ..services.mailer import queue_email
from ..services.ocr import OCRUnavailable, extract_id_codes, ocr_image
from ..services.sms import mask_phone, send_sms
from ..templating import flash, render
from .common import mask_email, redirect, safe_next

router = APIRouter()
log = logging.getLogger("classnest.auth")
HOUR = 3600


def lock_window() -> int:
    return settings.ACCOUNT_LOCK_MINUTES * 60


def account_locked(key: str) -> bool:
    """Per-ACCOUNT failure counter - the per-IP limit alone is dodged by guessing from many addresses."""
    return limiter.count(key, lock_window()) >= settings.ACCOUNT_LOCK_THRESHOLD


def record_failure(key: str) -> None:
    limiter.hit(key, 10**9, lock_window())


async def audit(db: AsyncSession, request: Request, username: str, method: str, success: bool,
                detail: str | None = None) -> None:
    db.add(LoginEvent(ip=client_ip(request), username=username[:120], method=method, success=success,
                      detail=detail, user_agent=(request.headers.get("user-agent") or "")[:300]))


async def complete_login(request: Request, db: AsyncSession, user: User, method: str) -> None:
    user.last_login_at = utcnow()
    user.last_ip = client_ip(request)
    await audit(db, request, user.username, method, True)
    await db.commit()
    limiter.reset(f"login-fail:u{user.id}")
    start_session(request, {"kind": user.role, "id": user.id, "fp": session_fingerprint(user.password_hash)})
    flash(request, f"Welcome back, {user.full_name.split()[0]}! 👋", "success")


async def start_otp(request: Request, db: AsyncSession, user: User, method: str, next_url: str):
    if not limiter.hit(f"otp-send:{user.id}", settings.OTP_SENDS_PER_HOUR, HOUR):
        # stops e-mail/SMS bombing of one person with a photo of their card (and SMS costs)
        await audit(db, request, user.username, method, False, "OTP send limit reached")
        await db.commit()
        flash(request, "Too many sign-in codes were requested for this account. Please wait an hour or sign in "
                       "with your username and password.", "warning")
        return redirect("/login")
    code = generate_otp()
    otp = OTPCode(user_id=user.id, code_hash=hash_otp(code), purpose="login",
                  expires_at=utcnow() + dt.timedelta(seconds=settings.OTP_TTL_SECONDS))
    db.add(otp)
    await db.flush()
    await audit(db, request, user.username, method, True, "OTP sent")
    await db.commit()
    request.session["otp"] = {"id": otp.id, "user": user.id, "method": method, "next": next_url}
    _send_code(user, code)
    flash(request, f"We sent a {settings.OTP_LENGTH}-digit code to {mask_email(user.email)} and "
                   f"{mask_phone(user.phone)}.", "info")
    return redirect("/login/otp")


def _send_code(user: User, code: str) -> None:
    site = site_cache.data["site_name"]
    minutes = max(1, settings.OTP_TTL_SECONDS // 60)
    queue_email(user.email, f"{code} is your {site} sign-in code", "otp.html", category="otp", code=code,
                name=user.full_name.split()[0], minutes=minutes)
    spawn(send_sms(user.phone, f"{code} is your {site} sign-in code. Valid for {minutes} min. Never share it."),
          name="sms:otp")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/", tab: str = "member", principal=Depends(optional_principal)):
    if principal is not None and principal.kind != "guest":
        return redirect(principal.home_url)
    return render(request, "auth/login.html", {"next": safe_next(next), "tab": tab,
                                               "account_notice": request.session.pop("account_notice", None)})


@router.post("/login", dependencies=[Depends(csrf_protect)])
async def member_login(request: Request, identifier: str = Form(..., max_length=255),
                       password: str = Form(..., max_length=200), next: str = Form("/"),
                       db: AsyncSession = Depends(get_db)):
    ip = client_ip(request)
    if not limiter.hit(f"login:{ip}", settings.LOGIN_RATE_LIMIT, 300):
        flash(request, "Too many sign-in attempts. Please wait a few minutes and try again.", "danger")
        return redirect("/login")
    ident = identifier.strip().lower()
    user = await db.scalar(select(User).where(or_(func.lower(User.username) == ident,
                                                  func.lower(User.id_code) == ident)).limit(1))
    if user is None and "@" in ident:
        matches = (await db.scalars(select(User).where(func.lower(User.email) == ident).limit(2))).all()
        if len(matches) > 1:
            flash(request, "Several accounts share this email — please sign in with your username or ID number.",
                  "warning")
            return redirect(f"/login?next={safe_next(next)}")
        user = matches[0] if matches else None
    fail_key = f"login-fail:u{user.id}" if user else f"login-fail:{ident[:120]}"
    if account_locked(fail_key):
        await audit(db, request, ident, "password", False, "account temporarily locked")
        await db.commit()
        flash(request, f"Too many failed sign-in attempts for this account. Please wait "
                       f"{settings.ACCOUNT_LOCK_MINUTES} minutes or contact the office.", "danger")
        return redirect("/login")
    valid = await averify_password(password, user.password_hash if user else dummy_hash())
    if user is None or not valid:
        record_failure(fail_key)
        await audit(db, request, ident, "password", False, "bad credentials")
        await db.commit()
        flash(request, "Incorrect username or password.", "danger")
        return redirect(f"/login?next={safe_next(next)}")
    if user.status != "active":  # only after the password matched, so the status never leaks to a guesser
        await audit(db, request, ident, "password", False, f"account {user.status}")
        await db.commit()
        request.session["account_notice"] = account_notice(user)
        return redirect("/login")
    target = safe_next(next, f"/{user.role}")
    if settings.REQUIRE_OTP_FOR_PASSWORD_LOGIN:
        return await start_otp(request, db, user, "password", target)
    await complete_login(request, db, user, "password")
    return redirect(target if target != "/" else f"/{user.role}")


@router.post("/login/admin", dependencies=[Depends(csrf_protect)])
async def admin_login(request: Request, username: str = Form(..., max_length=60),
                      password: str = Form(..., max_length=200), next: str = Form("/admin"),
                      db: AsyncSession = Depends(get_db)):
    ip = client_ip(request)
    if not limiter.hit(f"admin-login:{ip}", 6, 300):
        flash(request, "Too many attempts. Please wait a few minutes.", "danger")
        return redirect("/login?tab=admin")
    name = username.strip().lower()
    fail_key = f"admin-fail:{name[:60]}"
    if account_locked(fail_key):
        await audit(db, request, name, "admin", False, "account temporarily locked")
        await db.commit()
        flash(request, f"Too many failed attempts for this admin account. Please wait "
                       f"{settings.ACCOUNT_LOCK_MINUTES} minutes.", "danger")
        return redirect("/login?tab=admin")
    if not await admins.verify(username, password):
        record_failure(fail_key)
        await audit(db, request, username, "admin", False, "bad credentials")
        await db.commit()
        flash(request, "Incorrect admin username or password.", "danger")
        return redirect("/login?tab=admin")
    limiter.reset(fail_key)
    await audit(db, request, name, "admin", True)
    await db.commit()
    start_session(request, {"kind": "admin", "username": name, "name": name.replace(".", " ").title(),
                            "fp": admins.fingerprint(name)})
    flash(request, f"Signed in as administrator {name}.", "success")
    target = safe_next(next, "/admin")
    return redirect(target if target.startswith("/admin") else "/admin")


@router.post("/login/id-card", dependencies=[Depends(csrf_protect)])
async def idcard_login(request: Request, card: UploadFile = File(...), next: str = Form("/"),
                       db: AsyncSession = Depends(get_db)):
    ip = client_ip(request)
    if not limiter.hit(f"ocr-login:{ip}", 6, 600):
        flash(request, "Too many scans. Please wait a few minutes.", "danger")
        return redirect("/login?tab=card")
    data = await card.read()
    if not data or len(data) > 8 * 1024 * 1024 or not (card.content_type or "").startswith("image/"):
        flash(request, "Please upload a clear photo of your ID card (JPG/PNG, under 8 MB).", "danger")
        return redirect("/login?tab=card")
    try:
        text = await ocr_image(data, card.filename or "card.png", card.content_type or "image/png")
    except OCRUnavailable as exc:
        log.warning("%s", exc)
        flash(request, "The ID-card scanner is offline right now — please sign in with your username and "
                       "password instead.", "warning")
        return redirect("/login")
    codes = extract_id_codes(text)
    # any status: the one-time code proves who it is, then otp_verify explains a ban / deletion
    users = list((await db.scalars(select(User).where(User.id_code.in_(codes)))).all()) if codes else []
    if len(users) != 1:
        await audit(db, request, ",".join(codes) or "unreadable card", "ocr", False,
                    "no match" if codes else "no ID found")
        await db.commit()
        flash(request, "We couldn't recognise that ID card. Make sure it is flat, well lit and in focus — "
                       "or sign in with your password.", "danger")
        return redirect("/login?tab=card")
    user = users[0]
    if not any(part.lower() in text.lower() for part in user.full_name.split() if len(part) > 2):
        log.info("OCR login for %s: name not found on the card image (soft check)", user.id_code)
    return await start_otp(request, db, user, "ocr", safe_next(next, f"/{user.role}"))


@router.get("/login/otp", response_class=HTMLResponse)
async def otp_page(request: Request, db: AsyncSession = Depends(get_db)):
    pending = request.session.get("otp")
    if not pending:
        return redirect("/login")
    user = await db.get(User, pending["user"])
    if user is None:
        request.session.pop("otp", None)
        return redirect("/login")
    return render(request, "auth/otp.html", {"email": mask_email(user.email), "phone": mask_phone(user.phone),
                                             "length": settings.OTP_LENGTH,
                                             "resend_seconds": settings.OTP_RESEND_SECONDS})


@router.post("/login/otp", dependencies=[Depends(csrf_protect)])
async def otp_verify(request: Request, code: str = Form(..., max_length=12), db: AsyncSession = Depends(get_db)):
    pending = request.session.get("otp")
    if not pending:
        return redirect("/login")
    otp = await db.get(OTPCode, pending["id"])
    if otp is None or otp.user_id != pending["user"] or otp.consumed_at or otp.expires_at < utcnow():
        request.session.pop("otp", None)
        flash(request, "That code has expired — please start again.", "warning")
        return redirect("/login")
    fail_key = f"otp-fail:{otp.user_id}"
    if otp.attempts >= settings.OTP_MAX_ATTEMPTS or limiter.count(fail_key, HOUR) >= settings.OTP_FAILURES_PER_HOUR:
        request.session.pop("otp", None)
        flash(request, "Too many incorrect codes. Please start again later.", "danger")
        return redirect("/login")
    if not verify_otp(code, otp.code_hash):
        otp.attempts += 1
        limiter.hit(fail_key, 10**9, HOUR)  # per person, across codes and resends
        await db.commit()
        left = settings.OTP_MAX_ATTEMPTS - otp.attempts
        flash(request, f"Incorrect code — {left} attempt(s) left.", "danger")
        return redirect("/login/otp")
    otp.consumed_at = utcnow()
    limiter.reset(fail_key)
    user = await db.get(User, otp.user_id)
    if user is None or user.status != "active":
        await db.commit()
        request.session.pop("otp", None)
        if user is not None:
            request.session["account_notice"] = account_notice(user)
        else:
            flash(request, "This account is not active.", "danger")
        return redirect("/login")
    target = safe_next(pending.get("next"), f"/{user.role}")
    await complete_login(request, db, user, f"otp:{pending.get('method', 'ocr')}")
    return redirect(target if target != "/" else f"/{user.role}")


@router.post("/login/otp/resend", dependencies=[Depends(csrf_protect)])
async def otp_resend(request: Request, db: AsyncSession = Depends(get_db)):
    pending = request.session.get("otp")
    if not pending:
        return redirect("/login")
    otp = await db.get(OTPCode, pending["id"])
    user = await db.get(User, pending["user"])
    if otp is None or user is None or otp.consumed_at:
        return redirect("/login")
    wait = settings.OTP_RESEND_SECONDS - int((utcnow() - otp.last_sent_at).total_seconds())
    if wait > 0:
        flash(request, f"Please wait {wait} seconds before requesting a new code.", "warning")
        return redirect("/login/otp")
    if otp.attempts >= settings.OTP_MAX_ATTEMPTS:
        request.session.pop("otp", None)
        flash(request, "Too many incorrect codes. Please start again.", "danger")
        return redirect("/login")
    if not limiter.hit(f"otp-send:{user.id}", settings.OTP_SENDS_PER_HOUR, HOUR):
        flash(request, "Too many codes were requested. Please wait an hour or sign in with your password.",
              "warning")
        return redirect("/login/otp")
    code = generate_otp()
    # A new code keeps the wrong-attempt count: resetting it allowed unlimited guessing (5 per resend).
    otp.code_hash = hash_otp(code)
    otp.expires_at = utcnow() + dt.timedelta(seconds=settings.OTP_TTL_SECONDS)
    otp.last_sent_at = utcnow()
    await db.commit()
    _send_code(user, code)
    flash(request, "A new code is on its way.", "info")
    return redirect("/login/otp")


@router.post("/logout", dependencies=[Depends(csrf_protect)])
async def logout(request: Request):
    request.session.clear()
    flash(request, "You've been signed out. See you soon!", "info")
    return redirect("/login")
