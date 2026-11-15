"""Rate limiting with slowapi (`@limiter.limit("60/minute")` on every route).

The default in-memory storage is per process. On Vercel every instance has
its own counters; for global limits point RATELIMIT_STORAGE_URI at Redis
(e.g. Upstash: rediss://...) and add `redis` to requirements.txt.
"""
from __future__ import annotations

from slowapi import Limiter
from starlette.requests import Request

from .config import settings


def client_ip(request: Request) -> str:
    if settings.trust_proxy:  # Vercel sets these to the real client address
        for header in ("x-real-ip", "x-forwarded-for"):
            value = request.headers.get(header)
            if value:
                return value.split(",")[0].strip()
    return request.client.host if request.client else "anonymous"


limiter = Limiter(
    key_func=client_ip,
    storage_uri=settings.rate_limit_storage,
    headers_enabled=False,
    key_prefix="pf",
)

# Limits used across the app (kept in one place so they are easy to tune).
PAGE = "60/minute"
API = "120/minute"
LOGIN = "5/minute;30/hour"
ADMIN = "240/minute"
CRON = "12/hour"
