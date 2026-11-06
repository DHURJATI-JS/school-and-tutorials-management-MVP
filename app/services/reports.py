"""Complaints & safety reports: detection (AI with keyword fallback), person matching, admin alerts."""
from __future__ import annotations

import datetime as dt
import logging
import re

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import Report, User, utcnow
from .ai import GUARD, AIUnavailable, ai
from .mailer import queue_admin_alert

log = logging.getLogger("classnest.reports")

CATEGORIES = ["complaint", "harassment", "bullying", "safety", "misconduct", "academic", "other"]
COMPLAINT_RE = re.compile(
    r"\b(complain\w*|report(ing)?\b|harass\w*|bull(y|ies|ied|ying)|abus\w*|(hit|hits|beat|beats|slapped|pushed|"
    r"touched|touches|threatened|threatens)\s+(me|us|him|her|them)|threat\w*|unsafe|scared of|afraid of|insult\w*|"
    r"humiliat\w*|discriminat\w*|unfair(ly)?|rude(ly)?|misbehav\w*|(shout|shouted|yelled|yells)\s+at|scold\w*|"
    r"punish\w*|inappropriate(ly)?|creepy|stalk\w*|blackmail\w*)\b", re.I)
URGENT_RE = re.compile(r"\b(touched|hit|beat|slap|threat|unsafe|suicid\w*|hurt myself|kill|stalk|blackmail)\w*", re.I)


def looks_like_report(text: str) -> bool:
    return bool(COMPLAINT_RE.search(text or ""))


async def match_person(db: AsyncSession, text: str, exclude_id: int | None, prefer_staff: bool) -> User | None:
    words = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", text or "")}
    if not words:
        return None
    candidates = (await db.scalars(select(User).where(User.status == "active", or_(
        *[User.full_name.ilike(f"%{w}%") for w in list(words)[:25]])).limit(60))).all()
    best, best_score = None, 0.0
    for user in candidates:
        if user.id == exclude_id:
            continue
        parts = {p.lower() for p in user.full_name.split() if len(p) > 2}
        score = len(parts & words) + (0.5 if prefer_staff and user.is_staff else 0)
        if parts & words and score > best_score:
            best, best_score = user, score
    return best


async def classify(text: str, reporter_role: str) -> dict:
    """AI decides whether a message is a complaint/report; keyword heuristic if the AI is offline."""
    fallback = {"is_report": looks_like_report(text), "category": "complaint", "about": "",
                "severity": "high" if URGENT_RE.search(text) else "medium", "summary": text.strip()[:300]}
    if not ai.online:
        return fallback
    try:
        data = await ai.chat_json([
            {"role": "system", "content": (
                "You triage messages sent by a " + reporter_role + " to a school's private assistant. Decide if the "
                "user is reporting a concern about a specific person or a safety issue (complaint, harassment, "
                "bullying, safety, misconduct, academic unfairness). Reply with JSON: {\"is_report\": bool, "
                "\"category\": \"complaint|harassment|bullying|safety|misconduct|academic|other\", \"about\": "
                "\"name of the person the concern is about, or empty\", \"severity\": \"low|medium|high\", "
                "\"summary\": \"one neutral sentence, no names of the reporter\"}. " + GUARD)},
            {"role": "user", "content": f"<context>\n{text[:3000]}\n</context>"},
        ], temperature=0.0, max_tokens=300, timeout=60)
    except (AIUnavailable, ValueError):
        return fallback
    if not isinstance(data, dict):
        return fallback
    category = str(data.get("category") or "complaint").lower()
    severity = str(data.get("severity") or "medium").lower()
    return {"is_report": bool(data.get("is_report")), "category": category if category in CATEGORIES else "other",
            "about": str(data.get("about") or "")[:120], "summary": str(data.get("summary") or text)[:500],
            "severity": severity if severity in ("low", "medium", "high") else "medium"}


async def create_report(db: AsyncSession, *, reporter: User | None, subject: User | None, subject_name: str | None,
                        category: str, severity: str, source: str, summary: str,
                        evidence: list[dict]) -> Report | None:
    since = utcnow() - dt.timedelta(minutes=15)
    duplicate = await db.scalar(select(Report.id).where(
        Report.reporter_id == (reporter.id if reporter else None), Report.subject_id == (subject.id if subject else None),
        Report.source == source, Report.created_at >= since, Report.status == "open").limit(1))
    if duplicate:
        existing = await db.get(Report, duplicate)
        existing.evidence = (existing.evidence or []) + evidence[-3:]
        await db.commit()
        return existing
    report = Report(reporter_id=reporter.id if reporter else None, subject_id=subject.id if subject else None,
                    subject_name=(subject.full_name if subject else subject_name) or None, category=category,
                    severity=severity, source=source, summary=summary[:2000], evidence=evidence[-6:])
    db.add(report)
    await db.commit()
    who = f" about {report.subject_name}" if report.subject_name else ""
    queue_admin_alert(f"[{severity.upper()}] New {category} report{who} — #{report.id}", "report_alert.html",
                      category="report", report_id=report.id, summary=report.summary, severity=severity,
                      kind=category, about=report.subject_name or "Not identified",
                      reporter=(reporter.full_name if reporter else "Anonymous"), source=source,
                      link=f"{settings.BASE_URL}/admin/reports")
    log.info("Report #%s created (%s, %s)", report.id, category, severity)
    return report
