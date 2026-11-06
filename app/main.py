"""FastAPI application: middleware stack, routers, lifespan (DB, seed, caches, background jobs)."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import asyncpg
from sqlalchemy.exc import DataError, DBAPIError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from . import __version__
from .assets import STATIC_DIR, build_assets
from .cache import ban_cache, site_cache
from .config import settings
from .database import SessionLocal, engine, init_db
from .middleware import BodySizeLimitMiddleware, IPBanMiddleware, SecureHeadersMiddleware
from .routers import api, assistant, auth, chat, comments, files, meet, public, social, staff, student, webhooks
from .routers import admin as admin_router
from .routers.common import back, redirect
from .security import CSRFError, DuplicateSubmission, LoginRequired, admins
from .services import background, jobs, seed, storage
from .services.moderation import load_wordlists
from .templating import flash, render

log = logging.getLogger("classnest")


def setup_logging() -> None:
    logging.basicConfig(level=logging.DEBUG if settings.DEBUG else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    if settings.SECRET_KEY.startswith("insecure") or len(settings.SECRET_KEY) < 32:
        log.warning("SECRET_KEY is weak - set a long random value in .env")
    build_assets()
    storage.ensure_dirs()
    log.info("Moderation lists decoded: %d entries", load_wordlists())
    admins.load()
    await init_db()
    async with SessionLocal() as db:
        await seed.run(db)
    await ban_cache.refresh()
    await site_cache.refresh()
    tasks = [asyncio.create_task(jobs.cache_loop(), name="cache-loop")]
    if settings.JOBS_ENABLED:
        tasks.append(asyncio.create_task(jobs.scheduler_loop(), name="scheduler"))
    log.info("%s %s ready at %s (AI gateway: %s)", settings.APP_NAME, __version__, settings.BASE_URL,
             settings.OPENAI_BASE_URL)
    yield
    for task in tasks:
        task.cancel()
    await background.drain(5)
    await engine.dispose()


class CachedStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            fingerprinted = path.replace("\\", "/").startswith("dist/")  # Windows passes OS separators
            response.headers["Cache-Control"] = ("public, max-age=31536000, immutable" if fingerprinted
                                                 else "public, max-age=86400")
        return response


def is_bad_input(exc: DBAPIError) -> bool:
    """True for values PostgreSQL/asyncpg refuse (NUL bytes, numbers out of int32/int64 range, ...): SQLSTATE
    class 22 "data exception". asyncpg raises many of these while encoding parameters, before the server is
    reached, and SQLAlchemy then wraps them as a generic DBAPIError rather than DataError."""
    if isinstance(exc, DataError):
        return True
    for err in (exc.orig, getattr(exc.orig, "__cause__", None)):
        if isinstance(err, asyncpg.exceptions.DataError):
            return True
        if str(getattr(err, "sqlstate", "") or "").startswith("22"):
            return True
    return False


def wants_json(request: Request) -> bool:
    return (request.url.path.startswith(("/api/", "/webhooks/")) or request.headers.get("x-requested-with") == "fetch"
            or "application/json" in request.headers.get("accept", ""))


def create_app() -> FastAPI:
    app = FastAPI(title=settings.APP_NAME, version=__version__, lifespan=lifespan,
                  docs_url="/api/docs" if settings.DEBUG else None, redoc_url=None,
                  openapi_url="/api/openapi.json" if settings.DEBUG else None)
    # order: last added = outermost
    app.add_middleware(IPBanMiddleware)
    app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY, session_cookie="classnest_session",
                       max_age=settings.SESSION_MAX_AGE_HOURS * 3600, same_site="lax",
                       https_only=settings.SESSION_COOKIE_SECURE)
    app.add_middleware(GZipMiddleware, minimum_size=800)
    app.add_middleware(BodySizeLimitMiddleware)  # before sessions/parsing; inside the security headers
    app.add_middleware(SecureHeadersMiddleware, force_https=settings.SESSION_COOKIE_SECURE)
    app.mount("/static", CachedStaticFiles(directory=STATIC_DIR), name="static")
    for module in (public, auth, student, staff, meet, chat, social, assistant, comments, files, api, webhooks):
        app.include_router(module.router)
    app.include_router(admin_router.router)

    @app.exception_handler(LoginRequired)
    async def login_required(request: Request, exc: LoginRequired):
        if wants_json(request):
            return JSONResponse({"detail": "Please sign in."}, status_code=401)
        return redirect(f"/login?next={quote(exc.next_url)}")

    @app.exception_handler(CSRFError)
    async def csrf_error(request: Request, exc: CSRFError):
        if wants_json(request):
            return JSONResponse({"detail": "Your session expired — please refresh the page."}, status_code=403)
        flash(request, "Your session expired for security reasons — please try again.", "warning")
        return back(request)

    @app.exception_handler(DuplicateSubmission)
    async def duplicate(request: Request, exc: DuplicateSubmission):
        if wants_json(request):
            return JSONResponse({"detail": "Already submitted."}, status_code=409)
        flash(request, "That form was already submitted — the duplicate was ignored.", "info")
        return back(request)

    @app.exception_handler(RequestValidationError)
    async def validation(request: Request, exc: RequestValidationError):
        if wants_json(request):
            return JSONResponse({"detail": exc.errors()}, status_code=422)
        fields = sorted({str(e["loc"][-1]).replace("_", " ") for e in exc.errors() if e.get("loc")})
        flash(request, "Please check the form: " + ", ".join(fields[:6]) + ".", "danger")
        return back(request)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if wants_json(request):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        titles = {403: "Access denied", 404: "Page not found", 405: "Not allowed", 409: "Conflict",
                  413: "Too large", 429: "Slow down"}
        return render(request, "error.html", {"code": exc.status_code, "title": titles.get(exc.status_code, "Error"),
                                              "message": exc.detail if isinstance(exc.detail, str) else ""},
                      status_code=exc.status_code)

    @app.exception_handler(DBAPIError)
    async def database_error(request: Request, exc: DBAPIError):
        """Values PostgreSQL can't store/compare - NUL bytes, out-of-range numbers (e.g. /files/photo/9999...9) -
        are client errors (400), not server crashes. Anything else is a real bug: logged + 500 as before.
        The session is rolled back by get_db either way."""
        if not is_bad_input(exc):
            return await server_error(request, exc)
        log.warning("Rejected invalid input on %s: %s", request.url.path, str(exc.orig).splitlines()[0][:200])
        if wants_json(request):
            return JSONResponse({"detail": "Invalid input."}, status_code=400)
        return render(request, "error.html", {"code": 400, "title": "Invalid input",
                                              "message": "Some of the values sent were not valid."},
                      status_code=400)

    @app.exception_handler(Exception)
    async def server_error(request: Request, exc: Exception):
        log.exception("Unhandled error on %s", request.url.path)
        if wants_json(request):
            return JSONResponse({"detail": "Something went wrong on our side."}, status_code=500)
        return HTMLResponse("<!doctype html><title>Something went wrong</title><body style='font-family:system-ui;"
                            "display:grid;place-items:center;min-height:90vh'><div><h1>Something went wrong</h1>"
                            "<p>The error was logged. Please go back and try again.</p><p><a href='/'>Home</a></p>"
                            "</div></body>", status_code=500)

    return app


app = create_app()
