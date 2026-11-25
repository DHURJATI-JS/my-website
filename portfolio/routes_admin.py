"""Admin panel: login (credentials from .env), AI categorisation, research cards, profile editor."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Literal
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from . import ai, backup, categories, jobs, lockout, occasions, ratelimit as rl, research, security, views
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


BLOCKED_MESSAGE = ("This network has been blocked after 3 wrong passwords. "
                   "Only the site owner can unblock it.")


def _login_page(request: Request, error: str = "", status: int = 200, blocked: bool = False) -> Response:
    token = security.new_token()
    response = render(request, "admin/login.html", {
        "page": "admin", "csrf": token, "error": error, "configured": security.admin_configured(),
        "blocked": blocked, "max_failures": lockout.MAX_FAILURES,
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
    if lockout.is_blocked(rl.client_ip(request)):
        return _login_page(request, BLOCKED_MESSAGE, 403, blocked=True)
    return _login_page(request)


@router.post("/login")
@limiter.limit(rl.LOGIN)
async def login_submit(request: Request):
    body = await request.body()
    if len(body) > 4096:
        raise HTTPException(status_code=413, detail="Request too large")
    form = {key: values[0] for key, values in parse_qs(body.decode("utf-8", "replace")).items()}
    ip = rl.client_ip(request)
    if lockout.is_blocked(ip):  # checked before the password is even looked at
        return _login_page(request, BLOCKED_MESSAGE, 403, blocked=True)
    if not security.tokens_match(form.get("csrf"), request.cookies.get(security.LOGIN_CSRF_COOKIE)) \
            or not security.same_origin(request):
        return _login_page(request, "The login form expired. Please try again.", 400)
    if not security.admin_configured():
        return _login_page(request, "Admin login is disabled: set ADMIN_USERNAME and a strong ADMIN_PASSWORD "
                                    "(8+ characters) in .env, then restart.", 403)
    username = form.get("username", "")[:120]
    if not security.verify_credentials(username, form.get("password", "")[:256]):
        try:
            result = lockout.record_failure(ip, request.headers.get("user-agent", ""))
        except Exception:  # noqa: BLE001 - storage down: still refuse the login
            result = {"blocked": False, "left": None}
        if result["blocked"]:
            return _login_page(request, BLOCKED_MESSAGE, 403, blocked=True)
        left = result.get("left")
        warning = f" {left} attempt{'s' if left != 1 else ''} left before this network is blocked." if left else ""
        return _login_page(request, "Wrong username or password." + warning, 401)
    try:
        lockout.clear_failures(ip)
    except Exception:  # noqa: BLE001
        pass
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
        "page": "admin", "csrf": session["csrf"], "state": views.admin_state(rl.client_ip(request)),
        "admin_user": session["u"],
    })


# ---------------------------------------------------------------- JSON API --
class NameBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class OverrideBody(NameBody):
    # social / industry / other or one of your custom categories (checked in the route)
    category: str = Field(min_length=1, max_length=30, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    locked: bool = True


class CategoryBody(BaseModel):
    key: str = Field("", max_length=30, pattern=r"^([a-z0-9]+(?:-[a-z0-9]+)*)?$")  # empty = new
    name: str = Field(min_length=1, max_length=40)
    icon: str = Field(min_length=1, max_length=40)
    color: str = Field(min_length=7, max_length=7, pattern=r"^#[0-9a-fA-F]{6}$")
    description: str = Field("", max_length=160)


class CategoryKeyBody(BaseModel):
    key: str = Field(min_length=1, max_length=30, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    step: int = Field(0, ge=-1, le=1)


class TranslateBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    lang: str = Field(min_length=2, max_length=2, pattern=r"^[a-z]{2}$")


class TranslatedItem(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    lang: str = Field(min_length=2, max_length=2, pattern=r"^[a-z]{2}$")
    result: dict  # checked field by field in translate.clean_result


class TranslateSaveBody(BaseModel):
    items: list[TranslatedItem] = Field(min_length=1, max_length=40)


class UnblockBody(BaseModel):
    ip: str = Field(min_length=1, max_length=64)  # "*" = everyone


_KEY = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
_DATE_OR_EMPTY = r"^(\d{4}-\d{2}-\d{2})?$"


class OccasionSettingsBody(BaseModel):
    mode: Literal["auto", "forced", "off"]
    forced: str = Field("", max_length=40)
    until: str = Field("", max_length=10, pattern=_DATE_OR_EMPTY)
    home_fallback: bool = True


class FestivalBody(BaseModel):
    key: str = Field(min_length=1, max_length=40, pattern=_KEY)
    enabled: bool = True
    greeting: str = Field("", max_length=160)
    image: str = Field("", max_length=600)
    effect: str = Field("", max_length=20)


class CustomOccasionBody(BaseModel):
    key: str = Field("", max_length=40, pattern=r"^(my-[a-f0-9]{8})?$")  # empty = new
    name: str = Field(min_length=1, max_length=60)
    emoji: str = Field("🎉", max_length=8)
    start: str = Field(min_length=10, max_length=10, pattern=_DATE_OR_EMPTY)
    end: str = Field("", max_length=10, pattern=_DATE_OR_EMPTY)
    yearly: bool = False
    greeting: str = Field("", max_length=160)
    image: str = Field("", max_length=600)
    effect: str = Field("confetti", max_length=20)
    color: str = Field("#f59e0b", max_length=7, pattern=r"^#[0-9a-fA-F]{6}$")
    enabled: bool = True


class OccasionKeyBody(BaseModel):
    key: str = Field(min_length=1, max_length=40, pattern=r"^my-[a-f0-9]{8}$")


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


ValidatorKind = Literal[tuple(research.VALIDATOR_KINDS)]  # type: ignore[valid-type]


class ValidatorBody(BaseModel):
    kind: ValidatorKind = "other"
    name: str = Field(min_length=1, max_length=120)
    organisation: str = Field("", max_length=160)
    link: str = Field("", max_length=300)


class ResearchBody(BaseModel):
    id: str = Field("", max_length=24, pattern=r"^[a-f0-9]*$")  # empty = new card
    repo: str = Field(min_length=1, max_length=120)
    topic: str = Field("", max_length=160)
    validators: list[ValidatorBody] = Field(min_length=1, max_length=research.MAX_VALIDATORS)
    usage: str = Field("", max_length=2000)
    where: str = Field("", max_length=200)
    when: str = Field("", max_length=10, pattern=r"^(\d{4}-\d{2}-\d{2})?$")


class ResearchToggleBody(BaseModel):
    enabled: bool


class ResearchIdBody(BaseModel):
    id: str = Field(min_length=1, max_length=24, pattern=r"^[a-f0-9]+$")


def _bad_gateway(exc: Exception) -> HTTPException:
    return HTTPException(status_code=502, detail=str(exc))


@router.get("/api/state")
@limiter.limit(rl.ADMIN)
def api_state(request: Request, session: dict = Depends(require_admin)):
    return views.admin_state(rl.client_ip(request))


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
        if body.category not in categories.keys(doc):
            raise ValueError("Unknown category")
        _, entry = jobs.find_repo(doc, body.name)
        base = dict(entry.get("ai") or ai.heuristic(entry))
        base.update(category=body.category, manual=True, locked=body.locked, categorized_at=now_iso())
        entry["ai"] = base

    try:
        store.mutate(apply)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown repository") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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


@router.post("/api/research/toggle")
@limiter.limit(rl.ADMIN)
def api_research_toggle(request: Request, body: ResearchToggleBody, session: dict = Depends(require_csrf)):
    def apply(doc: dict) -> None:
        doc["research"] = {**research.section(doc), "enabled": body.enabled}

    store.mutate(apply)
    return {"research": views.admin_research(store.get())}


@router.post("/api/research/save")
@limiter.limit(rl.ADMIN)
def api_research_save(request: Request, body: ResearchBody, session: dict = Depends(require_csrf)):
    visible = {c["name"].lower(): c["name"] for c in views.project_cards()}
    repo = visible.get(body.repo.strip().lower())
    if not repo:
        # Only repositories shown on the site can be picked - never private, deleted or hidden ones.
        raise HTTPException(status_code=422, detail="Pick a public repository that is shown on the site.")
    validators = []
    for v in body.validators:
        name = v.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Every validator needs a name.")
        link = safe_url(v.link)
        if v.link.strip() and not link:
            raise HTTPException(status_code=422, detail=f"{name}: the proof link must be a full https:// address.")
        validators.append({"kind": v.kind, "name": name, "organisation": v.organisation.strip(), "link": link})
    record = {"repo": repo, "topic": body.topic.strip(), "validators": validators,
              "usage": body.usage.strip(), "where": body.where.strip(), "when": body.when}

    def apply(doc: dict) -> None:
        section = research.section(doc)
        items = research.entries(doc)
        clash = next((e for e in items if e["repo"].lower() == repo.lower() and e["id"] != body.id), None)
        if clash:
            raise HTTPException(status_code=409, detail=f"{repo} already has a research card - edit that one instead.")
        stamp = now_iso()
        if body.id:
            current = next((e for e in items if e["id"] == body.id), None)
            if current is None:
                raise HTTPException(status_code=404, detail="That research card no longer exists - reload the page.")
            current.update(record, updated_at=stamp)
        else:
            if len(items) >= research.MAX_ENTRIES:
                raise HTTPException(status_code=422, detail=f"You can have up to {research.MAX_ENTRIES} research cards.")
            items.append({"id": research.new_id(), **record, "created_at": stamp, "updated_at": stamp})
        doc["research"] = {**section, "entries": items}

    store.mutate(apply)
    return {"research": views.admin_research(store.get())}


@router.post("/api/research/delete")
@limiter.limit(rl.ADMIN)
def api_research_delete(request: Request, body: ResearchIdBody, session: dict = Depends(require_csrf)):
    def apply(doc: dict) -> None:
        items = research.entries(doc)
        kept = [e for e in items if e["id"] != body.id]
        if len(kept) == len(items):
            raise HTTPException(status_code=404, detail="That research card no longer exists - reload the page.")
        doc["research"] = {**research.section(doc), "entries": kept}

    store.mutate(apply)
    return {"research": views.admin_research(store.get())}



# --------------------------------------------------------------- categories --
def _mutate_or_400(fn):
    try:
        return store.mutate(fn)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Not found: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/categories/save")
@limiter.limit(rl.ADMIN)
def api_category_save(request: Request, body: CategoryBody, session: dict = Depends(require_csrf)):
    key = _mutate_or_400(lambda doc: categories.upsert(doc, key=body.key, name=body.name, icon=body.icon,
                                                        color=body.color, description=body.description))
    return {"key": key, "state": views.admin_state()}


@router.post("/api/categories/delete")
@limiter.limit(rl.ADMIN)
def api_category_delete(request: Request, body: CategoryKeyBody, session: dict = Depends(require_csrf)):
    moved = _mutate_or_400(lambda doc: categories.delete(doc, body.key))
    return {"moved": moved, "state": views.admin_state()}


@router.post("/api/categories/move")
@limiter.limit(rl.ADMIN)
def api_category_move(request: Request, body: CategoryKeyBody, session: dict = Depends(require_csrf)):
    _mutate_or_400(lambda doc: categories.move(doc, body.key, body.step))
    return {"state": views.admin_state()}


# ------------------------------------------------------------- translations --
@router.get("/api/translate/plan")
@limiter.limit(rl.ADMIN)
def api_translate_plan(request: Request, session: dict = Depends(require_admin)):
    return {"jobs": jobs.translation_plan()}


@router.post("/api/translate")
@limiter.limit(rl.ADMIN)
def api_translate(request: Request, body: TranslateBody, session: dict = Depends(require_csrf)):
    """Translate one project into one language and return it - NOT saved yet (see /api/translate/save)."""
    try:
        item = jobs.translate_preview(body.name, body.lang)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown repository") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ai.AIUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"AI unavailable - {exc}") from exc
    return {"item": item}


@router.post("/api/translate/save")
@limiter.limit(rl.ADMIN)
def api_translate_save(request: Request, body: TranslateSaveBody, session: dict = Depends(require_csrf)):
    """Save a batch of translations in ONE write (the Gist API rate-limits many small saves)."""
    try:
        result = jobs.save_translations([item.model_dump() for item in body.items])
    except StoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {**result, "state": views.admin_state(rl.client_ip(request))}


# ---------------------------------------------------------------- festivals --
@router.post("/api/occasions/settings")
@limiter.limit(rl.ADMIN)
def api_occasion_settings(request: Request, body: OccasionSettingsBody, session: dict = Depends(require_csrf)):
    _mutate_or_400(lambda doc: occasions.save_settings(doc, mode=body.mode, forced=body.forced, until=body.until,
                                                       home_fallback=body.home_fallback))
    return {"state": views.admin_state()}


@router.post("/api/occasions/festival")
@limiter.limit(rl.ADMIN)
def api_occasion_festival(request: Request, body: FestivalBody, session: dict = Depends(require_csrf)):
    _mutate_or_400(lambda doc: occasions.save_festival(doc, key=body.key, enabled=body.enabled,
                                                       greeting=body.greeting, image=body.image, effect=body.effect))
    return {"state": views.admin_state()}


@router.post("/api/occasions/custom")
@limiter.limit(rl.ADMIN)
def api_occasion_custom(request: Request, body: CustomOccasionBody, session: dict = Depends(require_csrf)):
    key = _mutate_or_400(lambda doc: occasions.save_custom(
        doc, key=body.key, name=body.name, emoji=body.emoji, start=body.start, end=body.end, yearly=body.yearly,
        greeting=body.greeting, image=body.image, effect=body.effect, color=body.color, enabled=body.enabled))
    return {"key": key, "state": views.admin_state()}


@router.post("/api/occasions/custom/delete")
@limiter.limit(rl.ADMIN)
def api_occasion_custom_delete(request: Request, body: OccasionKeyBody, session: dict = Depends(require_csrf)):
    _mutate_or_400(lambda doc: occasions.delete_custom(doc, body.key))
    return {"state": views.admin_state()}


@router.post("/api/occasions/refresh")
@limiter.limit("10/minute")
def api_occasion_refresh(request: Request, session: dict = Depends(require_csrf)):
    """Check Google Calendar now and load the next 5 years of festival dates."""
    try:
        report = jobs.sync_occasions()
    except StoreError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save the JSON: {exc}") from exc
    return {"report": report, "state": views.admin_state()}


# ----------------------------------------------------------------- security --
@router.post("/api/security/unblock")
@limiter.limit(rl.ADMIN)
def api_unblock(request: Request, body: UnblockBody, session: dict = Depends(require_csrf)):
    removed = lockout.unblock("*" if body.ip == "*" else body.ip.strip())
    return {"removed": removed, "state": views.admin_state(rl.client_ip(request))}



# ------------------------------------------------------------------- backup --
# Read-only: lists repositories/branches with the server's token (never sent to the browser)
# and hands out github.com links and a git script that contain no secrets. Nothing is stored.
@router.get("/api/backup/list")
@limiter.limit("20/minute")
def api_backup_list(request: Request, session: dict = Depends(require_admin)):
    try:
        return backup.list_repos()
    except backup.BackupError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/backup/github-backup.{kind}")
@limiter.limit("20/minute")
def backup_script(request: Request, kind: str, session: dict = Depends(require_admin)):
    if kind not in ("cmd", "sh"):
        raise HTTPException(status_code=404, detail="Unknown script")
    try:
        data = backup.list_repos()
    except backup.BackupError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    text = backup.script_cmd(data) if kind == "cmd" else backup.script_sh(data)
    return Response(text.encode("utf-8"), media_type="text/plain; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="github-backup.{kind}"',
        "Cache-Control": "no-store",
    })


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
