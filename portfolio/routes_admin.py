"""Admin panel: login (credentials from .env), AI categorisation, profile editor."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Literal
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from . import ai, jobs, ratelimit as rl, security, views
from .config import settings
from .github import GitHubError, safe_url
from .ratelimit import limiter
from .store import StoreError, now_iso, store
from .web import render

router = APIRouter(prefix="/admin", include_in_schema=False)


class AuthRequired(Exception):
    """Raised when an admin-only route is hit without a valid session."""


def current_session(request: Request) -> dict | None:
    return security.read_session(request.cookies.get(security.SESSION_COOKIE))


def require_admin(request: Request) -> dict:
    session = current_session(request)
    if not session:
        raise AuthRequired()
    return session


def require_csrf(request: Request, session: dict = Depends(require_admin)) -> dict:
    if not security.tokens_match(request.headers.get("x-csrf-token"), session.get("csrf")) \
            or not security.same_origin(request):
        raise HTTPException(status_code=403, detail="Security check failed - reload the page and try again.")
    return session


def _cookie_kwargs(request: Request, path: str = "/admin") -> dict:
    return {"httponly": True, "samesite": "strict", "secure": security.request_is_https(request), "path": path}


def _login_page(request: Request, error: str = "", status: int = 200) -> Response:
    token = security.new_token()
    response = render(request, "admin/login.html", {
        "page": "admin", "csrf": token, "error": error, "configured": security.admin_configured(),
    }, status=status)
    response.set_cookie(security.LOGIN_CSRF_COOKIE, token, max_age=1800, **_cookie_kwargs(request))
    return response


@router.get("", include_in_schema=False)
@limiter.limit(rl.ADMIN)
def admin_root(request: Request):
    return RedirectResponse("/admin/dashboard" if current_session(request) else "/admin/login", status_code=303)


@router.get("/login", response_class=HTMLResponse)
@limiter.limit("30/minute")
def login_page(request: Request):
    if current_session(request):
        return RedirectResponse("/admin/dashboard", status_code=303)
    return _login_page(request)


@router.post("/login")
@limiter.limit(rl.LOGIN)
async def login_submit(request: Request):
    body = await request.body()
    if len(body) > 4096:
        raise HTTPException(status_code=413, detail="Request too large")
    form = {key: values[0] for key, values in parse_qs(body.decode("utf-8", "replace")).items()}
    if not security.tokens_match(form.get("csrf"), request.cookies.get(security.LOGIN_CSRF_COOKIE)) \
            or not security.same_origin(request):
        return _login_page(request, "The login form expired. Please try again.", 400)
    if not security.admin_configured():
        return _login_page(request, "Admin login is disabled: set ADMIN_USERNAME and a strong ADMIN_PASSWORD "
                                    "(8+ characters) in .env, then restart.", 403)
    username = form.get("username", "")[:120]
    if not security.verify_credentials(username, form.get("password", "")[:256]):
        return _login_page(request, "Wrong username or password.", 401)
    response = RedirectResponse("/admin/dashboard", status_code=303)
    response.delete_cookie(security.SESSION_COOKIE, path="/admin")  # older builds scoped it to /admin
    # Path "/" so the public navbar can show "Dashboard" instead of "Login" (still HttpOnly + SameSite=Strict).
    response.set_cookie(security.SESSION_COOKIE, security.create_session(username),
                        max_age=settings.session_hours * 3600, **_cookie_kwargs(request, "/"))
    response.delete_cookie(security.LOGIN_CSRF_COOKIE, path="/admin")
    return response


@router.post("/logout")
@limiter.limit(rl.ADMIN)
def logout(request: Request, session: dict = Depends(require_csrf)):
    response = JSONResponse({"ok": True})
    response.delete_cookie(security.SESSION_COOKIE, path="/")
    response.delete_cookie(security.SESSION_COOKIE, path="/admin")
    return response


@router.get("/dashboard", response_class=HTMLResponse)
@limiter.limit(rl.ADMIN)
def dashboard(request: Request, session: dict = Depends(require_admin)):
    return render(request, "admin/dashboard.html", {
        "page": "admin", "csrf": session["csrf"], "state": views.admin_state(), "admin_user": session["u"],
    })


# ---------------------------------------------------------------- JSON API --
class NameBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class OverrideBody(NameBody):
    category: Literal["social", "industry"]
    locked: bool = True


class VisibilityBody(NameBody):
    hidden: bool | None = None
    featured: bool | None = None


class SettingsBody(BaseModel):
    include_forks: bool


ShortText = Annotated[str, Field(max_length=40)]


class ProfileBody(BaseModel):
    name: str = Field("", max_length=80)
    headline: str = Field("", max_length=220)
    bio: str = Field("", max_length=3000)
    age: str = Field("", max_length=3, pattern=r"^\d{0,3}$")
    birth_date: str = Field("", max_length=10, pattern=r"^(\d{4}-\d{2}-\d{2})?$")
    location: str = Field("", max_length=120)
    education: str = Field("", max_length=160)
    role: str = Field("", max_length=120)
    availability: str = Field("", max_length=80)
    skills: list[ShortText] = Field(default_factory=list, max_length=40)
    interests: list[ShortText] = Field(default_factory=list, max_length=40)
    email: str = Field("", max_length=120, pattern=r"^([^@\s]+@[^@\s]+\.[^@\s]+)?$")
    whatsapp: str = Field("", max_length=24, pattern=r"^[+\d\s().-]*$")
    avatar_url: str = Field("", max_length=300)
    resume_url: str = Field("", max_length=300)
    website: str = Field("", max_length=300)
    linkedin: str = Field("", max_length=300)
    twitter: str = Field("", max_length=300)
    instagram: str = Field("", max_length=300)
    youtube: str = Field("", max_length=300)
    show_age: bool = True
    show_stats: bool = True
    show_activity: bool = True


def _bad_gateway(exc: Exception) -> HTTPException:
    return HTTPException(status_code=502, detail=str(exc))


@router.get("/api/state")
@limiter.limit(rl.ADMIN)
def api_state(request: Request, session: dict = Depends(require_admin)):
    return views.admin_state()


@router.post("/api/sync")
@limiter.limit("20/minute")
def api_sync(request: Request, session: dict = Depends(require_csrf)):
    try:
        report = jobs.sync_github()
    except GitHubError as exc:
        raise _bad_gateway(exc) from exc
    except StoreError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save the JSON: {exc}") from exc
    return {"report": report, "state": views.admin_state()}


@router.get("/api/plan")
@limiter.limit(rl.ADMIN)
def api_plan(request: Request, mode: str = "new", include_locked: bool = False,
             session: dict = Depends(require_admin)):
    if mode not in ("new", "all"):
        raise HTTPException(status_code=400, detail="mode must be 'new' or 'all'")
    return {"mode": mode, "repos": jobs.plan(mode, include_locked)}


@router.post("/api/categorize")
@limiter.limit(rl.ADMIN)
def api_categorize(request: Request, body: NameBody, session: dict = Depends(require_csrf)):
    try:
        result = jobs.categorize(body.name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown repository") from exc
    except ai.AIUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"AI unavailable - {exc}") from exc
    except StoreError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save the JSON: {exc}") from exc
    return {"ai": result, "repo": views.admin_repo(body.name)}


@router.post("/api/override")
@limiter.limit(rl.ADMIN)
def api_override(request: Request, body: OverrideBody, session: dict = Depends(require_csrf)):
    def apply(doc: dict) -> None:
        _, entry = jobs.find_repo(doc, body.name)
        base = dict(entry.get("ai") or ai.heuristic(entry))
        base.update(category=body.category, manual=True, locked=body.locked, categorized_at=now_iso())
        entry["ai"] = base

    try:
        store.mutate(apply)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown repository") from exc
    return {"repo": views.admin_repo(body.name)}


@router.post("/api/reset")
@limiter.limit(rl.ADMIN)
def api_reset(request: Request, body: NameBody, session: dict = Depends(require_csrf)):
    def apply(doc: dict) -> None:
        _, entry = jobs.find_repo(doc, body.name)
        entry["ai"] = None  # becomes "new" again -> analysed by the next "categorize new" run

    try:
        store.mutate(apply)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown repository") from exc
    return {"repo": views.admin_repo(body.name)}


@router.post("/api/visibility")
@limiter.limit(rl.ADMIN)
def api_visibility(request: Request, body: VisibilityBody, session: dict = Depends(require_csrf)):
    def apply(doc: dict) -> None:
        jobs.find_repo(doc, body.name)
        prefs = doc["settings"]
        for field, flag in (("hidden", body.hidden), ("featured", body.featured)):
            if flag is None:
                continue
            names = [n for n in prefs.get(field, []) if n.lower() != body.name.lower()]
            if flag:
                names.append(body.name)
            prefs[field] = names

    try:
        store.mutate(apply)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown repository") from exc
    return {"repo": views.admin_repo(body.name)}


@router.post("/api/settings")
@limiter.limit(rl.ADMIN)
def api_settings(request: Request, body: SettingsBody, session: dict = Depends(require_csrf)):
    store.mutate(lambda doc: doc["settings"].update(include_forks=body.include_forks))
    return views.admin_state()


@router.post("/api/profile")
@limiter.limit(rl.ADMIN)
def api_profile(request: Request, body: ProfileBody, session: dict = Depends(require_csrf)):
    data = body.model_dump()
    for field in ("avatar_url", "resume_url", "website"):
        data[field] = safe_url(data[field])
    for field in ("linkedin", "twitter", "instagram", "youtube"):
        value = data[field].strip()
        if value and not views.social_url(field, value):
            raise HTTPException(status_code=422, detail=f"{field}: enter a full https:// link or a plain handle")
        data[field] = value
    for field in ("skills", "interests"):
        data[field] = [item.strip() for item in data[field] if item.strip()]
    store.mutate(lambda doc: doc["profile"].update(data))
    return {"profile": store.get()["profile"]}


@router.get("/api/ai-status")
@limiter.limit("30/minute")
def api_ai_status(request: Request, session: dict = Depends(require_admin)):
    chain = ai.providers()
    with ThreadPoolExecutor(max_workers=max(1, len(chain))) as pool:
        return {"providers": list(pool.map(ai.ping, chain))}


@router.get("/export")
@limiter.limit("30/minute")
def export(request: Request, session: dict = Depends(require_admin)):
    text = json.dumps(store.get(), ensure_ascii=False, indent=2)
    return Response(text, media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="portfolio.json"'})
