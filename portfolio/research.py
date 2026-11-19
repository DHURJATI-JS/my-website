"""Research-grade projects: hand-written validation records for repositories you choose.

Kept in the JSON document as::

    "research": {"enabled": false, "entries": [
        {"id", "repo", "topic", "validators": [{"kind", "name", "organisation", "link"}],
         "usage", "where", "when", "created_at", "updated_at"}]}

Nothing about research reaches the public pages, the navbar or the public API unless the
admin switch is ON *and* at least one entry points at a repository that is visible on the
site. Entries are matched against the same visible-repo list as the project cards, so
private, deleted, hidden or excluded-fork repositories never show up - not even by name.
"""
from __future__ import annotations

import secrets
from typing import Any

# Validator types offered in /admin. Key -> admin label (the public site translates vk_<key>).
VALIDATOR_KINDS: dict[str, str] = {
    "professor": "Professor / academic",
    "researcher": "Researcher",
    "industry": "Industry expert",
    "organisation": "Organisation / company",
    "institution": "University / institute",
    "government": "Government body",
    "peer_review": "Peer review",
    "user_study": "User study / field trial",
    "mentor": "Mentor / supervisor",
    "other": "Other",
}

MAX_ENTRIES = 60
MAX_VALIDATORS = 12


def new_id() -> str:
    return secrets.token_hex(6)


def section(doc: dict[str, Any]) -> dict[str, Any]:
    research = doc.get("research")
    return research if isinstance(research, dict) else {"enabled": False, "entries": []}


def entries(doc: dict[str, Any]) -> list[dict[str, Any]]:
    items = section(doc).get("entries")
    if not isinstance(items, list):
        return []
    return [e for e in items if isinstance(e, dict) and e.get("id") and e.get("repo")]


def ordered(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Newest first: by the date it was used, then by when the card was last edited."""
    return sorted(items, key=lambda e: (e.get("when") or "", e.get("updated_at") or ""), reverse=True)


def validators(entry: dict[str, Any]) -> list[dict[str, str]]:
    out = []
    for v in entry.get("validators") or []:
        if isinstance(v, dict) and str(v.get("name") or "").strip():
            kind = v.get("kind") if v.get("kind") in VALIDATOR_KINDS else "other"
            out.append({"kind": kind, "name": str(v["name"]).strip(),
                        "organisation": str(v.get("organisation") or "").strip(),
                        "link": str(v.get("link") or "").strip()})
    return out
