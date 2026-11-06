"""Public website: editable home page, CMS pages, contact form, health check."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..assets import STATIC_DIR
from ..cache import site_cache
from ..database import get_db
from ..models import ContactMessage, HomeSection, Page
from ..security import client_ip, csrf_protect, limiter, optional_principal
from ..services.ai import ai
from ..services.mailer import queue_admin_alert
from ..services.moderation import analyze
from ..services.storage import abs_path
from ..templating import flash, render
from .common import EMAIL_RE, redirect

router = APIRouter()
SITE_FILE = re.compile(r"^[a-f0-9]{32}\.(jpg|png|webp)$")


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, db: AsyncSession = Depends(get_db), _=Depends(optional_principal)):
    sections = (await db.scalars(select(HomeSection).where(HomeSection.is_visible.is_(True))
                                 .order_by(HomeSection.position, HomeSection.id))).all()
    return render(request, "public/home.html", {"sections": sections})


@router.get("/p/{slug}", response_class=HTMLResponse)
async def page(slug: str, request: Request, db: AsyncSession = Depends(get_db), _=Depends(optional_principal)):
    item = await db.scalar(select(Page).where(Page.slug == slug, Page.is_published.is_(True)))
    if item is None:
        raise HTTPException(404, "Page not found")
    return render(request, "public/page.html", {"page": item})


@router.get("/terms")
async def terms():
    return redirect("/p/terms", 307)


@router.post("/contact", dependencies=[Depends(csrf_protect)])
async def contact(request: Request, name: str = Form(..., max_length=120), email: str = Form(..., max_length=255),
                  phone: str = Form("", max_length=30), message: str = Form(..., max_length=4000),
                  website: str = Form(""), db: AsyncSession = Depends(get_db)):
    if website:  # honeypot field - bots fill it, humans never see it
        return redirect("/#contact")
    ip = client_ip(request)
    if not limiter.hit(f"contact:{ip}", 3, 600):
        flash(request, "You've sent several messages already — please wait a few minutes.", "warning")
        return redirect("/#contact")
    if not EMAIL_RE.match(email.strip()) or len(message.strip()) < 5 or not name.strip():
        flash(request, "Please enter your name, a valid email and a message.", "danger")
        return redirect("/#contact")
    if analyze(message).severe or analyze(name).severe:
        flash(request, "Please remove abusive language from your message.", "danger")
        return redirect("/#contact")
    db.add(ContactMessage(name=name.strip(), email=email.strip().lower(), phone=phone.strip() or None,
                          message=message.strip(), ip=ip))
    await db.commit()
    queue_admin_alert(f"New enquiry from {name.strip()}", "contact_notify.html", category="contact",
                      name=name.strip(), email=email.strip(), phone=phone.strip(), message=message.strip())
    flash(request, "Thank you! Your message has been sent — we'll get back to you soon.", "success")
    return redirect("/#contact")


@router.get("/media/site/{filename}")
async def site_media(filename: str):
    if not SITE_FILE.match(filename):
        raise HTTPException(404)
    path = abs_path(f"site/{filename}")
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "public, max-age=604800"})


@router.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Browsers, PDF viewers and bookmarks ask for /favicon.ico directly."""
    return FileResponse(STATIC_DIR / "favicon.ico", media_type="image/x-icon",
                        headers={"Cache-Control": "public, max-age=604800"})


@router.get("/site.webmanifest", include_in_schema=False)
async def manifest():
    """Install-to-home-screen metadata, using the institute's name and colours from Branding."""
    site = site_cache.data
    icons = ([{"src": site["logo_url"], "sizes": "any"}] if site.get("logo_url") else []) + [
        {"src": "/static/img/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/static/img/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        {"src": "/static/img/favicon.svg", "sizes": "any", "type": "image/svg+xml"}]
    return JSONResponse({"name": site["site_name"], "short_name": site["site_name"][:12],
                         "description": site.get("tagline", ""), "start_url": "/", "display": "standalone",
                         "theme_color": site["primary_color"], "background_color": "#ffffff", "icons": icons},
                        media_type="application/manifest+json", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/health")
async def health(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "database": db_ok, "ai_circuit": ai.breaker.state}
