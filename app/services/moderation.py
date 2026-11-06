"""Chat moderation: abusive-language filter + strike system + automatic IP bans.

The word lists are NOT stored in readable form: app/data/wordlists.dat holds them
zlib-compressed, XOR-masked and base64-encoded. They are decoded into memory when the app
starts (``load_wordlists()``). Edit them with:
    python -m app.cli wordlist-export words.json   # decode to an editable file
    python -m app.cli wordlist-import words.json   # re-encode (then delete words.json)

* Severe abuse (profanity, slurs - English & Hinglish) is masked and earns a strike.
* Mild insults are masked but do not earn strikes.
* ABUSE_MAX_STRIKES strikes inside ABUSE_WINDOW_HOURS -> automatic IP ban for ABUSE_BAN_HOURS,
  live sockets are disconnected, and both the admins and the person are emailed the reason.
* Obfuscation-aware: leetspeak (sh1t), repeated letters (fuuu…), spaced-out letters.
* Admin accounts are never auto-banned.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import logging
import re
import zlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..cache import ban_cache
from ..config import APP_DIR, settings
from ..models import IPBan, ModerationStrike, utcnow
from .mailer import queue_admin_alert, queue_email

log = logging.getLogger("classnest.moderation")

WORDLIST_FILE = APP_DIR / "data" / "wordlists.dat"
_KEY = hashlib.sha256(b"classnest/moderation/wordlists/v1").digest()

SEVERE_WORDS: set[str] = set()
SEVERE_STEMS: tuple[str, ...] = ()
MILD_WORDS: set[str] = set()
_loaded = False


# ------------------------------------------------------------------ encoded word lists
def _xor(data: bytes) -> bytes:
    return bytes(b ^ _KEY[i % len(_KEY)] for i, b in enumerate(data))


def encode_wordlists(lists: dict[str, list[str]]) -> str:
    raw = zlib.compress(json.dumps(lists, separators=(",", ":"), ensure_ascii=True).encode(), 9)
    blob = base64.b64encode(_xor(raw)).decode()
    return "\n".join(blob[i:i + 76] for i in range(0, len(blob), 76))


def decode_wordlists(blob: str) -> dict[str, list[str]]:
    payload = "".join(line.strip() for line in blob.splitlines() if line.strip() and not line.startswith("#"))
    return json.loads(zlib.decompress(_xor(base64.b64decode(payload))))


def load_wordlists() -> int:
    """Decode the word lists into memory (called at startup). Returns the number of entries."""
    global SEVERE_STEMS, _loaded
    try:
        data = decode_wordlists(WORDLIST_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, zlib.error) as exc:
        log.error("Could not load moderation word lists (%s) - only EXTRA_BLOCKED_WORDS will be used", exc)
        data = {}
    SEVERE_WORDS.clear()
    SEVERE_WORDS.update(w.strip().lower() for w in data.get("severe", []) if w.strip())
    SEVERE_WORDS.update(w.strip().lower() for w in settings.EXTRA_BLOCKED_WORDS.split(",") if w.strip())
    SEVERE_STEMS = tuple(s.strip().lower() for s in data.get("stems", []) if s.strip())
    MILD_WORDS.clear()
    MILD_WORDS.update(w.strip().lower() for w in data.get("mild", []) if w.strip())
    _loaded = True
    return len(SEVERE_WORDS) + len(SEVERE_STEMS) + len(MILD_WORDS)


def export_wordlists() -> dict[str, list[str]]:
    return decode_wordlists(WORDLIST_FILE.read_text(encoding="utf-8"))


def import_wordlists(lists: dict[str, list[str]]) -> None:
    clean = {k: sorted({w.strip().lower() for w in lists.get(k, []) if w.strip()}) for k in ("severe", "stems", "mild")}
    header = ("# ClassNest moderation word lists - encoded (zlib + xor + base64), decoded at app start.\n"
              "# Edit via: python -m app.cli wordlist-export / wordlist-import\n")
    WORDLIST_FILE.write_text(header + encode_wordlists(clean) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ analysis
LEET = str.maketrans({"@": "a", "4": "a", "0": "o", "1": "i", "!": "i", "3": "e", "$": "s", "5": "s", "7": "t",
                      "+": "t", "8": "b", "9": "g", "|": "i", "€": "e"})
TOKEN_RE = re.compile(r"\S+")
CORE_RE = re.compile(r"[A-Za-z0-9@$!|+€]")


def _variants(token: str) -> set[str]:
    t = re.sub(r"[^a-z]", "", token.lower().translate(LEET))
    if not t:
        return set()
    return {t, re.sub(r"(.)\1{2,}", r"\1\1", t), re.sub(r"(.)\1+", r"\1", t)}


def _severity(token: str) -> int:
    variants = _variants(token)
    for v in variants:
        if v in SEVERE_WORDS or (len(v) >= 4 and any(stem in v for stem in SEVERE_STEMS)):
            return 2
    return 1 if any(v in MILD_WORDS for v in variants) else 0


@dataclass
class Analysis:
    text: str
    severe: bool
    mild: bool
    hits: int


def analyze(text: str) -> Analysis:
    if not _loaded:
        load_wordlists()
    text = text.replace("\x00", "")  # PostgreSQL can't store NUL; chat/AI/submission text all passes here
    tokens = list(TOKEN_RE.finditer(text))
    severity = [_severity(m.group()) for m in tokens]
    i = 0
    while i < len(tokens):  # spaced-out letters, e.g. "x y z w"
        if len(CORE_RE.findall(tokens[i].group())) == 1:
            j = i
            while j < len(tokens) and len(CORE_RE.findall(tokens[j].group())) == 1:
                j += 1
            if j - i >= 3:
                level = _severity("".join(t.group() for t in tokens[i:j]))
                for k in range(i, j):
                    severity[k] = max(severity[k], level)
            i = j
        else:
            i += 1
    out, last = [], 0
    for match, level in zip(tokens, severity, strict=False):
        if level:
            word = match.group()
            out.append(text[last:match.start()])
            out.append(word[0] + CORE_RE.sub("*", word[1:]) if len(CORE_RE.findall(word)) > 1 else "*")
            last = match.end()
    out.append(text[last:])
    return Analysis("".join(out), any(s == 2 for s in severity), any(s == 1 for s in severity),
                    sum(1 for s in severity if s))


def mask(text: str | None) -> str:
    return analyze(text).text if text else ""


# ------------------------------------------------------------------ strikes & bans
BanHook = Callable[[str, str | None], Awaitable[None]]
_ban_hooks: list[BanHook] = []


def on_ban(fn: BanHook) -> BanHook:
    _ban_hooks.append(fn)
    return fn


@dataclass
class Outcome:
    text: str
    flagged: bool
    strikes: int = 0
    banned: bool = False
    notice: str | None = None


async def moderate(db: AsyncSession, *, text: str, principal, ip: str, context: str) -> Outcome:
    """Filter a chat message. May COMMIT the session (strike / ban records)."""
    result = analyze(text)
    if not result.severe:
        notice = "Please keep the conversation respectful 🙂" if result.mild else None
        return Outcome(result.text, result.mild, notice=notice)
    if principal is not None and principal.kind == "admin":
        return Outcome(result.text, True)

    user_id = principal.id if principal is not None else None
    db.add(ModerationStrike(user_id=user_id, ip=ip, context=context, excerpt=result.text[:500]))
    await db.flush()
    since = utcnow() - dt.timedelta(hours=settings.ABUSE_WINDOW_HOURS)
    who = ModerationStrike.user_id == user_id if user_id else ModerationStrike.ip == ip
    strikes = int(await db.scalar(select(func.count()).select_from(ModerationStrike)
                                  .where(who, ModerationStrike.created_at >= since)) or 0)
    if strikes < settings.ABUSE_MAX_STRIKES:
        await db.commit()
        left = settings.ABUSE_MAX_STRIKES - strikes
        return Outcome(result.text, True, strikes, False,
                       f"⚠️ Abusive language was filtered from your message. Strike {strikes}/"
                       f"{settings.ABUSE_MAX_STRIKES} — {left} more within {settings.ABUSE_WINDOW_HOURS} h "
                       "will automatically block your access and the administrator will be informed.")

    excerpts = list((await db.scalars(select(ModerationStrike.excerpt).where(who, ModerationStrike.created_at >= since)
                                      .order_by(ModerationStrike.id.desc()).limit(5))).all())
    expires = utcnow() + dt.timedelta(hours=settings.ABUSE_BAN_HOURS) if settings.ABUSE_BAN_HOURS > 0 else None
    where = context.replace("_", " ")
    reason = (f"Automatic ban: repeated abusive language ({strikes} strikes within "
              f"{settings.ABUSE_WINDOW_HOURS} hours) in {where}.")
    ban = await db.scalar(select(IPBan).where(IPBan.ip == ip))
    if ban is None:
        db.add(IPBan(ip=ip, reason=reason, banned_by="system", is_auto=True, user_id=user_id, expires_at=expires))
    else:
        ban.reason, ban.banned_by, ban.is_auto, ban.user_id, ban.expires_at = reason, "system", True, user_id, expires
    await db.commit()
    ban_cache.add(ip, reason, expires)
    log.warning("Auto IP ban %s (%s): %s", ip, principal.name if principal else "anonymous", reason)

    name = principal.name if principal is not None else "Unknown visitor"
    role = principal.kind if principal is not None else "guest"
    ctx = dict(name=name, role=role, ip=ip, reason=reason, strikes=strikes, excerpts=excerpts, expires=expires,
               where=where, category="moderation")
    queue_admin_alert(f"Automatic IP ban: {name} ({role}, {ip})", "moderation_ban.html", audience="admin", **ctx)
    if principal is not None and principal.email:
        queue_email(principal.email, "Your access has been blocked", "moderation_ban.html", audience="person", **ctx)
    key = principal.key if principal is not None else None
    for hook in _ban_hooks:
        try:
            await hook(ip, key)
        except Exception:
            log.exception("Ban hook failed")
    return Outcome(result.text, True, strikes, True,
                   "🚫 Your access has been blocked for repeated abusive language. The administrator has been "
                   "informed and the reason has been emailed to you.")
