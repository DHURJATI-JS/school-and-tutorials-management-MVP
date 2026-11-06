"""Website builder (WordPress-style): home sections, branding, CMS pages, image uploads, inbox."""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...cache import HEX_COLOR, site_cache
from ...constants import HOME_SECTION_KINDS
from ...database import get_db
from ...models import ContactMessage, HomeSection, Page, SiteSetting
from ...services.storage import UploadError, has_file, save_image
from ...templating import flash, render, sanitize_html
from ..common import FormError, back, check_email, redirect, truthy

router = APIRouter()
ICON_RE = re.compile(r"^fa-[a-z0-9-]{2,40}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,59}$")
SPECIAL_LINKS = {"whatsapp", "email", "phone"}


def clean_url(value: str | None) -> str:
    v = (value or "").strip()
    if not v:
        return ""
    if v in SPECIAL_LINKS or (v.startswith(("/", "#")) and not v.startswith("//")):
        return v[:300]
    if v.startswith(("https://", "http://", "mailto:", "tel:")):
        return v[:300]
    raise FormError(f"â€œ{v}â€ is not a valid link (use /path, #anchor, https://â€¦, whatsapp or email).")


def clean_items(kind: str, raw: str | None) -> list[dict]:
    fields = HOME_SECTION_KINDS[kind]["fields"]
    if not fields:
        return []
    try:
        items = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        raise FormError("Could not read the list items.") from exc
    out = []
    for item in items[:40] if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        row = {f: str(item.get(f, "")).strip()[:800] for f in fields}
        if row.get("icon") and not ICON_RE.match(row["icon"]):
            row["icon"] = "fa-star"
        if "src" in row and not row["src"].startswith("/media/site/"):
            continue
        if any(row.values()):
            out.append(row)
    return out


def media_url(rel: str) -> str:
    return "/media/site/" + rel.split("/", 1)[1]


@router.get("/site", response_class=HTMLResponse)
async def sections(request: Request, db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(HomeSection).order_by(HomeSection.position, HomeSection.id))).all()
    return render(request, "admin/site/sections.html", {"sections": rows, "kinds": HOME_SECTION_KINDS})


@router.get("/site/sections/new", response_class=HTMLResponse)
async def section_new(request: Request, kind: str = "richtext"):
    if kind not in HOME_SECTION_KINDS:
        raise HTTPException(404)
    section = HomeSection(id=0, kind=kind, title="", subtitle="", body="", data={}, is_visible=True)
    return render(request, "admin/site/section_form.html",
                  {"section": section, "meta": HOME_SECTION_KINDS[kind], "is_new": True})


@router.get("/site/sections/{sid:int}", response_class=HTMLResponse)
async def section_edit(sid: int, request: Request, db: AsyncSession = Depends(get_db)):
    section = await db.get(HomeSection, sid)
    if section is None:
        raise HTTPException(404)
    return render(request, "admin/site/section_form.html",
                  {"section": section, "meta": HOME_SECTION_KINDS.get(section.kind, {}), "is_new": False})


@router.post("/site/sections/{sid:int}")
async def section_save(sid: int, request: Request, db: AsyncSession = Depends(get_db),
                       kind: str = Form("richtext"), title: str = Form(""), subtitle: str = Form(""),
                       body: str = Form(""), items_json: str = Form("[]"), badge: str = Form(""),
                       primary_label: str = Form(""), primary_url: str = Form(""),
                       secondary_label: str = Form(""), secondary_url: str = Form(""),
                       show_form: str = Form(""), is_visible: str = Form(""), remove_image: str = Form(""),
                       image: UploadFile | None = File(None)):
    section = await db.get(HomeSection, sid) if sid else None
    if sid and section is None:
        raise HTTPException(404)
    kind = section.kind if section else kind
    if kind not in HOME_SECTION_KINDS:
        raise HTTPException(400, "Unknown section type")
    try:
        data = dict(section.data or {}) if section else {}
        data["items"] = clean_items(kind, items_json)
        if kind in ("hero", "cta"):
            data.update(badge=badge.strip()[:120], primary_label=primary_label.strip()[:60],
                        primary_url=clean_url(primary_url), secondary_label=secondary_label.strip()[:60],
                        secondary_url=clean_url(secondary_url))
        if kind == "contact":
            data["show_form"] = truthy(show_form)
        if truthy(remove_image):
            data.pop("image", None)
        if has_file(image):
            data["image"] = media_url(await save_image(image, "site", 1920))
    except (FormError, UploadError) as exc:
        flash(request, str(exc), "danger")
        return back(request, "/admin/site")
    if section is None:
        last = await db.scalar(select(func.coalesce(func.max(HomeSection.position), -1)))
        section = HomeSection(kind=kind, position=int(last) + 1)
        db.add(section)
    section.title = title.strip()[:200] or None
    section.subtitle = subtitle.strip()[:1000] or None
    section.body = sanitize_html(body) or None
    section.data = data
    section.is_visible = truthy(is_visible)
    await db.commit()
    flash(request, "Section saved â€” your home page is updated.", "success")
    return redirect("/admin/site")


@router.post("/site/sections/{sid:int}/toggle")
async def section_toggle(sid: int, request: Request, db: AsyncSession = Depends(get_db)):
    section = await db.get(HomeSection, sid)
    if section is None:
        raise HTTPException(404)
    section.is_visible = not section.is_visible
    await db.commit()
    flash(request, f"Section {'shown' if section.is_visible else 'hidden'}.", "success")
    return redirect("/admin/site")


@router.post("/site/sections/{sid:int}/delete")
async def section_delete(sid: int, request: Request, db: AsyncSession = Depends(get_db)):
    section = await db.get(HomeSection, sid)
    if section is not None:
        await db.delete(section)
        await db.commit()
    flash(request, "Section deleted.", "success")
    return redirect("/admin/site")


class ReorderIn(BaseModel):
    order: list[int]


@router.post("/site/sections/reorder")
async def section_reorder(payload: ReorderIn, db: AsyncSession = Depends(get_db)):
    if payload.order:
        mapping = {sid: pos for pos, sid in enumerate(payload.order[:200])}
        await db.execute(update(HomeSection).where(HomeSection.id.in_(mapping))
                         .values(position=case(mapping, value=HomeSection.id)))
        await db.commit()
    return {"ok": True}


@router.post("/site/upload-image")
async def upload_image(file: UploadFile = File(...)):
    try:
        rel = await save_image(file, "site", 1600)
    except UploadError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"url": media_url(rel)}


# ------------------------------------------------------------------ branding
BRAND_FIELDS = ("site_name", "tagline", "footer_text", "contact_email", "contact_whatsapp", "contact_phone",
                "contact_address", "facebook", "instagram", "youtube", "linkedin")


@router.get("/site/branding", response_class=HTMLResponse)
async def branding(request: Request):
    return render(request, "admin/site/branding.html", {"brand": site_cache.branding})


@router.post("/site/branding")
async def branding_save(request: Request, db: AsyncSession = Depends(get_db), logo: UploadFile | None = File(None)):
    form = await request.form()
    value = dict(site_cache.branding)
    try:
        for field in BRAND_FIELDS:
            value[field] = str(form.get(field, "")).strip()[:300]
        if not value["site_name"]:
            raise FormError("The institute name is required.")
        check_email(value["contact_email"], "contact email", required=False)
        for field in ("facebook", "instagram", "youtube", "linkedin"):
            if value[field] and not value[field].startswith("https://"):
                raise FormError(f"The {field} link must start with https://")
        for field in ("primary_color", "accent_color"):
            color = str(form.get(field, "")).strip()
            if not HEX_COLOR.match(color):
                raise FormError("Colours must be hex values like #4f46e5.")
            value[field] = color
        if truthy(str(form.get("remove_logo", ""))):
            value["logo"] = None
        if has_file(logo):
            value["logo"] = media_url(await save_image(logo, "site", 512, keep_png=True)).rsplit("/", 1)[1]
    except (FormError, UploadError) as exc:
        flash(request, str(exc), "danger")
        return redirect("/admin/site/branding")
    row = await db.get(SiteSetting, "branding")
    if row is None:
        db.add(SiteSetting(key="branding", value=value))
    else:
        row.value = value
    await db.commit()
    await site_cache.refresh()
    flash(request, "Branding saved.", "success")
    return redirect("/admin/site/branding")


# ------------------------------------------------------------------ CMS pages
@router.get("/site/pages", response_class=HTMLResponse)
async def pages(request: Request, db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(Page).order_by(Page.id))).all()
    return render(request, "admin/site/pages.html", {"pages": rows})


@router.get("/site/pages/{pid:int}", response_class=HTMLResponse)
async def page_edit(pid: int, request: Request, db: AsyncSession = Depends(get_db)):
    page = await db.get(Page, pid) if pid else Page(id=0, slug="", title="", body="", is_published=True,
                                                    show_in_footer=True)
    if page is None:
        raise HTTPException(404)
    return render(request, "admin/site/page_form.html", {"page": page})


@router.post("/site/pages/{pid:int}")
async def page_save(pid: int, request: Request, db: AsyncSession = Depends(get_db), title: str = Form(...),
                    slug: str = Form(""), body: str = Form(""), is_published: str = Form(""),
                    show_in_footer: str = Form("")):
    page = await db.get(Page, pid) if pid else None
    if pid and page is None:
        raise HTTPException(404)
    slug = (slug.strip().lower() or re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-"))[:60]
    if not SLUG_RE.match(slug):
        flash(request, "The URL slug may only contain lowercase letters, numbers and dashes.", "danger")
        return back(request, "/admin/site/pages")
    clash = await db.scalar(select(Page.id).where(Page.slug == slug, Page.id != (page.id if page else 0)))
    if clash:
        flash(request, f"Another page already uses /p/{slug}.", "danger")
        return back(request, "/admin/site/pages")
    if page is None:
        page = Page(slug=slug, title=title)
        db.add(page)
    if page.slug == "terms" and slug != "terms":
        slug = "terms"  # the Terms page (with the disclaimer) keeps its address
    page.slug, page.title = slug, title.strip()[:200]
    page.body = sanitize_html(body)
    page.is_published, page.show_in_footer = truthy(is_published), truthy(show_in_footer)
    await db.commit()
    await site_cache.refresh()
    flash(request, "Page saved.", "success")
    return redirect("/admin/site/pages")


@router.post("/site/pages/{pid:int}/delete")
async def page_delete(pid: int, request: Request, db: AsyncSession = Depends(get_db)):
    page = await db.get(Page, pid)
    if page is not None and page.slug == "terms":
        flash(request, "The Terms & Conditions page can't be deleted (it holds the legal disclaimer).", "warning")
        return redirect("/admin/site/pages")
    if page is not None:
        await db.delete(page)
        await db.commit()
        await site_cache.refresh()
    flash(request, "Page deleted.", "success")
    return redirect("/admin/site/pages")


# ------------------------------------------------------------------ contact inbox
@router.get("/inbox", response_class=HTMLResponse)
async def inbox(request: Request, db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(ContactMessage).order_by(ContactMessage.id.desc()).limit(200))).all()
    return render(request, "admin/inbox.html", {"messages": rows})


@router.post("/inbox/{mid}/read")
async def inbox_read(mid: int, db: AsyncSession = Depends(get_db)):
    await db.execute(update(ContactMessage).where(ContactMessage.id == mid)
                     .values(is_read=~ContactMessage.is_read))
    await db.commit()
    return redirect("/admin/inbox")


@router.post("/inbox/{mid}/delete")
async def inbox_delete(mid: int, request: Request, db: AsyncSession = Depends(get_db)):
    msg = await db.get(ContactMessage, mid)
    if msg is not None:
        await db.delete(msg)
        await db.commit()
    flash(request, "Message deleted.", "success")
    return redirect("/admin/inbox")
