"""Jinja2 environment, filters, globals and the render() helper."""
from __future__ import annotations

import datetime as dt
import json
import secrets
from decimal import Decimal
from typing import Any

import jinja2
import markdown as md
import nh3
from fastapi import Request
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from .assets import asset_url
from .cache import site_cache
from .config import APP_DIR, settings
from . import constants
from .security import csrf_token

env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(APP_DIR / "templates")),
    autoescape=jinja2.select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)
templates = Jinja2Templates(env=env)

RICH_TAGS = {"p", "br", "strong", "b", "em", "i", "u", "s", "a", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5",
             "blockquote", "pre", "code", "img", "span", "div", "hr", "table", "thead", "tbody", "tr", "th", "td",
             "sub", "sup", "mark", "small"}
RICH_ATTRS = {"*": {"class"}, "a": {"href", "target", "title"}, "img": {"src", "alt", "title", "width", "height"},
              "li": {"data-list"}, "td": {"colspan", "rowspan"}, "th": {"colspan", "rowspan"}}


MARKDOWN_ATTRS = {tag: attrs - {"class"} for tag, attrs in RICH_ATTRS.items()}
MARKDOWN_ATTRS["*"] = set()


def sanitize_html(value: str | None, attributes: dict | None = None) -> str:
    if not value:
        return ""
    return nh3.clean(value, tags=RICH_TAGS, attributes=attributes or RICH_ATTRS,
                     url_schemes={"http", "https", "mailto", "tel"})


def markdown_html(value: str | None) -> Markup:
    """Markdown from AI answers / students / teachers. No class attributes: otherwise text steered into an AI
    answer (prompt injection) could use the UI framework's classes - e.g. position-fixed w-100 h-100 - to
    draw a fake full-screen "session expired, sign in here" overlay over the real page."""
    if not value:
        return Markup("")
    rendered = md.markdown(value, extensions=["extra", "sane_lists", "nl2br"])
    return Markup(sanitize_html(rendered, MARKDOWN_ATTRS))


def rich(value: str | None) -> Markup:
    return Markup(sanitize_html(value))


# ------------------------------------------------------------------ filters
def to_local(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(settings.tz)


def f_datetime(value: dt.datetime | None, fmt: str = "%d %b %Y, %I:%M %p") -> str:
    local = to_local(value)
    return local.strftime(fmt) if local else "—"


def f_date(value: dt.date | dt.datetime | None, fmt: str = "%d %b %Y") -> str:
    if value is None:
        return "—"
    if isinstance(value, dt.datetime):
        value = to_local(value)
    return value.strftime(fmt)


def f_time(value: dt.time | dt.datetime | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, dt.datetime):
        value = to_local(value)
    return value.strftime("%I:%M %p").lstrip("0")


def format_inr(value: Any) -> str:
    """Indian digit grouping: 12,34,567.00"""
    try:
        amount = Decimal(str(value or 0)).quantize(Decimal("0.01"))
    except Exception:
        return str(value)
    sign = "-" if amount < 0 else ""
    whole, frac = f"{abs(amount):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{sign}{settings.CURRENCY_SYMBOL}{whole}.{frac}"


def timeago(value: dt.datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    seconds = int((dt.datetime.now(dt.timezone.utc) - value).total_seconds())
    if seconds < 60:
        return "just now"
    for unit, size in (("year", 31536000), ("month", 2592000), ("week", 604800), ("day", 86400),
                       ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            n = seconds // size
            return f"{n} {unit}{'s' if n > 1 else ''} ago"
    return "just now"


def filesize(num: int | None) -> str:
    size = float(num or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def duration(seconds: int | None) -> str:
    if not seconds:
        return "—"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"


def nl2p(text: str | None) -> Markup:
    if not text:
        return Markup("")
    paragraphs = [p.strip() for p in str(text).replace("\r\n", "\n").split("\n\n") if p.strip()]
    return Markup("".join(f"<p>{escape(p).replace(chr(10), Markup('<br>'))}</p>" for p in paragraphs))


def initials(name: str | None) -> str:
    parts = (name or "?").split()
    return ((parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")) if parts else "?").upper()


def num(value: Any, places: int = 1) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{f:.{places}f}".rstrip("0").rstrip(".") if places else f"{f:.0f}"


env.filters.update(
    dt=f_datetime, d=f_date, t=f_time, money=format_inr, timeago=timeago, filesize=filesize,
    duration=duration, nl2p=nl2p, initials=initials, markdown=markdown_html, rich=rich, num=num,
)


@jinja2.pass_context
def csrf_field(ctx: Any) -> Markup:
    """CSRF token + one-time token (server-side double-submit guard)."""
    token = escape(ctx.get("csrf_token", ""))
    return Markup(f'<input type="hidden" name="csrf_token" value="{token}">'
                  f'<input type="hidden" name="_once" value="{secrets.token_urlsafe(16)}">')


def page_json(value: Any) -> Markup:
    """Serialise data for <script type="application/json"> blocks (safe against </script>)."""
    text = json.dumps(value, default=str).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return Markup(text)


env.globals.update(
    asset=asset_url, settings=settings, csrf_field=csrf_field, page_json=page_json, C=constants,
    now=lambda: dt.datetime.now(settings.tz), today=lambda: dt.datetime.now(settings.tz).date(),
)


# ------------------------------------------------------------------ flash messages + render
def flash(request: Request, message: str, category: str = "success") -> None:
    flashes = request.session.get("_flashes") or []
    flashes.append([category, message])
    request.session["_flashes"] = flashes[-5:]


def render(request: Request, name: str, context: dict[str, Any] | None = None, status_code: int = 200):
    ctx: dict[str, Any] = {
        "user": getattr(request.state, "principal", None),
        "csrf_token": csrf_token(request),
        "csp_nonce": getattr(request.state, "csp_nonce", ""),
        "flashes": request.session.pop("_flashes", []),
        "site": site_cache.data,
        "footer_pages": site_cache.footer_pages,
        "current_path": request.url.path,
    }
    if context:
        ctx.update(context)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def render_string(template_name: str, /, **context: Any) -> str:
    """Render a template outside a request (emails). Positional-only so contexts may contain `name`."""
    return env.get_template(template_name).render(site=site_cache.data, settings=settings, C=constants, **context)
