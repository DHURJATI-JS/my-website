"""Translations of the AI-written project text (and your profile text) into every site language.

The interface strings live in i18n.py. What the AI writes about each project - title,
summary, problem, key points, audience, "why this group" - is generated in English, so it is
translated once per language by the same AI and stored in the JSON next to the analysis:

    repos[key]["ai"]["i18n"][lang] = {"title": ..., "summary": ..., ..., "src": <hash>}
    profile_i18n[lang]             = {"headline": ..., "bio": ..., "src": <hash>}

`src` is a hash of the English text it was made from, so a translation is ignored (and
redone) as soon as the English changes. Until a translation exists, visitors see English.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .ai import LANGUAGE_NAMES

TARGET_LANGS = tuple(LANGUAGE_NAMES)  # every site language except English
REPO_FIELDS = ("title", "summary", "problem", "reason", "audience", "key_points")
PROFILE_FIELDS = ("headline", "bio")
PROFILE_KEY = "__profile__"


def src_hash(fields: dict[str, Any]) -> str:
    raw = json.dumps(fields, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def repo_source(entry: dict[str, Any]) -> dict[str, Any] | None:
    """The English text shown on a project's card, or None while it awaits the AI."""
    current = entry.get("ai")
    if not current:
        return None
    from .views import pretty_title  # local import: views imports this module

    fields = {
        "title": current.get("title") or pretty_title(entry.get("name", "")),
        "summary": current.get("summary") or entry.get("description") or "",
        "problem": current.get("problem", ""),
        "reason": current.get("reason", ""),
        "audience": current.get("audience", ""),
        "key_points": list(current.get("key_points") or []),
    }
    return {k: v for k, v in fields.items() if v}


def repo_translations(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """lang -> translated fields, only for translations that still match the English text."""
    source = repo_source(entry)
    if not source:
        return {}
    wanted = src_hash(source)
    stored = (entry.get("ai") or {}).get("i18n") or {}
    return {lang: data for lang, data in stored.items()
            if lang in TARGET_LANGS and isinstance(data, dict) and data.get("src") == wanted}


def profile_source(profile: dict[str, Any]) -> dict[str, str]:
    return {k: (profile.get(k) or "").strip() for k in PROFILE_FIELDS if (profile.get(k) or "").strip()}


def profile_translation(doc: dict[str, Any], lang: str) -> dict[str, Any]:
    source = profile_source(doc.get("profile") or {})
    if not source or lang not in TARGET_LANGS:
        return {}
    data = (doc.get("profile_i18n") or {}).get(lang) or {}
    return data if data.get("src") == src_hash(source) else {}


def plan(doc: dict[str, Any], visible: list[tuple[str, dict[str, Any]]]) -> list[dict[str, str]]:
    """Every (project, language) pair that still needs translating; the profile comes first."""
    todo: list[dict[str, str]] = []
    if profile_source(doc.get("profile") or {}):
        todo += [{"name": PROFILE_KEY, "lang": lang} for lang in TARGET_LANGS
                 if not profile_translation(doc, lang)]
    for _, entry in sorted(visible, key=lambda item: item[1].get("name", "").lower()):
        if not repo_source(entry):
            continue
        done = repo_translations(entry)
        todo += [{"name": entry["name"], "lang": lang} for lang in TARGET_LANGS if lang not in done]
    return todo


def progress(doc: dict[str, Any], visible: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    per_lang = {lang: {"done": 0, "total": 0} for lang in TARGET_LANGS}
    for _, entry in visible:
        if not repo_source(entry):
            continue
        done = repo_translations(entry)
        for lang in TARGET_LANGS:
            per_lang[lang]["total"] += 1
            per_lang[lang]["done"] += lang in done
    total = sum(v["total"] for v in per_lang.values())
    done = sum(v["done"] for v in per_lang.values())
    return {"languages": per_lang, "done": done, "total": total,
            "names": {lang: LANGUAGE_NAMES[lang].split(" (")[0] for lang in TARGET_LANGS}}


_FIELD_LIMITS = {"title": 80, "summary": 900, "problem": 400, "reason": 400, "audience": 200,
                 "headline": 400, "bio": 4500}


def clean_result(result: Any, source: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any] | None:
    """Validate a translation before it is stored (it comes back from the admin's browser).

    None if it no longer matches the English text (stale) or is malformed. Only known fields,
    plain strings and lists of strings of bounded size are kept; the site escapes them on output.
    """
    if not isinstance(result, dict) or not source or result.get("src") != src_hash(source):
        return None
    clean: dict[str, Any] = {"src": result["src"], "at": str(result.get("at") or "")[:32],
                             "provider": str(result.get("provider") or "")[:40],
                             "model": str(result.get("model") or "")[:80]}
    for key in fields:
        if key not in source:
            continue
        value = result.get(key)
        if isinstance(source[key], list):
            if not isinstance(value, list) or len(value) != len(source[key]):
                return None
            items = [str(x).strip()[:220] for x in value]
            if not all(items):
                return None
            clean[key] = items
        else:
            text = str(value or "").strip()
            if not text:
                return None
            clean[key] = text[: _FIELD_LIMITS.get(key, 400)]
    return clean
