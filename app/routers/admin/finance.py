"""Fee payments (dues, proof verification, receipts) and staff salary payouts.

Proof rule: payment proofs (screenshots / receipts) are demanded ONLY when Cashfree is not set up.
* Cashfree PG active      -> students pay online, fees reflect automatically; no proof is required.
* Cashfree PG not set up  -> every payment needs a screenshot proof, verified by the office.
* Cashfree Payouts active -> salaries are paid through Cashfree and reflect automatically.
* Payouts not set up      -> the salary receipt / transfer proof is mandatory.
"""
from __future__ import annotations

import csv
import io
import math

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from ...config import settings
from ...constants import PAYMENT_METHODS
from ...database import get_db
from ...models import Batch, Enrollment, FeePayment, SalaryPayment, User, utcnow
from ...security import Principal, require_admin
from ...services import cashfree
from ...services.mailer import queue_email
from ...services.notify import notify_users
from ...services.payments import mark_fee_paid, notify_salary, send_fee_receipt, start_payout, sync_payout
from ...services.storage import DOC_EXT, UploadError, delete_file, has_file, save_upload
from ...templating import flash, format_inr, render
from ..common import (FormError, csv_row, ints, month_label, parse_date, parse_money, parse_period, redirect, today,
                      truthy)

router = APIRouter()
PAGE = 30


def fee_proof_required() -> bool:
    return not settings.cashfree_pg_active


def salary_proof_required() -> bool:
    return not settings.cashfree_payouts_active


async def _proof(upload: UploadFile | None, required: bool) -> str | None:
    if not has_file(upload):
        if required:
            raise FormError("Cashfree is not set up, so a payment proof (screenshot / photo of the receipt) is "
                            "mandatory.")
        return None
    return (await save_upload(upload, "receipts", DOC_EXT))[0]


def _fee_filters(stmt, status: str, batch: int, period: str, q: str):
    if status:
        stmt = stmt.where(FeePayment.status == status)
    if batch:
        stmt = stmt.where(FeePayment.batch_id == batch)
    if period:
        stmt = stmt.where(FeePayment.period == period)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(User.full_name.ilike(like), User.id_code.ilike(like), FeePayment.receipt_no.ilike(like)))
    return stmt


@router.get("/fees", response_class=HTMLResponse)
async def fees(request: Request, db: AsyncSession = Depends(get_db), status: str = "", batch: int = 0,
               period: str = "", q: str = "", page: int = 1):
    base = _fee_filters(select(FeePayment).join(User, User.id == FeePayment.user_id), status, batch, period, q)
    total = int(await db.scalar(select(func.count()).select_from(base.subquery())) or 0)
    page = max(1, page)
    rows = (await db.scalars(base.options(joinedload(FeePayment.user), joinedload(FeePayment.batch))
                             .order_by(FeePayment.id.desc()).offset((page - 1) * PAGE).limit(PAGE))).unique().all()
    summary_stmt = _fee_filters(select(FeePayment.status, func.count(), func.coalesce(func.sum(FeePayment.amount), 0))
                                .join(User, User.id == FeePayment.user_id), "", batch, period, q)
    summary = {r[0]: {"count": r[1], "amount": r[2]}
               for r in (await db.execute(summary_stmt.group_by(FeePayment.status))).all()}
    students = (await db.execute(select(User.id, User.full_name, User.id_code).where(
        User.role == "student", User.status == "active").order_by(User.full_name).limit(2000))).all()
    batches = (await db.scalars(select(Batch).order_by(Batch.name))).all()
    return render(request, "admin/finance/fees.html", {
        "fees": rows, "summary": summary, "students": students, "batches": batches, "methods": PAYMENT_METHODS,
        "status": status, "batch": batch, "period": period, "q": q, "page": page, "total": total,
        "pages": max(1, math.ceil(total / PAGE)), "proof_required": fee_proof_required(),
        "cashfree": settings.cashfree_pg_active})


@router.post("/fees/new")
async def fee_create(request: Request, db: AsyncSession = Depends(get_db), admin: Principal = Depends(require_admin),
                     user_id: int = Form(...), batch_id: int = Form(0), title: str = Form("Tuition fee"),
                     period: str = Form(...), amount: str = Form(...), due_date: str = Form(""),
                     status: str = Form("pending"), paid_on: str = Form(""), method: str = Form(""),
                     reference: str = Form(""), notes: str = Form(""), email_receipt: str = Form(""),
                     proof: UploadFile | None = File(None)):
    user = await db.get(User, user_id)
    proof_rel = None
    try:
        if user is None or not user.is_student:
            raise FormError("Choose a student.")
        fee = FeePayment(user_id=user.id, batch_id=batch_id or None, title=title.strip()[:80] or "Tuition fee",
                         period=parse_period(period), amount=parse_money(amount),
                         due_date=parse_date(due_date, "due date", required=False), status="pending",
                         notes=notes.strip() or None, recorded_by=admin.username)
        batch_cond = FeePayment.batch_id == batch_id if batch_id else FeePayment.batch_id.is_(None)
        if await db.scalar(select(FeePayment.id).where(FeePayment.user_id == fee.user_id, batch_cond,
                                                       FeePayment.period == fee.period,
                                                       FeePayment.title == fee.title)):
            raise FormError("A fee with the same title already exists for this student and month.")
        if status == "paid":
            proof_rel = await _proof(proof, fee_proof_required())
            paid_date = parse_date(paid_on, "payment date", required=False) or today()
    except (FormError, UploadError) as exc:
        flash(request, str(exc), "danger")
        return redirect("/admin/fees")
    db.add(fee)
    await db.flush()
    if status == "paid":
        fee.proof_path = proof_rel
        await mark_fee_paid(db, fee, method=method or "Cash", reference=reference.strip() or None, paid_on=paid_date,
                            actor=admin.username)
    await db.commit()
    if fee.status == "paid" and truthy(email_receipt):
        send_fee_receipt(fee, user)
    flash(request, f"Fee recorded for {user.full_name}.", "success")
    return redirect("/admin/fees")


@router.post("/fees/generate")
async def fee_generate(request: Request, db: AsyncSession = Depends(get_db), admin: Principal = Depends(require_admin),
                       period: str = Form(...), due_date: str = Form("")):
    form = await request.form()
    ids = ints(form.getlist("batch_ids"))
    try:
        period = parse_period(period)
        due = parse_date(due_date, "due date", required=False)
    except FormError as exc:
        flash(request, str(exc), "danger")
        return redirect("/admin/fees")
    stmt = (select(Enrollment.user_id, Batch.id, Batch.monthly_fee).join(Batch, Batch.id == Enrollment.batch_id)
            .join(User, User.id == Enrollment.user_id)
            .where(User.status == "active", Batch.is_active.is_(True), Batch.monthly_fee > 0))
    if ids:
        stmt = stmt.where(Batch.id.in_(ids))
    rows = [{"user_id": u, "batch_id": b, "title": "Tuition fee", "period": period, "amount": fee, "due_date": due,
             "status": "pending", "recorded_by": admin.username, "created_at": utcnow()}
            for u, b, fee in (await db.execute(stmt)).all()]
    created = 0
    if rows:
        result = await db.execute(pg_insert(FeePayment).values(rows)
                                  .on_conflict_do_nothing(constraint="uq_fee_period").returning(FeePayment.user_id))
        user_ids = list(result.scalars().all())
        created = len(user_ids)
        await notify_users(db, user_ids, f"Fee due for {month_label(period)}",
                           f"Due {due:%d %b %Y}" if due else None, "/student/fees", "fa-indian-rupee-sign")
    await db.commit()
    flash(request, f"Generated {created} fee due(s) for {month_label(period)} "
                   f"({len(rows) - created} already existed).", "success")
    return redirect(f"/admin/fees?period={period}")


@router.post("/fees/{fid}/pay")
async def fee_pay(fid: int, request: Request, db: AsyncSession = Depends(get_db),
                  admin: Principal = Depends(require_admin), paid_on: str = Form(""), method: str = Form("Cash"),
                  reference: str = Form(""), email_receipt: str = Form(""), proof: UploadFile | None = File(None)):
    fee = await db.scalar(select(FeePayment).options(joinedload(FeePayment.user)).where(FeePayment.id == fid))
    if fee is None:
        raise HTTPException(404)
    if fee.status == "paid":
        flash(request, "This fee is already marked as paid.", "info")
        return redirect("/admin/fees")
    try:
        proof_rel = await _proof(proof, fee_proof_required() and not fee.proof_path)
        paid_date = parse_date(paid_on, "payment date", required=False) or today()
    except (FormError, UploadError) as exc:
        flash(request, str(exc), "danger")
        return redirect("/admin/fees")
    if proof_rel:
        delete_file(fee.proof_path)
        fee.proof_path = proof_rel
    await mark_fee_paid(db, fee, method=method, reference=reference.strip() or None, paid_on=paid_date,
                        actor=admin.username)
    await db.commit()
    if truthy(email_receipt):
        send_fee_receipt(fee, fee.user)
    flash(request, f"Payment recorded — receipt {fee.receipt_no}.", "success")
    return redirect("/admin/fees")


@router.post("/fees/{fid}/verify")
async def fee_verify(fid: int, request: Request, db: AsyncSession = Depends(get_db),
                     admin: Principal = Depends(require_admin)):
    fee = await db.scalar(select(FeePayment).options(joinedload(FeePayment.user)).where(FeePayment.id == fid))
    if fee is None or fee.status != "verifying":
        raise HTTPException(400, "Nothing to verify")
    await mark_fee_paid(db, fee, method=fee.method or "UPI", reference=fee.proof_reference, paid_on=fee.paid_on,
                        actor=admin.username)
    fee.review_note = f"Proof verified by {admin.username}"
    await db.commit()
    send_fee_receipt(fee, fee.user)
    flash(request, f"Proof verified — {fee.user.full_name}'s receipt {fee.receipt_no} was emailed.", "success")
    return redirect("/admin/fees?status=verifying")


@router.post("/fees/{fid}/reject")
async def fee_reject(fid: int, request: Request, db: AsyncSession = Depends(get_db),
                     admin: Principal = Depends(require_admin), reason: str = Form("")):
    fee = await db.scalar(select(FeePayment).options(joinedload(FeePayment.user)).where(FeePayment.id == fid))
    if fee is None or fee.status != "verifying":
        raise HTTPException(400)
    if len(reason.strip()) < 3:
        flash(request, "Please tell the student why the proof was rejected.", "danger")
        return redirect("/admin/fees?status=verifying")
    fee.status = "overdue" if fee.due_date and fee.due_date < today() else "pending"
    fee.review_note, fee.reviewed_by = reason.strip()[:500], admin.username
    await notify_users(db, [fee.user_id], "Payment proof needs attention", reason.strip()[:160], "/student/fees",
                       "fa-triangle-exclamation")
    await db.commit()
    queue_email(fee.user.email, f"Payment proof not accepted — {fee.title} ({month_label(fee.period)})",
                "broadcast.html", category="fees", name=fee.user.full_name, sender="Accounts office",
                body=f"We could not verify the payment proof you submitted for {fee.title} "
                     f"({month_label(fee.period)}, {format_inr(fee.amount)}).\n\nReason: {reason.strip()}\n\n"
                     "Please upload a clearer screenshot or contact the office.")
    flash(request, "Proof rejected and the student was informed.", "success")
    return redirect("/admin/fees?status=verifying")


@router.post("/fees/{fid}/waive")
async def fee_waive(fid: int, request: Request, db: AsyncSession = Depends(get_db)):
    fee = await db.get(FeePayment, fid)
    if fee is None:
        raise HTTPException(404)
    fee.status = "waived"
    await db.commit()
    flash(request, "Fee waived.", "success")
    return redirect("/admin/fees")


@router.post("/fees/{fid}/delete")
async def fee_delete(fid: int, request: Request, db: AsyncSession = Depends(get_db)):
    fee = await db.get(FeePayment, fid)
    if fee is not None:
        path = fee.proof_path
        await db.delete(fee)
        await db.commit()
        delete_file(path)
    flash(request, "Fee record deleted.", "success")
    return redirect("/admin/fees")


@router.get("/fees/{fid}/receipt", response_class=HTMLResponse)
async def fee_receipt(fid: int, request: Request, db: AsyncSession = Depends(get_db)):
    fee = await db.scalar(select(FeePayment).options(joinedload(FeePayment.user), joinedload(FeePayment.batch))
                          .where(FeePayment.id == fid))
    if fee is None or fee.status != "paid":
        raise HTTPException(404, "Receipt not available")
    return render(request, "finance/receipt.html", {"fee": fee})


@router.get("/fees/export.csv")
async def fee_export(db: AsyncSession = Depends(get_db), status: str = "", batch: int = 0, period: str = "",
                     q: str = ""):
    stmt = _fee_filters(select(FeePayment).join(User, User.id == FeePayment.user_id), status, batch, period, q)
    rows = (await db.scalars(stmt.options(joinedload(FeePayment.user), joinedload(FeePayment.batch))
                             .order_by(FeePayment.id))).unique().all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Receipt", "Student", "ID", "Batch", "Title", "Month", "Amount", "Status", "Due", "Paid on", "Method",
                "Reference", "Source"])
    for f in rows:
        w.writerow(csv_row([f.receipt_no or "", f.user.full_name, f.user.id_code, f.batch.name if f.batch else "",
                            f.title, f.period, f.amount, f.status, f.due_date or "", f.paid_on or "", f.method or "",
                            f.reference or f.proof_reference or "",
                            "Cashfree" if f.gateway == "cashfree" else ("Proof" if f.proof_path else "Office")]))
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="fees.csv"'})


# ------------------------------------------------------------------ salaries / payouts
@router.get("/salaries", response_class=HTMLResponse)
async def salaries(request: Request, db: AsyncSession = Depends(get_db), period: str = ""):
    stmt = select(SalaryPayment).options(joinedload(SalaryPayment.user)).order_by(SalaryPayment.id.desc())
    if period:
        stmt = stmt.where(SalaryPayment.period == period)
    rows = (await db.scalars(stmt.limit(300))).unique().all()
    staff = (await db.scalars(select(User).where(User.role == "staff", User.status == "active")
                              .order_by(User.full_name))).all()
    totals = (await db.execute(select(SalaryPayment.period, func.sum(SalaryPayment.amount))
                               .where(SalaryPayment.status == "paid").group_by(SalaryPayment.period)
                               .order_by(SalaryPayment.period.desc()).limit(6))).all()
    return render(request, "admin/finance/salaries.html", {
        "payments": rows, "staff": staff, "totals": totals, "methods": PAYMENT_METHODS, "period": period,
        "proof_required": salary_proof_required(), "payouts": settings.cashfree_payouts_active})


async def _salary_from_form(db: AsyncSession, admin: Principal, user_id: int, title: str, period: str, amount: str,
                            notes: str) -> tuple[User, SalaryPayment]:
    user = await db.get(User, user_id)
    if user is None or not user.is_staff:
        raise FormError("Choose a staff member.")
    payment = SalaryPayment(user_id=user.id, title=title.strip()[:80] or "Salary", period=parse_period(period),
                            amount=parse_money(amount), notes=notes.strip() or None, recorded_by=admin.username)
    if await db.scalar(select(SalaryPayment.id).where(SalaryPayment.user_id == user.id,
                                                      SalaryPayment.period == payment.period,
                                                      SalaryPayment.title == payment.title)):
        raise FormError("A payment with this title already exists for that month.")
    return user, payment


@router.post("/salaries/new")
async def salary_create(request: Request, db: AsyncSession = Depends(get_db), admin: Principal = Depends(require_admin),
                        user_id: int = Form(...), title: str = Form("Salary"), period: str = Form(...),
                        amount: str = Form(...), paid_on: str = Form(""), method: str = Form("Bank transfer"),
                        reference: str = Form(""), notes: str = Form(""), receipt: UploadFile | None = File(None)):
    """Manual payout (cash / bank / UPI done outside the app)."""
    try:
        user, payment = await _salary_from_form(db, admin, user_id, title, period, amount, notes)
        payment.paid_on = parse_date(paid_on, "payment date", required=False) or today()
        payment.method, payment.reference = method[:20] or None, reference.strip()[:80] or None
        if not has_file(receipt) and salary_proof_required():
            raise FormError("Cashfree Payouts is not set up, so the salary receipt / transfer proof is mandatory.")
        if has_file(receipt):
            payment.receipt_path = (await save_upload(receipt, "receipts", DOC_EXT))[0]
    except (FormError, UploadError) as exc:
        flash(request, str(exc), "danger")
        return redirect("/admin/salaries")
    payment.status = "paid"
    db.add(payment)
    await db.flush()
    await notify_users(db, [user.id], f"Salary slip for {month_label(payment.period)} is available",
                       format_inr(payment.amount), "/staff/salary", "fa-file-invoice")
    await db.commit()
    notify_salary(payment, user)
    flash(request, f"Salary payment recorded and slip shared with {user.full_name}.", "success")
    return redirect("/admin/salaries")


@router.post("/salaries/payout")
async def salary_payout(request: Request, db: AsyncSession = Depends(get_db), admin: Principal = Depends(require_admin),
                        user_id: int = Form(...), title: str = Form("Salary"), period: str = Form(...),
                        amount: str = Form(...), notes: str = Form("")):
    """Pay through Cashfree Payouts - reflects in the statement automatically, no proof needed."""
    if not settings.cashfree_payouts_active:
        raise HTTPException(400, "Cashfree Payouts is not configured")
    try:
        user, payment = await _salary_from_form(db, admin, user_id, title, period, amount, notes)
        if not ((user.bank_account_number and user.bank_ifsc) or user.upi_id):
            raise FormError(f"Add {user.full_name}'s bank account + IFSC or UPI ID on their profile first.")
    except FormError as exc:
        flash(request, str(exc), "danger")
        return redirect("/admin/salaries")
    db.add(payment)
    await db.flush()
    try:
        await start_payout(db, payment, user)
    except cashfree.CashfreeError as exc:
        flash(request, f"Cashfree rejected the payout: {exc}. You can retry it from the list.", "danger")
        return redirect("/admin/salaries")
    msg = {"paid": "Salary paid via Cashfree ✓", "processing": "Payout initiated — the status will update "
           "automatically.", "failed": f"Payout failed: {payment.failure_reason}"}[payment.status]
    flash(request, msg, "success" if payment.status != "failed" else "danger")
    return redirect("/admin/salaries")


@router.post("/salaries/{sid}/sync")
async def salary_sync(sid: int, request: Request, db: AsyncSession = Depends(get_db)):
    payment = await db.scalar(select(SalaryPayment).options(joinedload(SalaryPayment.user))
                              .where(SalaryPayment.id == sid))
    if payment is None:
        raise HTTPException(404)
    try:
        if payment.status == "failed" and settings.cashfree_payouts_active:
            await start_payout(db, payment, payment.user)
        else:
            await sync_payout(db, payment)
    except cashfree.CashfreeError as exc:
        flash(request, f"Cashfree: {exc}", "danger")
        return redirect("/admin/salaries")
    flash(request, f"Payout status: {payment.status}.", "info")
    return redirect("/admin/salaries")


@router.post("/salaries/{sid}/delete")
async def salary_delete(sid: int, request: Request, db: AsyncSession = Depends(get_db)):
    payment = await db.get(SalaryPayment, sid)
    if payment is not None:
        if payment.status == "processing":
            flash(request, "A Cashfree payout is still processing — wait for it to finish.", "warning")
            return redirect("/admin/salaries")
        path = payment.receipt_path
        await db.delete(payment)
        await db.commit()
        delete_file(path)
    flash(request, "Salary record deleted.", "success")
    return redirect("/admin/salaries")


@router.get("/salaries/{sid}/slip", response_class=HTMLResponse)
async def salary_slip(sid: int, request: Request, db: AsyncSession = Depends(get_db)):
    payment = await db.scalar(select(SalaryPayment).options(joinedload(SalaryPayment.user))
                              .where(SalaryPayment.id == sid))
    if payment is None or payment.status != "paid":
        raise HTTPException(404)
    return render(request, "finance/salary_slip.html", {"payment": payment})


@router.get("/salaries/export.csv")
async def salary_export(db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(SalaryPayment).options(joinedload(SalaryPayment.user))
                             .order_by(SalaryPayment.period, SalaryPayment.id))).unique().all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Staff", "ID", "Role", "Title", "Month", "Amount", "Status", "Paid on", "Method", "Reference"])
    for p in rows:
        w.writerow(csv_row([p.user.full_name, p.user.id_code, p.user.role_label, p.title, p.period, p.amount,
                            p.status, p.paid_on or "", p.method or "", p.payout_reference or p.reference or ""]))
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="salaries.csv"'})
