"""Batch group chat: real-time (WebSockets), moderated (abusive words masked, strikes, auto IP ban)."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..constants import STAFF_ROLES, SUPERVISOR_ROLES, TEACHING_ROLES
from ..database import SessionLocal, get_db
from ..models import Batch, BatchChatMessage, Enrollment
from ..security import Principal, client_ip, csrf_protect, limiter, load_principal, require_user
from ..services.accounts import DELETED_NAME, statuses
from ..services.chat import ChatPeer, hub
from ..services.moderation import moderate
from ..templating import render, to_local
from .common import redirect
from .meet import same_origin

router = APIRouter(dependencies=[Depends(csrf_protect)])
log = logging.getLogger("classnest.chat")


async def chat_batches(db: AsyncSession, principal: Principal) -> list[Batch]:
    stmt = select(Batch).where(Batch.is_active.is_(True)).order_by(Batch.name)
    if principal.kind == "student":
        stmt = stmt.where(Batch.id.in_(select(Enrollment.batch_id).where(Enrollment.user_id == principal.id)))
    elif principal.kind == "staff":
        if principal.staff_role in SUPERVISOR_ROLES:
            pass
        elif principal.staff_role in TEACHING_ROLES:
            stmt = stmt.where(Batch.teacher_id == principal.id)
        else:
            return []
    elif principal.kind != "admin":
        return []
    return list((await db.scalars(stmt)).all())


async def can_chat(db: AsyncSession, principal: Principal, batch_id: int) -> bool:
    return batch_id in {b.id for b in await chat_batches(db, principal)}


def can_moderate(principal: Principal, batch: Batch) -> bool:
    return principal.kind == "admin" or (principal.kind == "staff" and (
        batch.teacher_id == principal.id or principal.staff_role in SUPERVISOR_ROLES))


def message_json(m: BatchChatMessage, status: str | None = None) -> dict:
    """`status` is the sender's account status when not active: banned -> labelled, deleted -> dormant (hidden)."""
    hidden = status == "deleted"
    return {"type": "message", "id": m.id, "name": DELETED_NAME if hidden else m.sender_name, "role": m.sender_role,
            "text": "Message from a deleted account" if hidden else m.text, "flagged": m.flagged,
            "ts": to_local(m.created_at).strftime("%d %b, %I:%M %p"), "uid": m.user_id,
            "badge": "Banned" if status == "banned" else None}


async def messages_json(db: AsyncSession, rows) -> list[dict]:
    st = await statuses(db, [m.user_id for m in rows])
    return [message_json(m, st.get(m.user_id)) for m in rows]


@router.get("/chat", response_class=HTMLResponse)
async def index(request: Request, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_user)):
    batches = await chat_batches(db, me)
    if len(batches) == 1:
        return redirect(f"/chat/{batches[0].id}")
    return render(request, "chat/index.html", {"batches": batches})


@router.get("/chat/{batch_id}", response_class=HTMLResponse)
async def room(batch_id: int, request: Request, db: AsyncSession = Depends(get_db),
               me: Principal = Depends(require_user)):
    batches = await chat_batches(db, me)
    batch = next((b for b in batches if b.id == batch_id), None)
    if batch is None:
        raise HTTPException(403, "You are not part of this batch chat.")
    rows = (await db.scalars(select(BatchChatMessage).where(BatchChatMessage.batch_id == batch_id,
                                                            BatchChatMessage.deleted.is_(False))
                             .order_by(BatchChatMessage.id.desc()).limit(60))).all()
    return render(request, "chat/room.html", {
        "batch": batch, "batches": batches, "messages": await messages_json(db, list(reversed(rows))),
        "can_moderate": can_moderate(me, batch), "my_id": me.id})


@router.get("/api/chat/{batch_id}/history")
async def history(batch_id: int, before: int, db: AsyncSession = Depends(get_db),
                  me: Principal = Depends(require_user)):
    if not await can_chat(db, me, batch_id):
        raise HTTPException(403)
    rows = (await db.scalars(select(BatchChatMessage).where(BatchChatMessage.batch_id == batch_id,
                                                            BatchChatMessage.deleted.is_(False),
                                                            BatchChatMessage.id < before)
                             .order_by(BatchChatMessage.id.desc()).limit(50))).all()
    return {"messages": await messages_json(db, list(reversed(rows)))}


@router.post("/chat/messages/{mid}/delete")
async def delete_message(mid: int, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_user)):
    msg = await db.get(BatchChatMessage, mid)
    if msg is None:
        raise HTTPException(404)
    batch = await db.get(Batch, msg.batch_id)
    if batch is None or not can_moderate(me, batch):
        raise HTTPException(403)
    await db.execute(update(BatchChatMessage).where(BatchChatMessage.id == mid).values(deleted=True))
    await db.commit()
    await hub.broadcast(msg.batch_id, {"type": "deleted", "id": mid})
    return {"ok": True}


@router.websocket("/ws/chat/{batch_id}")
async def socket(websocket: WebSocket, batch_id: int):
    if not same_origin(websocket):
        await websocket.close(code=4400)
        return
    async with SessionLocal() as db:
        principal = await load_principal(websocket.session, db)
        allowed = principal is not None and principal.kind != "guest" and await can_chat(db, principal, batch_id)
    if not allowed:
        await websocket.close(code=4403)
        return
    if hub.connections_of(principal.key) >= settings.MAX_SOCKETS_PER_USER:
        await websocket.close(code=4429)  # too many tabs/sockets: protects memory & broadcast fan-out
        return
    await websocket.accept()
    role = STAFF_ROLES.get(principal.staff_role or "", "Staff") if principal.kind == "staff" else principal.kind.title()
    peer = ChatPeer(ws=websocket, key=principal.key, name=principal.name, role=role, ip=client_ip(websocket),
                    user_id=principal.id)
    hub.join(batch_id, peer)
    await hub.broadcast(batch_id, {"type": "presence", "online": hub.online(batch_id)})
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            if data.get("type") == "ping":
                await peer.send({"type": "pong"})
                continue
            if data.get("type") != "message":
                continue
            text = str(data.get("text") or "").strip()[:1000]
            if not text:
                continue
            if not limiter.hit(f"chat:{peer.key}", settings.CHAT_RATE_LIMIT, 30):
                await peer.send({"type": "notice", "level": "warning",
                                 "text": "You're sending messages too fast — please slow down."})
                continue
            async with SessionLocal() as db:
                outcome = await moderate(db, text=text, principal=principal, ip=peer.ip, context="batch_chat")
                if outcome.banned:
                    break
                msg = BatchChatMessage(batch_id=batch_id, user_id=principal.id, sender_name=principal.name,
                                       sender_role=role[:12], text=outcome.text, flagged=outcome.flagged)
                db.add(msg)
                await db.commit()
            await hub.broadcast(batch_id, message_json(msg))
            if outcome.notice:
                await peer.send({"type": "notice", "level": "warning", "text": outcome.notice})
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("Chat socket error")
    finally:
        hub.leave(batch_id, peer)
        await hub.broadcast(batch_id, {"type": "presence", "online": hub.online(batch_id)})
