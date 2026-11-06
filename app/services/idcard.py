"""Virtual ID card generator (Pillow). The ID code is printed large in a monospace font
so the Tesseract OCR login can read it reliably."""
from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

W, H = 1012, 638
FONTS = {
    "bold": ["DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf",
             "/System/Library/Fonts/Supplemental/Arial Bold.ttf"],
    "regular": ["DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
                "/System/Library/Fonts/Supplemental/Arial.ttf"],
    "mono": ["DejaVuSansMono-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
             "C:/Windows/Fonts/consolab.ttf", "C:/Windows/Fonts/courbd.ttf",
             "/System/Library/Fonts/Menlo.ttc"],
}


@lru_cache(maxsize=64)
def font(kind: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in FONTS[kind]:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return 79, 70, 229


def _fit(draw: ImageDraw.ImageDraw, text: str, kind: str, size: int, max_width: int):
    while size > 14:
        f = font(kind, size)
        if draw.textlength(text, font=f) <= max_width:
            return f
        size -= 2
    return font(kind, size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, f, max_width: int, max_lines: int = 2) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=f) <= max_width:
            line = trial
        else:
            if line:
                lines.append(line)
            line = word
        if len(lines) == max_lines:
            break
    if line and len(lines) < max_lines:
        lines.append(line)
    if len(lines) == max_lines and " ".join(lines) != text:
        lines[-1] = lines[-1].rstrip(".,") + "…"
    return lines


def render_id_card(*, name: str, id_code: str, role_line: str, details: list[tuple[str, str]],
                   photo_path: Path | None, site_name: str, primary: str = "#4f46e5", accent: str = "#06b6d4",
                   footer: str = "") -> bytes:
    card = Image.new("RGB", (W, H), (255, 255, 255))
    draw = ImageDraw.Draw(card)
    p, a = _rgb(primary), _rgb(accent)

    # gradient header
    header_h = 150
    for x in range(W):
        t = x / (W - 1)
        color = tuple(int(p[i] + (a[i] - p[i]) * t) for i in range(3))
        draw.line([(x, 0), (x, header_h)], fill=color)
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.ellipse([W - 260, -120, W + 80, 220], fill=(255, 255, 255, 34))
    od.ellipse([W - 140, 40, W + 60, 240], fill=(255, 255, 255, 24))
    card = Image.alpha_composite(card.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(card)

    draw.text((40, 34), site_name, font=_fit(draw, site_name, "bold", 42, W - 330), fill="white")
    draw.text((42, 92), "IDENTITY CARD", font=font("bold", 22), fill=(255, 255, 255))

    # photo
    px, py, pw, ph = 40, 186, 230, 290
    draw.rounded_rectangle([px - 6, py - 6, px + pw + 6, py + ph + 6], radius=22, fill=(255, 255, 255),
                           outline=(226, 232, 240), width=3)
    photo = None
    if photo_path and photo_path.exists():
        try:
            photo = ImageOps.fit(ImageOps.exif_transpose(Image.open(photo_path)).convert("RGB"), (pw, ph),
                                 Image.Resampling.LANCZOS)
        except OSError:
            photo = None
    if photo is None:
        photo = Image.new("RGB", (pw, ph), (241, 245, 249))
        initials = "".join(part[0] for part in name.split()[:2]).upper() or "?"
        ImageDraw.Draw(photo).text((pw / 2, ph / 2), initials, font=font("bold", 90), fill=p, anchor="mm")
    mask = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, pw, ph], radius=18, fill=255)
    card.paste(photo, (px, py), mask)

    # details
    x0, maxw = 310, W - 350
    draw.text((x0, 182), name, font=_fit(draw, name, "bold", 44, maxw), fill=(15, 23, 42))
    draw.text((x0, 238), role_line, font=_fit(draw, role_line, "regular", 26, maxw), fill=(100, 116, 139))
    draw.rounded_rectangle([x0 - 4, 284, x0 + 470, 360], radius=14, fill=(248, 250, 252),
                           outline=(203, 213, 225), width=2)
    draw.text((x0 + 14, 290), "ID NUMBER", font=font("bold", 16), fill=(100, 116, 139))
    draw.text((x0 + 14, 308), id_code, font=font("mono", 44), fill=(2, 6, 23))

    y = 378
    label_font, value_font = font("bold", 18), font("regular", 22)
    for label, value in details[:4]:
        draw.text((x0, y), label.upper(), font=label_font, fill=(100, 116, 139))
        lines = _wrap(draw, value or "—", value_font, maxw - 180, max_lines=2)
        for i, line in enumerate(lines):
            draw.text((x0 + 180, y - 3 + i * 26), line, font=value_font, fill=(15, 23, 42))
        y += 30 + 26 * (len(lines) - 1) + 6

    # footer
    draw.rectangle([0, H - 64, W, H], fill=(241, 245, 249))
    draw.rectangle([0, H - 64, W, H - 60], fill=p)
    footer = footer or f"Property of {site_name}. If found, please return to the office."
    draw.text((40, H - 44), footer, font=_fit(draw, footer, "regular", 20, W - 80), fill=(71, 85, 105))

    out = io.BytesIO()
    card.save(out, "PNG", optimize=True)
    return out.getvalue()
