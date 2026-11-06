"""Knowledge base (RAG): upload notes / PDFs / images, extract text, chunk + embed into pgvector."""
from __future__ import annotations

import logging
from pathlib import Path

from anyio import to_thread
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from ...database import SessionLocal, get_db
from ...models import Batch, KnowledgeChunk, Material, Meeting, Video
from ...security import Principal, require_admin
from ...services import rag
from ...services.background import spawn
from ...services.ocr import OCRUnavailable, ocr_image
from ...services.storage import MATERIAL_EXT, UploadError, abs_path, delete_file, has_file, save_bytes, save_upload
from ...templating import flash, render
from ..common import redirect

router = APIRouter()
log = logging.getLogger("classnest.knowledge")


def _pdf_text(path: Path) -> str:
    import pypdf

    reader = pypdf.PdfReader(str(path))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages[:300])


async def extract_text(material: Material) -> str:
    path = abs_path(material.file_path or "")
    ext = path.suffix.lower()
    if ext == ".pdf":
        return await to_thread.run_sync(_pdf_text, path)
    if ext in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="ignore")
    return await ocr_image(path.read_bytes(), material.original_name or path.name, material.mime or "image/png")


async def process_material(material_id: int) -> None:
    async with SessionLocal() as db:
        material = await db.get(Material, material_id)
        if material is None:
            return
        try:
            text = (await extract_text(material)).strip()
            if len(text) < 20:
                raise ValueError("No readable text was found (for scanned PDFs, upload page images instead).")
            material.chunks = await rag.index_document(
                db, source_type="material", source_id=material.id, title=material.title, content=text,
                batch_id=material.batch_id, class_level=material.class_level, subject=material.subject)
            material.text_chars, material.status, material.error = len(text), "ready", None
        except (OCRUnavailable, ValueError, OSError) as exc:
            material.status, material.error = "failed", str(exc)[:500]
        except Exception as exc:  # pypdf and friends raise many types
            log.exception("Material %s failed", material_id)
            material.status, material.error = "failed", f"{exc.__class__.__name__}: {exc}"[:500]
        await db.commit()


async def reindex_all() -> None:
    async with SessionLocal() as db:
        material_ids = (await db.scalars(select(Material.id))).all()
        meetings = (await db.scalars(select(Meeting).options(joinedload(Meeting.batch))
                                     .where(Meeting.ai_notes.is_not(None), Meeting.batch_id.is_not(None)))).unique().all()
        for m in meetings:
            await rag.index_document(db, source_type="meeting", source_id=m.id, title=f"Class notes: {m.title}",
                                     content=f"{m.ai_notes}\n\n{m.transcript or ''}", batch_id=m.batch_id,
                                     class_level=m.batch.class_level, subject=m.batch.subject)
        videos = (await db.scalars(select(Video).options(joinedload(Video.batch)))).unique().all()
        for v in videos:
            await rag.index_document(db, source_type="video", source_id=v.id, title=f"Video: {v.title}",
                                     content="\n\n".join(filter(None, [v.title, v.description, v.notes])),
                                     batch_id=v.batch_id, class_level=v.batch.class_level, subject=v.batch.subject)
        await db.commit()
    for mid in material_ids:
        await process_material(mid)


@router.get("/knowledge", response_class=HTMLResponse)
async def knowledge(request: Request, db: AsyncSession = Depends(get_db), q: str = "", batch: int = 0):
    materials = (await db.scalars(select(Material).options(joinedload(Material.batch))
                                  .order_by(Material.id.desc()).limit(300))).unique().all()
    stats = dict((await db.execute(select(KnowledgeChunk.source_type, func.count())
                                   .group_by(KnowledgeChunk.source_type))).all())
    semantic = int(await db.scalar(select(func.count()).select_from(KnowledgeChunk)
                                   .where(KnowledgeChunk.embedding.is_not(None))) or 0)
    batches = (await db.scalars(select(Batch).order_by(Batch.name))).all()
    hits = []
    if q.strip():
        b = await db.get(Batch, batch) if batch else None
        hits = await rag.search(db, q.strip(), batch_ids=[b.id] if b else None,
                                class_level=b.class_level if b else None, subject=b.subject if b else None, k=8)
    return render(request, "admin/knowledge.html", {"materials": materials, "stats": stats, "semantic": semantic,
                                                    "batches": batches, "q": q, "batch": batch, "hits": hits})


@router.post("/knowledge/upload")
async def upload(request: Request, db: AsyncSession = Depends(get_db), admin: Principal = Depends(require_admin),
                 title: str = Form(...), batch_id: int = Form(0), class_level: str = Form(""),
                 subject: str = Form(""), text: str = Form(""), file: UploadFile | None = File(None)):
    batch = await db.get(Batch, batch_id) if batch_id else None
    try:
        if has_file(file):
            rel, size = await save_upload(file, "materials", MATERIAL_EXT, 40)
            original, mime = file.filename, file.content_type
        elif len(text.strip()) >= 20:
            rel = save_bytes(text.strip().encode("utf-8"), "materials", ".txt")
            size, original, mime = len(text.encode("utf-8")), "pasted-notes.txt", "text/plain"
        else:
            raise UploadError("Upload a file or paste at least a few lines of notes.")
    except UploadError as exc:
        flash(request, str(exc), "danger")
        return redirect("/admin/knowledge")
    material = Material(title=title.strip()[:200] or "Untitled", batch_id=batch.id if batch else None,
                        class_level=(class_level.strip() or (batch.class_level if batch else None)),
                        subject=(subject.strip() or (batch.subject if batch else None)), file_path=rel,
                        original_name=(original or "")[:255], mime=mime, size_bytes=size,
                        uploaded_by=admin.username)
    db.add(material)
    await db.commit()
    spawn(process_material(material.id), name=f"material:{material.id}")
    flash(request, "Material uploaded — it's being read and indexed for AI tests and the doubt solver.", "success")
    return redirect("/admin/knowledge")


@router.post("/knowledge/{mid}/delete")
async def delete_material(mid: int, request: Request, db: AsyncSession = Depends(get_db)):
    material = await db.get(Material, mid)
    if material is None:
        raise HTTPException(404)
    await rag.remove_document(db, "material", mid)
    path = material.file_path
    await db.delete(material)
    await db.commit()
    delete_file(path)
    flash(request, "Material removed from the knowledge base.", "success")
    return redirect("/admin/knowledge")


@router.post("/knowledge/reindex")
async def reindex(request: Request):
    spawn(reindex_all(), name="reindex")
    flash(request, "Re-indexing the whole knowledge base in the background.", "info")
    return redirect("/admin/knowledge")
