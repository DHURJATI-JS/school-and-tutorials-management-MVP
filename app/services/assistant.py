"""Private AI assistant: live chat with long-term memory, role-aware data context and concern reporting.

* Conversations are visible only to their owner (no admin UI shows them).
* Memory = last messages + a rolling AI summary (fallback: truncated notes) so context survives sessions.
* Staff can ask about the progress of students in their batches (real attendance / score data is injected).
* Students get their own progress, today's classes and upcoming deadlines.
* If a message raises a complaint or safety concern, only that message is shared with the admins as a report.
"""
from __future__ import annotations

import datetime as dt
import logging
import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..cache import site_cache
from ..config import settings
from ..constants import SUPERVISOR_ROLES, TEACHING_ROLES
from ..database import SessionLocal
from ..models import (AssistantMemory, AssistantMessage, Attendance, Batch, Enrollment, Lesson, Task, Test,
                      TestAttempt, User)
from .ai import GUARD, AIUnavailable, ai, tone_for
from .notify import notify_users
from .reports import classify, create_report, looks_like_report, match_person

log = logging.getLogger("classnest.assistant")
HISTORY = 12
SUMMARIZE_AFTER = 16


async def student_snapshots(db: AsyncSession, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    users = {u.id: u for u in (await db.scalars(select(User).where(User.id.in_(ids)))).all()}
    att = {r[0]: (r[1], r[2]) for r in (await db.execute(
        select(Attendance.user_id, func.count().filter(Attendance.status == "present"), func.count())
        .where(Attendance.user_id.in_(ids)).group_by(Attendance.user_id))).all()}
    scores = {r[0]: (r[1], r[2]) for r in (await db.execute(
        select(TestAttempt.user_id, func.avg(TestAttempt.score * 100 / TestAttempt.max_score), func.count())
        .where(TestAttempt.user_id.in_(ids), TestAttempt.status == "graded", TestAttempt.max_score > 0)
        .group_by(TestAttempt.user_id))).all()}
    recent: dict[int, list[str]] = {}
    for uid, title, score, max_score in (await db.execute(
            select(TestAttempt.user_id, Test.title, TestAttempt.score, TestAttempt.max_score)
            .join(Test, Test.id == TestAttempt.test_id)
            .where(TestAttempt.user_id.in_(ids), TestAttempt.status == "graded")
            .order_by(TestAttempt.submitted_at.desc()).limit(len(ids) * 3))).all():
        if len(recent.setdefault(uid, [])) < 3:
            recent[uid].append(f"{title}: {float(score or 0):g}/{float(max_score or 0):g}")
    batches: dict[int, list[str]] = {}
    for uid, name in (await db.execute(select(Enrollment.user_id, Batch.name).join(Batch, Batch.id == Enrollment.batch_id)
                                       .where(Enrollment.user_id.in_(ids)))).all():
        batches.setdefault(uid, []).append(name)
    out = {}
    for uid, user in users.items():
        present, total = att.get(uid, (0, 0))
        avg, n = scores.get(uid, (None, 0))
        out[uid] = {"name": user.full_name, "class": user.class_level, "batches": batches.get(uid, []),
                    "attendance": f"{present}/{total} classes ({present * 100 // total}%)" if total else "no classes yet",
                    "average": f"{float(avg):.0f}% over {n} test(s)" if avg is not None else "no graded tests yet",
                    "recent": recent.get(uid, []), "special_needs": user.special_needs}
    return out


def _fmt_snapshot(s: dict) -> str:
    line = (f"- {s['name']} ({s['class'] or 'student'}; batches: {', '.join(s['batches']) or '—'}): attendance "
            f"{s['attendance']}; average score {s['average']}")
    if s["recent"]:
        line += "; recent: " + "; ".join(s["recent"])
    if s["special_needs"]:
        line += " (has special needs — discuss with extra sensitivity)"
    return line


async def staff_context(db: AsyncSession, me: User, text: str) -> str:
    if me.staff_role not in TEACHING_ROLES:
        return f"The user is {me.full_name}, a {me.role_label} at the institute (not a teacher)."
    stmt = select(Batch).where(Batch.is_active.is_(True))
    if me.staff_role not in SUPERVISOR_ROLES:
        stmt = stmt.where(Batch.teacher_id == me.id)
    batches = list((await db.scalars(stmt)).all())
    ids = [b.id for b in batches]
    lines = [f"The user is {me.full_name}, {me.role_label}. Batches they can see: "
             + (", ".join(f"{b.name} ({b.class_level} {b.subject})" for b in batches) or "none") + "."]
    if not ids:
        return lines[0]
    words = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", text)}
    students = (await db.execute(select(User.id, User.full_name).join(Enrollment, Enrollment.user_id == User.id)
                                 .where(Enrollment.batch_id.in_(ids), User.status == "active").distinct())).all()
    mentioned = [uid for uid, name in students if {p.lower() for p in name.split() if len(p) > 2} & words]
    if mentioned:
        snaps = await student_snapshots(db, mentioned[:5])
        lines.append("Data for the students mentioned (only use this to answer):")
        lines += [_fmt_snapshot(s) for s in snaps.values()]
    elif re.search(r"\b(batch|class|students|progress|performance|attendance|weak|struggling|top)\b", text, re.I):
        rows = (await db.execute(
            select(Batch.name, func.avg(TestAttempt.score * 100 / TestAttempt.max_score), func.count(TestAttempt.id))
            .join(Test, Test.batch_id == Batch.id).join(TestAttempt, TestAttempt.test_id == Test.id)
            .where(Batch.id.in_(ids), TestAttempt.status == "graded", TestAttempt.max_score > 0)
            .group_by(Batch.name))).all()
        if rows:
            lines.append("Batch averages: " + "; ".join(f"{n}: {float(a):.0f}% ({c} graded attempts)" for n, a, c in rows))
        lines.append(f"There are {len(students)} active students in these batches. Ask for a student's name for details.")
    return "\n".join(lines)


async def student_context(db: AsyncSession, me: User) -> str:
    snap = (await student_snapshots(db, [me.id])).get(me.id)
    today = dt.datetime.now(settings.tz).date()
    ids = list((await db.scalars(select(Enrollment.batch_id).where(Enrollment.user_id == me.id))).all())
    tasks = (await db.execute(select(Task.title, Task.due_date, Batch.name).join(Batch, Batch.id == Task.batch_id)
                              .where(Task.batch_id.in_(ids or [0]), Task.assign_date <= today, Task.due_date >= today)
                              .order_by(Task.due_date).limit(8))).all()
    classes = (await db.execute(select(Lesson.title, Lesson.start_time, Batch.name).join(Batch, Batch.id == Lesson.batch_id)
                                .where(Lesson.batch_id.in_(ids or [0]), Lesson.date == today,
                                       Lesson.status != "cancelled"))).all()
    lines = [f"The user is {me.full_name}, a student ({me.class_level})."]
    if snap:
        lines.append("Their progress: " + _fmt_snapshot(snap)[2:])
    if tasks:
        lines.append("Open tasks: " + "; ".join(f"{t} ({b}) due {d:%a %d %b}" for t, d, b in tasks))
    if classes:
        lines.append("Classes today: " + "; ".join(f"{t} ({b}) at {s:%I:%M %p}" for t, s, b in classes))
    return "\n".join(lines)


async def build_messages(db: AsyncSession, me: User, text: str, concern: bool) -> list[dict]:
    memory = await db.get(AssistantMemory, me.id)
    history = list(reversed((await db.scalars(select(AssistantMessage).where(AssistantMessage.user_id == me.id)
                                              .order_by(AssistantMessage.id.desc()).limit(HISTORY))).all()))
    context = await (staff_context(db, me, text) if me.is_staff else student_context(db, me))
    system = (f"You are the private assistant of {site_cache.data['site_name']}, a school / tuition centre. "
              f"{tone_for(bool(me.special_needs))} Be helpful, concise and warm. Only use the data below for facts "
              "about people; never invent marks or attendance. Never reveal one student's data to another student. "
              f"Today is {dt.datetime.now(settings.tz):%A %d %B %Y}. {GUARD}\n\n<context>\n{context}\n</context>")
    if memory and memory.summary:
        system += f"\n\nWhat you remember about this user from earlier conversations:\n<context>\n{memory.summary}\n</context>"
    if concern:
        system += ("\n\nThe user may be raising a concern or complaint. Respond with empathy, thank them for "
                   "speaking up, tell them their concern has been shared confidentially with the administration, "
                   "and gently ask for helpful details (what happened, when, where) if missing. If anyone is in "
                   "immediate danger, tell them to contact a trusted adult or call 112 (India emergency).")
    messages = [{"role": "system", "content": system}]
    messages += [{"role": m.role, "content": m.content[:2000]} for m in history]
    messages.append({"role": "user", "content": text})
    return messages


def offline_reply(concern: bool) -> str:
    base = ("🤖 I'm offline for a moment (the school's AI server isn't responding), so I can't give a full answer "
            "right now. Your message is saved — please try again in a few minutes.")
    if concern:
        base += ("\n\n🛡️ Your concern has still been **recorded and shared confidentially with the "
                 "administration**. If you are in danger, please tell a trusted adult or call **112**.")
    return base


async def file_concern(user_id: int, text: str, message_id: int) -> None:
    """Background: classify the message and, if it is a report, log it for the admins."""
    async with SessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None:
            return
        verdict = await classify(text, user.role_label)
        if not verdict["is_report"]:
            return
        subject = await match_person(db, f"{verdict.get('about', '')} {text}", user.id, prefer_staff=user.is_student)
        report = await create_report(
            db, reporter=user, subject=subject, subject_name=verdict.get("about") or None,
            category=verdict["category"], severity=verdict["severity"], source="ai_assistant",
            summary=verdict["summary"], evidence=[{"from": user.full_name, "text": text[:2000], "message_id": message_id,
                                                   "at": dt.datetime.now(settings.tz).isoformat(timespec="minutes")}])
        if report is not None:
            await notify_users(db, [user.id], "Your concern was shared with the administration",
                               f"Reference #{report.id}. You'll be notified when it is reviewed.", "/assistant",
                               "fa-shield-heart")
            await db.commit()


async def refresh_memory(user_id: int) -> None:
    async with SessionLocal() as db:
        memory = await db.get(AssistantMemory, user_id)
        start = memory.summarized_until if memory else 0
        rows = list((await db.scalars(select(AssistantMessage).where(AssistantMessage.user_id == user_id,
                                                                     AssistantMessage.id > start)
                                      .order_by(AssistantMessage.id))).all())
        if len(rows) < SUMMARIZE_AFTER:
            return
        older = rows[:-HISTORY]
        if not older:
            return
        transcript = "\n".join(f"{m.role}: {m.content[:600]}" for m in older)
        previous = memory.summary if memory else ""
        try:
            summary = await ai.chat([
                {"role": "system", "content": "Update the long-term memory about this user for a school assistant. "
                                              "Keep stable facts, preferences, goals, struggles and ongoing issues "
                                              "as short bullet points (max 12). Drop small talk. " + GUARD},
                {"role": "user", "content": f"Current memory:\n<context>\n{previous}\n</context>\n\nNew conversation:"
                                            f"\n<context>\n{transcript[-8000:]}\n</context>"},
            ], temperature=0.2, max_tokens=500, timeout=120)
        except AIUnavailable:
            notes = [f"- asked: {m.content[:140]}" for m in older if m.role == "user"][-6:]
            summary = "\n".join(filter(None, [previous] + notes))[-2000:]
        if memory is None:
            memory = AssistantMemory(user_id=user_id)
            db.add(memory)
        memory.summary, memory.summarized_until = summary[:3000], older[-1].id
        await db.commit()


__all__ = ["build_messages", "offline_reply", "file_concern", "refresh_memory", "looks_like_report"]
