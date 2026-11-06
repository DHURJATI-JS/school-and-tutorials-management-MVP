"""Upload handling: extension allow-lists, size limits, random file names, image re-encoding
(strips EXIF/GPS metadata) and path-traversal-safe resolution."""
from __future__ import annotations

import base64
import binascii
import io
import mimetypes
import re
import uuid
from pathlib import Path

from anyio import to_thread
from fastapi import UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError

from ..config import settings

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
DOC_EXT = IMAGE_EXT | {".pdf"}
VIDEO_EXT = {".mp4", ".webm", ".mov", ".m4v", ".ogv"}
MATERIAL_EXT = {".pdf", ".txt", ".md"} | IMAGE_EXT
SUBMISSION_EXT = DOC_EXT | {".txt", ".docx", ".pptx", ".xlsx", ".zip"}
CATEGORIES = ("photos", "idproofs", "idcards", "videos", "recordings", "materials", "receipts", "tasks",
              "submissions", "site", "doubts")
DATA_URL_RE = re.compile(r"^data:image/(png|jpeg|jpg|webp);base64,([A-Za-z0-9+/=\s]+)$")


MAX_IMAGE_PIXELS = 50_000_000  # ~50 MP (8160x6120): every real photo; far below "decompression bombs"
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS  # Pillow raises DecompressionBombError above 2x this


def open_image(data: bytes, max_side: int) -> Image.Image:
    """Open an untrusted image safely: verify it, refuse huge pixel counts BEFORE decoding (a 6 MB PNG can
    declare ~170 MP = ~0.5 GB of RAM, which Pillow only warns about) and let JPEGs decode at reduced scale."""
    try:
        Image.open(io.BytesIO(data)).verify()
        img = Image.open(io.BytesIO(data))
        if img.width * img.height > MAX_IMAGE_PIXELS:
            raise UploadError("That image's resolution is too large - please use a normal photo (under 50 MP).")
        if img.format == "JPEG":
            img.draft("RGB", (max_side * 2, max_side * 2))  # DCT scaling: fast, low memory
        img.load()
        return img
    except UploadError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise UploadError("That file is not a valid image.") from exc


def decode_frame(data_url: str, max_side: int = 1280) -> bytes:
    """Validate a browser-captured video frame (data URL) and re-encode it as a clean JPEG."""
    match = DATA_URL_RE.match(data_url or "")
    if not match:
        raise UploadError("Invalid screenshot data.")
    try:
        raw = base64.b64decode(match.group(2), validate=False)
    except (ValueError, binascii.Error) as exc:
        raise UploadError("Invalid screenshot data.") from exc
    if len(raw) > 6 * 1024 * 1024:
        raise UploadError("The screenshot is too large.")
    img = open_image(raw, max_side).convert("RGB")
    img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=82, optimize=True)
    return out.getvalue()


class UploadError(ValueError):
    pass


def ensure_dirs() -> None:
    for category in CATEGORIES:
        (settings.upload_root / category).mkdir(parents=True, exist_ok=True)


def abs_path(rel: str) -> Path:
    root = settings.upload_root.resolve()
    path = (root / rel).resolve()
    if root != path and root not in path.parents:
        raise UploadError("Invalid file path")
    return path


def has_file(upload: UploadFile | None) -> bool:
    return bool(upload is not None and upload.filename)


def guess_mime(rel: str) -> str:
    return mimetypes.guess_type(rel)[0] or "application/octet-stream"


def _ext(filename: str | None) -> str:
    return Path(filename or "").suffix.lower()


async def save_upload(upload: UploadFile | None, category: str, allowed: set[str], max_mb: int | None = None
                      ) -> tuple[str, int]:
    if not has_file(upload):
        raise UploadError("Please choose a file.")
    assert upload is not None
    ext = _ext(upload.filename)
    if ext not in allowed:
        raise UploadError(f"Unsupported file type “{ext or '?'}”. Allowed: {', '.join(sorted(allowed))}.")
    limit_mb = max_mb or settings.MAX_UPLOAD_MB
    rel = f"{category}/{uuid.uuid4().hex}{ext}"
    dest = abs_path(rel)
    dest.parent.mkdir(parents=True, exist_ok=True)

    def copy() -> int:
        size = 0
        upload.file.seek(0)
        with dest.open("wb") as out:
            while chunk := upload.file.read(1024 * 1024):
                size += len(chunk)
                if size > limit_mb * 1024 * 1024:
                    raise UploadError(f"The file is larger than {limit_mb} MB.")
                out.write(chunk)
        return size

    try:
        size = await to_thread.run_sync(copy)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    if size == 0:
        dest.unlink(missing_ok=True)
        raise UploadError("The file is empty.")
    return rel, size


async def save_image(upload: UploadFile | None, category: str, max_side: int = 900, keep_png: bool = False) -> str:
    if not has_file(upload):
        raise UploadError("Please choose an image.")
    assert upload is not None
    if _ext(upload.filename) not in IMAGE_EXT:
        raise UploadError("Please upload a JPG, PNG or WebP image.")
    data = await upload.read()
    if len(data) > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise UploadError(f"The image is larger than {settings.MAX_UPLOAD_MB} MB.")
    return await to_thread.run_sync(_store_image, data, category, max_side, keep_png)


def _store_image(data: bytes, category: str, max_side: int, keep_png: bool = False) -> str:
    """Re-encode the image (drops EXIF/GPS and any embedded payloads)."""
    img = open_image(data, max_side)
    try:
        img = ImageOps.exif_transpose(img).convert("RGBA" if keep_png else "RGB")
    except (OSError, ValueError) as exc:
        raise UploadError("That file is not a valid image.") from exc
    img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    rel = f"{category}/{uuid.uuid4().hex}.{'png' if keep_png else 'jpg'}"
    dest = abs_path(rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if keep_png:
        img.save(dest, "PNG", optimize=True)
    else:
        img.save(dest, "JPEG", quality=88, optimize=True)
    return rel


def probe_mp4_duration(path: Path) -> int | None:
    """Seconds of an MP4 / MOV / M4V read from its 'moov/mvhd' box - only box headers are read, so it is fast
    even for multi-GB files. Returns None for other formats or damaged files."""
    import struct

    def scan(f, start: int, end: int, depth: int) -> int | None:
        pos = start
        while pos + 8 <= end:
            f.seek(pos)
            header = f.read(8)
            if len(header) < 8:
                return None
            size, kind = struct.unpack(">I4s", header)
            head = 8
            if size == 1:
                size, head = struct.unpack(">Q", f.read(8))[0], 16
            elif size == 0:
                size = end - pos
            if size < head:
                return None
            if kind == b"moov" and depth == 0:
                return scan(f, pos + head, pos + size, 1)
            if kind == b"mvhd" and depth == 1:
                body = f.read(32)
                if len(body) < 20:
                    return None
                if body[0] == 1 and len(body) >= 32:
                    timescale, length = struct.unpack(">IQ", body[20:32])
                else:
                    timescale, length = struct.unpack(">II", body[12:20])
                seconds = round(length / timescale) if timescale else 0
                return seconds if 1 <= seconds <= 24 * 3600 else None
            pos += size
        return None

    try:
        with path.open("rb") as f:
            return scan(f, 0, path.stat().st_size, 0)
    except (OSError, ValueError, struct.error):
        return None


def save_bytes(data: bytes, category: str, ext: str) -> str:
    rel = f"{category}/{uuid.uuid4().hex}{ext}"
    dest = abs_path(rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return rel


def delete_file(rel: str | None) -> None:
    if not rel:
        return
    try:
        abs_path(rel).unlink(missing_ok=True)
    except (UploadError, OSError):
        pass
