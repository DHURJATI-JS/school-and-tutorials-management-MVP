"""AI test generation (grounded in the batch's class/subject knowledge base) and grading.

Fail-safes: strict validation of AI output, de-duplication, profanity masking, a deterministic
fallback generator (cloze / true-false / short answers from class notes, or editable templates),
and keyword-based short-answer scoring when the AI cannot grade.
"""
from __future__ import annotations

import json
import logging
import random
import re
from collections import Counter
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from ..constants import QUESTION_KINDS
from ..database import SessionLocal
from ..models import Batch, Question, Test, TestAttempt, User
from . import rag
from .ai import GUARD, AIUnavailable, ai, tone_for
from .moderation import mask
from .notify import notify_users

log = logging.getLogger("classnest.tests")
KIND_ALIASES = {"mcq": "mcq", "multiple_choice": "mcq", "multiplechoice": "mcq", "choice": "mcq",
                "true_false": "true_false", "truefalse": "true_false", "tf": "true_false", "boolean": "true_false",
                "short": "short", "short_answer": "short", "shortanswer": "short", "subjective": "short"}
MAX_QUESTIONS = 30


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def resolve_answer(answer: Any, options: list[str]) -> int | None:
    if isinstance(answer, bool):
        return None
    if isinstance(answer, (int, float)):
        i = int(answer)
        return i if 0 <= i < len(options) else (i - 1 if i == len(options) else None)
    s = _clean(answer)
    if re.fullmatch(r"[0-9]{1,3}", s):  # not isdigit(): "²".isdigit() is True but int("²") raises
        return resolve_answer(int(s), options)
    m = re.match(r"^\(?([A-Fa-f])\)?(?:[\).:\s]|$)", s)
    if m and ord(m.group(1).upper()) - 65 < len(options):
        return ord(m.group(1).upper()) - 65
    for i, option in enumerate(options):
        if s.lower() == option.lower():
            return i
    return None


def validate_questions(data: Any, kinds: list[str], count: int) -> list[dict[str, Any]]:
    items = data.get("questions") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("AI response did not contain a question list")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in items:
        if not isinstance(raw, dict):
            continue
        key = re.sub(r"[\s\-/]+", "_", _clean(raw.get("kind") or raw.get("type")).lower())
        kind = KIND_ALIASES.get(key) or ("mcq" if isinstance(raw.get("options"), list) else None)
        if kind not in kinds:
            continue
        text = _clean(raw.get("text") or raw.get("question"))
        norm = re.sub(r"\W+", "", text.lower())
        if not text or len(text) > 1200 or norm in seen:
            continue
        try:
            marks = float(raw.get("marks") or (2 if kind == "short" else 1))
        except (TypeError, ValueError):
            marks = 2 if kind == "short" else 1
        q: dict[str, Any] = {"kind": kind, "text": mask(text), "options": [], "answer": "",
                             "explanation": mask(_clean(raw.get("explanation"))[:1500]),
                             "marks": max(0.5, min(10.0, marks))}
        if kind == "mcq":
            options: list[str] = []
            for opt in raw.get("options") or []:
                opt = re.sub(r"^\(?[A-Fa-f][\).:\-]\s+", "", _clean(opt))
                if opt and opt.lower() not in {o.lower() for o in options}:
                    options.append(mask(opt[:300]))
            idx = resolve_answer(raw.get("answer", raw.get("correct")), options)
            if not 3 <= len(options) <= 6 or idx is None:
                continue
            q["options"], q["answer"] = options, str(idx)
        elif kind == "true_false":
            ans = _clean(raw.get("answer")).lower()
            if ans in {"true", "t", "yes", "1"}:
                q["answer"] = "true"
            elif ans in {"false", "f", "no", "0"}:
                q["answer"] = "false"
            else:
                continue
            q["options"] = ["True", "False"]
        else:
            q["answer"] = mask(_clean(raw.get("answer") or raw.get("model_answer"))[:2000])
            if not q["answer"]:
                continue
        out.append(q)
        seen.add(norm)
        if len(out) >= count:
            break
    return out


def fallback_questions(test: Test, hits: list[rag.Hit], count: int, kinds: list[str]) -> list[dict[str, Any]]:
    sentences: list[str] = []
    for hit in hits:
        for s in re.split(r"(?<=[.!?])\s+", hit.content.replace("\n", " ")):
            s = s.strip(" -•*#")
            if 40 <= len(s) <= 240 and not s.endswith("?") and s not in sentences:
                sentences.append(s)
    vocab = Counter(w for s in sentences for w in re.findall(r"\b[A-Za-z][A-Za-z\-]{5,}\b", s)
                    if w.lower() not in rag.STOPWORDS)
    keywords = [w for w, _ in vocab.most_common(60)]
    rng = random.Random(test.id or 1)
    out: list[dict[str, Any]] = []
    for s in sentences:
        if len(out) >= count:
            break
        kind = kinds[len(out) % len(kinds)]
        kw = next((w for w in keywords if re.search(rf"\b{re.escape(w)}\b", s)), None)
        if kind == "mcq" and kw:
            distractors = [w for w in keywords if w.lower() != kw.lower()]
            if len(distractors) < 3:
                continue
            options = rng.sample(distractors[:15], 3) + [kw]
            rng.shuffle(options)
            blanked = re.sub(rf"\b{re.escape(kw)}\b", "_____", s, count=1)
            out.append({"kind": "mcq", "text": f"Fill in the blank: {blanked}", "options": options,
                        "answer": str(options.index(kw)), "explanation": f"From your notes: “{s}”", "marks": 1})
        elif kind == "true_false":
            out.append({"kind": "true_false", "text": f"True or false: {s}", "options": ["True", "False"],
                        "answer": "true", "explanation": "Taken from class notes — edit into a false statement "
                                                         "for variety.", "marks": 1})
        elif kind == "short":
            focus = kw or test.topic
            out.append({"kind": "short", "text": f"Explain “{focus}” in your own words, with an example.",
                        "options": [], "answer": s, "explanation": "Model answer drawn from class notes.",
                        "marks": 2})
    while len(out) < count:
        n = len(out) + 1
        kind = kinds[len(out) % len(kinds)]
        base = f"[Edit me] Question {n} on {test.topic} ({test.subject}, {test.class_level})"
        if kind == "mcq":
            out.append({"kind": "mcq", "text": base, "options": ["Option A", "Option B", "Option C", "Option D"],
                        "answer": "0", "explanation": "", "marks": 1})
        elif kind == "true_false":
            out.append({"kind": "true_false", "text": base, "options": ["True", "False"], "answer": "true",
                        "explanation": "", "marks": 1})
        else:
            out.append({"kind": "short", "text": base, "options": [], "answer": "Write the model answer here.",
                        "explanation": "", "marks": 2})
    return out[:count]


# One concrete example per type. Small models copy placeholder templates such as "kind": "mcq|true_false|short"
# literally (seen with Gemma 3 1B: 0 of 10 questions usable), but imitate real examples well.
FORMAT_EXAMPLES = {
    "mcq": {"kind": "mcq", "text": "Which of these numbers is prime?", "options": ["9", "15", "7", "21"],
            "answer": "2", "explanation": "7 has exactly two factors: 1 and 7.", "marks": 1},
    "true_false": {"kind": "true_false", "text": "Water boils at 100 °C at sea level.", "options": ["True", "False"],
                   "answer": "true", "explanation": "At normal air pressure water boils at 100 °C.", "marks": 1},
    "short": {"kind": "short", "text": "Why do we see lightning before we hear thunder?", "options": [],
              "answer": "Light travels much faster than sound, so the flash reaches us first.",
              "explanation": "Light: about 300,000 km/s; sound: about 343 m/s.", "marks": 2},
}
ANSWER_RULES = {
    "mcq": '- mcq: exactly 4 different options; "answer" is the position of the correct option counted from 0, '
           'written as a string ("0" = first option, "3" = last).',
    "true_false": '- true_false: "options" is ["True", "False"] and "answer" is "true" or "false".',
    "short": '- short: "options" is [] and "answer" is a model answer of 1-3 sentences.',
}


def question_schema(kinds: list[str], count: int) -> dict[str, Any]:
    """Structured-output schema: the model can only produce this exact shape (and, on vLLM, exactly `count`)."""
    item = {"type": "object", "additionalProperties": False,
            "required": ["kind", "text", "options", "answer", "explanation", "marks"],
            "properties": {"kind": {"type": "string", "enum": list(kinds)}, "text": {"type": "string"},
                           "options": {"type": "array", "items": {"type": "string"}}, "answer": {"type": "string"},
                           "explanation": {"type": "string"}, "marks": {"type": "number"}}}
    return {"type": "object", "additionalProperties": False, "required": ["questions"],
            "properties": {"questions": {"type": "array", "minItems": count, "maxItems": count, "items": item}}}


GRADE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["grades"], "properties": {
    "grades": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                          "required": ["id", "marks", "feedback"],
                                          "properties": {"id": {"type": "integer"}, "marks": {"type": "number"},
                                                         "feedback": {"type": "string"}}}}}}


async def build_questions(test: Test, hits: list[rag.Hit], count: int, kinds: list[str],
                          extra: str) -> tuple[list[dict[str, Any]], str, str]:
    context = rag.format_context(hits, 6000) if hits else ""
    examples = json.dumps({"questions": [FORMAT_EXAMPLES[k] for k in kinds if k in FORMAT_EXAMPLES]},
                          ensure_ascii=False)
    user_prompt = (
        f"Create exactly {count} {test.difficulty} questions for a classroom test.\n"
        f"Class: {test.class_level}\nSubject: {test.subject}\nTopic: {test.topic}\n"
        f"Allowed question types (\"kind\"): {', '.join(kinds)}\n"
        + (f"Teacher instructions: {extra}\n" if extra else "")
        + "Rules:\n"
        + "\n".join(ANSWER_RULES[k] for k in kinds if k in ANSWER_RULES) + "\n"
        f"- Questions must suit {test.class_level} (Indian curriculum level), be about \"{test.topic}\", be "
        "unambiguous and have exactly one correct answer.\n"
        "- Mix easy recall with reasoning; never repeat a fact.\n"
        "- marks: 1 for mcq / true_false, 2-5 for short answers.\n"
        + (f"Use the class notes below where relevant:\n{context}\n" if context else "")
        + f"Format example - ONE question of each allowed type, about a different subject; it only shows the "
          f"format. Write {count} NEW questions about {test.topic}:\n{examples}\n"
        + f'Return ONLY the JSON object {{"questions": [...]}} with {count} questions.')
    messages = [
        {"role": "system", "content": f"You are an experienced {test.class_level} {test.subject} teacher who writes "
                                      f"accurate, curriculum-aligned assessment questions. {GUARD} Output only JSON."},
        {"role": "user", "content": user_prompt},
    ]
    try:
        data = await ai.chat_json(messages, schema=question_schema(kinds, count), temperature=0.5,
                                  max_tokens=min(6000, 300 + count * 180), timeout=300)
        questions = validate_questions(data, kinds, count)
        if len(questions) < max(1, count // 2):
            raise ValueError(f"only {len(questions)} usable questions were returned")
        note = ("Generated by AI" + (" using your knowledge base" if hits else "") + ". Review and edit before "
                "publishing.")
        missing = count - len(questions)
        if missing > 0:  # never hand back fewer questions than the teacher asked for
            seen = {q["text"].lower() for q in questions}
            questions += [q for q in fallback_questions(test, hits, missing + len(seen), kinds)
                          if q["text"].lower() not in seen][:missing]
            note = (f"AI wrote {count - missing} of the {count} questions; {missing} editable starter "
                    f"question{'s were' if missing > 1 else ' was'} added. Review and edit before publishing.")
        return questions, "ai", note
    except (AIUnavailable, ValueError) as exc:
        questions = fallback_questions(test, hits, count, kinds)
        note = (f"AI could not generate this test ({exc}). {len(questions)} starter questions were drafted "
                + ("from your knowledge base" if hits else "as editable templates") + " — please review them.")
        return questions, "fallback", note


async def generate_for_test(test_id: int, *, count: int, kinds: list[str], use_kb: bool, extra: str) -> None:
    """Background job: fill a Test in status 'generating' with questions."""
    count = max(1, min(MAX_QUESTIONS, count))
    kinds = [k for k in kinds if k in QUESTION_KINDS] or ["mcq"]
    async with SessionLocal() as db:
        test = await db.get(Test, test_id)
        if test is None:
            return
        batch = await db.get(Batch, test.batch_id)
        try:
            hits = []
            if use_kb and batch is not None:
                hits = await rag.search(db, f"{test.subject} {test.topic} {extra}", batch_ids=[batch.id],
                                        class_level=batch.class_level, subject=batch.subject, k=8)
            questions, source, note = await build_questions(test, hits, count, kinds, extra)
        except Exception as exc:  # absolute last resort: never leave the test stuck in "generating"
            log.exception("Test generation crashed")
            questions, source = fallback_questions(test, [], count, kinds), "fallback"
            note = f"Generation failed unexpectedly ({exc.__class__.__name__}); templates were created."
        await db.execute(delete(Question).where(Question.test_id == test.id))
        total = Decimal("0")
        for pos, q in enumerate(questions):
            marks = Decimal(str(q["marks"]))
            total += marks
            db.add(Question(test_id=test.id, position=pos, kind=q["kind"], text=q["text"], options=q["options"],
                            answer=q["answer"], explanation=q["explanation"] or None, marks=marks))
        test.total_marks = total
        test.status = "draft"
        test.ai_generated = source == "ai"
        test.generation_note = note
        await db.commit()


# ------------------------------------------------------------------ grading
def keyword_ratio(model_answer: str, given: str) -> float:
    def tokens(s: str) -> set[str]:
        return {rag._stem(t) for t in rag.TOKEN_RE.findall(s.lower()) if t not in rag.STOPWORDS and len(t) > 2}

    expected = tokens(model_answer)
    if not expected or not given.strip():
        return 0.0
    return len(expected & tokens(given)) / len(expected)


async def ai_grade_short(test: Test, items: list[tuple[Question, str]], special_needs: bool) -> dict[str, tuple]:
    payload = [{"id": q.id, "question": q.text, "model_answer": q.answer, "student_answer": given[:1500],
                "max_marks": float(q.marks)} for q, given in items]
    data = await ai.chat_json([
        {"role": "system", "content": (
            f"You grade short answers for {test.class_level} {test.subject}. Award partial credit for correct "
            "understanding even if wording differs. " + ("Be especially lenient and generous with partial credit. "
                                                         if special_needs else "")
            + tone_for(special_needs) + " Feedback: one short, kind sentence per answer. " + GUARD
            + ' Reply with JSON: {"grades":[{"id":int,"marks":number,"feedback":str}]}')},
        {"role": "user", "content": f"<context>\n{json.dumps(payload, ensure_ascii=False)}\n</context>"},
    ], schema=GRADE_SCHEMA, temperature=0.1, max_tokens=1500, timeout=180)
    grades = data.get("grades") if isinstance(data, dict) else data
    result: dict[str, tuple] = {}
    limits = {q.id: float(q.marks) for q, _ in items}
    for g in grades or []:
        try:
            qid = int(g["id"])
            marks = max(0.0, min(limits[qid], float(g.get("marks", 0))))
        except (KeyError, TypeError, ValueError):
            continue
        result[str(qid)] = (round(marks * 2) / 2, mask(_clean(g.get("feedback")))[:400])
    if not result:
        raise ValueError("No grades returned")
    return result


def feedback_template(pct: float, special_needs: bool, first: str) -> str:
    if pct >= 85:
        return f"Outstanding work, {first}! You clearly understand this topic. Keep it up! 🌟"
    if pct >= 60:
        return f"Good effort, {first}! Review the questions you missed and you'll master this soon."
    if special_needs:
        return (f"Thank you for trying your best, {first}. 💛 Every step counts — let's go through the tricky "
                "parts together, one at a time.")
    return f"Keep going, {first}. Revisit your class notes and ask the AI tutor about the questions you missed."


async def grade_attempt(attempt_id: int) -> None:
    async with SessionLocal() as db:
        attempt = await db.get(TestAttempt, attempt_id)
        if attempt is None or attempt.status == "graded":
            return
        test = (await db.scalars(select(Test).options(selectinload(Test.questions))
                                 .where(Test.id == attempt.test_id))).one()
        student = await db.get(User, attempt.user_id)
        special = bool(student and student.special_needs)
        answers = attempt.answers or {}
        per_q: dict[str, Any] = {}
        score = max_score = 0.0
        shorts: list[tuple[Question, str]] = []
        for q in test.questions:
            marks = float(q.marks)
            max_score += marks
            given = _clean(answers.get(str(q.id), ""))
            if q.kind in ("mcq", "true_false"):
                correct = bool(given) and given.lower() == q.answer.lower()
                per_q[str(q.id)] = {"given": given, "correct": correct, "awarded": marks if correct else 0.0}
                score += marks if correct else 0.0
            else:
                shorts.append((q, given))
        note = None
        if shorts:
            graded: dict[str, tuple] | None = None
            if ai.online and any(g for _, g in shorts):
                try:
                    graded = await ai_grade_short(test, shorts, special)
                except (AIUnavailable, ValueError) as exc:
                    log.info("AI grading unavailable: %s", exc)
            for q, given in shorts:
                marks = float(q.marks)
                if graded and str(q.id) in graded:
                    awarded, fb = graded[str(q.id)]
                else:
                    ratio = min(1.0, keyword_ratio(q.answer, given) + (0.15 if special and given else 0))
                    awarded = round(ratio * marks * 2) / 2
                    fb = "Auto-scored by keyword match — your teacher may review this answer."
                    note = ("Short answers were scored by keyword matching because the AI was unavailable. "
                            "Teacher review recommended.")
                per_q[str(q.id)] = {"given": given, "awarded": awarded, "feedback": fb, "correct": awarded >= marks}
                score += awarded
        attempt.per_question = per_q
        attempt.score = Decimal(str(round(score, 2)))
        attempt.max_score = Decimal(str(round(max_score, 2)))
        attempt.grading_note = note
        pct = score * 100 / max_score if max_score else 0
        first = student.full_name.split()[0] if student else "there"
        feedback = feedback_template(pct, special, first)
        if ai.online:
            try:
                feedback = mask(await ai.chat([
                    {"role": "system", "content": "You are a supportive tutor. " + tone_for(special)
                                                  + " Write 2-3 sentences of feedback addressed to the student."},
                    {"role": "user", "content": f"Student: {first}\nTest: {test.title} ({test.subject})\n"
                                                f"Score: {score:.1f}/{max_score:.1f} ({pct:.0f}%)"},
                ], temperature=0.5, max_tokens=200, timeout=60))
            except AIUnavailable:
                pass
        attempt.feedback = feedback
        attempt.status = "graded"
        await notify_users(db, [attempt.user_id], f"Your result for “{test.title}” is ready",
                           f"Score: {score:.1f}/{max_score:.1f}", f"/student/attempts/{attempt.id}", "fa-square-poll-vertical")
        await db.commit()
