"""Front-end asset pipeline: minify (rcssmin / rjsmin) + content-hash fingerprinting.

Sources live in app/static/css and app/static/js. On startup (or `python -m app.cli build-assets`)
each file is minified to app/static/dist/<name>.<hash>.min.<ext>; templates call asset('js/app.js').
Fingerprinted files are served with an immutable, 1-year cache header.
"""
from __future__ import annotations

import hashlib
import json
import logging

from .config import APP_DIR, settings

log = logging.getLogger("classnest.assets")

STATIC_DIR = APP_DIR / "static"
DIST_DIR = STATIC_DIR / "dist"
_manifest: dict[str, str] = {}


def _minify(ext: str, source: str) -> str:
    if not settings.MINIFY_ASSETS:
        return source
    try:
        if ext == ".css":
            import rcssmin

            return rcssmin.cssmin(source)
        if ext == ".js":
            import rjsmin

            return rjsmin.jsmin(source)
    except Exception:  # pragma: no cover - never break the site because of minification
        log.exception("Minification failed for %s asset - serving original", ext)
    return source


def build_assets() -> dict[str, str]:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, str] = {}
    keep: set[str] = {"manifest.json"}
    for folder, ext in (("css", ".css"), ("js", ".js")):
        for path in sorted((STATIC_DIR / folder).glob(f"*{ext}")):
            output = _minify(ext, path.read_text(encoding="utf-8"))
            digest = hashlib.sha256(output.encode("utf-8")).hexdigest()[:10]
            name = f"{path.stem}.{digest}.min{ext}"
            target = DIST_DIR / name
            if not target.exists():
                target.write_text(output, encoding="utf-8")
            keep.add(name)
            manifest[f"{folder}/{path.name}"] = f"dist/{name}"
    for old in DIST_DIR.iterdir():
        if old.is_file() and old.name not in keep:
            old.unlink(missing_ok=True)
    (DIST_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    _manifest.clear()
    _manifest.update(manifest)
    log.info("Asset pipeline: %d files minified=%s", len(manifest), settings.MINIFY_ASSETS)
    return manifest


def asset_url(rel: str) -> str:
    return "/static/" + _manifest.get(rel, rel)
