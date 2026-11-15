"""Security: Talisman-style headers + CSP nonces, admin sessions and CSRF.

Flask-Talisman only works with Flask, so `TalismanMiddleware` is a small
ASGI port of its core protections for FastAPI:
  * a fresh, unguessable CSP nonce per request (request.state.csp_nonce) -
    only <script>/<style> tags carrying it may run; no 'unsafe-inline'
  * strict Content-Security-Policy, HSTS on HTTPS, nosniff, Referrer-Policy,
    Permissions-Policy, COOP
  * public pages may be embedded in iframes (FRAME_ANCESTORS, default *),
    while /admin is never frameable (clickjacking protection)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any
from urllib.parse import urlsplit

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import settings

# MDB's bundle injects this one tiny <style> (input-autofill detection) at
# runtime; allowing it by hash keeps the nonce-only CSP free of console errors.
_MDB_AUTOFILL_CSS = (
    "INPUT:-webkit-autofill,SELECT:-webkit-autofill,TEXTAREA:-webkit-autofill{animation-name:onautofillstart}"
    "INPUT:not(:-webkit-autofill),SELECT:not(:-webkit-autofill),TEXTAREA:not(:-webkit-autofill)"
    "{animation-name:onautofillcancel}@keyframes onautofillstart{}@keyframes onautofillcancel{}"
)
_MDB_STYLE_HASH = "'sha256-" + base64.b64encode(hashlib.sha256(_MDB_AUTOFILL_CSS.encode()).digest()).decode() + "'"

CDN = "https://cdn.jsdelivr.net"


def _frame_ancestors(value: str) -> str:
    value = value.strip()
    if value in ("*", "'none'", "'self'"):
        return value
    sources = [part for part in value.replace(",", " ").split() if part]
    return " ".join(sources) or "*"


def build_csp(nonce: str, *, frame_ancestors: str, https: bool) -> str:
    n = f"'nonce-{nonce}'"
    directives = [
        "default-src 'self'",
        f"script-src 'self' {n} {CDN}",
        f"style-src 'self' {n} {_MDB_STYLE_HASH} {CDN} https://fonts.googleapis.com",
        f"font-src 'self' data: https://fonts.gstatic.com {CDN}",
        "img-src 'self' data: https:",
        "media-src 'self' https:",
        "connect-src 'self'",
        "frame-src 'none'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "manifest-src 'self'",
        "worker-src 'none'",
        f"frame-ancestors {frame_ancestors}",
    ]
    if https:
        directives.append("upgrade-insecure-requests")
    return "; ".join(directives)


def _is_https(scope: Scope) -> bool:
    if scope.get("scheme") == "https":
        return True
    if settings.trust_proxy:
        for key, value in scope.get("headers") or []:
            if key == b"x-forwarded-proto":
                return value.decode("latin-1").split(",")[0].strip() == "https"
    return False


class TalismanMiddleware:
    def __init__(self, app: ASGIApp, *, frame_ancestors: str = "*") -> None:
        self.app = app
        self.public_frame_ancestors = _frame_ancestors(frame_ancestors)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        nonce = secrets.token_urlsafe(18)
        scope.setdefault("state", {})["csp_nonce"] = nonce
        is_admin = scope.get("path", "").startswith("/admin")
        https = _is_https(scope)
        ancestors = "'none'" if is_admin else self.public_frame_ancestors
        csp = build_csp(nonce, frame_ancestors=ancestors, https=https)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if "content-security-policy" not in headers:
                    headers["Content-Security-Policy"] = csp
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                headers["Permissions-Policy"] = (
                    "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), "
                    "microphone=(), payment=(), usb=(), browsing-topics=()"
                )
                headers["Cross-Origin-Opener-Policy"] = "same-origin" if is_admin else "same-origin-allow-popups"
                if https:
                    headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
                if is_admin or ancestors == "'none'":
                    headers["X-Frame-Options"] = "DENY"
                elif ancestors == "'self'":
                    headers["X-Frame-Options"] = "SAMEORIGIN"
                if is_admin:
                    headers["Cache-Control"] = "no-store"
                    headers["X-Robots-Tag"] = "noindex, nofollow"
            await send(message)

        await self.app(scope, receive, send_with_headers)


def request_is_https(request: Request) -> bool:
    return _is_https(request.scope)


# ------------------------------------------------------------------ sessions --
SESSION_COOKIE = "pf_session"
LOGIN_CSRF_COOKIE = "pf_login_csrf"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(body: str) -> str:
    return _b64(hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).digest())


def _password_fingerprint() -> str:
    material = settings.admin_password_hash or settings.admin_password
    return hashlib.sha256(f"fp::{material}".encode()).hexdigest()[:16]


def create_session(username: str) -> str:
    now = int(time.time())
    payload = {"u": username, "iat": now, "exp": now + settings.session_hours * 3600,
               "csrf": secrets.token_urlsafe(24), "fp": _password_fingerprint()}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{_sign(body)}"


def read_session(token: str | None) -> dict[str, Any] | None:
    if not token or token.count(".") != 1:
        return None
    body, signature = token.split(".")
    if not hmac.compare_digest(signature, _sign(body)):
        return None
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("exp", 0) < time.time():
        return None
    if payload.get("fp") != _password_fingerprint() or not admin_configured():
        return None  # password changed -> every old session is invalid
    return payload


# ----------------------------------------------------------------- passwords --
_WEAK = {"", "admin", "password", "changeme", "change-me", "123456", "12345678", "qwerty"}


def admin_configured() -> bool:
    if not settings.admin_username:
        return False
    if settings.admin_password_hash:
        return settings.admin_password_hash.startswith("pbkdf2_sha256$")
    return settings.admin_password.lower() not in _WEAK and len(settings.admin_password) >= 8


def hash_password(password: str, iterations: int = 600_000) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64(salt)}${_b64(digest)}"


def _verify_hash(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), _unb64(salt), int(iterations))
        return hmac.compare_digest(_b64(digest), expected)
    except (ValueError, TypeError):
        return False


def verify_credentials(username: str, password: str) -> bool:
    if not admin_configured():
        return False
    user_ok = hmac.compare_digest(hashlib.sha256(username.encode()).digest(),
                                  hashlib.sha256(settings.admin_username.encode()).digest())
    if settings.admin_password_hash:
        pass_ok = _verify_hash(password, settings.admin_password_hash)
    else:
        pass_ok = hmac.compare_digest(hashlib.sha256(password.encode()).digest(),
                                      hashlib.sha256(settings.admin_password.encode()).digest())
    return user_ok and pass_ok


# ---------------------------------------------------------------------- CSRF --
def new_token() -> str:
    return secrets.token_urlsafe(24)


def tokens_match(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(str(a), str(b))


def same_origin(request: Request) -> bool:
    """Reject cross-site POSTs (defence in depth on top of CSRF tokens)."""
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return True  # non-browser client; the CSRF token still applies
    host = request.headers.get("host", "")
    if settings.trust_proxy:
        host = request.headers.get("x-forwarded-host", host).split(",")[0].strip()
    return urlsplit(origin).netloc.lower() == host.lower()
