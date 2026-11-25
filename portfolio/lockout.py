"""Admin login lockout: 3 wrong passwords from one network bans it until YOU unblock it.

A banned network gets 403 for every request - GET and POST, pages and API alike
(BanMiddleware in app.py, before any route runs). The one exception is a request carrying a
valid signed-in admin session, so the owner can never be locked out of the unblock button.
Heads-up: phone networks often put many people behind one IP address (carrier-grade NAT),
so a ban can also hit other visitors on that network until you unblock it.

Locked yourself out? Sign in from another network (e.g. phone data) and unblock, or run
    python scripts/unblock_ip.py --all
on your PC (it uses the same .env / Gist as the site).

Counts and blocks live in the JSON document (under "security"), so they hold across every
Vercel instance. IPv6 addresses are grouped by their /64 network, since one device can
rotate through a whole /64.

Privacy: no address is stored. Each network is kept as a keyed hash (HMAC with SECRET_KEY),
plus a masked form for the admin page ("203.0.x.x", "2001:db8:…/64"); browser details are
not kept at all. Older entries with plain addresses are converted the next time they're touched
(or when the admin page opens).
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import settings
from .store import now_iso, store

MAX_FAILURES = 3
FAILURE_WINDOW = timedelta(hours=24)  # wrong passwords older than this are forgotten
MAX_TRACKED = 500                      # keeps the JSON small under a distributed attack


def network_key(ip: str) -> str:
    """The address a block applies to: the IPv4 address, or the IPv6 /64 network."""
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return (ip or "unknown").strip()[:64]
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


def hashed(network: str) -> str:
    """The stored key for a network: a keyed hash, useless without SECRET_KEY."""
    digest = hmac.new(settings.secret_key.encode("utf-8"), b"lockout|" + network.encode("utf-8"), hashlib.sha256)
    return "h:" + digest.hexdigest()[:32]


def key_for(ip: str) -> str:
    return hashed(network_key(ip))


def mask(network: str) -> str:
    """What the admin page shows: enough to tell networks apart, never the full address."""
    try:
        net = ipaddress.ip_network(network, strict=False)
    except ValueError:
        return "unknown network"
    if net.version == 4:
        a, b = str(net.network_address).split(".")[:2]
        return f"{a}.{b}.x.x"
    first = net.network_address.exploded.split(":")[:2]
    return f"{int(first[0], 16):x}:{int(first[1], 16):x}:…/64"


def _is_legacy(key: str) -> bool:
    return not key.startswith("h:")


def _clean_entry(entry: Any, shown: str) -> dict[str, Any]:
    entry = dict(entry) if isinstance(entry, dict) else {}
    entry.pop("agent", None)  # browser details are not kept
    entry.setdefault("shown", shown)
    return entry


def _section(doc: dict[str, Any]) -> dict[str, Any]:
    sec = doc.setdefault("security", {})
    sec.setdefault("failures", {})
    sec.setdefault("blocked", {})
    for part in ("failures", "blocked"):  # convert older entries that kept the plain address
        table = sec[part]
        for key in [k for k in table if _is_legacy(k) or "agent" in (table[k] or {})]:
            entry = table.pop(key)
            if _is_legacy(key):
                table[hashed(key)] = _clean_entry(entry, mask(key))
            else:
                table[key] = _clean_entry(entry, "unknown network")
    return sec


def needs_migration(doc: dict[str, Any]) -> bool:
    sec = doc.get("security") or {}
    return any(_is_legacy(k) or "agent" in (v or {})
               for part in ("failures", "blocked") for k, v in (sec.get(part) or {}).items())


def migrate_if_needed() -> None:
    if needs_migration(store.get()):
        store.mutate(_section)  # converting happens inside _section


def _parse(ts: str) -> datetime:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.fromtimestamp(0, timezone.utc)


def blocked_keys() -> frozenset[str]:
    sec = store.get().get("security") or {}
    return frozenset((sec.get("blocked") or {}).keys())


def is_blocked(ip: str) -> bool:
    blocked = (store.get().get("security") or {}).get("blocked") or {}
    return bool(blocked) and (key_for(ip) in blocked or network_key(ip) in blocked)


def record_failure(ip: str) -> dict[str, Any]:
    """Count one wrong password. Returns {"count": n, "blocked": bool, "left": attempts left}."""
    key, shown = key_for(ip), mask(network_key(ip))

    def apply(doc: dict[str, Any]) -> dict[str, Any]:
        sec = _section(doc)
        if key in sec["blocked"]:
            return {"count": sec["blocked"][key].get("count", MAX_FAILURES), "blocked": True, "left": 0}
        now = datetime.now(timezone.utc)
        entry = sec["failures"].get(key)
        if not entry or now - _parse(entry.get("first", "")) > FAILURE_WINDOW:
            entry = {"count": 0, "first": now_iso(), "shown": shown}
        entry["count"] = int(entry.get("count", 0)) + 1
        entry["last"] = now_iso()
        if entry["count"] >= MAX_FAILURES:
            sec["failures"].pop(key, None)
            sec["blocked"][key] = {"at": now_iso(), "count": entry["count"], "first": entry["first"],
                                   "shown": shown}
            return {"count": entry["count"], "blocked": True, "left": 0}
        sec["failures"][key] = entry
        if len(sec["failures"]) > MAX_TRACKED:  # drop the oldest
            for old in sorted(sec["failures"], key=lambda k: sec["failures"][k].get("last", ""))[:-MAX_TRACKED]:
                sec["failures"].pop(old, None)
        return {"count": entry["count"], "blocked": False, "left": MAX_FAILURES - entry["count"]}

    return store.mutate(apply)


def clear_failures(ip: str) -> None:
    """A correct password resets that network's count (only writes if there was one)."""
    keys = {key_for(ip), network_key(ip)}
    if keys & set((store.get().get("security") or {}).get("failures") or {}):
        def apply(doc: dict[str, Any]) -> None:
            failures = _section(doc)["failures"]
            for k in keys:
                failures.pop(k, None)

        store.mutate(apply)


def unblock(target: str) -> int:
    """Unblock one network - its key from the admin list, or an address - or "*" for all.
    Returns how many were removed."""
    target = target.strip()
    keys = {target} if target.startswith("h:") else {key_for(target), hashed(target), network_key(target)}

    def apply(doc: dict[str, Any]) -> int:
        sec = _section(doc)
        if target == "*":
            removed = len(sec["blocked"])
            sec["blocked"].clear()
            sec["failures"].clear()
            return removed
        removed = sum(1 for k in keys if sec["blocked"].pop(k, None) is not None)
        for k in keys:
            sec["failures"].pop(k, None)
        return removed

    return store.mutate(apply)


def admin_view(doc: dict[str, Any], current_ip: str = "") -> dict[str, Any]:
    """For the admin page: masked networks only (never a full address or a browser)."""
    sec = doc.get("security") or {}
    yours = {key_for(current_ip), network_key(current_ip)} if current_ip else set()

    def row(key: str, value: dict[str, Any]) -> dict[str, Any]:
        value = value or {}
        shown = value.get("shown") or (mask(key) if _is_legacy(key) else "unknown network")
        return {"key": key if not _is_legacy(key) else hashed(key), "ip": shown, "you": key in yours,
                **{k: value[k] for k in ("at", "count", "first", "last") if k in value}}

    blocked = [row(k, v) for k, v in (sec.get("blocked") or {}).items()]
    blocked.sort(key=lambda x: x.get("at", ""), reverse=True)
    now = datetime.now(timezone.utc)
    watching = [row(k, v) for k, v in (sec.get("failures") or {}).items()
                if now - _parse((v or {}).get("first", "")) <= FAILURE_WINDOW]
    watching.sort(key=lambda x: x.get("last", ""), reverse=True)
    return {"blocked": blocked, "watching": watching[:50], "max_failures": MAX_FAILURES}


# ---------------------------------------------------------------- middleware --
_BLOCKED_HTML = (
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"robots\" content=\"noindex\">"
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>Access blocked</title></head>"
    "<body><h1>Access blocked</h1><p>This network was blocked after repeated wrong admin passwords.</p>"
    "<p>Only the site owner can unblock it.</p></body></html>"
).encode("utf-8")


class BanMiddleware:
    """Pure ASGI: refuse every HTTP request from a banned network (403), except a signed-in admin."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from starlette.concurrency import run_in_threadpool
        from starlette.requests import Request

        from .ratelimit import client_ip
        from .security import SESSION_COOKIE, read_session

        request = Request(scope)
        try:
            banned = await run_in_threadpool(blocked_keys)  # store.get() may refresh the Gist
        except Exception:  # noqa: BLE001 - storage trouble must not take the site down
            banned = frozenset()
        ip = client_ip(request) if banned else ""
        if banned and (key_for(ip) in banned or network_key(ip) in banned) \
                and read_session(request.cookies.get(SESSION_COOKIE)) is None:
            wants_json = scope["path"].startswith(("/api/", "/admin/api/"))
            body = b'{"detail":"Access blocked: this network was blocked after repeated wrong admin passwords."}' \
                if wants_json else _BLOCKED_HTML
            await send({"type": "http.response.start", "status": 403, "headers": [
                (b"content-type", b"application/json" if wants_json else b"text/html; charset=utf-8"),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-store"),
            ]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)
