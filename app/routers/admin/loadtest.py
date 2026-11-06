"""Load test report: the latest Locust stress test of this app, from app/data/loadtest_report.json
(written by the load-test tooling - see docs/load-test). Read-only; re-read whenever the file changes."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ...config import APP_DIR
from ...templating import render

router = APIRouter()
REPORT = APP_DIR / "data" / "loadtest_report.json"
_cache: dict[str, Any] = {"mtime": None, "data": None}


def load_report() -> dict | None:
    try:
        mtime = REPORT.stat().st_mtime
    except OSError:
        return None
    if _cache["mtime"] != mtime:
        try:
            _cache["data"] = json.loads(REPORT.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        _cache["mtime"] = mtime
    return _cache["data"]


def _clock(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


@router.get("/load-test", response_class=HTMLResponse)
async def load_test(request: Request):
    report = load_report()
    charts: dict[str, list[dict]] = {}
    if report:
        s = report["series"]
        charts = {key: [{"label": _clock(t), "value": v} for t, v in zip(s["t"], s[key], strict=False)]
                  for key in ("rps", "p95", "users", "cpu")}
    return render(request, "admin/load_test.html", {"report": report, "charts": charts})
