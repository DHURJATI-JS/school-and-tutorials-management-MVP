"""Live meeting rooms (Google-Meet-style): pages, WebRTC signalling WebSocket, recording upload,
and PTM guest access via invitation tokens."""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from urllib.parse import urlparse

from anyio import to_thread
from fastapi import APIRouter, Depends, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from ..config import settings
from ..constants import SUPERVISOR_ROLES
from ..database import SessionLocal, get_db
from ..models import PTM, Batch, Enrollment, Meeting, PTMInvite, utcnow
from ..security import (LoginRequired, Principal, client_ip, csrf_protect, limiter, load_principal,
                        optional_principal)
from ..services.background import spawn
from ..services.meetings import Peer, end_meeting, record_join, record_leave, recording_dir, rooms, save_message
from ..services.moderation import mask, moderate
from ..services.notify import batch_student_ids, notify_users
from ..templating import flash, render
from .common import redirect

router = APIRouter(dependencies=[Depends(csrf_protect)])
log = logging.getLogger("classnest.meet")


async def can_join(db: AsyncSession, principal: Principal | None, meeting: Meeting | None) -> tuple[bool, bool]:
    """Return (allowed, is_host)."""
    if principal is None or meeting is None or meeting.status == "cancelled":
        return False, False
    if principal.kind == "admin":
        return True, True
    if meeting.kind == "ptm":
        if principal.kind == "guest":
            return principal.meeting_code == meeting.code, False
        if principal.kind == "staff" and principal.staff_role in SUPERVISOR_ROLES:
            return True, True
        ptm = await db.scalar(select(PTM).where(PTM.meeting_id == meeting.id))
        invited = ptm is not None and await db.scalar(select(PTMInvite.id).where(
            PTMInvite.ptm_id == ptm.id, PTMInvite.user_id == principal.id).limit(1))
        return bool(invited) or principal.is_teaching, False
    if principal.kind == "student":
        enrolled = await db.scalar(select(Enrollment.id).where(Enrollment.user_id == principal.id,
                                                               Enrollment.batch_id == meeting.batch_id))
        return bool(enrolled), False
    if principal.kind == "staff":
        if meeting.host_user_id == principal.id or principal.staff_role in SUPERVISOR_ROLES:
            return True, True
        batch = await db.get(Batch, meeting.batch_id) if meeting.batch_id else None
        if batch is not None and batch.teacher_id == principal.id:
            return True, True
        return principal.is_teaching, False
    return False, False


async def _meeting(db: AsyncSession, code: str) -> Meeting:
    meeting = await db.scalar(select(Meeting).options(joinedload(Meeting.batch)).where(Meeting.code == code))
    if meeting is None:
        raise HTTPException(404, "Meeting not found")
    return meeting


@router.get("/meet/{code}", response_class=HTMLResponse)
async def room(code: str, request: Request, db: AsyncSession = Depends(get_db),
               principal: Principal | None = Depends(optional_principal)):
    if principal is None:
        raise LoginRequired(request.url.path)
    meeting = await _meeting(db, code)
    allowed, is_host = await can_join(db, principal, meeting)
    if not allowed:
        raise HTTPException(403, "You are not a participant of this meeting.")
    config = {
        "code": code, "title": meeting.title, "status": meeting.status, "isHost": is_host,
        "kind": meeting.kind, "me": {"name": principal.name, "role": principal.kind},
        "ice": settings.ice_servers, "ws": f"/ws/meet/{code}", "statusUrl": f"/meet/{code}/status",
        "startUrl": f"/meet/{code}/start", "endUrl": f"/meet/{code}/end", "uploadUrl": f"/meet/{code}/recording",
        "leaveUrl": principal.home_url if principal.kind != "guest" else "/", "lang": "en-IN",
        "batch": meeting.batch.name if meeting.batch else "Parent-Teacher Meeting",
    }
    return render(request, "meet/room.html", {"meeting": meeting, "config": config, "is_host": is_host})


@router.get("/meet/{code}/status")
async def status(code: str, db: AsyncSession = Depends(get_db),
                 principal: Principal | None = Depends(optional_principal)):
    meeting = await _meeting(db, code)
    allowed, _ = await can_join(db, principal, meeting)
    if not allowed:
        raise HTTPException(404, "Meeting not found")
    return {"status": meeting.status, "participants": rooms.count(code)}


@router.post("/meet/{code}/start")
async def start(code: str, db: AsyncSession = Depends(get_db), principal: Principal | None = Depends(optional_principal)):
    meeting = await _meeting(db, code)
    _, is_host = await can_join(db, principal, meeting)
    if not is_host:
        raise HTTPException(403)
    if meeting.status == "ended":
        return JSONResponse({"ok": False, "error": "This meeting has already ended."}, status_code=409)
    if meeting.status != "live":
        meeting.status, meeting.started_at = "live", utcnow()
        if meeting.batch_id:
            ids = await batch_student_ids(db, [meeting.batch_id])
            await notify_users(db, ids, f"🔴 Live now: {meeting.title}", "Join the class", f"/meet/{code}", "fa-video")
        await db.commit()
    return {"ok": True}


@router.post("/meet/{code}/end")
async def end(code: str, db: AsyncSession = Depends(get_db), principal: Principal | None = Depends(optional_principal)):
    meeting = await _meeting(db, code)
    _, is_host = await can_join(db, principal, meeting)
    if not is_host:
        raise HTTPException(403)
    await end_meeting(meeting.id, reason=f"ended by {principal.name}")
    return {"ok": True}


@router.post("/meet/{code}/recording/{seq:int}")
async def recording_chunk(code: str, seq: int, request: Request, db: AsyncSession = Depends(get_db),
                          principal: Principal | None = Depends(optional_principal)):
    meeting = await _meeting(db, code)
    _, is_host = await can_join(db, principal, meeting)
    if not is_host:
        raise HTTPException(403)
    if meeting.status not in ("live", "ended") or not 0 <= seq < 100000:
        raise HTTPException(409)
    body = await request.body()
    if not body or len(body) > 64 * 1024 * 1024:
        raise HTTPException(413)
    folder = recording_dir(code)

    def write() -> None:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"chunk_{seq:06d}.webm").write_bytes(body)

    await to_thread.run_sync(write)
    return {"ok": True, "seq": seq}


# ------------------------------------------------------------------ signalling WebSocket
def same_origin(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin")
    return origin is None or urlparse(origin).netloc == ws.headers.get("host")


@router.websocket("/ws/meet/{code}")
async def meet_socket(websocket: WebSocket, code: str):
    if not same_origin(websocket):  # cross-site WebSocket hijacking protection
        await websocket.close(code=4400)
        return
    async with SessionLocal() as db:
        principal = await load_principal(websocket.session, db)
        if principal is None:
            await websocket.close(code=4403)
            return
        meeting = await db.scalar(select(Meeting).where(Meeting.code == code))
        allowed, is_host = await can_join(db, principal, meeting)
    if not allowed or meeting is None:
        await websocket.close(code=4403)
        return
    if meeting.status != "live":
        await websocket.close(code=4409)
        return
    await websocket.accept()
    peer = Peer(peer_id=secrets.token_hex(4), ws=websocket, key=principal.key, name=principal.name,
                role=principal.kind, user_id=principal.id, is_host=is_host, ip=client_ip(websocket))
    existing = rooms.get(code)
    if existing is not None:  # same person in another tab: the newest connection wins
        for old in [p for p in existing.peers.values() if p.key == peer.key]:
            await old.send({"type": "replaced"})
            try:
                await old.ws.close(code=4000)
            except Exception:
                pass
    room_state = rooms.add(code, meeting.id, peer)
    await record_join(meeting.id, principal)
    await peer.send({"type": "welcome", "peer_id": peer.peer_id, "is_host": is_host, "ice": settings.ice_servers,
                     "recording": room_state.recording,
                     "peers": [p.public() for p in room_state.peers.values() if p.peer_id != peer.peer_id]})
    await rooms.broadcast(code, {"type": "peer-joined", "peer": peer.public()}, exclude=peer.peer_id)
    try:
        while True:
            raw = await websocket.receive_text()
            if len(raw) > 200_000:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue
            if await handle_message(code, meeting.id, peer, principal, msg) == "stop":
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("Meeting socket error")
    finally:
        if rooms.remove(code, peer.peer_id) is not None:
            await record_leave(meeting.id, peer.key, int(time.monotonic() - peer.joined))
            await rooms.broadcast(code, {"type": "peer-left", "peer_id": peer.peer_id})


# Per-peer budgets for each socket message type (count per 10 s). Each message is broadcast to everyone in
# the room and final captions are stored, so one participant must not be able to flood the room or the DB.
# WebRTC signalling is bursty while connections are negotiated (many ICE candidates per peer).
MESSAGE_BUDGETS = {"signal": 600, "caption": 25, "state": 30, "reaction": 10, "ping": 30}


async def handle_message(code: str, meeting_id: int, peer: Peer, principal: Principal, msg: dict) -> str | None:
    kind = msg.get("type")
    budget = MESSAGE_BUDGETS.get(str(kind))
    if budget is not None and not limiter.hit(f"meet-{kind}:{peer.peer_id}", budget, 10):
        return None  # over budget: drop silently (legitimate clients never get close)
    if kind == "signal":
        target = str(msg.get("to") or "")
        await rooms.send(code, target, {"type": "signal", "from": peer.peer_id, "data": msg.get("data")})
    elif kind == "chat":
        text = str(msg.get("text") or "").strip()[:1000]
        if not text:
            return None
        if not limiter.hit(f"meetchat:{peer.key}", settings.CHAT_RATE_LIMIT, 30):
            await peer.send({"type": "notice", "level": "warning", "text": "You're sending messages too fast."})
            return None
        async with SessionLocal() as db:
            outcome = await moderate(db, text=text, principal=principal, ip=peer.ip, context="live_class_chat")
        if outcome.banned:
            return "stop"
        await save_message(meeting_id, "chat", peer.key, peer.name, outcome.text, outcome.flagged)
        await rooms.broadcast(code, {"type": "chat", "from": peer.peer_id, "name": peer.name, "text": outcome.text,
                                     "ts": int(time.time() * 1000), "flagged": outcome.flagged})
        if outcome.notice:
            await peer.send({"type": "notice", "level": "warning", "text": outcome.notice})
    elif kind == "caption":
        text = mask(str(msg.get("text") or "").strip()[:500])
        final = bool(msg.get("final"))
        if text:
            if final:
                await save_message(meeting_id, "transcript", peer.key, peer.name, text)
            await rooms.broadcast(code, {"type": "caption", "peer_id": peer.peer_id, "name": peer.name, "text": text,
                                         "final": final}, exclude=peer.peer_id)
    elif kind == "state":
        for key in ("audio", "video", "screen", "hand"):
            if key in msg:
                peer.state[key] = bool(msg[key])
        await rooms.broadcast(code, {"type": "peer-state", "peer_id": peer.peer_id, "state": peer.state},
                              exclude=peer.peer_id)
    elif kind == "reaction":
        emoji = str(msg.get("emoji") or "")[:4]
        if emoji in {"👍", "👏", "❤️", "😂", "🎉", "🤔", "✋"}:
            await rooms.broadcast(code, {"type": "reaction", "peer_id": peer.peer_id, "name": peer.name,
                                         "emoji": emoji})
    elif kind == "ping":
        await peer.send({"type": "pong"})
    elif peer.is_host:
        target = str(msg.get("peer_id") or "")
        if kind == "end":
            spawn(end_meeting(meeting_id, reason=f"ended by {peer.name}"), name=f"end:{code}")
        elif kind == "mute":
            await rooms.send(code, target, {"type": "force-mute", "by": peer.name})
        elif kind == "remove":
            room = rooms.get(code)
            victim = room.peers.get(target) if room else None
            if victim is not None and not victim.is_host:
                await victim.send({"type": "kicked", "reason": f"Removed from the meeting by {peer.name}."})
                await victim.ws.close(code=4001)
        elif kind == "recording":
            room = rooms.get(code)
            if room is not None:
                room.recording = bool(msg.get("on"))
                await rooms.broadcast(code, {"type": "recording", "on": room.recording})
    return None


# ------------------------------------------------------------------ PTM invitations (no login needed)
async def _invite(db: AsyncSession, token: str) -> PTMInvite:
    invite = await db.scalar(select(PTMInvite).options(joinedload(PTMInvite.ptm).joinedload(PTM.meeting))
                             .where(PTMInvite.token == token))
    if invite is None:
        raise HTTPException(404, "This invitation link is not valid.")
    return invite


@router.get("/ptm/invite/{token}", response_class=HTMLResponse)
async def invite_page(token: str, request: Request, db: AsyncSession = Depends(get_db),
                      _=Depends(optional_principal)):
    invite = await _invite(db, token)
    return render(request, "ptm/invite.html", {"invite": invite, "ptm": invite.ptm})


@router.post("/ptm/invite/{token}/rsvp")
async def rsvp(token: str, request: Request, db: AsyncSession = Depends(get_db), answer: str = Form(...)):
    invite = await _invite(db, token)
    if answer not in ("yes", "no", "maybe"):
        raise HTTPException(400)
    invite.rsvp, invite.responded_at = answer, utcnow()
    await db.commit()
    flash(request, {"yes": "Thank you — we look forward to meeting you!", "no": "Thank you for letting us know.",
                    "maybe": "Thanks — we've noted that you might attend."}[answer], "success")
    return redirect(f"/ptm/invite/{token}")


@router.get("/ptm/join/{token}")
async def join(token: str, request: Request, db: AsyncSession = Depends(get_db),
               principal: Principal | None = Depends(optional_principal)):
    invite = await _invite(db, token)
    meeting = invite.ptm.meeting
    if meeting is None or meeting.status in ("ended", "cancelled") or invite.ptm.status == "cancelled":
        flash(request, "This PTM has no active online room.", "warning")
        return redirect(f"/ptm/invite/{token}")
    if principal is not None and principal.kind in ("admin", "staff", "student") and invite.invitee_type != "parent":
        return redirect(f"/meet/{meeting.code}")
    request.session["guest"] = {"name": f"{invite.invitee_name} (parent of {invite.regarding})"
                                if invite.regarding else invite.invitee_name,
                                "email": invite.invitee_email, "meeting": meeting.code}
    request.session.pop("auth", None) if principal is not None and principal.kind == "guest" else None
    return redirect(f"/meet/{meeting.code}")


__all__ = ["router", "asyncio"]
