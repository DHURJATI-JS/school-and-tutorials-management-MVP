"""Automated performance monitor.

Aggregates test scores, attendance and task completion per (student, batch) with a fixed number of
grouped SQL queries (no N+1), flags concerns with transparent rules, words the alerts with the local
LLM when available (template fallback otherwise) and notifies admins, the batch teacher and parents.

Students with special needs / health issues get lenient thresholds and gentle wording - except for
formal *warning* level notices, which stay polite but clear.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..cache import site_cache
from ..config import settings
from ..models import (Attendance, Batch, Enrollment, Lesson, PerformanceAlert, Task, TaskSubmission, Test,
                      TestAttempt, User, utcnow)
from .ai import AIUnavailable, ai, tone_for
from .mailer import queue_bulk, queue_email
from .notify import notify_users

log = logging.getLogger("classnest.performance")

LEVELS = {"info": 0, "concern": 1, "warning": 2}
THRESHOLDS = {
    False: {"score_concern": 40, "score_warning": 25, "att_concern": 75, "att_warning": 50, "missing": 3,
            "decline": 15},
    True: {"score_concern": 30, "score_warning": 20, "att_concern": 60, "att_warning": 40, "missing": 4,
           "decline": 20},
}
MAX_AI_MESSAGES_PER_RUN = 20


async def collect_metrics(db: AsyncSession) -> dict[tuple[int, int], dict[str, Any]]:
    today = dt.datetime.now(settings.tz).date()
    since = today - dt.timedelta(days=30)
    pairs = (await db.execute(
        select(Enrollment.user_id, Enrollment.batch_id, User.full_name, User.special_needs, User.parent_email,
               User.parent_name, User.email, Batch.name.label("batch_name"), Batch.teacher_id,
               Batch.subject, Batch.class_level)
        .join(User, User.id == Enrollment.user_id).join(Batch, Batch.id == Enrollment.batch_id)
        .where(User.status == "active", User.role == "student", Batch.is_active.is_(True)))).all()
    if not pairs:
        return {}

    attempts = (await db.execute(
        select(TestAttempt.user_id, Test.batch_id, TestAttempt.score, TestAttempt.max_score)
        .join(Test, Test.id == TestAttempt.test_id)
        .where(TestAttempt.status.in_(("submitted", "graded")), TestAttempt.max_score > 0,
               TestAttempt.submitted_at >= utcnow() - dt.timedelta(days=120))
        .order_by(TestAttempt.submitted_at))).all()
    scores: dict[tuple[int, int], list[float]] = defaultdict(list)
    for a in attempts:
        scores[(a.user_id, a.batch_id)].append(float(a.score or 0) * 100 / float(a.max_score))

    lessons = dict((await db.execute(
        select(Lesson.batch_id, func.count()).where(Lesson.date >= since, Lesson.date < today,
                                                    Lesson.status != "cancelled").group_by(Lesson.batch_id))).all())
    present = {(r[0], r[1]): r[2] for r in (await db.execute(
        select(Attendance.user_id, Attendance.batch_id, func.count())
        .where(Attendance.date >= since, Attendance.date < today, Attendance.status == "present")
        .group_by(Attendance.user_id, Attendance.batch_id))).all()}
    due = dict((await db.execute(
        select(Task.batch_id, func.count()).where(Task.due_date >= since, Task.due_date < today)
        .group_by(Task.batch_id))).all())
    done: dict[tuple[int, int], int] = defaultdict(int)
    for r in (await db.execute(
            select(TaskSubmission.user_id, Task.batch_id, func.count()).join(Task, Task.id == TaskSubmission.task_id)
            .where(Task.due_date >= since, Task.due_date < today)
            .group_by(TaskSubmission.user_id, Task.batch_id))).all():
        done[(r[0], r[1])] += r[2]
    for r in (await db.execute(
            select(TestAttempt.user_id, Task.batch_id, func.count()).join(Task, Task.test_id == TestAttempt.test_id)
            .where(Task.due_date >= since, Task.due_date < today, TestAttempt.status != "in_progress")
            .group_by(TestAttempt.user_id, Task.batch_id))).all():
        done[(r[0], r[1])] += r[2]

    metrics: dict[tuple[int, int], dict[str, Any]] = {}
    for p in pairs:
        key = (p.user_id, p.batch_id)
        s = scores.get(key, [])
        held = int(lessons.get(p.batch_id, 0))
        metrics[key] = {
            "student": p.full_name, "special_needs": bool(p.special_needs), "parent_email": p.parent_email,
            "parent_name": p.parent_name, "email": p.email, "batch": p.batch_name, "teacher_id": p.teacher_id,
            "subject": p.subject, "class_level": p.class_level,
            "tests": len(s), "avg_score": round(sum(s[-5:]) / len(s[-5:]), 1) if s else None,
            "recent_avg": round(sum(s[-3:]) / len(s[-3:]), 1) if len(s) >= 4 else None,
            "previous_avg": round(sum(s[-6:-3]) / len(s[-6:-3]), 1) if len(s) >= 4 else None,
            "lessons": held, "present": int(present.get(key, 0)),
            "attendance": round(present.get(key, 0) * 100 / held, 1) if held >= 3 else None,
            "missing_tasks": max(0, int(due.get(p.batch_id, 0)) - done.get(key, 0)),
        }
    return metrics


def evaluate(m: dict[str, Any]) -> list[tuple[str, str, str]]:
    t = THRESHOLDS[m["special_needs"]]
    issues: list[tuple[str, str, str]] = []
    # every observation states the expectation, so neither a parent nor a small AI model can read a low number
    # as good news (the 1B model once called 50% attendance "consistently strong")
    if m["avg_score"] is not None and m["tests"] >= 2:
        low = f"average test score is {m['avg_score']}% (we look for at least {t['score_concern']}%)"
        if m["avg_score"] < t["score_warning"]:
            issues.append(("low_scores", "warning", low))
        elif m["avg_score"] < t["score_concern"]:
            issues.append(("low_scores", "concern", low))
    if m["recent_avg"] is not None and m["previous_avg"] is not None:
        delta = m["recent_avg"] - m["previous_avg"]
        if delta <= -t["decline"]:
            issues.append(("declining", "concern", f"scores dropped from {m['previous_avg']}% to {m['recent_avg']}%"))
        elif delta >= t["decline"]:
            issues.append(("improving", "info", f"scores improved from {m['previous_avg']}% to {m['recent_avg']}%"))
    if m["attendance"] is not None:
        absent = f"attendance is {m['attendance']}% over the last 30 days (we expect at least {t['att_concern']}%)"
        if m["attendance"] < t["att_warning"]:
            issues.append(("attendance", "warning", absent))
        elif m["attendance"] < t["att_concern"]:
            issues.append(("attendance", "concern", absent))
    if m["missing_tasks"] >= t["missing"]:
        issues.append(("missing_tasks", "concern", f"{m['missing_tasks']} tasks were not submitted on time"))
    return issues


def template_messages(m: dict[str, Any], level: str, issues: list[tuple[str, str, str]], *, steps: str = "",
                      note: str = "") -> tuple[str, str, str]:
    """The alert wording. The facts are always stated by these rules; `steps` (next steps for the teacher) and
    `note` (a supportive paragraph for the parent) are optional AI-written parts that replace the stock sentences."""
    facts = "; ".join(i[2] for i in issues)
    first = m["student"].split()[0]
    title = {"warning": f"Warning: {m['student']} needs support in {m['batch']}",
             "concern": f"{m['student']} may need extra help in {m['batch']}",
             "info": f"{m['student']} is improving in {m['batch']}"}[level]
    if level == "info":
        return title, f"Good news — {first}'s {facts}. A word of encouragement will go a long way.", ""
    gentle = m["special_needs"] and level != "warning"
    staff = (f"{m['student']} ({m['batch']}): {facts}. "
             + (steps or ("Please check in gently, offer simplified material and extra time, and celebrate small "
                          "wins." if gentle else
                          "Please review recent work, speak with the student and consider a remedial plan.")))
    parent = (f"Dear {m['parent_name'] or 'Parent'},\n\nWe wanted to share a quick update about {first} in "
              f"{m['batch']}: {facts}."
              + (f"\n\n{note}" if note else " " + (
                  "Every child learns at their own pace, and we are here to support " + first
                  + " with patience and care. A short, relaxed chat with us could help us plan together."
                  if gentle else "We would appreciate your support at home and are happy to discuss a plan with you."))
              + f"\n\nWarm regards,\n{site_cache.data['site_name']}")
    return title, staff, parent


NOTE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["teacher_steps", "parent_note"],
               "properties": {"teacher_steps": {"type": "string"}, "parent_note": {"type": "string"}}}
PLACEHOLDER = re.compile(r"\[[^\]\n]{0,40}\]")  # "Dear [Teacher's Name]" - a template the model never filled in
GREETING = re.compile(r"^\s*(?:dear|hi|hello)\b[^,\n]{0,60},\s*", re.IGNORECASE)
SIGN_OFF = re.compile(r"\n\s*(?:warm|best|kind|sincerely|regards|yours|thank you)\b.*\Z", re.IGNORECASE | re.DOTALL)


def ai_part(text: Any) -> str:
    """One AI-written paragraph, or "" when it fails the quality gate (the stock sentence is used instead).
    Numbers always come from the rules - the 1B model turned "25% attendance" into "consistently above the 75%
    threshold" and wrote parent letters as "Dear [Teacher's Name], ..." - so AI text may not state percentages
    or leave placeholders, and greetings / sign-offs are trimmed (the rules add their own)."""
    text = SIGN_OFF.sub("", GREETING.sub("", str(text or "").strip(), count=1)).strip()
    if len(text) < 25 or "%" in text or "percent" in text.lower() or PLACEHOLDER.search(text):
        return ""
    return text[:1500]


async def ai_messages(m: dict[str, Any], level: str, issues: list[tuple[str, str, str]]) -> tuple[str, str, str]:
    first = m["student"].split()[0]
    shape = '{"teacher_steps": str, "parent_note": str}'
    data = await ai.chat_json([
        {"role": "system", "content": (
            "You help a school word a progress alert. " + tone_for(m["special_needs"], level == "warning")
            + " The school states the facts itself: do not repeat any numbers or percentages, and never invent facts "
              "about the student. Each observation is marked CONCERN or GOOD NEWS - treat a CONCERN as something to "
              f"work on and never call it good or strong. Reply with JSON {shape}. teacher_steps: 2-3 practical next "
              f"steps for the batch teacher to help {first}. parent_note: 2-4 warm, polite sentences to the parent "
              f"about how they can support {first} at home. No greetings, no sign-offs, no blame, no placeholders.")},
        {"role": "user", "content": (
            f"Student: {m['student']}\nBatch: {m['batch']} ({m['class_level']}, {m['subject']})\n"
            f"Alert level: {level}\nObservations:\n"
            + "\n".join(f"- {'GOOD NEWS' if i[1] == 'info' else 'CONCERN'}: {i[2]}" for i in issues))},
    ], schema=NOTE_SCHEMA, temperature=0.4, max_tokens=500, timeout=90)
    if not isinstance(data, dict):
        raise AIUnavailable("Unexpected AI output")
    steps, note = ai_part(data.get("teacher_steps")), ai_part(data.get("parent_note"))
    if not (steps or note):
        raise AIUnavailable("The AI text failed the quality check")
    return template_messages(m, level, issues, steps=steps, note=note)


async def analyze_all(db: AsyncSession) -> int:
    metrics = await collect_metrics(db)
    week = dt.date.today().isocalendar()
    new_alerts: list[tuple[PerformanceAlert, dict[str, Any]]] = []
    ai_budget = MAX_AI_MESSAGES_PER_RUN
    candidates = []
    for (user_id, batch_id), m in metrics.items():
        issues = evaluate(m)
        if not issues:
            continue
        level = max(issues, key=lambda i: LEVELS[i[1]])[1]
        fingerprint = f"{user_id}:{batch_id}:{'-'.join(sorted(i[0] for i in issues))}:{week[0]}W{week[1]}"
        candidates.append((user_id, batch_id, m, issues, level, fingerprint))
    if not candidates:
        return 0
    existing = set((await db.scalars(select(PerformanceAlert.fingerprint).where(
        PerformanceAlert.fingerprint.in_([c[5] for c in candidates])))).all())

    for user_id, batch_id, m, issues, level, fingerprint in candidates:
        if fingerprint in existing:
            continue
        source = "rules"
        title, staff_msg, parent_msg = template_messages(m, level, issues)
        if level != "info" and ai_budget > 0 and ai.online:
            try:
                title, staff_msg, parent_msg = await ai_messages(m, level, issues)
                source = "ai"
                ai_budget -= 1
            except (AIUnavailable, ValueError):
                pass
        alert = PerformanceAlert(user_id=user_id, batch_id=batch_id, level=level, title=title, message=staff_msg,
                                 parent_message=parent_msg or None, fingerprint=fingerprint, source=source,
                                 metrics={k: v for k, v in m.items() if k not in ("email", "parent_email")})
        db.add(alert)
        new_alerts.append((alert, m))
    if not new_alerts:
        return 0
    await db.flush()

    # ---- notifications: teacher in-app + digests (admins, teachers) + individual parent notes
    teacher_ids = {m["teacher_id"] for _, m in new_alerts if m["teacher_id"]}
    teachers = {u.id: u for u in (await db.scalars(select(User).where(User.id.in_(teacher_ids)))).all()} \
        if teacher_ids else {}
    by_teacher: dict[int, list[dict[str, Any]]] = defaultdict(list)
    digest = []
    for alert, m in new_alerts:
        row = {"title": alert.title, "message": alert.message, "level": alert.level, "batch": m["batch"],
               "student": m["student"], "link": f"{settings.BASE_URL}/admin/insights"}
        digest.append(row)
        notified = ["admins"]
        if m["teacher_id"] in teachers:
            by_teacher[m["teacher_id"]].append(row)
            notified.append("teacher")
        if alert.level != "info" and settings.NOTIFY_PARENTS and m["parent_email"] and alert.parent_message:
            queue_email(m["parent_email"], f"Update about {m['student'].split()[0]} — {m['batch']}",
                        "parent_update.html", category="performance", message=alert.parent_message,
                        student=m["student"])
            notified.append("parent")
        alert.notified = notified
    actionable = [d for d in digest if d["level"] != "info"]
    if actionable:
        queue_email(settings.admin_alert_list, f"AI insights: {len(actionable)} student(s) need attention",
                    "insights_digest.html", category="performance", alerts=actionable, audience="admin")
    teacher_mails = [(teachers[tid].email, {"alerts": rows, "audience": "teacher", "name": teachers[tid].full_name})
                     for tid, rows in by_teacher.items()]
    queue_bulk(teacher_mails, "AI insights for your batches", "insights_digest.html", category="performance")
    for tid, rows in by_teacher.items():
        await notify_users(db, [tid], f"{len(rows)} new AI insight(s) for your batches", rows[0]["title"],
                           "/staff/insights", "fa-wand-magic-sparkles")
    await db.commit()
    log.info("Performance analysis created %d alerts", len(new_alerts))
    return len(new_alerts)
