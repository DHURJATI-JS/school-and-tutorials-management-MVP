"""Small helpers shared by routers: redirects, form parsing, validation."""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import RedirectResponse

from ..config import settings

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9\s\-]{8,16}$")
NUMBER_RE = re.compile(r"^[+-]?\d+(\.\d+)?$")


class FormError(ValueError):
    pass


def redirect(url: str, status_code: int = 303) -> RedirectResponse:
    return RedirectResponse(url, status_code=status_code)


def back(request: Request, fallback: str = "/") -> RedirectResponse:
    ref = request.headers.get("referer")
    if ref:
        parsed = urlparse(ref)
        if parsed.netloc in ("", request.url.netloc):
            # safe_next: a same-host Referer like http://host//evil.com would otherwise become "//evil.com"
            # - a protocol-relative open redirect.
            return redirect(safe_next(parsed.path + (f"?{parsed.query}" if parsed.query else ""), fallback))
    return redirect(fallback)


INT_RE = re.compile(r"[0-9]{1,9}")


def int_or(value: object, default: int | None = None) -> int | None:
    """A form / JSON value as an int, or `default`. Stricter than str.isdigit(): "²".isdigit() is True but int("²")
    raises, and a 5000-digit string makes int() raise too - both used to turn crafted input into a 500."""
    s = str(value if value is not None else "").strip()
    return int(s) if INT_RE.fullmatch(s) else default


def ints(values) -> list[int]:
    """Every valid int in a list of form values (e.g. form.getlist("user_ids")); junk is skipped."""
    return [i for i in (int_or(v) for v in values) if i is not None]


def safe_next(url: str | None, default: str = "/") -> str:
    if url and url.startswith("/") and not url.startswith("//") and "\\" not in url:
        return url
    return default


def today() -> dt.date:
    return dt.datetime.now(settings.tz).date()


def now_local() -> dt.datetime:
    return dt.datetime.now(settings.tz)


def parse_date(value: str | None, field: str = "date", required: bool = True) -> dt.date | None:
    if not value:
        if required:
            raise FormError(f"Please enter a {field}.")
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise FormError(f"Invalid {field}.") from exc


def parse_time(value: str | None, field: str = "time") -> dt.time:
    try:
        return dt.time.fromisoformat(value or "")
    except ValueError as exc:
        raise FormError(f"Invalid {field}.") from exc


def parse_money(value: str | None, field: str = "amount", allow_zero: bool = False) -> Decimal:
    try:
        amount = Decimal(str(value or "").replace(",", "").strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise FormError(f"Invalid {field}.") from exc
    if amount < 0 or (amount == 0 and not allow_zero) or amount > Decimal("99999999"):
        raise FormError(f"Please enter a valid {field}.")
    return amount


def parse_period(value: str | None) -> str:
    if not value or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        raise FormError("Please choose a valid month.")
    return value


def check_email(value: str | None, field: str = "email", required: bool = True) -> str | None:
    value = (value or "").strip()
    if not value:
        if required:
            raise FormError(f"The {field} is required.")
        return None
    if not EMAIL_RE.match(value) or len(value) > 255:
        raise FormError(f"The {field} doesn't look valid.")
    return value.lower()


def check_phone(value: str | None, field: str = "phone number", required: bool = True) -> str | None:
    value = (value or "").strip()
    if not value:
        if required:
            raise FormError(f"The {field} is required.")
        return None
    if not PHONE_RE.match(value):
        raise FormError(f"The {field} doesn't look valid (10 digits, optionally with +91).")
    return value


def csv_safe(value) -> str:
    """Neutralise spreadsheet formulas (CSV injection): a cell such as =HYPERLINK(...) or =cmd|... typed by a
    student would otherwise execute when an admin opens the export in Excel / Sheets."""
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r") and not NUMBER_RE.match(text):
        return "'" + text
    return text


def csv_row(values) -> list[str]:
    return [csv_safe(v) for v in values]


def truthy(value: str | None) -> bool:
    return str(value or "").lower() in {"1", "true", "on", "yes"}


def mask_email(email: str | None) -> str:
    if not email or "@" not in email:
        return email or ""
    name, domain = email.split("@", 1)
    return (name[:2] + "•" * max(1, len(name) - 2)) + "@" + domain


def month_label(period: str) -> str:
    try:
        return dt.date.fromisoformat(period + "-01").strftime("%b %Y")
    except ValueError:
        return period
