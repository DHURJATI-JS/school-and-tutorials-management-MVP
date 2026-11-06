"""Security primitives.

* bcrypt password hashing (run in a worker thread so the event loop never blocks)
* admin registry from .env (plain text or bcrypt hashes)
* CSRF protection (session token, checked on every unsafe request)
* double-submit guard (one-time ``_once`` form tokens)
* in-memory rate limiting
* authentication dependencies (admin / student / staff / guest)
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import string
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import bcrypt
from anyio import to_thread
from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection

from .config import settings
from .constants import TEACHING_ROLES
from .database import get_db

log = logging.getLogger("classnest.security")

BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2y$")


# ------------------------------------------------------------------ passwords (bcrypt)
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


@lru_cache(maxsize=1)
def dummy_hash() -> str:
    """A real bcrypt hash used to equalise timing when a username does not exist."""
    return hash_password(secrets.token_hex(8))


async def ahash_password(password: str) -> str:
    return await to_thread.run_sync(hash_password, password)


async def averify_password(password: str, hashed: str) -> bool:
    return await to_thread.run_sync(verify_password, password, hashed)


def generate_password(length: int = 12) -> str:
    """Readable but strong: no ambiguous characters (0/O, 1/l/I)."""
    lower, upper, digits, symbols = "abcdefghijkmnpqrstuvwxyz", "ABCDEFGHJKLMNPQRSTUVWXYZ", "23456789", "@#$%&*?"
    pool = lower + upper + digits + symbols
    while True:
        pwd = [secrets.choice(lower), secrets.choice(upper), secrets.choice(digits), secrets.choice(symbols)]
        pwd += [secrets.choice(pool) for _ in range(length - 4)]
        secrets.SystemRandom().shuffle(pwd)
        candidate = "".join(pwd)
        if candidate[0] in string.ascii_letters:
            return candidate


COMMON_PASSWORDS = {"admin", "password", "123456", "12345678", "qwerty", "letmein", "welcome", "changeme",
                    "admin123", "admin@123", "password1", "principal", "school", "classnest", "teacher"}


def session_fingerprint(secret: str) -> str:
    """Binds a session to the account's current credentials: changing/resetting the password (or the admin
    secret in .env) signs out every existing session. HMAC'd with SECRET_KEY because the session cookie is
    signed but readable - it must not leak anything brute-forceable about the password."""
    return hmac.new(settings.SECRET_KEY.encode(), b"session-fp:" + secret.encode(), hashlib.sha256).hexdigest()[:24]


def is_weak_password(username: str, password: str) -> bool:
    p = password.strip().lower()
    return len(password) < 10 or p in COMMON_PASSWORDS or p == username.strip().lower() or p.isdigit()


class AdminRegistry:
    """Admins come from ADMIN_USERS in .env. Plain-text values are bcrypt-hashed in memory."""

    def __init__(self) -> None:
        self._hashes: dict[str, str] = {}
        self._fingerprints: dict[str, str] = {}
        self.weak: set[str] = set()
        self._dummy = ""

    def load(self) -> None:
        self._hashes, self._fingerprints, self.weak = {}, {}, set()
        for username, secret in settings.ADMIN_USERS.items():
            name = username.strip().lower()
            if not name or not secret:
                continue
            self._fingerprints[name] = session_fingerprint(secret)
            if secret.startswith(BCRYPT_PREFIXES):
                self._hashes[name] = secret
            else:
                log.warning("Admin '%s' has a plain-text password in .env - prefer a bcrypt hash "
                            "(python -m app.cli hash-password).", name)
                if is_weak_password(name, secret):
                    self.weak.add(name)
                    log.warning("SECURITY: admin '%s' has a weak password (short, common or equal to the "
                                "username). Anyone guessing it controls the whole school - change it in .env.", name)
                self._hashes[name] = hash_password(secret)
        self._dummy = dummy_hash()
        if not self._hashes:
            log.error("No admins configured! Set ADMIN_USERS in .env")

    def exists(self, username: str) -> bool:
        return username.strip().lower() in self._hashes

    def fingerprint(self, username: str) -> str:
        return self._fingerprints.get(username.strip().lower(), "")

    async def verify(self, username: str, password: str) -> bool:
        hashed = self._hashes.get(username.strip().lower())
        if hashed is None:
            await averify_password(password, self._dummy)  # equalise timing
            return False
        return await averify_password(password, hashed)

    @property
    def usernames(self) -> list[str]:
        return sorted(self._hashes)


admins = AdminRegistry()


# ------------------------------------------------------------------ OTP helpers
def generate_otp(length: int | None = None) -> str:
    return "".join(secrets.choice(string.digits) for _ in range(length or settings.OTP_LENGTH))


def hash_otp(code: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()


def verify_otp(code: str, hashed: str) -> bool:
    return hmac.compare_digest(hash_otp(code.strip()), hashed)


# ------------------------------------------------------------------ rate limiting
class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str, limit: int, window: float) -> bool:
        """Record a hit. Returns False when the limit is exceeded."""
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True

    def count(self, key: str, window: float) -> int:
        """Hits recorded for key within the window (does not record a new one)."""
        q = self._hits.get(key)
        if not q:
            return 0
        now = time.monotonic()
        while q and now - q[0] > window:
            q.popleft()
        return len(q)

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)

    def cleanup(self, max_age: float = 3600) -> None:
        now = time.monotonic()
        for key in [k for k, q in self._hits.items() if not q or now - q[-1] > max_age]:
            self._hits.pop(key, None)


limiter = RateLimiter()


# ------------------------------------------------------------------ CSRF + double submit
CSRF_KEY = "_csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


class CSRFError(Exception):
    pass


class DuplicateSubmission(Exception):
    pass


class OnceTokens:
    """Each rendered form carries a random ``_once`` token; a token may be used once."""

    def __init__(self, ttl: float = 4 * 3600) -> None:
        self.ttl = ttl
        self._used: dict[str, float] = {}

    def consume(self, token: str) -> bool:
        token = token[:64]
        now = time.monotonic()
        if len(self._used) > 20000:
            self._used = {k: v for k, v in self._used.items() if v > now}
            if len(self._used) > 20000:  # flood of unique tokens: keep the newest half (bounded memory)
                self._used = dict(list(self._used.items())[-10000:])
        if token in self._used:
            return False
        self._used[token] = now + self.ttl
        return True


once_tokens = OnceTokens()


def csrf_token(request: Request) -> str:
    token = request.session.get(CSRF_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_KEY] = token
    return token


async def csrf_protect(conn: HTTPConnection) -> None:
    """Router-level dependency. Accepts the token from a header (fetch) or a form field.
    Typed as HTTPConnection so routers that also contain WebSocket routes work (sockets check Origin instead)."""
    if not isinstance(conn, Request) or conn.method in SAFE_METHODS:
        return
    request = conn
    expected = request.session.get(CSRF_KEY)
    token = request.headers.get("x-csrf-token")
    once = request.headers.get("x-once-token")
    if token is None:
        ctype = request.headers.get("content-type", "")
        if ctype.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            form = await request.form()
            token = form.get("csrf_token")
            once = form.get("_once")
    if not expected or not isinstance(token, str) or not hmac.compare_digest(token, expected):
        raise CSRFError()
    if isinstance(once, str) and once and not once_tokens.consume(once):
        raise DuplicateSubmission()


# ------------------------------------------------------------------ client IP
def client_ip_from_scope(scope: dict) -> str:
    """The connecting address. Behind a reverse proxy (TRUST_PROXY_HEADERS=true) uvicorn's proxy-headers
    middleware has already replaced it with the right-most address not in FORWARDED_ALLOW_IPS - never the
    left-most X-Forwarded-For entry, which the client controls (that allowed dodging rate limits and IP bans,
    or framing someone else's IP for an auto-ban)."""
    client = scope.get("client")
    return client[0] if client else "unknown"


def client_ip(request: Any) -> str:
    return client_ip_from_scope(request.scope)


# ------------------------------------------------------------------ authentication
@dataclass
class Principal:
    kind: str  # admin | student | staff | guest
    username: str
    name: str
    id: int | None = None
    user: Any = None
    staff_role: str | None = None
    email: str | None = None
    meeting_code: str | None = None  # guests (PTM parents) only

    @property
    def key(self) -> str:
        if self.id:
            return f"u:{self.id}"
        return f"a:{self.username}" if self.kind == "admin" else f"g:{self.username}"

    @property
    def is_admin(self) -> bool:
        return self.kind == "admin"

    @property
    def is_teaching(self) -> bool:
        return self.kind == "admin" or (self.kind == "staff" and self.staff_role in TEACHING_ROLES)

    @property
    def home_url(self) -> str:
        return {"admin": "/admin", "student": "/student", "staff": "/staff"}.get(self.kind, "/")

    @property
    def initials(self) -> str:
        parts = self.name.split()
        return ((parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")) if parts else "?").upper()


class LoginRequired(Exception):
    def __init__(self, next_url: str = "/") -> None:
        self.next_url = next_url


def contact_email() -> str:
    """The administrator address shown to anyone who is blocked (Branding overrides .env)."""
    from .cache import site_cache

    return (site_cache.data or {}).get("contact_email") or settings.CONTACT_EMAIL


def account_notice(user: Any) -> dict[str, str] | None:
    """What the sign-in page tells someone whose account is not active (JSON-safe, kept in the session)."""
    if user is None or user.status == "active":
        return None
    notice = {"kind": user.status, "email": contact_email(), "reason": (user.status_reason or "")[:300]}
    if user.status == "banned":
        notice["title"] = "This account has been banned"
    elif user.status == "deleted":
        notice["title"] = "This account has been deleted"
        notice["reason"] = ""
        if user.purge_on:
            notice["until"] = user.purge_on.astimezone(settings.tz).strftime("%d %b %Y")
    else:
        notice["title"] = "This account is not active"
    return notice


async def load_principal(session: dict, db: AsyncSession) -> Principal | None:
    from .models import User

    auth = session.get("auth")
    if not auth:
        guest = session.get("guest")
        if guest:
            return Principal("guest", guest.get("email", "guest"), guest.get("name", "Guest"),
                             email=guest.get("email"), meeting_code=guest.get("meeting"))
        return None
    kind = auth.get("kind")
    fp = str(auth.get("fp") or "")
    if kind == "admin":
        name = str(auth.get("username") or "")
        if admins.exists(name) and hmac.compare_digest(fp, admins.fingerprint(name)):
            return Principal("admin", name, auth.get("name") or name.title())
    elif kind in ("student", "staff") and isinstance(auth.get("id"), int):
        user = await db.get(User, auth["id"])
        if (user is not None and user.role == kind
                and hmac.compare_digest(fp, session_fingerprint(user.password_hash))):
            if user.status == "active":
                return Principal(kind, user.username, user.full_name, user.id, user, user.staff_role, user.email)
            # banned / deleted / removed while signed in: the session ends now and the sign-in page says why
            session["account_notice"] = account_notice(user)
    session.pop("auth", None)
    return None


def start_session(request: Request, auth: dict) -> None:
    """Rotate the session on login (fixation protection) and keep pending flash messages."""
    flashes = request.session.get("_flashes")
    request.session.clear()
    request.session["auth"] = auth
    request.session[CSRF_KEY] = secrets.token_urlsafe(32)
    request.session["_login_at"] = int(time.time())
    if flashes:
        request.session["_flashes"] = flashes


def require(*kinds: str):
    async def dependency(request: Request, db: AsyncSession = Depends(get_db)) -> Principal:
        principal = await load_principal(request.session, db)
        if principal is None or principal.kind == "guest" and "guest" not in kinds:
            raise LoginRequired(request.url.path)
        if kinds and principal.kind not in kinds:
            raise HTTPException(status_code=403, detail="You don't have access to this page.")
        request.state.principal = principal
        return principal

    return dependency


require_admin = require("admin")
require_student = require("student")
require_staff = require("staff")
require_member = require("student", "staff")
require_user = require("admin", "student", "staff")
require_admin_or_staff = require("admin", "staff")


async def require_teaching(principal: Principal = Depends(require_admin_or_staff)) -> Principal:
    if not principal.is_teaching:
        raise HTTPException(status_code=403, detail="Only teaching staff can do this.")
    return principal


async def optional_principal(request: Request, db: AsyncSession = Depends(get_db)) -> Principal | None:
    principal = await load_principal(request.session, db)
    request.state.principal = principal
    return principal
