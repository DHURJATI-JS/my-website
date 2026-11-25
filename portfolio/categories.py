"""Project categories: the three built-in groups plus your own, each with an icon.

Built-in groups never change:
  social    Social impact        (chosen by the AI)
  industry  Industry & startup   (chosen by the AI)
  other     Other                (only ever chosen by hand)

Custom groups are made in /admin -> Categories and live in the JSON document
under settings.categories. The AI never picks a custom group: you move a project
there by hand, exactly like "Other". Until you edit the list, a ready-made
"Games" group is offered.

Icons are Icons8 "3D Fluency" names, limited to ICON_CHOICES (each one checked
to exist), and colours to a fixed palette - so nothing a visitor sees can come
from free-typed HTML, CSS or URLs.
"""
from __future__ import annotations

import re
from typing import Any, Callable

BUILTIN = ("social", "industry", "other")
BUILTIN_ICONS = {"social": "handshake", "industry": "rocket", "other": "code"}
MAX_CUSTOM = 12

DEFAULT_CUSTOM: list[dict[str, str]] = [
    {"key": "games", "name": "Games", "icon": "game-controller", "color": "#f59e0b",
     "description": "Games, game engines and playful side projects."},
]

# Icons8 3D Fluency names (all checked to exist), grouped loosely by theme.
ICON_CHOICES = (
    "game-controller", "joystick", "puzzle", "trophy", "star", "sparkling", "crown", "fire-element",
    "rocket", "handshake", "code", "source-code", "console", "command-line", "api", "python", "github", "bug",
    "brain", "robot", "ai", "chatbot", "artificial-intelligence", "processor", "motherboard", "chip",
    "server", "database", "cloud", "workstation", "laptop", "smartphone", "web", "globe",
    "shield", "lock", "key", "fingerprint", "face-id", "eye", "search",
    "graduation-cap", "school", "books", "book", "idea", "test-tube", "microscope", "telescope", "satellite",
    "hospital", "stethoscope", "heart-with-pulse", "medical-doctor", "nurse", "pill", "ambulance", "wheelchair", "hand",
    "farm", "farmer", "tractor", "wheat", "sprout", "solar-panel", "wind-turbine", "lightning-bolt", "water", "sun", "rainbow",
    "money-bag", "coins", "wallet", "bank", "briefcase", "factory", "shop", "shopping-cart", "delivery", "truck",
    "bar-chart", "combo-chart", "analytics", "news", "newspaper", "megaphone", "mail", "phone", "chat",
    "camera", "film-reel", "musical-notes", "music", "microphone", "paint-palette",
    "dumbbell", "restaurant", "coffee", "law", "scales", "language", "volunteering", "user-group-man-man",
    "home", "city", "office", "mountain", "beach", "calendar", "gear", "tools", "wrench", "gift",
    "cat", "dog", "butterfly", "opened-folder", "document",
)
ICON_SET = frozenset(ICON_CHOICES)

COLOR_CHOICES = (
    "#f59e0b", "#ef4444", "#ec4899", "#a855f7", "#7c3aed", "#6366f1", "#2563eb",
    "#0ea5e9", "#06b6d4", "#14b8a6", "#10b981", "#22c55e", "#84cc16", "#eab308", "#f97316", "#64748b",
)
COLOR_SET = frozenset(COLOR_CHOICES)

_KEY_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def slugify(name: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return key[:30].strip("-")


def valid_key(key: str) -> bool:
    return bool(_KEY_RE.match(key)) and len(key) <= 30


def custom(doc: dict[str, Any]) -> list[dict[str, str]]:
    """The custom groups, in their admin order (the ready-made list until it is edited)."""
    stored = doc.get("settings", {}).get("categories")
    items = DEFAULT_CUSTOM if stored is None else stored
    clean = []
    for item in items:
        key = str(item.get("key", ""))
        if not valid_key(key) or key in BUILTIN:
            continue
        clean.append({
            "key": key,
            "name": str(item.get("name") or key)[:40],
            "icon": item.get("icon") if item.get("icon") in ICON_SET else "opened-folder",
            "color": item.get("color") if item.get("color") in COLOR_SET else COLOR_CHOICES[-1],
            "description": str(item.get("description") or "")[:160],
        })
    return clean


def keys(doc: dict[str, Any]) -> list[str]:
    """Every group key in display order: social, industry, custom..., other."""
    return ["social", "industry", *[c["key"] for c in custom(doc)], "other"]


def resolve(doc: dict[str, Any], key: str | None) -> str:
    """A project's group, falling back to "other" if its custom group was deleted."""
    return key if key in keys(doc) else "other"


def meta(doc: dict[str, Any], t: Callable[..., str] | None = None) -> dict[str, dict[str, Any]]:
    """key -> label, description, icon, colour (labels translated where a translation exists)."""
    def tr(key: str, default: str) -> str:
        if t is None:
            return default
        text = t(key)
        return default if text == key else text

    out: dict[str, dict[str, Any]] = {}
    for key in ("social", "industry"):
        out[key] = {"key": key, "label": tr(f"cat_{key}", key), "desc": tr(f"cat_{key}_desc", ""),
                    "icon": BUILTIN_ICONS[key], "color": "", "builtin": True}
    for c in custom(doc):
        label, desc = c["name"], c["description"]
        default = next((d for d in DEFAULT_CUSTOM if d["key"] == c["key"]), None)
        if default and label == default["name"]:
            label = tr(f"cat_{c['key']}", label)  # the ready-made "Games" is translated until renamed
        if default and desc == default["description"]:
            desc = tr(f"cat_{c['key']}_desc", desc)
        out[c["key"]] = {"key": c["key"], "label": label, "desc": desc,
                         "icon": c["icon"], "color": c["color"], "builtin": False}
    out["other"] = {"key": "other", "label": tr("cat_other", "Other"), "desc": tr("cat_other_desc", ""),
                    "icon": BUILTIN_ICONS["other"], "color": "", "builtin": True}
    return out


def upsert(doc: dict[str, Any], *, key: str, name: str, icon: str, color: str, description: str) -> str:
    """Create (empty key) or update a custom group. Returns its key. Runs inside store.mutate."""
    name = " ".join(name.split())[:40]
    if not name:
        raise ValueError("Give the category a name.")
    if icon not in ICON_SET:
        raise ValueError("Pick an icon from the list.")
    if color not in COLOR_SET:
        raise ValueError("Pick a colour from the list.")
    items = custom(doc)
    if key:
        target = next((c for c in items if c["key"] == key), None)
        if target is None:
            raise KeyError(key)
    else:
        if len(items) >= MAX_CUSTOM:
            raise ValueError(f"You can have up to {MAX_CUSTOM} custom categories.")
        base = slugify(name) or "category"
        key, n = base, 2
        taken = set(BUILTIN) | {c["key"] for c in items}
        while key in taken:
            key, n = f"{base[:26]}-{n}", n + 1
        target = {"key": key}
        items.append(target)
    target.update(name=name, icon=icon, color=color, description=" ".join(description.split())[:160])
    doc["settings"]["categories"] = items
    return key


def delete(doc: dict[str, Any], key: str) -> int:
    """Remove a custom group; its projects move to "Other" (kept as your manual choice)."""
    items = custom(doc)
    if key not in {c["key"] for c in items}:
        raise KeyError(key)
    doc["settings"]["categories"] = [c for c in items if c["key"] != key]
    moved = 0
    for entry in doc.get("repos", {}).values():
        analysis = entry.get("ai")
        if analysis and analysis.get("category") == key:
            analysis["category"] = "other"
            moved += 1
    return moved


def move(doc: dict[str, Any], key: str, step: int) -> None:
    items = custom(doc)
    index = next((i for i, c in enumerate(items) if c["key"] == key), None)
    if index is None:
        raise KeyError(key)
    new = max(0, min(len(items) - 1, index + step))
    items.insert(new, items.pop(index))
    doc["settings"]["categories"] = items
