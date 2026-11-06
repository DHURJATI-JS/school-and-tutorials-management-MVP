"""AI insights (performance alerts) and system status (AI gateway, OCR, SMTP)."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from ...config import settings
from ...database import get_db
from ...models import Batch, KnowledgeChunk, PerformanceAlert
from ...security import Principal, require_admin
from ...services.ai import AIUnavailable, ai
from ...services.background import spawn
from ...services.jobs import maybe_performance
from ...services.mailer import build_message, deliver
from ...services.ocr import ocr_health
from ...templating import flash, render, render_string
from ..common import redirect

router = APIRouter()


@router.get("/insights", response_class=HTMLResponse)
async def insights(request: Request, db: AsyncSession = Depends(get_db), level: str = "", show: str = "open",
                   batch: int = 0):
    stmt = select(PerformanceAlert).options(joinedload(PerformanceAlert.user), joinedload(PerformanceAlert.batch))
    if level:
        stmt = stmt.where(PerformanceAlert.level == level)
    if show == "open":
        stmt = stmt.where(PerformanceAlert.resolved.is_(False))
    if batch:
        stmt = stmt.where(PerformanceAlert.batch_id == batch)
    alerts = (await db.scalars(stmt.order_by(PerformanceAlert.id.desc()).limit(200))).unique().all()
    counts = dict((await db.execute(select(PerformanceAlert.level, func.count())
                                    .where(PerformanceAlert.resolved.is_(False))
                                    .group_by(PerformanceAlert.level))).all())
    batches = (await db.scalars(select(Batch).order_by(Batch.name))).all()
    return render(request, "admin/insights.html", {"alerts": alerts, "counts": counts, "level": level, "show": show,
                                                   "batch": batch, "batches": batches})


@router.post("/insights/run")
async def run_now(request: Request):
    spawn(maybe_performance(force=True), name="performance-now")
    flash(request, "AI performance analysis started — new insights will appear here shortly.", "info")
    return redirect("/admin/insights")


@router.post("/insights/{aid}/resolve")
async def resolve(aid: int, request: Request, db: AsyncSession = Depends(get_db)):
    alert = await db.get(PerformanceAlert, aid)
    if alert is None:
        raise HTTPException(404)
    alert.resolved = not alert.resolved
    await db.commit()
    return redirect("/admin/insights")


@router.get("/ai", response_class=HTMLResponse)
async def status(request: Request, db: AsyncSession = Depends(get_db)):
    chunks = dict((await db.execute(select(KnowledgeChunk.source_type, func.count())
                                    .group_by(KnowledgeChunk.source_type))).all())
    return render(request, "admin/ai_status.html", {
        "ai": await ai.health(), "ocr": await ocr_health(), "chunks": chunks,
        "smtp": {"host": settings.smtp_host, "port": settings.SMTP_PORT, "user": settings.SMTP_USERNAME or "—",
                 "tls": "SSL" if settings.SMTP_SSL else ("STARTTLS" if settings.SMTP_STARTTLS else "none"),
                 "enabled": settings.EMAIL_ENABLED, "from": settings.SMTP_FROM_EMAIL},
        "sms": settings.SMS_PROVIDER})


@router.post("/ai/test-email")
async def test_email(request: Request, admin: Principal = Depends(require_admin)):
    body = render_string("emails/broadcast.html", subject="Test email", name=admin.name,
                         body="If you can read this, SMTP is configured correctly. 🎉", sender="ClassNest")
    ok = await deliver(build_message(settings.admin_alert_list, "ClassNest SMTP test", body), "test")
    flash(request, f"Test email {'sent' if ok else 'FAILED — see the email log for the error'} "
                   f"({', '.join(settings.admin_alert_list)}).", "success" if ok else "danger")
    return redirect("/admin/ai")


class PromptIn(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)


@router.post("/ai/test-prompt")
async def test_prompt(payload: PromptIn):
    started = time.monotonic()
    try:
        answer = await ai.chat([{"role": "user", "content": payload.prompt}], max_tokens=300, timeout=90)
        return {"ok": True, "answer": answer, "ms": int((time.monotonic() - started) * 1000),
                "model": await ai.model()}
    except AIUnavailable as exc:
        return {"ok": False, "error": str(exc), "circuit": ai.breaker.state}
