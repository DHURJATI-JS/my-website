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
"""
from __future__ import annotations

import ipaddress
from datetime import datetime, timedelta, timezone
from typing import Any

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


def _section(doc: dict[str, Any]) -> dict[str, Any]:
    sec = doc.setdefault("security", {})
    sec.setdefault("failures", {})
    sec.setdefault("blocked", {})
    return sec


def _parse(ts: str) -> datetime:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.fromtimestamp(0, timezone.utc)


def blocked_keys() -> frozenset[str]:
    sec = store.get().get("security") or {}
    return frozenset((sec.get("blocked") or {}).keys())


def is_blocked(ip: str) -> bool:
    sec = store.get().get("security") or {}
    return network_key(ip) in (sec.get("blocked") or {})


def record_failure(ip: str, user_agent: str = "") -> dict[str, Any]:
    """Count one wrong password. Returns {"count": n, "blocked": bool, "left": attempts left}."""
    key = network_key(ip)

    def apply(doc: dict[str, Any]) -> dict[str, Any]:
        sec = _section(doc)
        if key in sec["blocked"]:
            return {"count": sec["blocked"][key].get("count", MAX_FAILURES), "blocked": True, "left": 0}
        now = datetime.now(timezone.utc)
        entry = sec["failures"].get(key)
        if not entry or now - _parse(entry.get("first", "")) > FAILURE_WINDOW:
            entry = {"count": 0, "first": now_iso()}
        entry["count"] = int(entry.get("count", 0)) + 1
        entry["last"] = now_iso()
        if entry["count"] >= MAX_FAILURES:
            sec["failures"].pop(key, None)
            sec["blocked"][key] = {"at": now_iso(), "count": entry["count"], "first": entry["first"],
                                   "agent": (user_agent or "")[:160]}
            return {"count": entry["count"], "blocked": True, "left": 0}
        sec["failures"][key] = entry
        if len(sec["failures"]) > MAX_TRACKED:  # drop the oldest
            for old in sorted(sec["failures"], key=lambda k: sec["failures"][k].get("last", ""))[:-MAX_TRACKED]:
                sec["failures"].pop(old, None)
        return {"count": entry["count"], "blocked": False, "left": MAX_FAILURES - entry["count"]}

    return store.mutate(apply)


def clear_failures(ip: str) -> None:
    """A correct password resets that network's count (only writes if there was one)."""
    key = network_key(ip)
    if key in ((store.get().get("security") or {}).get("failures") or {}):
        store.mutate(lambda doc: _section(doc)["failures"].pop(key, None))


def unblock(target: str) -> int:
    """Unblock one network (exact key from the list) or "*" for all. Returns how many were removed."""
    def apply(doc: dict[str, Any]) -> int:
        sec = _section(doc)
        if target == "*":
            removed = len(sec["blocked"])
            sec["blocked"].clear()
            sec["failures"].clear()
            return removed
        removed = 1 if sec["blocked"].pop(target, None) else 0
        sec["failures"].pop(target, None)
        return removed

    return store.mutate(apply)


def admin_view(doc: dict[str, Any], current_ip: str = "") -> dict[str, Any]:
    sec = doc.get("security") or {}
    blocked = [{"ip": k, **v} for k, v in (sec.get("blocked") or {}).items()]
    blocked.sort(key=lambda x: x.get("at", ""), reverse=True)
    now = datetime.now(timezone.utc)
    watching = [{"ip": k, **v} for k, v in (sec.get("failures") or {}).items()
                if now - _parse(v.get("first", "")) <= FAILURE_WINDOW]
    watching.sort(key=lambda x: x.get("last", ""), reverse=True)
    return {"blocked": blocked, "watching": watching[:50], "max_failures": MAX_FAILURES,
            "you": network_key(current_ip) if current_ip else ""}


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
        if banned and network_key(client_ip(request)) in banned \
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
