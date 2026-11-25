"""Public pages + a small read-only JSON API (CORS-enabled, iframe-friendly)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from . import ai, jobs, ratelimit as rl, views
from .config import settings
from .i18n import LANGS, resolve_lang
from .ratelimit import limiter
from .security import tokens_match
from .store import store
from .web import render

router = APIRouter()
_PUBLIC_CACHE = "public, max-age=120, s-maxage=300, stale-while-revalidate=600"


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
@limiter.limit(rl.PAGE)
def home(request: Request):
    lang, _ = resolve_lang(request)
    return render(request, "index.html", views.home(store.get(), lang))


@router.get("/issues", response_class=HTMLResponse, include_in_schema=False)
@limiter.limit(rl.PAGE)
def issues(request: Request, state: str = "open", repo: str = ""):
    state = state if state in ("open", "closed", "all") else "open"
    lang, _ = resolve_lang(request)
    return render(request, "issues.html", views.issues_view(store.get(), state, repo[:100], lang))


@router.get("/disclaimer", response_class=HTMLResponse, include_in_schema=False)
@limiter.limit(rl.PAGE)
def disclaimer(request: Request):
    return render(request, "disclaimer.html", {"page": "disclaimer"})


@router.get("/api/projects")
@limiter.limit(rl.API)
def api_projects(request: Request, lang: str = "en"):
    """All visible projects. ?lang=hi|mr|es|fr|de|ja|ru returns the AI text translated where available."""
    lang = lang if lang in LANGS else "en"
    data = {"synced_at": store.get().get("synced_at"), "lang": lang,
            "projects": [views.public_card(c) for c in views.cards_for(lang)]}
    return JSONResponse(data, headers={"Cache-Control": _PUBLIC_CACHE})


@router.get("/api/projects/{name}")
@limiter.limit(rl.API)
def api_project(request: Request, name: str, lang: str = "en"):
    detail = views.project_detail(name[:120], lang if lang in LANGS else "en")
    if detail is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return JSONResponse(detail, headers={"Cache-Control": _PUBLIC_CACHE})


@router.get("/api/research")
@limiter.limit(rl.API)
def api_research(request: Request):
    """Research-grade projects (empty while the section is switched off in /admin)."""
    items = [views.public_research(x) for x in views.research_cards()]
    return JSONResponse({"enabled": bool(items), "projects": items}, headers={"Cache-Control": _PUBLIC_CACHE})


@router.get("/api/profile")
@limiter.limit(rl.API)
def api_profile(request: Request):
    doc = store.get()
    me = views.identity(doc)
    profile = doc["profile"]
    data = {
        "name": me["name"], "avatar": me["avatar"], "headline": profile.get("headline", ""),
        "bio": profile.get("bio", ""), "age": views.compute_age(profile) if profile.get("show_age", True) else None,
        "location": profile.get("location", ""), "education": profile.get("education", ""),
        "skills": profile.get("skills", []), "email": me["email"], "whatsapp": me["whatsapp"],
        "github": me["github_url"], "links": me["socials"],
    }
    return JSONResponse(data, headers={"Cache-Control": _PUBLIC_CACHE})


@router.get("/api/profile-readme")
@limiter.limit(rl.API)
def api_profile_readme(request: Request):
    html = store.get()["github"].get("profile_readme_html", "")
    return JSONResponse({"html": html}, headers={"Cache-Control": _PUBLIC_CACHE})


@router.get("/api/ai-status")
@limiter.limit(rl.API)
def api_ai_status(request: Request):
    """Is the AI (gateway or Docker failsafe) online? Powers the navbar badge."""
    return JSONResponse(ai.public_status(), headers={"Cache-Control": "public, max-age=20, s-maxage=30"})


@router.get("/api/cron/sync", include_in_schema=False)
@limiter.limit(rl.CRON)
def cron_sync(request: Request):
    """Vercel Cron: sync GitHub + AI-categorize NEW repos (needs CRON_SECRET)."""
    expected = settings.cron_secret
    if not expected or not tokens_match(request.headers.get("authorization"), f"Bearer {expected}"):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return jobs.auto_run(budget_seconds=40)


@router.get("/healthz", include_in_schema=False)
@limiter.limit(rl.API)
def healthz(request: Request):
    return {"ok": True, "synced_at": store.get().get("synced_at")}


@router.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
@limiter.limit(rl.API)
def robots(request: Request):
    lines = ["User-agent: *", "Allow: /", "Disallow: /admin", "Disallow: /api/"]
    return "\n".join(lines) + "\n"


@router.get("/favicon.ico", include_in_schema=False)
@limiter.limit(rl.API)
def favicon(request: Request):
    return RedirectResponse("/static/favicon.svg", status_code=301)
