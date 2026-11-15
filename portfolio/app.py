"""FastAPI application factory."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware

from . import __version__, jobs
from .config import STATIC_DIR, settings
from .i18n import resolve_lang, translator
from .ratelimit import limiter
from .routes_admin import AuthRequired, router as admin_router
from .routes_public import router as public_router
from .security import TalismanMiddleware
from .web import render

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("portfolio")


def _wants_json(request: Request) -> bool:
    path = request.url.path
    return path.startswith(("/api/", "/admin/api/")) or "application/json" in request.headers.get("accept", "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    worker = None
    if settings.auto_sync and not settings.on_vercel:
        worker = jobs.AutoSync(settings.auto_sync_minutes)  # self-hosted: keep new repos categorised
        worker.start()
    yield
    if worker:
        worker.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Portfolio",
        version=__version__,
        lifespan=lifespan,
        docs_url="/admin/docs" if settings.enable_docs else None,
        redoc_url=None,
        openapi_url="/admin/openapi.json" if settings.enable_docs else None,
    )
    app.state.limiter = limiter

    @app.exception_handler(RateLimitExceeded)
    async def rate_limited(request: Request, exc: RateLimitExceeded):
        headers = {"Retry-After": "60"}
        if _wants_json(request) or request.method != "GET":
            return JSONResponse({"detail": "Too many requests - please slow down."}, status_code=429, headers=headers)
        message = translator(resolve_lang(request)[0])("too_many")
        response = render(request, "error.html", {"code": 429, "message": message}, status=429)
        response.headers.update(headers)
        return response

    @app.exception_handler(AuthRequired)
    async def auth_required(request: Request, exc: AuthRequired):
        if request.url.path.startswith("/admin/api/") or request.method != "GET":
            return JSONResponse({"detail": "Login required"}, status_code=401)
        return RedirectResponse("/admin/login", status_code=303)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if _wants_json(request) or request.method != "GET":
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                                headers=getattr(exc, "headers", None))
        t = translator(resolve_lang(request)[0])
        message = t("not_found") if exc.status_code == 404 else (
            exc.detail if isinstance(exc.detail, str) else t("error_title"))
        return render(request, "error.html", {"code": exc.status_code, "message": message}, status=exc.status_code)

    @app.exception_handler(Exception)
    async def crashed(request: Request, exc: Exception):
        log.exception("unhandled error on %s", request.url.path)
        if _wants_json(request):
            return JSONResponse({"detail": "Internal server error"}, status_code=500)
        try:
            t = translator(resolve_lang(request)[0])
            return render(request, "error.html", {"code": 500, "message": t("error_title")}, status=500)
        except Exception:  # noqa: BLE001
            return PlainTextResponse("Internal server error", status_code=500)

    # Last added = outermost. Talisman wraps everything so every response gets the headers.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET"],
        allow_headers=["Accept", "Content-Type"],
        allow_credentials=False,
        max_age=86400,
    )
    app.add_middleware(GZipMiddleware, minimum_size=800)
    app.add_middleware(TalismanMiddleware, frame_ancestors=settings.frame_ancestors)

    app.include_router(public_router)
    app.include_router(admin_router)

    # Vercel serves public/static straight from its CDN; mount it only for local runs.
    if not settings.on_vercel and STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app


app = create_app()
