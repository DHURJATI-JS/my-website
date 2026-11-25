"""Turn the JSON document into small view models (cached per JSON revision)."""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from . import ai, categories, lockout, occasions, research, translate
from .config import settings
from .github import safe_url
from .i18n import translator
from .sanitize import extract_banner
from .store import store

# Built-in groups. Custom ones (with their own icons) come from categories.py; "other" and every
# custom group are only ever chosen by hand in /admin.
GROUP_ICONS = dict(categories.BUILTIN_ICONS)
CATEGORY_KEYS = categories.BUILTIN
_SMALL_WORDS = {"and", "or", "of", "the", "a", "an", "for", "from", "to", "in", "on", "with", "by"}
_ACRONYMS = {"ai": "AI", "mvp": "MVP", "api": "API", "ui": "UI", "ux": "UX", "ml": "ML", "llm": "LLM",
             "hr": "HR", "crm": "CRM", "erp": "ERP", "sql": "SQL", "iot": "IoT", "ar": "AR", "vr": "VR",
             "js": "JS", "yt": "YT", "pdf": "PDF", "ocr": "OCR", "gpt": "GPT"}
_EVENT_ICONS = {"push": "code-branch", "create_repo": "plus", "create_ref": "plus", "star": "star",
                "fork": "code-branch", "issue": "dot-circle", "pr": "code-branch", "comment": "comment",
                "release": "tag", "public": "globe", "other": "bolt"}


def pretty_title(name: str) -> str:
    words = [w for w in re.split(r"[-_\s]+", name.strip()) if w]
    out = []
    for index, word in enumerate(words):
        lower = word.lower()
        if lower in _ACRONYMS:
            out.append(_ACRONYMS[lower])
        elif word != lower:
            out.append(word)  # keep CamelCase like IronPulse / AuraMind
        elif index and lower in _SMALL_WORDS:
            out.append(lower)
        else:
            out.append(lower[:1].upper() + lower[1:])
    return " ".join(out) or name


def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "-", name)


def lang_slug(language: str) -> str:
    text = (language or "").lower().replace("+", "p").replace("#", "sharp")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def compute_age(profile: dict[str, Any]) -> int | None:
    born = str(profile.get("birth_date") or "")
    if born:
        try:
            d = date.fromisoformat(born)
            today = date.today()
            return today.year - d.year - ((today.month, today.day) < (d.month, d.day))
        except ValueError:
            pass
    age = str(profile.get("age") or "").strip()
    return int(age) if age.isdigit() and 0 < int(age) < 130 else None


_SOCIAL_BASES = {
    "linkedin": "https://www.linkedin.com/in/{}",
    "twitter": "https://x.com/{}",
    "instagram": "https://www.instagram.com/{}",
    "youtube": "https://www.youtube.com/@{}",
}


def social_url(kind: str, value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if re.match(r"^https?://", value, re.I) or "." in value.split("/")[0]:
        return safe_url(value)
    handle = value.lstrip("@").strip("/")
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,60}", handle) and kind in _SOCIAL_BASES:
        return _SOCIAL_BASES[kind].format(handle)
    return ""


def identity(doc: dict[str, Any]) -> dict[str, Any]:
    profile = doc["profile"]
    user = doc["github"].get("user") or {}
    username = settings.github_username
    name = profile.get("name") or user.get("name") or user.get("login") or username
    whatsapp = profile.get("whatsapp") or settings.contact_whatsapp
    digits = re.sub(r"\D", "", whatsapp)
    socials = [{"key": "github", "icon": "github", "label": "GitHub", "url": f"https://github.com/{username}"}]
    for key, icon, label in (("linkedin", "linkedin", "LinkedIn"), ("twitter", "twitter", "X / Twitter"),
                             ("instagram", "instagram", "Instagram"), ("youtube", "youtube", "YouTube")):
        url = social_url(key, profile.get(key, ""))
        if url:
            socials.append({"key": key, "icon": icon, "label": label, "url": url})
    for key, icon, label in (("website", "globe", "Website"), ("resume_url", "file", "Resume")):
        url = safe_url(profile.get(key))
        if url:
            socials.append({"key": key, "icon": icon, "label": label, "url": url})
    return {
        "name": name,
        "first_name": name.split()[0] if name else username,
        "avatar": safe_url(profile.get("avatar_url")) or user.get("avatar_url")
        or f"https://github.com/{username}.png",
        "email": profile.get("email") or settings.contact_email,
        "whatsapp": whatsapp,
        "wa_link": f"https://wa.me/{digits}" if digits else "",
        "github_url": f"https://github.com/{username}",
        "feed_url": f"https://github.com/{username}.atom",
        "username": username,
        "socials": socials,
    }


# ------------------------------------------------------------------ projects --
_heuristic_cache: dict[tuple, dict[str, Any]] = {}
_cards_cache: dict[str, Any] = {"rev": None, "cards": []}


def _analysis(key: str, entry: dict[str, Any]) -> tuple[dict[str, Any], str]:
    current = entry.get("ai")
    if current:
        return current, "manual" if current.get("manual") else "ai"
    cache_key = (key, entry.get("readme_sha"), entry.get("description"))
    if cache_key not in _heuristic_cache:
        _heuristic_cache[cache_key] = ai.heuristic(entry)
    return _heuristic_cache[cache_key], "pending"


def visible_entries(doc: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    prefs = doc["settings"]
    hidden = {n.lower() for n in prefs.get("hidden", [])}
    include_forks = bool(prefs.get("include_forks"))
    return [
        (key, entry) for key, entry in doc["repos"].items()
        if not entry.get("gone") and entry.get("name", "").lower() not in hidden
        and (include_forks or not entry.get("fork"))
    ]


_banner_cache: dict[tuple, str] = {}


def banner(entry: dict[str, Any]) -> str:
    """Card picture: the repo's GitHub social preview, else the README's top image, else GitHub's own card."""
    custom = safe_url(entry.get("social_image"))
    if custom:
        return custom
    cache_key = (entry.get("full_name"), entry.get("readme_sha"))
    if cache_key not in _banner_cache:
        _banner_cache[cache_key] = extract_banner(entry.get("readme_html", ""))
    if _banner_cache[cache_key]:
        return _banner_cache[cache_key]
    full_name = entry.get("full_name") or ""
    return f"https://opengraph.githubassets.com/1/{full_name}" if "/" in full_name else ""


def project_cards() -> list[dict[str, Any]]:
    doc = store.get()
    if _cards_cache["rev"] == store.revision:
        return _cards_cache["cards"]
    featured = [n.lower() for n in doc["settings"].get("featured", [])]
    research_names = ({e["repo"].lower() for e in research.entries(doc)}
                      if research.section(doc).get("enabled") else set())
    open_issues: dict[str, int] = {}
    for issue in doc["github"].get("issues") or []:
        if issue.get("state") == "open":
            open_issues[issue["repo"].lower()] = open_issues.get(issue["repo"].lower(), 0) + 1
    cards = []
    for key, entry in visible_entries(doc):
        analysis, status = _analysis(key, entry)
        name = entry["name"]
        tech = list(analysis.get("tech_stack") or ([entry["language"]] if entry.get("language") else []))
        title = analysis.get("title") or pretty_title(name)
        summary = analysis.get("summary") or entry.get("description") or ""
        confidence = analysis.get("confidence")
        card = {
            "key": key, "name": name, "slug": slug(name), "title": title,
            "description": entry.get("description", ""), "summary": summary,
            "problem": analysis.get("problem", ""), "key_points": analysis.get("key_points") or [],
            "reason": analysis.get("reason", ""), "audience": analysis.get("audience", ""),
            "tech": tech, "category": categories.resolve(doc, analysis.get("category", "industry")), "status": status,
            "provider": analysis.get("provider", ""), "model": analysis.get("model", ""),
            "confidence": int(round(confidence * 100)) if isinstance(confidence, (int, float)) and status != "pending" else None,
            "banner": banner(entry),
            "language": entry.get("language", ""), "lang_slug": lang_slug(entry.get("language", "")),
            "stars": entry.get("stars", 0), "forks": entry.get("forks", 0),
            "issue_count": open_issues.get(name.lower(), 0), "license": entry.get("license", ""),
            "topics": entry.get("topics") or [], "homepage": safe_url(entry.get("homepage")),
            "html_url": entry.get("html_url", ""), "created_at": entry.get("created_at") or "",
            "updated_at": entry.get("pushed_at") or entry.get("updated_at") or "",
            "featured": name.lower() in featured, "featured_rank": featured.index(name.lower()) if name.lower() in featured else 999,
            "archived": bool(entry.get("archived")), "has_readme": bool(entry.get("readme_html")),
            "research": name.lower() in research_names,
            "translated": False,
            "_i18n": translate.repo_translations(entry),  # never sent to visitors as-is
        }
        cards.append(card)
    cards.sort(key=lambda c: c["updated_at"], reverse=True)
    cards.sort(key=lambda c: c["featured_rank"])
    _cards_cache.update(rev=store.revision, cards=cards)
    return cards


_localized_cache: dict[str, Any] = {"rev": None, "by_lang": {}}


def cards_for(lang: str) -> list[dict[str, Any]]:
    """project_cards() with the AI-written text in the visitor's language where a translation exists."""
    cards = project_cards()
    if lang not in translate.TARGET_LANGS:
        return cards
    if _localized_cache["rev"] != store.revision:
        _localized_cache.update(rev=store.revision, by_lang={})
    cached = _localized_cache["by_lang"].get(lang)
    if cached is not None:
        return cached
    out = []
    for card in cards:
        tr = card["_i18n"].get(lang)
        if tr:
            card = {**card, **{k: tr[k] for k in translate.REPO_FIELDS if tr.get(k)}, "translated": True}
        out.append(card)
    _localized_cache["by_lang"][lang] = out
    return out


_research_cache: dict[str, Any] = {"rev": None, "items": []}


def research_cards() -> list[dict[str, Any]]:
    """Research-grade projects for the public site and API.

    Empty while the admin switch is off, and an entry only appears while its repository is
    visible on the site (public, not hidden, not an excluded fork) - so private repos never show.
    """
    doc = store.get()
    if _research_cache["rev"] == store.revision:
        return _research_cache["items"]
    items: list[dict[str, Any]] = []
    if research.section(doc).get("enabled"):
        cards = {c["name"].lower(): c for c in project_cards()}
        seen: set[str] = set()
        for entry in research.ordered(research.entries(doc)):
            card = cards.get(entry["repo"].lower())
            if not card or card["name"].lower() in seen:
                continue
            seen.add(card["name"].lower())
            validators = [{**v, "link": safe_url(v["link"])} for v in research.validators(entry)]
            items.append({
                "id": entry["id"], "name": card["name"], "slug": card["slug"], "title": card["title"],
                "topic": entry.get("topic", ""), "summary": card["summary"], "category": card["category"],
                "html_url": card["html_url"], "homepage": card["homepage"], "tech": card["tech"],
                "validators": validators, "usage": entry.get("usage", ""), "where": entry.get("where", ""),
                "when": entry.get("when", ""),
            })
    _research_cache.update(rev=store.revision, items=items)
    return items


def public_research(item: dict[str, Any]) -> dict[str, Any]:
    keys = ("name", "title", "topic", "summary", "category", "html_url", "homepage", "tech",
            "validators", "usage", "where", "when")
    return {key: item[key] for key in keys}


PUBLIC_FIELDS = ("name", "title", "description", "summary", "problem", "key_points", "reason", "audience",
                 "tech", "category", "status", "confidence", "language", "stars", "forks", "issue_count",
                 "license", "topics", "homepage", "html_url", "created_at", "updated_at", "featured", "archived",
                 "banner", "translated")


def public_card(card: dict[str, Any]) -> dict[str, Any]:
    data = {field: card[field] for field in PUBLIC_FIELDS}
    names = categories.meta(store.get(), translator("en"))
    data["category_name"] = names.get(card["category"], names["other"])["label"]
    return data


def project_detail(name: str, lang: str = "en") -> dict[str, Any] | None:
    wanted = name.lower()
    for card in cards_for(lang):
        if card["name"].lower() == wanted:
            entry = store.get()["repos"].get(card["key"], {})
            return {**public_card(card), "readme_html": entry.get("readme_html", "")}
    return None


def home(doc: dict[str, Any], lang: str = "en") -> dict[str, Any]:
    cards = cards_for(lang)
    profile = doc["profile"]
    profile_tr = translate.profile_translation(doc, lang)
    groups = []
    for key in categories.keys(doc):
        repos = [c for c in cards if c["category"] == key]
        groups.append({"key": key, "repos": repos})
    by_key = {g["key"]: len(g["repos"]) for g in groups}
    user = doc["github"].get("user") or {}
    events = []
    for event in (doc["github"].get("events") or [])[:8]:
        repo = event.get("repo", "")
        short = repo.split("/", 1)[1] if repo.lower().startswith(settings.github_username.lower() + "/") else repo
        kind = event.get("type") or "other"
        kind = "create_ref" if kind == "create" else kind  # older JSON used "create"
        kind = kind if kind in _EVENT_ICONS else "other"
        events.append({**event, "type": kind, "short": short, "icon": _EVENT_ICONS[kind]})
    bio = (profile_tr.get("bio") or profile.get("bio") or "").strip()
    return {
        "page": "home",
        "cards": cards,
        "groups": groups,
        "counts": {**by_key, "all": len(cards),
                   "stars": sum(c["stars"] for c in cards), "followers": user.get("followers") or 0},
        "custom_categories": categories.custom(doc),
        "followers_url": f"https://github.com/{settings.github_username}?tab=followers",
        "research": research_cards(),
        "research_by_name": {x["name"]: x for x in research_cards()},
        "profile": profile,
        "headline": profile_tr.get("headline") or profile.get("headline", ""),
        "bio_paragraphs": [p.strip() for p in re.split(r"\n\s*\n", bio) if p.strip()],
        "age": compute_age(profile) if profile.get("show_age", True) else None,
        "skills": profile.get("skills") or [],
        "interests": profile.get("interests") or [],
        "stats_images": doc["github"].get("stats_images") or [] if profile.get("show_stats", True) else [],
        "has_profile_readme": bool(doc["github"].get("profile_readme_html")),
        "events": events if profile.get("show_activity", True) else None,
    }


def issues_view(doc: dict[str, Any], state: str, repo: str, lang: str = "en") -> dict[str, Any]:
    visible = {c["name"].lower(): c for c in cards_for(lang)}
    issues = [i for i in doc["github"].get("issues") or [] if i.get("repo", "").lower() in visible]
    counts = {"open": sum(i["state"] == "open" for i in issues),
              "closed": sum(i["state"] == "closed" for i in issues), "all": len(issues)}
    repos = sorted({i["repo"] for i in issues}, key=str.lower)
    if repo:
        issues = [i for i in issues if i["repo"].lower() == repo.lower()]
    if state in ("open", "closed"):
        issues = [i for i in issues if i["state"] == state]
    groups: dict[str, dict[str, Any]] = {}
    for issue in issues:
        card = visible[issue["repo"].lower()]
        group = groups.setdefault(card["name"], {"repo": card["name"], "title": card["title"],
                                                 "url": card["html_url"], "issues": []})
        group["issues"].append({**issue, "tid": f"{slug(issue['repo'])}-{issue['number']}"})
    return {"page": "issues", "groups": list(groups.values()), "counts": counts, "repos": repos,
            "state": state, "repo": repo}


# --------------------------------------------------------------------- admin --
def admin_repo_row(key: str, entry: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
    prefs = doc["settings"]
    hidden = {n.lower() for n in prefs.get("hidden", [])}
    featured = {n.lower() for n in prefs.get("featured", [])}
    analysis, status = _analysis(key, entry)
    current = entry.get("ai") or {}
    name = entry.get("name", key)
    return {
        "name": name, "title": analysis.get("title") or pretty_title(name),
        "description": entry.get("description", ""), "language": entry.get("language", ""),
        "stars": entry.get("stars", 0), "fork": bool(entry.get("fork")), "archived": bool(entry.get("archived")),
        "gone": bool(entry.get("gone")), "hidden": name.lower() in hidden, "featured": name.lower() in featured,
        "category": categories.resolve(doc, analysis.get("category", "industry")), "status": status,
        "provider": current.get("provider", ""), "model": current.get("model", ""),
        "confidence": current.get("confidence"), "locked": bool(current.get("locked")),
        "categorized_at": current.get("categorized_at", ""), "summary": analysis.get("summary", ""),
        "reason": analysis.get("reason", ""), "key_points": analysis.get("key_points") or [],
        "has_readme": bool(entry.get("readme_html")),
        "readme_changed": bool(current.get("readme_sha") and entry.get("readme_sha")
                               and current["readme_sha"] != entry["readme_sha"]),
        "html_url": entry.get("html_url", ""), "pushed_at": entry.get("pushed_at", ""),
    }


def admin_repo(name: str) -> dict[str, Any] | None:
    doc = store.get()
    for key, entry in doc["repos"].items():
        if entry.get("name", "").lower() == name.lower():
            return admin_repo_row(key, entry, doc)
    return None


def admin_research(doc: dict[str, Any]) -> dict[str, Any]:
    """Everything the admin research card needs. The repo picker only offers repositories that
    are visible on the site, so a private, deleted or hidden repo can't be chosen."""
    section = research.section(doc)
    visible = {c["name"].lower(): c for c in project_cards()}
    by_name = {e.get("name", "").lower(): e for e in doc["repos"].values() if e.get("name")}
    hidden = {n.lower() for n in doc["settings"].get("hidden", [])}
    taken = set()
    entries = []
    for entry in research.ordered(research.entries(doc)):
        key = entry["repo"].lower()
        taken.add(key)
        card = visible.get(key)
        repo = by_name.get(key)
        if card:
            status = "shown" if section.get("enabled") else "ready"
        elif repo is None or repo.get("gone"):
            status = "not_public"
        elif key in hidden:
            status = "hidden"
        else:
            status = "fork"
        entries.append({
            "id": entry["id"], "repo": card["name"] if card else entry["repo"],
            "title": card["title"] if card else pretty_title(entry["repo"]), "status": status,
            "topic": entry.get("topic", ""), "validators": research.validators(entry),
            "usage": entry.get("usage", ""), "where": entry.get("where", ""), "when": entry.get("when", ""),
            "updated_at": entry.get("updated_at", ""),
        })
    repos = [{"name": c["name"], "title": c["title"], "taken": c["name"].lower() in taken}
             for c in sorted(visible.values(), key=lambda c: c["title"].lower())]
    return {
        "enabled": bool(section.get("enabled")),
        "entries": entries,
        "shown": sum(e["status"] == "shown" for e in entries),
        "repos": repos,
        "kinds": [{"key": k, "label": v} for k, v in research.VALIDATOR_KINDS.items()],
        "max_entries": research.MAX_ENTRIES,
        "max_validators": research.MAX_VALIDATORS,
    }


def admin_state(client_ip: str = "") -> dict[str, Any]:
    doc = store.get()
    rows = sorted((admin_repo_row(k, e, doc) for k, e in doc["repos"].items()), key=lambda r: r["name"].lower())
    storage = store.describe()
    warnings = []
    if storage.get("warning"):
        warnings.append(storage["warning"])
    if storage.get("error"):
        warnings.append(f"Storage error: {storage['error']}")
    if not storage.get("persistent"):
        warnings.append("Changes are saved to TEMPORARY storage on this host (Vercel's disk is read-only). "
                        "Use STORAGE_BACKEND=gist, or click “Download JSON” and commit data/portfolio.json.")
    if not settings.github_token:
        warnings.append("No GITHUB_TOKEN set: GitHub allows only 60 API requests/hour. A read-only token is recommended.")
    if settings.on_vercel and all(ai.is_private_url(p.base_url) for p in ai.providers()):
        warnings.append("Both AI endpoints are private/LAN addresses that Vercel cannot reach. Run the AI locally "
                        "(python scripts/sync.py) and commit the JSON, or expose the gateway over HTTPS.")
    if settings.cors_origins == ("*",) and settings.frame_ancestors == "*":
        pass  # intentional: the public site is embeddable and its API is public
    counts = {
        "total": sum(1 for r in rows if not r["gone"]),
        "ai": sum(r["status"] == "ai" for r in rows if not r["gone"]),
        "manual": sum(r["status"] == "manual" for r in rows if not r["gone"]),
        "pending": sum(r["status"] == "pending" for r in rows if not r["gone"] and (not r["fork"] or doc["settings"].get("include_forks"))),
        "other": sum(r["category"] == "other" for r in rows if not r["gone"]),
    }
    return {
        "repos": rows,
        "profile": doc["profile"],
        "settings": doc["settings"],
        "research": admin_research(doc),
        "synced_at": doc.get("synced_at"),
        "updated_at": doc.get("updated_at"),
        "storage": storage,
        "warnings": warnings,
        "counts": counts,
        "providers": [{"key": p.key, "label": p.label, "model": p.model, "url": p.base_url} for p in ai.providers()],
        "on_vercel": settings.on_vercel,
        "categories": admin_categories(doc),
        "occasions": occasions.admin_view(doc),
        "translations": translate.progress(doc, visible_entries(doc)),
        "security": lockout.admin_view(doc, client_ip),
    }


def admin_categories(doc: dict[str, Any]) -> dict[str, Any]:
    labels = categories.meta(doc, translator("en"))
    used: dict[str, int] = {}
    for key, entry in doc["repos"].items():
        if not entry.get("gone"):
            group = categories.resolve(doc, _analysis(key, entry)[0].get("category", "industry"))
            used[group] = used.get(group, 0) + 1
    return {
        "all": [{"key": k, "label": labels[k]["label"], "icon": labels[k]["icon"], "builtin": labels[k]["builtin"],
                 "count": used.get(k, 0)} for k in categories.keys(doc)],
        "custom": [{**c, "count": used.get(c["key"], 0)} for c in categories.custom(doc)],
        "icons": list(categories.ICON_CHOICES),
        "colors": list(categories.COLOR_CHOICES),
        "max": categories.MAX_CUSTOM,
    }
