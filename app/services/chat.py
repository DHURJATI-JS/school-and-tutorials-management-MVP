"""Batch group chat hub (WebSockets). Moderated by services.moderation; banned users are disconnected."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from .moderation import on_ban

SEND_TIMEOUT = 5.0


async def send_or_drop(ws: WebSocket, payload: dict[str, Any]) -> bool:
    """Send with a deadline. Broadcasts await every recipient, so a client that simply stops reading (its TCP
    buffer fills and send() blocks) would otherwise freeze the room for everyone; such a peer is dropped."""
    try:
        await asyncio.wait_for(ws.send_json(payload), SEND_TIMEOUT)
        return True
    except Exception:  # timeout, or the socket is already gone
        try:
            await asyncio.wait_for(ws.close(code=1013), 1)
        except Exception:
            pass
        return False


@dataclass(eq=False)
class ChatPeer:
    ws: WebSocket
    key: str
    name: str
    role: str
    ip: str
    user_id: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    async def send(self, payload: dict[str, Any]) -> bool:
        return await send_or_drop(self.ws, payload)


class ChatHub:
    def __init__(self) -> None:
        self.rooms: dict[int, set[ChatPeer]] = {}

    def join(self, batch_id: int, peer: ChatPeer) -> None:
        self.rooms.setdefault(batch_id, set()).add(peer)

    def leave(self, batch_id: int, peer: ChatPeer) -> None:
        room = self.rooms.get(batch_id)
        if room is not None:
            room.discard(peer)
            if not room:
                self.rooms.pop(batch_id, None)

    def online(self, batch_id: int) -> list[str]:
        return sorted({p.name for p in self.rooms.get(batch_id, set())})

    def connections_of(self, key: str) -> int:
        return sum(1 for room in self.rooms.values() for p in room if p.key == key)

    async def broadcast(self, batch_id: int, payload: dict[str, Any]) -> None:
        peers = list(self.rooms.get(batch_id, set()))
        await asyncio.gather(*(p.send(payload) for p in peers))

    async def kick_where(self, predicate: Callable[[ChatPeer], bool], reason: str) -> int:
        kicked = 0
        for room in list(self.rooms.values()):
            for peer in [p for p in room if predicate(p)]:
                await peer.send({"type": "kicked", "reason": reason})
                try:
                    await peer.ws.close(code=4403)
                except Exception:
                    pass
                kicked += 1
        return kicked


hub = ChatHub()


@on_ban
async def _disconnect_banned(ip: str, key: str | None) -> None:
    await hub.kick_where(lambda p: p.ip == ip or (key is not None and p.key == key),
                         "Your access was blocked for repeated abusive language.")
