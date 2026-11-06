"""Reports desk: complaints & safety concerns (from the AI assistant or Report buttons).
Admins can accept (investigating), resolve, dismiss, or ban the reported person / the reporter."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from ...database import get_db
from ...models import Report, User, utcnow
from ...security import Principal, client_ip, require_admin
from ...services.accounts import ban_account
from ...services.notify import notify_users
from ...templating import flash, render
from ..common import redirect

router = APIRouter()
ACTIONS = {"accept": "accepted", "resolve": "resolved", "dismiss": "dismissed", "ban_subject": "banned",
           "ban_reporter": "banned"}


@router.get("/reports", response_class=HTMLResponse)
async def reports(request: Request, db: AsyncSession = Depends(get_db), status: str = "open"):
    stmt = select(Report).options(joinedload(Report.reporter), joinedload(Report.subject))
    if status != "all":
        stmt = stmt.where(Report.status == status)
    rows = (await db.scalars(stmt.order_by(Report.id.desc()).limit(200))).unique().all()
    counts = dict((await db.execute(select(Report.status, func.count()).group_by(Report.status))).all())
    return render(request, "admin/reports.html", {"reports": rows, "counts": counts, "status": status})


async def ban_user(db: AsyncSession, user: User, reason: str, admin: Principal, admin_ip: str) -> None:
    """Reports desk ban: the account shows as *Banned* (data kept) and its last network address is blocked."""
    await ban_account(db, user, reason=reason, actor=admin.username, block_ip=True, admin_ip=admin_ip)


@router.post("/reports/{rid}/action")
async def act(rid: int, request: Request, db: AsyncSession = Depends(get_db), admin: Principal = Depends(require_admin),
              action: str = Form(...), note: str = Form("")):
    report = await db.scalar(select(Report).options(joinedload(Report.reporter), joinedload(Report.subject))
                             .where(Report.id == rid))
    if report is None or action not in ACTIONS:
        raise HTTPException(400)
    note = note.strip()
    if action in ("resolve", "dismiss", "ban_subject", "ban_reporter") and len(note) < 3:
        flash(request, "Please add a short note explaining the decision.", "danger")
        return redirect("/admin/reports")
    target = report.subject if action == "ban_subject" else report.reporter if action == "ban_reporter" else None
    if action.startswith("ban") and target is None:
        flash(request, "There is no linked account to ban for this report.", "warning")
        return redirect("/admin/reports")
    report.status, report.admin_note, report.handled_by, report.handled_at = ACTIONS[action], note or None, \
        admin.username, utcnow()
    if report.reporter_id:
        outcome = {"accept": "is being looked into", "resolve": "has been resolved", "dismiss": "was reviewed and closed",
                   "ban_subject": "was reviewed and action was taken", "ban_reporter": "was reviewed"}[action]
        await notify_users(db, [report.reporter_id], f"Your report #{report.id} {outcome}", None, "/assistant",
                           "fa-shield-heart")
    await db.commit()
    if target is not None:
        await ban_user(db, target, f"Report #{report.id}: {note}", admin, client_ip(request))
    flash(request, f"Report #{report.id} marked {report.status}.", "success")
    return redirect("/admin/reports")


__all__ = ["router", "dt"]
