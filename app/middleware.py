"""Pure-ASGI middleware.

* SecureHeadersMiddleware - a Talisman-style port for FastAPI: per-request CSP nonce,
  HSTS (when HTTPS), frame/sniff/referrer/permissions policies, no-store for private pages.
* BodySizeLimitMiddleware - rejects oversized request bodies before anything is parsed or spooled.
* IPBanMiddleware - blocks banned IPs for HTTP and WebSocket traffic.
"""
from __future__ import annotations

import html
import re
import secrets

from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.responses import HTMLResponse, JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .cache import ban_cache, site_cache
from .config import settings
from .security import client_ip_from_scope, contact_email

CDN_HOSTS = "https://cdnjs.cloudflare.com https://cdn.jsdelivr.net"
# Scripts are pinned to exact files (a host-wide allow-list would let an injected tag load ANY package
# from jsDelivr/cdnjs). Each <script> tag also carries an SRI integrity hash.
CDN_SCRIPTS = " ".join([
    "https://cdnjs.cloudflare.com/ajax/libs/mdb-ui-kit/7.3.2/mdb.umd.min.js",
    "https://cdn.jsdelivr.net/npm/fullcalendar@6.1.15/index.global.min.js",
    "https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js",
    "https://cdn.jsdelivr.net/npm/quill@2.0.3/dist/quill.js",
    "https://cdn.jsdelivr.net/npm/sortablejs@1.15.3/Sortable.min.js",
])


def build_csp(nonce: str, host: str) -> str:
    sockets = f"ws://{host} wss://{host}" if host else "ws: wss:"
    pay = " https://sdk.cashfree.com https://*.cashfree.com" if settings.CASHFREE_ENABLED else ""
    return "; ".join([
        "default-src 'self'",
        f"script-src 'self' 'nonce-{nonce}' {CDN_SCRIPTS}{pay}",
        # Inline style attributes are needed by MDB/Chart.js/FullCalendar; scripts stay nonce-locked.
        f"style-src 'self' 'unsafe-inline' {CDN_HOSTS} https://fonts.googleapis.com{pay}",
        f"font-src 'self' data: {CDN_HOSTS} https://fonts.gstatic.com",
        "img-src 'self' data: blob:" + pay,
        "media-src 'self' blob: https:",
        f"connect-src 'self' {sockets}{pay}",
        "worker-src 'self' blob:",
        f"frame-src {pay.strip() or chr(39) + 'none' + chr(39)}",
        "object-src 'none'",
        "base-uri 'self'",
        f"form-action 'self'{pay}",
        "frame-ancestors 'none'",
    ])


class SecureHeadersMiddleware:
    def __init__(self, app: ASGIApp, force_https: bool = False) -> None:
        self.app = app
        self.hsts = force_https

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        nonce = secrets.token_urlsafe(18)
        scope.setdefault("state", {})["csp_nonce"] = nonce
        csp = build_csp(nonce, Headers(scope=scope).get("host", ""))

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                ctype = headers.get("content-type", "")
                if ctype.startswith("text/html"):
                    headers["Content-Security-Policy"] = csp  # documents only (PDF viewers break under CSP)
                if ctype.startswith(("text/html", "application/json", "text/csv")):
                    # Pages carry personal data: never keep them in the browser cache (shared school PCs,
                    # back button after logout). Responses that set their own policy keep it.
                    headers.setdefault("Cache-Control", "no-store")
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
                headers.setdefault("Permissions-Policy",
                                   "camera=(self), microphone=(self), display-capture=(self), "
                                   "geolocation=(), payment=(), usb=()")
                headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
                if self.hsts:
                    headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
            await send(message)

        await self.app(scope, receive, send_with_headers)


MB = 1024 * 1024


def body_limit_for(path: str, content_type: str) -> int:
    """Largest request body accepted for this endpoint (bytes)."""
    if re.fullmatch(r"/admin/(videos/upload|lessons/new|lessons/\d+/attach-video)", path):
        return (settings.MAX_VIDEO_MB + 8) * MB
    if path.startswith("/meet/") and "/recording/" in path:
        return 65 * MB  # 5-second recording chunks (handler allows 64 MB)
    if path == "/login/id-card":
        return 9 * MB  # anonymous: the handler accepts cards up to 8 MB
    if path == "/api/ai/ask":
        return 10 * MB  # JSON incl. a base64 video frame (<= 8 MB)
    if path.startswith("/webhooks/"):
        return 1 * MB
    if content_type.startswith("multipart/form-data"):
        return (2 * settings.MAX_UPLOAD_MB + 2) * MB  # e.g. photo + ID proof on one form
    return (8 if path.startswith("/admin/") else settings.MAX_FORM_MB) * MB


class BodySizeLimitMiddleware:
    """Starlette parses (and spools to disk) a whole multipart body before any handler or CSRF check runs,
    and JSON bodies are read into memory - so without a cap an anonymous client could stream gigabytes.
    Declared sizes are refused up front; chunked bodies are counted while they stream."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        limit = body_limit_for(scope["path"], headers.get("content-type", ""))
        declared = headers.get("content-length")
        if declared is not None and (not re.fullmatch(r"[0-9]{1,15}", declared) or int(declared) > limit):
            await self._reject(scope, receive, send, limit)
            return
        received, exceeded = 0, False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise HTTPException(413, f"That upload is too large (limit {limit // MB} MB).")
            return message

        async def closing_send(message: Message) -> None:
            if exceeded and message["type"] == "http.response.start":
                # Otherwise the server keeps the connection alive and drains the rest of the body.
                MutableHeaders(scope=message)["Connection"] = "close"
            await send(message)

        await self.app(scope, limited_receive, closing_send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send, limit: int) -> None:
        detail = f"That upload is too large (limit {limit // MB} MB)."
        accept = Headers(scope=scope).get("accept", "")
        if scope["path"].startswith(("/api/", "/webhooks/", "/meet/")) or "application/json" in accept:
            response = JSONResponse({"detail": detail}, status_code=413, headers={"Connection": "close"})
        else:
            response = HTMLResponse(f"<!doctype html><title>Too large</title><p>{html.escape(detail)} "
                                    "<a href='/'>Home</a></p>", status_code=413, headers={"Connection": "close"})
        await response(scope, receive, send)


BANNED_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport"
content="width=device-width,initial-scale=1"><title>Access blocked</title><style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;font-family:system-ui,Segoe UI,Roboto,sans-serif;
background:radial-gradient(circle at 20% 20%,#312e81,#0b1020 60%);color:#e5e7eb}}
.card{{max-width:520px;margin:24px;padding:36px;border-radius:20px;background:rgba(17,24,39,.75);
border:1px solid rgba(148,163,184,.2);box-shadow:0 30px 60px rgba(0,0,0,.4)}}
h1{{margin:0 0 8px;font-size:26px}}p{{color:#cbd5e1;line-height:1.6}}.r{{padding:12px 14px;border-radius:12px;
background:rgba(239,68,68,.12);border:1px solid rgba(239,68,68,.35);color:#fecaca}}a{{color:#a5b4fc}}
</style></head><body><div class="card"><h1>&#128683; Access blocked</h1>
<p>Your network address has been blocked from using {site}.</p><p class="r"><strong>Reason:</strong> {reason}</p>
<p>{until}If you believe this is a mistake, contact the administrator at
<a href="mailto:{email}">{email}</a>.</p></div></body></html>"""


class IPBanMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            ban = ban_cache.lookup(client_ip_from_scope(scope))
            if ban is not None:
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 4403, "reason": "banned"})
                    return
                reason, expires = ban
                until = ""
                if expires is not None:
                    until = f"The block lifts on {expires.astimezone(settings.tz):%d %b %Y, %I:%M %p}. "
                site = (site_cache.data or {}).get("site_name") or settings.APP_NAME
                page = BANNED_PAGE.format(site=html.escape(site), reason=html.escape(reason),
                                          until=until, email=html.escape(contact_email()))
                # the header tells open pages (CN.api) to reload into this page at once
                await HTMLResponse(page, status_code=403, headers={"X-Access-Blocked": "ip", "Cache-Control": "no-store"})(
                    scope, receive, send)
                return
        await self.app(scope, receive, send)
