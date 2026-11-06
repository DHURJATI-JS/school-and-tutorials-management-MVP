"""ClassNest OCR micro-service: Tesseract via pytesseract, with light image pre-processing.

POST /ocr   (multipart "file")  ->  {"text": "...", "variants": n}
GET  /health                     ->  {"status": "ok", "tesseract": "5.x"}
Protected by the X-OCR-Secret header when OCR_SHARED_SECRET is set.
"""
from __future__ import annotations

import hmac
import io
import os

import pytesseract
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError

SECRET = os.getenv("OCR_SHARED_SECRET", "")
MAX_BYTES = 10 * 1024 * 1024
app = FastAPI(title="ClassNest OCR", docs_url=None, redoc_url=None)


def variants(img: Image.Image) -> list[Image.Image]:
    gray = ImageOps.grayscale(ImageOps.exif_transpose(img))
    if gray.width < 1400:  # upscale small photos - Tesseract likes ~300 dpi text
        scale = 1400 / gray.width
        gray = gray.resize((int(gray.width * scale), int(gray.height * scale)), Image.Resampling.LANCZOS)
    sharp = gray.filter(ImageFilter.SHARPEN)
    contrast = ImageOps.autocontrast(sharp, cutoff=2)
    binary = contrast.point(lambda p: 255 if p > 150 else 0)
    return [contrast, binary]


@app.get("/health")
def health() -> dict:
    try:
        version = str(pytesseract.get_tesseract_version())
    except pytesseract.TesseractNotFoundError:
        raise HTTPException(503, "tesseract binary not found") from None
    return {"status": "ok", "tesseract": version}


@app.post("/ocr")
def ocr(file: UploadFile = File(...), x_ocr_secret: str | None = Header(default=None)) -> dict:
    if SECRET and not hmac.compare_digest(x_ocr_secret or "", SECRET):
        raise HTTPException(401, "bad secret")
    data = file.file.read(MAX_BYTES + 1)
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(413, "image too large or empty")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError):
        raise HTTPException(415, "not an image") from None
    texts = []
    for variant in variants(img):
        for psm in (6, 11):
            texts.append(pytesseract.image_to_string(variant, lang="eng", config=f"--oem 3 --psm {psm}"))
    return {"text": "\n".join(texts), "variants": len(texts)}
