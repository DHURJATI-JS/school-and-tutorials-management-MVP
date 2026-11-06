"""Friends (requests, unfriend, block), direct messages, groups across batches, user reports, live socket."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import settings
from ..constants import TEACHING_ROLES
from ..database import SessionLocal, get_db
from ..models import ChatMember, ChatMessage, ChatRoom, Friendship, User, utcnow
from ..security import Principal, client_ip, csrf_protect, limiter, load_principal, require_member
from ..services.accounts import DELETED_NAME, statuses
from ..services.chat import ChatPeer
from ..services.moderation import moderate
from ..services.notify import notify_users
from ..services.reports import CATEGORIES as REPORT_CATEGORIES
from ..services.reports import create_report
from ..services.social import (are_friends, blocked_ids, can_post, friend_ids, get_or_create_direct, member_ids, pair,
                               relation, social_hub, unread_counts, users_by_id)
from ..templating import flash, render, to_local
from .common import back, int_or, ints, redirect
from .meet import same_origin

router = APIRouter(dependencies=[Depends(csrf_protect)])
log = logging.getLogger("classnest.social")


def message_json(m: ChatMessage, status: str | None = None) -> dict:
    """`status` is the sender's account status when not active: banned -> labelled, deleted -> dormant (hidden)."""
    hidden = status == "deleted"
    return {"type": "message", "room": m.room_id, "id": m.id, "uid": m.user_id,
            "name": DELETED_NAME if hidden else m.sender_name,
            "text": "Message from a deleted account" if hidden else m.text, "flagged": m.flagged,
            "ts": to_local(m.created_at).strftime("%d %b, %I:%M %p"), "badge": "Banned" if status == "banned" else None}


async def messages_json(db: AsyncSession, rows) -> list[dict]:
    st = await statuses(db, [m.user_id for m in rows])
    return [message_json(m, st.get(m.user_id)) for m in rows]


async def _member(db: AsyncSession, uid: int) -> User:
    user = await db.get(User, uid)
    if user is None or user.status != "active":
        raise HTTPException(404, "That person isn't available.")
    return user


# ------------------------------------------------------------------ friends
@router.get("/friends", response_class=HTMLResponse)
async def friends(request: Request, q: str = "", db: AsyncSession = Depends(get_db),
                  me: Principal = Depends(require_member)):
    rels = (await db.scalars(select(Friendship).where(or_(Friendship.user_a == me.id, Friendship.user_b == me.id)))).all()
    other = {r.id: (r.user_b if r.user_a == me.id else r.user_a) for r in rels}
    # deleted accounts are dormant - hidden; banned friends stay listed with a "Banned" label
    people = {uid: u for uid, u in (await users_by_id(db, other.values())).items() if u.status != "deleted"}
    accepted = [(r, people[other[r.id]]) for r in rels if r.status == "accepted" and other[r.id] in people]
    incoming = [(r, people[other[r.id]]) for r in rels if r.status == "pending" and r.requested_by != me.id
                and other[r.id] in people]
    outgoing = [(r, people[other[r.id]]) for r in rels if r.status == "pending" and r.requested_by == me.id
                and other[r.id] in people]
    blocked = [(r, people[other[r.id]]) for r in rels if r.status == "blocked" and r.blocked_by == me.id
               and other[r.id] in people]
    results = []
    if q.strip():
        like = f"%{q.strip()}%"
        hidden = await blocked_ids(db, me.id) | {me.id}
        results = (await db.scalars(select(User).where(User.status == "active", User.id.not_in(hidden),
                                                       or_(User.full_name.ilike(like), User.id_code.ilike(like)))
                                    .order_by(User.full_name).limit(30))).all()
    status_of = {other[r.id]: r for r in rels}
    return render(request, "social/friends.html", {"accepted": accepted, "incoming": incoming, "outgoing": outgoing,
                                                   "blocked": blocked, "results": results, "q": q,
                                                   "status_of": status_of})


@router.post("/friends/request/{uid}")
async def friend_request(uid: int, request: Request, db: AsyncSession = Depends(get_db),
                         me: Principal = Depends(require_member)):
    target = await _member(db, uid)
    if target.id == me.id:
        raise HTTPException(400)
    if not limiter.hit(f"friendreq:{me.key}", 30, 3600):
        flash(request, "You've sent a lot of requests — please try again later.", "warning")
        return back(request, "/friends")
    rel = await relation(db, me.id, uid)
    if rel is not None:
        if rel.status == "pending" and rel.requested_by == uid:  # they already asked me: accept
            rel.status, rel.responded_at = "accepted", utcnow()
            await notify_users(db, [uid], f"{me.name} accepted your friend request", None, "/friends", "fa-user-check")
            await db.commit()
            flash(request, f"You and {target.full_name} are now friends! 🎉", "success")
        else:
            flash(request, "A request or connection already exists.", "info")
        return back(request, "/friends")
    a, b = pair(me.id, uid)
    db.add(Friendship(user_a=a, user_b=b, requested_by=me.id, status="pending"))
    await notify_users(db, [uid], f"{me.name} sent you a friend request", None, "/friends#requests", "fa-user-plus")
    await db.commit()
    flash(request, f"Friend request sent to {target.full_name}.", "success")
    return back(request, "/friends")


async def _own_relation(db: AsyncSession, fid: int, me: Principal) -> Friendship:
    rel = await db.get(Friendship, fid)
    if rel is None or me.id not in (rel.user_a, rel.user_b):
        raise HTTPException(404)
    return rel


@router.post("/friends/{fid}/accept")
async def friend_accept(fid: int, request: Request, db: AsyncSession = Depends(get_db),
                        me: Principal = Depends(require_member)):
    rel = await _own_relation(db, fid, me)
    if rel.status != "pending" or rel.requested_by == me.id:
        raise HTTPException(400)
    rel.status, rel.responded_at = "accepted", utcnow()
    await notify_users(db, [rel.requested_by], f"{me.name} accepted your friend request", None, "/messages",
                       "fa-user-check")
    await db.commit()
    flash(request, "Friend request accepted 🎉", "success")
    return redirect("/friends")


@router.post("/friends/{fid}/decline")
async def friend_decline(fid: int, request: Request, db: AsyncSession = Depends(get_db),
                         me: Principal = Depends(require_member)):
    rel = await _own_relation(db, fid, me)
    if rel.status == "pending":
        await db.delete(rel)
        await db.commit()
    flash(request, "Request removed.", "info")
    return redirect("/friends")


@router.post("/friends/unfriend/{uid}")
async def unfriend(uid: int, request: Request, db: AsyncSession = Depends(get_db),
                   me: Principal = Depends(require_member)):
    rel = await relation(db, me.id, uid)
    if rel is not None and rel.status == "accepted":
        await db.delete(rel)
        await db.commit()
    flash(request, "Removed from your friends.", "info")
    return back(request, "/friends")


@router.post("/friends/block/{uid}")
async def block(uid: int, request: Request, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_member)):
    await _member(db, uid)
    if uid == me.id:
        raise HTTPException(400)
    rel = await relation(db, me.id, uid)
    if rel is None:
        a, b = pair(me.id, uid)
        rel = Friendship(user_a=a, user_b=b, requested_by=me.id)
        db.add(rel)
    rel.status, rel.blocked_by, rel.responded_at = "blocked", me.id, utcnow()
    await db.commit()
    flash(request, "Blocked. They can't message you or send requests anymore.", "success")
    return back(request, "/friends")


@router.post("/friends/unblock/{uid}")
async def unblock(uid: int, request: Request, db: AsyncSession = Depends(get_db),
                  me: Principal = Depends(require_member)):
    rel = await relation(db, me.id, uid)
    if rel is not None and rel.status == "blocked" and rel.blocked_by == me.id:
        await db.delete(rel)
        await db.commit()
    flash(request, "Unblocked.", "info")
    return redirect("/friends#blocked")


@router.post("/report/user/{uid}")
async def report_user(uid: int, request: Request, db: AsyncSession = Depends(get_db),
                      me: Principal = Depends(require_member), category: str = Form("complaint"),
                      reason: str = Form(...)):
    target = await db.get(User, uid)
    if target is None or not reason.strip():
        flash(request, "Please describe what happened.", "danger")
        return back(request, "/friends")
    if not limiter.hit(f"report:{me.key}", settings.REPORTS_PER_HOUR, 3600):  # each report e-mails the admins
        flash(request, "You've sent several reports recently. The administration has them — please wait a "
                       "while before sending more, or speak to a teacher.", "warning")
        return back(request, "/friends")
    category = category if category in REPORT_CATEGORIES else "other"
    shared = select(ChatMember.room_id).where(ChatMember.user_id == me.id)
    recent = (await db.scalars(select(ChatMessage).where(ChatMessage.user_id == uid, ChatMessage.room_id.in_(shared))
                               .order_by(ChatMessage.id.desc()).limit(5))).all()
    evidence = [{"from": me.name, "text": reason.strip()[:2000]}] + [
        {"from": target.full_name, "text": m.text, "message_id": m.id} for m in reversed(recent)]
    await create_report(db, reporter=me.user, subject=target, subject_name=target.full_name, category=category,
                        severity="medium", source="manual", summary=reason.strip()[:500], evidence=evidence)
    flash(request, "Thank you — your report was sent confidentially to the administration.", "success")
    return back(request, "/friends")


# ------------------------------------------------------------------ conversations
@router.get("/messages", response_class=HTMLResponse)
@router.get("/messages/{rid}", response_class=HTMLResponse)
async def messages(request: Request, rid: int = 0, db: AsyncSession = Depends(get_db),
                   me: Principal = Depends(require_member)):
    rooms = (await db.scalars(select(ChatRoom).join(ChatMember, ChatMember.room_id == ChatRoom.id)
                              .options(selectinload(ChatRoom.members).selectinload(ChatMember.user))
                              .where(ChatMember.user_id == me.id)
                              .order_by(ChatRoom.last_message_at.desc().nulls_last(), ChatRoom.id.desc()))).all()
    unread = await unread_counts(db, me.id)
    active = next((r for r in rooms if r.id == rid), None) if rid else None
    if rid and active is None:
        raise HTTPException(404)
    history, can_send, reason = [], False, ""
    if active is not None:
        rows = (await db.scalars(select(ChatMessage).where(ChatMessage.room_id == active.id,
                                                           ChatMessage.deleted.is_(False))
                                 .order_by(ChatMessage.id.desc()).limit(60))).all()
        history = await messages_json(db, list(reversed(rows)))
        can_send, reason = await can_post(db, active, me.id)
        if rows:
            await db.execute(update(ChatMember).where(ChatMember.room_id == active.id, ChatMember.user_id == me.id)
                             .values(last_read_id=rows[0].id))
            await db.commit()
    friends_list = list((await users_by_id(db, await friend_ids(db, me.id))).values())
    teachers = (await db.scalars(select(User).where(User.role == "staff", User.status == "active",
                                                    User.staff_role.in_(TEACHING_ROLES), User.id != me.id)
                                 .order_by(User.full_name).limit(200))).all()
    return render(request, "social/messages.html", {
        "rooms": rooms, "unread": unread, "active": active, "history": history, "can_send": can_send,
        "reason": reason, "friends": sorted(friends_list, key=lambda u: u.full_name), "teachers": teachers,
        "online": set(social_hub.online([m.user_id for r in rooms for m in r.members]))})


@router.post("/messages/direct/{uid}")
async def start_direct(uid: int, request: Request, db: AsyncSession = Depends(get_db),
                       me: Principal = Depends(require_member)):
    if not await are_friends(db, me.id, uid):
        flash(request, "You can message people once they accept your friend request.", "warning")
        return back(request, "/friends")
    room = await get_or_create_direct(db, me.id, uid)
    await db.commit()
    return redirect(f"/messages/{room.id}")


@router.post("/groups")
async def create_group(request: Request, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_member),
                       name: str = Form(...), description: str = Form("")):
    form = await request.form()
    ids = set(ints(form.getlist("member_ids"))) - {me.id}
    allowed = set(await friend_ids(db, me.id)) | set((await db.scalars(select(User.id).where(
        User.role == "staff", User.status == "active", User.staff_role.in_(TEACHING_ROLES)))).all())
    ids &= allowed
    if not name.strip() or not ids:
        flash(request, "Give the group a name and add at least one friend or teacher.", "danger")
        return back(request, "/messages")
    room = ChatRoom(kind="group", name=name.strip()[:80], description=description.strip()[:300] or None,
                    created_by=me.id, last_message_at=utcnow())
    db.add(room)
    await db.flush()
    db.add(ChatMember(room_id=room.id, user_id=me.id, role="owner"))
    db.add_all([ChatMember(room_id=room.id, user_id=u) for u in ids])
    await notify_users(db, ids, f"{me.name} added you to “{room.name}”", None, f"/messages/{room.id}", "fa-users")
    await db.commit()
    await social_hub.push(list(ids), {"type": "room-added", "room": room.id})
    flash(request, f"Group “{room.name}” created.", "success")
    return redirect(f"/messages/{room.id}")


async def _group(db: AsyncSession, rid: int, me: Principal, owner: bool = False) -> ChatRoom:
    room = await db.scalar(select(ChatRoom).options(selectinload(ChatRoom.members)).where(ChatRoom.id == rid))
    if room is None or room.kind != "group":
        raise HTTPException(404)
    membership = next((m for m in room.members if m.user_id == me.id), None)
    if membership is None or (owner and membership.role != "owner"):
        raise HTTPException(403)
    return room


@router.post("/groups/{rid}/members")
async def add_members(rid: int, request: Request, db: AsyncSession = Depends(get_db),
                      me: Principal = Depends(require_member)):
    room = await _group(db, rid, me, owner=True)
    form = await request.form()
    current = {m.user_id for m in room.members}
    allowed = set(await friend_ids(db, me.id)) | set((await db.scalars(select(User.id).where(
        User.role == "staff", User.status == "active", User.staff_role.in_(TEACHING_ROLES)))).all())
    new = set(ints(form.getlist("member_ids"))) & allowed - current
    db.add_all([ChatMember(room_id=rid, user_id=u) for u in new])
    await notify_users(db, new, f"{me.name} added you to “{room.name}”", None, f"/messages/{rid}", "fa-users")
    await db.commit()
    flash(request, f"Added {len(new)} member(s).", "success")
    return redirect(f"/messages/{rid}")


@router.post("/groups/{rid}/remove/{uid}")
async def remove_member(rid: int, uid: int, request: Request, db: AsyncSession = Depends(get_db),
                        me: Principal = Depends(require_member)):
    await _group(db, rid, me, owner=True)
    if uid != me.id:
        await db.execute(delete(ChatMember).where(ChatMember.room_id == rid, ChatMember.user_id == uid))
        await db.commit()
    flash(request, "Member removed.", "info")
    return redirect(f"/messages/{rid}")


@router.post("/groups/{rid}/leave")
async def leave_group(rid: int, request: Request, db: AsyncSession = Depends(get_db),
                      me: Principal = Depends(require_member)):
    room = await _group(db, rid, me)
    rest = sorted((m for m in room.members if m.user_id != me.id), key=lambda m: m.joined_at)
    mine = next(m for m in room.members if m.user_id == me.id)
    if not rest:
        await db.delete(room)
    else:
        if mine.role == "owner":
            rest[0].role = "owner"
        await db.delete(mine)
    await db.commit()
    flash(request, "You left the group.", "info")
    return redirect("/messages")


@router.post("/groups/{rid}/rename")
async def rename_group(rid: int, request: Request, db: AsyncSession = Depends(get_db),
                       me: Principal = Depends(require_member), name: str = Form(...)):
    room = await _group(db, rid, me, owner=True)
    if name.strip():
        room.name = name.strip()[:80]
        await db.commit()
    return redirect(f"/messages/{rid}")


@router.get("/api/messages/{rid}/history")
async def history(rid: int, before: int, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_member)):
    if me.id not in await member_ids(db, rid):
        raise HTTPException(403)
    rows = (await db.scalars(select(ChatMessage).where(ChatMessage.room_id == rid, ChatMessage.deleted.is_(False),
                                                       ChatMessage.id < before)
                             .order_by(ChatMessage.id.desc()).limit(50))).all()
    return {"messages": await messages_json(db, list(reversed(rows)))}


@router.post("/api/messages/{rid}/read")
async def mark_read(rid: int, db: AsyncSession = Depends(get_db), me: Principal = Depends(require_member)):
    last = await db.scalar(select(func.max(ChatMessage.id)).where(ChatMessage.room_id == rid))
    await db.execute(update(ChatMember).where(ChatMember.room_id == rid, ChatMember.user_id == me.id)
                     .values(last_read_id=last or 0))
    await db.commit()
    return {"ok": True}


@router.websocket("/ws/social")
async def social_socket(websocket: WebSocket):
    if not same_origin(websocket):
        await websocket.close(code=4400)
        return
    async with SessionLocal() as db:
        principal = await load_principal(websocket.session, db)
    if principal is None or principal.kind not in ("student", "staff"):
        await websocket.close(code=4403)
        return
    if social_hub.connections_of(principal.id) >= settings.MAX_SOCKETS_PER_USER:
        await websocket.close(code=4429)  # too many tabs/sockets for one person
        return
    await websocket.accept()
    peer = ChatPeer(ws=websocket, key=principal.key, name=principal.name, role=principal.kind,
                    ip=client_ip(websocket), user_id=principal.id)
    social_hub.add(peer)
    try:
        while True:
            try:
                data = json.loads(await websocket.receive_text())
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            kind = data.get("type")
            if kind == "ping":
                await peer.send({"type": "pong"})
                continue
            room_id = int_or(data.get("room"), 0)
            if kind == "typing" and room_id:
                async with SessionLocal() as db:
                    ids = await member_ids(db, room_id)
                if principal.id in ids:
                    await social_hub.push([u for u in ids if u != principal.id],
                                          {"type": "typing", "room": room_id, "name": principal.name})
                continue
            if kind != "message" or not room_id:
                continue
            text = str(data.get("text") or "").strip()[:2000]
            if not text:
                continue
            if not limiter.hit(f"social:{peer.key}", settings.CHAT_RATE_LIMIT, 30):
                await peer.send({"type": "notice", "text": "You're sending messages too fast — slow down a little."})
                continue
            async with SessionLocal() as db:
                room = await db.get(ChatRoom, room_id)
                ok, reason = await can_post(db, room, principal.id) if room else (False, "Conversation not found.")
                if not ok:
                    await peer.send({"type": "notice", "text": reason})
                    continue
                outcome = await moderate(db, text=text, principal=principal, ip=peer.ip, context="friends_chat")
                if outcome.banned:
                    break
                msg = ChatMessage(room_id=room_id, user_id=principal.id, sender_name=principal.name,
                                  text=outcome.text, flagged=outcome.flagged)
                db.add(msg)
                room.last_message_at = utcnow()
                await db.commit()
                ids = await member_ids(db, room_id)
            await social_hub.push(ids, message_json(msg))
            if outcome.notice:
                await peer.send({"type": "notice", "text": outcome.notice})
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("Social socket error")
    finally:
        social_hub.remove(peer)
