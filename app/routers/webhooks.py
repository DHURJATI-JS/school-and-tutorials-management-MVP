"""Cashfree webhooks (server-to-server). Signatures are verified and every event is re-checked
against the Cashfree API before any fee or salary is updated."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..config import settings
from ..services import cashfree
from ..services.payments import handle_payout_webhook, handle_pg_webhook

router = APIRouter(prefix="/webhooks")
log = logging.getLogger("classnest.webhooks")


async def _verified_json(request: Request, secret: str) -> dict | None:
    raw = await request.body()
    if not cashfree.verify_signature(raw, request.headers.get("x-webhook-timestamp", ""),
                                     request.headers.get("x-webhook-signature", ""), secret):
        log.warning("Rejected Cashfree webhook with an invalid signature")
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


@router.post("/cashfree")
async def cashfree_payment(request: Request):
    if not settings.cashfree_pg_active:
        return JSONResponse({"ok": False}, status_code=404)
    payload = await _verified_json(request, settings.CASHFREE_SECRET_KEY)
    if payload is None:
        return JSONResponse({"ok": False}, status_code=401)
    try:
        await handle_pg_webhook(payload)
    except cashfree.CashfreeError as exc:  # Cashfree retries webhooks on non-2xx
        log.warning("Webhook verification against Cashfree failed: %s", exc)
        return JSONResponse({"ok": False}, status_code=503)
    return {"ok": True}


@router.post("/cashfree/payouts")
async def cashfree_payout(request: Request):
    if not settings.cashfree_payouts_active:
        return JSONResponse({"ok": False}, status_code=404)
    payload = await _verified_json(request, settings.CASHFREE_PAYOUT_CLIENT_SECRET)
    if payload is None:
        return JSONResponse({"ok": False}, status_code=401)
    await handle_payout_webhook(payload)
    return {"ok": True}
