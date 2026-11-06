"""Cashfree client (optional): Payment Gateway orders for fees and Payouts V2 transfers for salaries.

Docs: https://www.cashfree.com/docs/  - verify endpoint versions against your Cashfree dashboard.
Every result is re-verified server-side (GET order / transfer) before money is marked as received or paid,
so a forged browser redirect or webhook can never mark a fee as paid.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
from decimal import Decimal
from typing import Any

import httpx

from ..config import settings

log = logging.getLogger("classnest.cashfree")


class CashfreeError(Exception):
    pass


def _pg_base() -> str:
    return "https://api.cashfree.com/pg" if settings.CASHFREE_ENV == "production" else "https://sandbox.cashfree.com/pg"


def _payout_base() -> str:
    return ("https://api.cashfree.com/payout" if settings.CASHFREE_ENV == "production"
            else "https://sandbox.cashfree.com/payout")


def _pg_headers() -> dict[str, str]:
    return {"x-client-id": settings.CASHFREE_APP_ID, "x-client-secret": settings.CASHFREE_SECRET_KEY,
            "x-api-version": settings.CASHFREE_API_VERSION, "Content-Type": "application/json",
            "Accept": "application/json"}


def _payout_headers() -> dict[str, str]:
    return {"x-client-id": settings.CASHFREE_PAYOUT_CLIENT_ID,
            "x-client-secret": settings.CASHFREE_PAYOUT_CLIENT_SECRET,
            "x-api-version": settings.CASHFREE_PAYOUT_API_VERSION, "Content-Type": "application/json",
            "Accept": "application/json"}


async def _request(method: str, url: str, headers: dict[str, str], json: dict | None = None) -> Any:
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.request(method, url, headers=headers, json=json)
    except httpx.HTTPError as exc:
        raise CashfreeError(f"Cashfree is unreachable ({exc.__class__.__name__})") from exc
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code >= 400:
        message = data.get("message") if isinstance(data, dict) else None
        raise CashfreeError(message or f"Cashfree returned HTTP {resp.status_code}")
    return data


def phone10(phone: str | None) -> str:
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:] if len(digits) >= 10 else "9999999999"


# ------------------------------------------------------------------ payment gateway (fees)
async def create_order(*, order_id: str, amount: Decimal, customer: dict[str, str], return_url: str,
                       notify_url: str, note: str) -> dict[str, Any]:
    payload = {
        "order_id": order_id, "order_amount": float(amount), "order_currency": "INR",
        "customer_details": customer, "order_note": note[:200],
        "order_meta": {"return_url": return_url, "notify_url": notify_url},
    }
    return await _request("POST", f"{_pg_base()}/orders", _pg_headers(), payload)


async def get_order(order_id: str) -> dict[str, Any]:
    return await _request("GET", f"{_pg_base()}/orders/{order_id}", _pg_headers())


async def get_order_payments(order_id: str) -> list[dict[str, Any]]:
    data = await _request("GET", f"{_pg_base()}/orders/{order_id}/payments", _pg_headers())
    return data if isinstance(data, list) else []


def verify_signature(raw_body: bytes, timestamp: str, signature: str, secret: str) -> bool:
    """Cashfree webhook signature: base64(HMAC-SHA256(timestamp + raw_body, secret))."""
    if not (timestamp and signature and secret):
        return False
    digest = hmac.new(secret.encode(), timestamp.encode() + raw_body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)


# ------------------------------------------------------------------ payouts (salaries)
PAYOUT_SUCCESS = {"SUCCESS", "COMPLETED"}
PAYOUT_FAILED = {"FAILED", "REJECTED", "REVERSED", "MANUALLY_REJECTED", "CANCELLED"}


async def create_transfer(*, transfer_id: str, amount: Decimal, name: str, email: str, phone: str,
                          account_number: str | None, ifsc: str | None, vpa: str | None,
                          remarks: str) -> dict[str, Any]:
    instrument: dict[str, str] = {}
    if account_number and ifsc:
        instrument = {"bank_account_number": account_number, "bank_ifsc": ifsc}
        mode = "banktransfer"
    elif vpa:
        instrument = {"vpa": vpa}
        mode = "upi"
    else:
        raise CashfreeError("The staff member has no bank account / UPI ID on file.")
    payload = {
        "transfer_id": transfer_id, "transfer_amount": float(amount), "transfer_currency": "INR",
        "transfer_mode": mode, "transfer_remarks": re.sub(r"[^A-Za-z0-9 ]", "", remarks)[:70],
        "beneficiary_details": {
            "beneficiary_name": name[:100], "beneficiary_instrument_details": instrument,
            "beneficiary_contact_details": {"beneficiary_email": email, "beneficiary_phone": phone10(phone)},
        },
    }
    return await _request("POST", f"{_payout_base()}/transfers", _payout_headers(), payload)


async def get_transfer(transfer_id: str) -> dict[str, Any]:
    return await _request("GET", f"{_payout_base()}/transfers?transfer_id={transfer_id}", _payout_headers())
