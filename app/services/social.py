"""Friends, direct messages and groups. One WebSocket per user carries all of their conversations."""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ChatMember, ChatRoom, Friendship, User
from .chat import ChatPeer
from .moderation import on_ban


def pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


async def relation(db: AsyncSession, me: int, other: int) -> Friendship | None:
    a, b = pair(me, other)
    return await db.scalar(select(Friendship).where(Friendship.user_a == a, Friendship.user_b == b))


async def are_friends(db: AsyncSession, me: int, other: int) -> bool:
    rel = await relation(db, me, other)
    return rel is not None and rel.status == "accepted"


async def friend_ids(db: AsyncSession, me: int) -> list[int]:
    rows = (await db.execute(select(Friendship.user_a, Friendship.user_b).where(
        Friendship.status == "accepted", or_(Friendship.user_a == me, Friendship.user_b == me)))).all()
    return [b if a == me else a for a, b in rows]


async def blocked_ids(db: AsyncSession, me: int) -> set[int]:
    """Users I blocked or who blocked me (hidden from each other)."""
    rows = (await db.execute(select(Friendship.user_a, Friendship.user_b).where(
        Friendship.status == "blocked", or_(Friendship.user_a == me, Friendship.user_b == me)))).all()
    return {b if a == me else a for a, b in rows}


async def direct_room(db: AsyncSession, a: int, b: int) -> ChatRoom | None:
    room_id = await db.scalar(
        select(ChatMember.room_id).join(ChatRoom, ChatRoom.id == ChatMember.room_id)
        .where(ChatRoom.kind == "direct", ChatMember.user_id.in_([a, b]))
        .group_by(ChatMember.room_id).having(func.count(func.distinct(ChatMember.user_id)) == 2).limit(1))
    return await db.get(ChatRoom, room_id) if room_id else None


async def get_or_create_direct(db: AsyncSession, a: int, b: int) -> ChatRoom:
    room = await direct_room(db, a, b)
    if room is None:
        room = ChatRoom(kind="direct", created_by=a)
        db.add(room)
        await db.flush()
        db.add_all([ChatMember(room_id=room.id, user_id=a, role="member"),
                    ChatMember(room_id=room.id, user_id=b, role="member")])
        await db.flush()
    return room


async def member_ids(db: AsyncSession, room_id: int) -> list[int]:
    return list((await db.scalars(select(ChatMember.user_id).where(ChatMember.room_id == room_id))).all())


async def can_post(db: AsyncSession, room: ChatRoom, me: int) -> tuple[bool, str]:
    members = await member_ids(db, room.id)
    if me not in members:
        return False, "You are not a member of this conversation."
    if room.kind == "direct":
        other = next((m for m in members if m != me), None)
        if other is None:
            return False, "This conversation has ended."
        status = await db.scalar(select(User.status).where(User.id == other))
        if status == "banned":
            return False, "This person's account is banned, so messages can't be sent."
        if status != "active":
            return False, "This account is no longer available."
        rel = await relation(db, me, other)
        if rel is None or rel.status != "accepted":
            return False, "You can only message friends. This person is no longer on your friends list."
    return True, ""


async def unread_counts(db: AsyncSession, me: int) -> dict[int, int]:
    from ..models import ChatMessage

    rows = (await db.execute(
        select(ChatMember.room_id, func.count(ChatMessage.id))
        .join(ChatMessage, and_(ChatMessage.room_id == ChatMember.room_id, ChatMessage.id > ChatMember.last_read_id,
                                ChatMessage.user_id != me, ChatMessage.deleted.is_(False)))
        .where(ChatMember.user_id == me).group_by(ChatMember.room_id))).all()
    return {r[0]: r[1] for r in rows}


class SocialHub:
    """user_id -> live sockets (a user may have several tabs open)."""

    def __init__(self) -> None:
        self.sockets: dict[int, set[ChatPeer]] = {}

    def add(self, peer: ChatPeer) -> None:
        self.sockets.setdefault(peer.user_id or 0, set()).add(peer)

    def remove(self, peer: ChatPeer) -> None:
        group = self.sockets.get(peer.user_id or 0)
        if group is not None:
            group.discard(peer)
            if not group:
                self.sockets.pop(peer.user_id or 0, None)

    def online(self, user_ids: list[int]) -> list[int]:
        return [u for u in user_ids if u in self.sockets]

    def connections_of(self, user_id: int | None) -> int:
        return len(self.sockets.get(user_id or 0, ()))

    async def push(self, user_ids: list[int], payload: dict[str, Any]) -> None:
        peers = [p for u in set(user_ids) for p in self.sockets.get(u, set())]
        await asyncio.gather(*(p.send(payload) for p in peers))

    async def kick_where(self, predicate, reason: str) -> None:
        for peers in list(self.sockets.values()):
            for peer in [p for p in peers if predicate(p)]:
                await peer.send({"type": "kicked", "reason": reason})
                try:
                    await peer.ws.close(code=4403)
                except Exception:
                    pass


social_hub = SocialHub()


@on_ban
async def _disconnect_banned(ip: str, key: str | None) -> None:
    await social_hub.kick_where(lambda p: p.ip == ip or (key is not None and p.key == key),
                                "Your access was blocked for repeated abusive language.")


async def users_by_id(db: AsyncSession, ids) -> dict[int, User]:
    ids = list(set(ids))
    if not ids:
        return {}
    return {u.id: u for u in (await db.scalars(select(User).where(User.id.in_(ids)))).all()}
