"""Payment workflows shared by admin, student portal, webhooks and jobs.

Fees
* manual payment  -> the student (or office) uploads a screenshot as proof -> status "verifying"
                     -> the office approves (paid + receipt) or rejects (with a reason)
* Cashfree active -> online checkout; the order is verified with Cashfree and the fee is marked paid
                     automatically (no screenshot), so it reflects in the statement immediately
Salaries
* manual payout   -> the salary receipt / transfer screenshot is mandatory proof
* Cashfree Payouts -> transfer via API; status (processing -> paid / failed) syncs automatically
"""
from __future__ import annotations

import logging
import secrets
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from ..config import settings
from ..database import SessionLocal
from ..models import FeePayment, SalaryPayment, User
from ..templating import format_inr
from . import cashfree
from .mailer import queue_email
from .notify import notify_users

log = logging.getLogger("classnest.payments")


def _today():
    import datetime as dt

    return dt.datetime.now(settings.tz).date()


def month_label(period: str) -> str:
    import datetime as dt

    try:
        return dt.date.fromisoformat(period + "-01").strftime("%b %Y")
    except ValueError:
        return period


def send_fee_receipt(fee: FeePayment, user: User) -> None:
    cc = [user.parent_email] if user.parent_email else None
    queue_email(user.email, f"Fee receipt {fee.receipt_no} — {format_inr(fee.amount)}", "fee_receipt.html", cc=cc,
                category="fees", name=user.full_name, fee={
                    "receipt_no": fee.receipt_no, "title": fee.title, "period": month_label(fee.period),
                    "amount": format_inr(fee.amount), "paid_on": f"{fee.paid_on:%d %b %Y}" if fee.paid_on else "",
                    "method": fee.method or "", "reference": fee.reference or "",
                    "online": fee.gateway == "cashfree"})


async def mark_fee_paid(db: AsyncSession, fee: FeePayment, *, method: str, reference: str | None = None,
                        paid_on=None, gateway: str | None = None, gateway_payment_id: str | None = None,
                        actor: str | None = None) -> bool:
    """Mark a fee as paid (idempotent). Caller commits, then calls send_fee_receipt."""
    if fee.status == "paid":
        return False
    fee.status, fee.method = "paid", (method or "Other")[:20]
    fee.reference = (reference or fee.reference or None) and str(reference or fee.reference)[:80]
    fee.paid_on = paid_on or fee.paid_on or _today()
    fee.gateway = gateway or fee.gateway
    fee.gateway_payment_id = gateway_payment_id or fee.gateway_payment_id
    fee.reviewed_by = actor or fee.reviewed_by
    if not fee.receipt_no:
        fee.receipt_no = f"RCPT-{_today():%Y%m}-{fee.id:05d}"
    await notify_users(db, [fee.user_id], f"Payment received — {format_inr(fee.amount)}",
                       f"{fee.title} · {month_label(fee.period)} · receipt {fee.receipt_no}", "/student/fees",
                       "fa-circle-check")
    return True


def new_order_id(fee: FeePayment) -> str:
    return f"CN{fee.id}T{secrets.token_hex(5)}"


async def sync_cashfree_order(db: AsyncSession, order_id: str) -> FeePayment | None:
    """Ask Cashfree for the real order state and update the fee. Never trusts the client."""
    fee = await db.scalar(select(FeePayment).options(joinedload(FeePayment.user))
                          .where(FeePayment.gateway_order_id == order_id))
    if fee is None:
        return None
    if fee.status == "paid":
        return fee
    order = await cashfree.get_order(order_id)
    if str(order.get("order_status")) != "PAID":
        return fee
    if Decimal(str(order.get("order_amount", 0))) + Decimal("0.01") < Decimal(fee.amount):
        log.error("Cashfree order %s amount mismatch (%s < %s)", order_id, order.get("order_amount"), fee.amount)
        return fee
    payments = await cashfree.get_order_payments(order_id)
    success = next((p for p in payments if str(p.get("payment_status")) == "SUCCESS"), {})
    changed = await mark_fee_paid(db, fee, method="Cashfree", reference=str(success.get("cf_payment_id") or
                                                                           order.get("cf_order_id") or order_id),
                                  gateway="cashfree", gateway_payment_id=str(success.get("cf_payment_id") or ""),
                                  actor="cashfree")
    await db.commit()
    if changed:
        send_fee_receipt(fee, fee.user)
    return fee


async def handle_pg_webhook(payload: dict) -> None:
    order_id = (((payload.get("data") or {}).get("order") or {}).get("order_id"))
    if not order_id:
        return
    async with SessionLocal() as db:
        await sync_cashfree_order(db, str(order_id))


# ------------------------------------------------------------------ salary payouts
def notify_salary(payment: SalaryPayment, user: User) -> None:
    queue_email(user.email, f"Your salary for {month_label(payment.period)} has been paid", "salary_slip.html",
                category="salary", name=user.full_name, period=month_label(payment.period),
                amount=format_inr(payment.amount), title=payment.title,
                online=payment.method == "Cashfree", reference=payment.payout_reference or payment.reference)


async def apply_transfer_status(db: AsyncSession, payment: SalaryPayment, data: dict) -> None:
    status = str(data.get("status") or data.get("transfer_status") or "").upper()
    payment.payout_reference = str(data.get("cf_transfer_id") or data.get("utr") or payment.payout_reference or "")[:80] or None
    if status in cashfree.PAYOUT_SUCCESS and payment.status != "paid":
        payment.status, payment.paid_on = "paid", _today()
        user = await db.get(User, payment.user_id)
        await notify_users(db, [payment.user_id], f"Salary credited — {format_inr(payment.amount)}",
                           f"{payment.title} · {month_label(payment.period)}", "/staff/salary", "fa-building-columns")
        await db.commit()
        if user is not None:
            notify_salary(payment, user)
        return
    if status in cashfree.PAYOUT_FAILED:
        payment.status = "failed"
        payment.failure_reason = str(data.get("status_description") or data.get("reason") or status)[:500]
    elif payment.status != "paid":
        payment.status = "processing"
    await db.commit()


async def start_payout(db: AsyncSession, payment: SalaryPayment, staff: User) -> None:
    payment.payout_transfer_id = payment.payout_transfer_id or f"SAL{staff.id}T{secrets.token_hex(5)}"
    payment.status, payment.method, payment.failure_reason = "processing", "Cashfree", None
    await db.commit()
    try:
        data = await cashfree.create_transfer(
            transfer_id=payment.payout_transfer_id, amount=payment.amount, name=staff.bank_account_name or
            staff.full_name, email=staff.email, phone=staff.phone, account_number=staff.bank_account_number,
            ifsc=staff.bank_ifsc, vpa=staff.upi_id, remarks=f"{payment.title} {payment.period}")
    except cashfree.CashfreeError as exc:
        payment.status, payment.failure_reason = "failed", str(exc)[:500]
        await db.commit()
        raise
    await apply_transfer_status(db, payment, data if isinstance(data, dict) else {})


async def sync_payout(db: AsyncSession, payment: SalaryPayment) -> None:
    if not payment.payout_transfer_id or payment.status != "processing":
        return
    data = await cashfree.get_transfer(payment.payout_transfer_id)
    if isinstance(data, list):
        data = data[0] if data else {}
    await apply_transfer_status(db, payment, data)


async def poll_processing_payouts() -> None:
    if not settings.cashfree_payouts_active:
        return
    async with SessionLocal() as db:
        rows = (await db.scalars(select(SalaryPayment).where(SalaryPayment.status == "processing").limit(50))).all()
        for payment in rows:
            try:
                await sync_payout(db, payment)
            except cashfree.CashfreeError as exc:
                log.warning("Payout sync failed for %s: %s", payment.payout_transfer_id, exc)


async def handle_payout_webhook(payload: dict) -> None:
    data = payload.get("data") or {}
    transfer = data.get("transfer") if isinstance(data.get("transfer"), dict) else data
    transfer_id = transfer.get("transfer_id")
    if not transfer_id:
        return
    async with SessionLocal() as db:
        payment = await db.scalar(select(SalaryPayment).where(SalaryPayment.payout_transfer_id == str(transfer_id)))
        if payment is not None and payment.status == "processing":
            try:
                await sync_payout(db, payment)  # re-verify with Cashfree instead of trusting the payload
            except cashfree.CashfreeError:
                await apply_transfer_status(db, payment, transfer)
