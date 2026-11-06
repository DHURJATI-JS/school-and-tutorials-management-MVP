"""Security centre: IP bans (manual + automatic), login activity and moderation strikes."""
from __future__ import annotations

import datetime as dt
import ipaddress

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...cache import ban_cache
from ...database import get_db
from ...models import IPBan, LoginEvent, ModerationStrike, User, utcnow
from ...security import Principal, client_ip, require_admin
from ...services.accounts import disconnect_ip
from ...templating import flash, render
from ..common import redirect

router = APIRouter()


@router.get("/security", response_class=HTMLResponse)
async def security(request: Request, db: AsyncSession = Depends(get_db), failed: int = 0):
    bans = (await db.execute(select(IPBan, User.full_name).outerjoin(User, User.id == IPBan.user_id)
                             .order_by(IPBan.id.desc()))).all()
    events_stmt = select(LoginEvent).order_by(LoginEvent.id.desc()).limit(150)
    if failed:
        events_stmt = events_stmt.where(LoginEvent.success.is_(False))
    events = (await db.scalars(events_stmt)).all()
    strikes = (await db.execute(select(ModerationStrike, User.full_name, User.role)
                                .outerjoin(User, User.id == ModerationStrike.user_id)
                                .order_by(ModerationStrike.id.desc()).limit(60))).all()
    return render(request, "admin/security.html", {"bans": bans, "events": events, "strikes": strikes,
                                                   "my_ip": client_ip(request), "failed": failed})


@router.post("/security/ban")
async def ban(request: Request, db: AsyncSession = Depends(get_db), admin: Principal = Depends(require_admin),
              ip: str = Form(...), reason: str = Form(...), hours: int = Form(0)):
    ip = ip.strip()
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        flash(request, "That is not a valid IPv4/IPv6 address.", "danger")
        return redirect("/admin/security")
    if ip == client_ip(request):
        flash(request, "You can't ban the IP address you are using right now.", "danger")
        return redirect("/admin/security")
    if len(reason.strip()) < 3:
        flash(request, "Please give a reason for the ban.", "danger")
        return redirect("/admin/security")
    expires = utcnow() + dt.timedelta(hours=hours) if hours > 0 else None
    existing = await db.scalar(select(IPBan).where(IPBan.ip == ip))
    if existing is None:
        db.add(IPBan(ip=ip, reason=reason.strip(), banned_by=admin.username, expires_at=expires))
    else:
        existing.reason, existing.banned_by, existing.expires_at, existing.is_auto = (reason.strip(), admin.username,
                                                                                      expires, False)
    await db.commit()
    ban_cache.add(ip, reason.strip(), expires)  # every new request from it gets the block page from now on
    # ...and every open connection is cut on the spot: live classes, batch chats and friends' chats
    await disconnect_ip(ip, "Your network address was blocked by the administrator.")
    flash(request, f"{ip} is now blocked" + (f" for {hours} hours." if hours > 0 else " permanently.")
          + " Its open connections were closed.", "success")
    return redirect("/admin/security")


@router.post("/security/unban/{bid}")
async def unban(bid: int, request: Request, db: AsyncSession = Depends(get_db)):
    record = await db.get(IPBan, bid)
    if record is None:
        raise HTTPException(404)
    ip = record.ip
    await db.delete(record)
    await db.commit()
    ban_cache.remove(ip)
    flash(request, f"{ip} was unblocked.", "success")
    return redirect("/admin/security")
