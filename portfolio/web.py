"""Jinja2 setup and the shared render() helper."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from starlette.requests import Request
from starlette.responses import Response

from . import views
from .config import TEMPLATES_DIR, settings
from .i18n import LANGS, js_strings, resolve_lang, translator
from .security import SESSION_COOKIE, read_session, request_is_https
from .store import store

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.trim_blocks = True
templates.env.lstrip_blocks = True

MDB_VERSION = "9.3.0"
MDB_CSS_SRI = "sha384-GpFoyPQNL9Cd/Zc0GujAdo/6oVI34lnXg+7E8+rIhRo1kgxbujqXU1TlKwdc79mI"
MDB_JS_SRI = "sha384-wm149nUGAcjSR+VI0SefSbxQhUP3rVwkj4hZieXuXH8w6nibLuWruYYHb8tukbXT"


def static_url(path: str) -> str:
    return f"/static/{path}?v={settings.asset_version}"


def icon(name: str, cls: str = "") -> Markup:
    """Inline <svg><use> into the cached Line Awesome (Icons8) sprite."""
    extra = f" {escape(cls)}" if cls else ""
    return Markup(
        f'<svg class="ic{extra}" aria-hidden="true" focusable="false">'
        f'<use href="{static_url("icons.svg")}#i-{escape(name)}"></use></svg>'
    )


def i8(name: str, size: int = 94) -> str:
    """Icons8 3D Fluency icon URL."""
    return f"https://img.icons8.com/3d-fluency/{int(size)}/{escape(name)}.png"


templates.env.globals.update(
    static=static_url, icon=icon, i8=i8, mdb_version=MDB_VERSION, mdb_css_sri=MDB_CSS_SRI, mdb_js_sri=MDB_JS_SRI,
)


def base_context(request: Request, lang: str, explicit: bool) -> dict[str, Any]:
    doc = store.get()

    def link(path: str) -> str:
        if not explicit:
            return path
        joiner = "&" if "?" in path else "?"
        anchor = ""
        if "#" in path:
            path, anchor = path.split("#", 1)
            anchor = "#" + anchor
        return f"{path}{joiner}lang={lang}{anchor}"

    def lang_url(code: str) -> str:
        params = dict(request.query_params)
        params["lang"] = code
        return "?" + urlencode(params)

    profile = doc["profile"]
    return {
        "nav_stats": bool(profile.get("show_stats", True) and doc["github"].get("stats_images")),
        "nav_activity": bool(profile.get("show_activity", True)),
        "nav_research": bool(views.research_cards()),  # only while switched on AND something is shown
        "is_admin": read_session(request.cookies.get(SESSION_COOKIE)) is not None,
        "nonce": getattr(request.state, "csp_nonce", ""),
        "lang": lang,
        "langs": LANGS,
        "t": translator(lang),
        "link": link,
        "lang_url": lang_url,
        "me": views.identity(doc),
        "synced_at": doc.get("synced_at") or "",
        "year": datetime.now(timezone.utc).year,
        "js_strings": js_strings(lang),
        "site_url": settings.site_url,
        "page": "",
    }


def render(request: Request, name: str, context: dict[str, Any] | None = None, status: int = 200) -> Response:
    lang, explicit = resolve_lang(request)
    data = base_context(request, lang, explicit)
    if context:
        data.update(context)
    response = templates.TemplateResponse(request, name, data, status_code=status)
    if explicit:
        response.set_cookie("lang", lang, max_age=31_536_000, samesite="lax", httponly=True,
                            secure=request_is_https(request))
    return response
