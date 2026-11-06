"""Live-class engine: WebRTC signalling rooms (mesh), attendance, recording assembly and AI notes.

* Signalling over WebSockets; media flows peer-to-peer (STUN/TURN configurable).
* Join/leave times are tracked per participant; a student who attends >= MEETING_ATTENDANCE_RATIO
  of the meeting ("the whole meeting", default 90% to tolerate network hiccups) is marked present.
* The host's browser records a composite of the class and uploads it in chunks; chunks are
  assembled when the meeting ends and published as a recording video for the lesson.
* Captions (browser speech recognition) + chat form the transcript; the local LLM turns it into
  structured class notes (with an extractive fallback when AI is offline) which are indexed into
  pgvector for the AI doubt solver.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
import secrets
import shutil
import string
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket
from sqlalchemy import bindparam, case, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..config import settings
from ..database import SessionLocal
from ..models import (Attendance, Batch, Enrollment, Lesson, Meeting, MeetingMessage, MeetingParticipant, User,
                      Video, utcnow)
from ..templating import to_local
from . import rag
from .ai import GUARD, AIUnavailable, ai
from .background import spawn
from .chat import send_or_drop
from .moderation import mask, on_ban
from .notify import batch_student_ids, notify_users
from .storage import abs_path

log = logging.getLogger("classnest.meet")
EMPTY_ROOM_GRACE_SECONDS = 180
STOP = set("this that with have from your they them were what when where which will would there their about "
           "into also just like okay yeah sure then than very really because should could".split())


def new_code() -> str:
    letters = string.ascii_lowercase
    return "-".join("".join(secrets.choice(letters) for _ in range(n)) for n in (3, 4, 3))


# ------------------------------------------------------------------ in-memory rooms
@dataclass(eq=False)
class Peer:
    peer_id: str
    ws: WebSocket
    key: str
    name: str
    role: str
    user_id: int | None
    is_host: bool
    ip: str
    joined: float = field(default_factory=time.monotonic)
    state: dict[str, Any] = field(default_factory=lambda: {"audio": True, "video": True, "screen": False,
                                                           "hand": False})

    def public(self) -> dict[str, Any]:
        return {"peer_id": self.peer_id, "name": self.name, "role": self.role, "is_host": self.is_host,
                "state": self.state}

    async def send(self, payload: dict[str, Any]) -> bool:
        return await send_or_drop(self.ws, payload)  # a stalled reader must not freeze the meeting


@dataclass(eq=False)
class Room:
    code: str
    meeting_id: int
    peers: dict[str, Peer] = field(default_factory=dict)
    end_task: asyncio.Task | None = None
    recording: bool = False


class RoomManager:
    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}

    def get(self, code: str) -> Room | None:
        return self.rooms.get(code)

    def add(self, code: str, meeting_id: int, peer: Peer) -> Room:
        room = self.rooms.setdefault(code, Room(code, meeting_id))
        if room.end_task is not None:
            room.end_task.cancel()
            room.end_task = None
        room.peers[peer.peer_id] = peer
        return room

    def remove(self, code: str, peer_id: str) -> Peer | None:
        room = self.rooms.get(code)
        if room is None:
            return None
        peer = room.peers.pop(peer_id, None)
        if not room.peers and room.end_task is None:
            room.end_task = spawn(_auto_end(code, room.meeting_id), name=f"auto-end:{code}")
        return peer

    async def broadcast(self, code: str, payload: dict[str, Any], exclude: str | None = None) -> None:
        room = self.rooms.get(code)
        if room is None:
            return
        await asyncio.gather(*(p.send(payload) for p in list(room.peers.values()) if p.peer_id != exclude))

    async def send(self, code: str, peer_id: str, payload: dict[str, Any]) -> bool:
        room = self.rooms.get(code)
        peer = room.peers.get(peer_id) if room else None
        return await peer.send(payload) if peer else False

    def count(self, code: str) -> int:
        room = self.rooms.get(code)
        return len(room.peers) if room else 0

    async def kick_where(self, predicate: Callable[[Peer], bool], reason: str) -> int:
        kicked = 0
        for room in list(self.rooms.values()):
            for peer in [p for p in room.peers.values() if predicate(p)]:
                await peer.send({"type": "kicked", "reason": reason})
                try:
                    await peer.ws.close(code=4403)
                except Exception:
                    pass
                kicked += 1
        return kicked


rooms = RoomManager()


@on_ban
async def _disconnect_banned(ip: str, key: str | None) -> None:
    await rooms.kick_where(lambda p: p.ip == ip or (key is not None and p.key == key),
                           "Your access was blocked for repeated abusive language.")


async def _auto_end(code: str, meeting_id: int) -> None:
    await asyncio.sleep(EMPTY_ROOM_GRACE_SECONDS)
    room = rooms.get(code)
    if room is not None and not room.peers:
        await end_meeting(meeting_id, reason="everyone left")


# ------------------------------------------------------------------ participant tracking
async def record_join(meeting_id: int, principal) -> None:
    async with SessionLocal() as db:
        stmt = pg_insert(MeetingParticipant).values(
            meeting_id=meeting_id, participant_key=principal.key, user_id=principal.id,
            display_name=principal.name[:120], role=principal.kind, first_joined_at=utcnow(), total_seconds=0,
            join_count=1)
        stmt = stmt.on_conflict_do_update(constraint="uq_meeting_participant",
                                          set_={"join_count": MeetingParticipant.join_count + 1})
        await db.execute(stmt)
        await db.commit()


async def record_leave(meeting_id: int, key: str, seconds: int) -> None:
    async with SessionLocal() as db:
        await db.execute(update(MeetingParticipant)
                         .where(MeetingParticipant.meeting_id == meeting_id, MeetingParticipant.participant_key == key)
                         .values(total_seconds=MeetingParticipant.total_seconds + max(0, seconds),
                                 last_left_at=utcnow()))
        await db.commit()


async def save_message(meeting_id: int, kind: str, key: str, name: str, text: str, flagged: bool = False) -> None:
    async with SessionLocal() as db:
        db.add(MeetingMessage(meeting_id=meeting_id, kind=kind, sender_key=key, sender_name=name[:120],
                              text=text[:4000], flagged=flagged))
        await db.commit()


# ------------------------------------------------------------------ lifecycle
async def end_meeting(meeting_id: int, reason: str = "host") -> None:
    async with SessionLocal() as db:
        meeting = await db.get(Meeting, meeting_id, with_for_update=True)
        if meeting is None or meeting.status in ("ended", "cancelled"):
            return
        now = utcnow()
        meeting.status, meeting.ended_at = "ended", now
        meeting.started_at = meeting.started_at or now
        meeting.notes_status = "pending"
        room = rooms.get(meeting.code)
        if room is not None and room.peers:
            stamp, rows = time.monotonic(), []
            for peer in room.peers.values():
                rows.append({"pkey": peer.key, "secs": int(stamp - peer.joined)})
                peer.joined = stamp
            t = MeetingParticipant.__table__  # Core executemany: one round-trip for the whole room (was one per peer)
            await db.execute(update(t).where(t.c.meeting_id == meeting_id, t.c.participant_key == bindparam("pkey"))
                             .values(total_seconds=t.c.total_seconds + bindparam("secs"), last_left_at=now), rows)
        if meeting.lesson_id:
            lesson = await db.get(Lesson, meeting.lesson_id)
            if lesson is not None and lesson.status == "scheduled":
                lesson.status = "completed"
        code = meeting.code
        await db.commit()
    log.info("Meeting %s ended (%s)", code, reason)
    room = rooms.rooms.pop(code, None)
    if room is not None:
        for peer in list(room.peers.values()):
            await peer.send({"type": "ended", "reason": reason})
            try:
                await peer.ws.close(code=1000)
            except Exception:
                pass
    spawn(finalize_meeting(meeting_id), name=f"finalize:{meeting_id}")


async def finalize_meeting(meeting_id: int) -> None:
    await asyncio.sleep(3)  # let the host's last recording chunk land
    await assemble_recording(meeting_id)
    await compute_attendance(meeting_id)
    await generate_notes(meeting_id)


def recording_dir(code: str):
    return abs_path(f"recordings/{code}")


def _concat_chunks(code: str) -> int:
    folder = recording_dir(code)
    parts = sorted(folder.glob("chunk_*.webm")) if folder.exists() else []
    if not parts:
        return 0
    target = abs_path(f"recordings/{code}.webm")
    with target.open("ab") as out:
        for part in parts:
            with part.open("rb") as src:
                shutil.copyfileobj(src, out)
    shutil.rmtree(folder, ignore_errors=True)
    return target.stat().st_size


async def assemble_recording(meeting_id: int) -> None:
    async with SessionLocal() as db:
        meeting = await db.get(Meeting, meeting_id)
        if meeting is None:
            return
        size = await asyncio.to_thread(_concat_chunks, meeting.code)
        if not size:
            return
        meeting.recording_path = f"recordings/{meeting.code}.webm"
        meeting.recording_bytes = size
        if meeting.batch_id and meeting.kind == "class":
            video = await db.scalar(select(Video).where(Video.meeting_id == meeting.id))
            if video is None:
                video = Video(batch_id=meeting.batch_id, title=f"Recording · {meeting.title}"[:160],
                              file_path=meeting.recording_path, mime="video/webm", size_bytes=size,
                              duration_seconds=meeting.duration_seconds, source="recording", meeting_id=meeting.id,
                              uploaded_by=meeting.host_name, description="Automatically recorded live class.")
                db.add(video)
                await db.flush()
            else:
                video.size_bytes = size
            if meeting.lesson_id:
                lesson = await db.get(Lesson, meeting.lesson_id)
                if lesson is not None and lesson.video_id is None:
                    lesson.video_id = video.id
        await db.commit()


async def compute_attendance(meeting_id: int) -> None:
    async with SessionLocal() as db:
        meeting = await db.get(Meeting, meeting_id)
        if meeting is None or meeting.kind != "class" or not meeting.lesson_id or not meeting.batch_id:
            return
        lesson = await db.get(Lesson, meeting.lesson_id)
        if lesson is None:
            return
        duration = meeting.duration_seconds or 1
        seconds = dict((await db.execute(
            select(MeetingParticipant.user_id, MeetingParticipant.total_seconds)
            .where(MeetingParticipant.meeting_id == meeting_id, MeetingParticipant.role == "student",
                   MeetingParticipant.user_id.is_not(None)))).all())
        enrolled = (await db.scalars(select(Enrollment.user_id).join(User, User.id == Enrollment.user_id)
                                     .where(Enrollment.batch_id == meeting.batch_id, User.status == "active"))).all()
        rows = []
        for uid in enrolled:
            ratio = min(1.0, seconds.get(uid, 0) / duration)
            present = ratio >= settings.MEETING_ATTENDANCE_RATIO
            rows.append({"user_id": uid, "batch_id": meeting.batch_id, "lesson_id": lesson.id, "date": lesson.date,
                         "status": "present" if present else "absent", "source": "meeting",
                         "detail": f"Attended {ratio:.0%} of the live class", "created_at": utcnow()})
        if rows:
            stmt = pg_insert(Attendance).values(rows)
            keep_present = Attendance.status == "present"
            stmt = stmt.on_conflict_do_update(constraint="uq_attendance_user_lesson", set_={
                "status": case((keep_present, Attendance.status), else_=stmt.excluded.status),
                "source": case((keep_present, Attendance.source), else_=stmt.excluded.source),
                "detail": case((keep_present, Attendance.detail), else_=stmt.excluded.detail),
            })
            await db.execute(stmt)
        await db.commit()


# ------------------------------------------------------------------ notes
def extractive_notes(title: str, transcript: str) -> str:
    """Deterministic fallback when the AI is offline: key sentences, key terms, questions."""
    lines = [ln.split(": ", 1)[-1].strip() for ln in transcript.splitlines() if ": " in ln]
    text = " ".join(lines)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if 25 <= len(s.strip()) <= 300]
    words = [w for w in re.findall(r"[a-zA-Z]{4,}", text.lower()) if w not in STOP]
    freq = Counter(words)
    best = set(sorted(sentences, key=lambda s: sum(freq[w] for w in re.findall(r"[a-zA-Z]{4,}", s.lower()))
                      / (1 + len(s) / 80), reverse=True)[:8])
    points = [s for s in sentences if s in best]
    questions = [ln for ln in lines if ln.endswith("?")][:10]
    out = [f"## {title}", "",
           "> ⚠️ These notes were generated automatically **without AI** because the AI server was offline. "
           "Please review them.", "", "### Key points"]
    out += [f"- {p}" for p in points] or ["- (not enough spoken content was captured)"]
    if freq:
        out += ["", "### Key terms", ", ".join(w for w, _ in freq.most_common(10))]
    if questions:
        out += ["", "### Questions asked in class"] + [f"- {q}" for q in questions]
    return "\n".join(out)


async def ai_notes(batch: Batch | None, title: str, topic: str, transcript: str) -> str:
    audience = f"{batch.class_level} students studying {batch.subject}" if batch else "parents and teachers"
    system = (f"You are an expert teaching assistant who writes clear, well-structured class notes for {audience}. "
              "Use Markdown with these sections: ## Summary, ## Key concepts, ## Detailed notes, ## Examples, "
              "## Questions & answers from class, ## Homework / action items (only if mentioned). Stay faithful to "
              "the transcript, never invent facts, and keep the language simple. " + GUARD)
    if len(transcript) <= 14000:
        return await ai.chat([{"role": "system", "content": system},
                              {"role": "user", "content": f"Class: {title}\nTopic: {topic}\n<context>\n{transcript}"
                                                          f"\n</context>"}],
                             temperature=0.3, max_tokens=1800, timeout=240)
    partials = []
    for i in range(0, min(len(transcript), 72000), 12000):  # map-reduce for long classes
        part = transcript[i:i + 12000]
        partials.append(await ai.chat(
            [{"role": "system", "content": "Summarise this part of a class transcript as concise bullet notes. "
                                           + GUARD},
             {"role": "user", "content": f"<context>\n{part}\n</context>"}], temperature=0.2, max_tokens=700,
            timeout=180))
    return await ai.chat([{"role": "system", "content": system},
                          {"role": "user", "content": f"Class: {title}\nTopic: {topic}\nPartial notes:\n<context>\n"
                                                      + "\n\n".join(partials) + "\n</context>"}],
                         temperature=0.3, max_tokens=1800, timeout=240)


async def generate_notes(meeting_id: int) -> None:
    async with SessionLocal() as db:
        meeting = await db.get(Meeting, meeting_id)
        if meeting is None:
            return
        batch = await db.get(Batch, meeting.batch_id) if meeting.batch_id else None
        lesson = await db.get(Lesson, meeting.lesson_id) if meeting.lesson_id else None
        messages = (await db.execute(
            select(MeetingMessage.kind, MeetingMessage.sender_name, MeetingMessage.text, MeetingMessage.created_at)
            .where(MeetingMessage.meeting_id == meeting_id, MeetingMessage.kind.in_(("chat", "transcript")))
            .order_by(MeetingMessage.id))).all()
        transcript = "\n".join(
            f"[{to_local(m.created_at):%H:%M}] {m.sender_name}{' (chat)' if m.kind == 'chat' else ''}: {m.text}"
            for m in messages)
        if meeting.recording_path:
            spoken = await ai.transcribe(abs_path(meeting.recording_path))
            if spoken:
                transcript = f"{transcript}\n\n[Recording transcript]\n{spoken}".strip()
        meeting.transcript = transcript or None
        topic = lesson.title if lesson else meeting.title
        if transcript.strip():
            try:
                notes, source = await ai_notes(batch, meeting.title, topic, transcript), "ai"
            except AIUnavailable as exc:
                log.info("AI notes unavailable for meeting %s: %s", meeting.code, exc)
                notes, source = extractive_notes(meeting.title, transcript), "fallback"
        else:
            notes, source = (f"## {meeting.title}\n\n_No transcript was captured. Turn on captions (CC) during the "
                             "class so the AI can take notes automatically._"), "fallback"
        meeting.ai_notes = mask(notes)
        meeting.notes_source, meeting.notes_status = source, "ready"
        if batch is not None:
            await rag.index_document(db, source_type="meeting", source_id=meeting.id,
                                     title=f"Class notes: {meeting.title}",
                                     content=f"{meeting.ai_notes}\n\n{transcript}", batch_id=batch.id,
                                     class_level=batch.class_level, subject=batch.subject)
            ids = await batch_student_ids(db, [batch.id])
            link = f"/student/lessons/{lesson.id}" if lesson else "/student/classes"
            await notify_users(db, ids, "Class notes & recording are ready", meeting.title, link, "fa-note-sticky")
        await db.commit()


def meeting_card(meeting: Meeting) -> dict[str, Any]:
    start = meeting.scheduled_start or meeting.created_at
    return {"code": meeting.code, "title": meeting.title, "status": meeting.status,
            "starts": to_local(start).strftime("%d %b, %I:%M %p") if start else "", "live": rooms.count(meeting.code)}


def ice_config() -> list[dict[str, Any]]:
    return settings.ice_servers


__all__ = ["rooms", "Peer", "new_code", "end_meeting", "record_join", "record_leave", "save_message",
           "recording_dir", "meeting_card", "ice_config", "dt"]
