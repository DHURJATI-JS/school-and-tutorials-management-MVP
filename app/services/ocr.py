"""Client for the Tesseract OCR micro-service + ID-card code extraction."""
from __future__ import annotations

import re
from typing import Any

import httpx

from ..config import settings


class OCRUnavailable(Exception):
    pass


async def ocr_image(data: bytes, filename: str = "image.png", content_type: str = "image/png") -> str:
    url = settings.OCR_SERVICE_URL.rstrip("/") + "/ocr"
    try:
        async with httpx.AsyncClient(timeout=settings.OCR_TIMEOUT_SECONDS) as client:
            resp = await client.post(url, files={"file": (filename, data, content_type)},
                                     headers={"X-OCR-Secret": settings.OCR_SHARED_SECRET})
        resp.raise_for_status()
        return str(resp.json().get("text", ""))
    except (httpx.HTTPError, ValueError) as exc:
        raise OCRUnavailable(f"OCR service unavailable at {settings.OCR_SERVICE_URL} ({exc.__class__.__name__})") from exc


async def ocr_health() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(settings.OCR_SERVICE_URL.rstrip("/") + "/health")
        resp.raise_for_status()
        return {"reachable": True, **resp.json()}
    except (httpx.HTTPError, ValueError) as exc:
        return {"reachable": False, "error": exc.__class__.__name__}


# STU-000123 / STF-000045, tolerant to common OCR confusions (S<->5, O<->0, I<->1 …)
ID_PATTERN = re.compile(r"([S5$][\s.]*T[\s.]*[UVFE])[\s\-–—_:.]*([0-9OQDILSZB|]{6})")
DIGIT_FIX = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "|": "1", "S": "5", "Z": "2",
                           "B": "8"})


def extract_id_codes(text: str) -> list[str]:
    found: list[str] = []
    for match in ID_PATTERN.finditer(text.upper()):
        prefix = "STU" if match.group(1)[-1] in "UV" else "STF"
        digits = match.group(2).translate(DIGIT_FIX)
        if digits.isdigit():
            code = f"{prefix}-{digits}"
            if code not in found:
                found.append(code)
    return found
