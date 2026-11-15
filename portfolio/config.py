"""Settings, read from environment variables (plus a local `.env` file).

Real environment variables always win over `.env`, so on Vercel you configure
everything in Project -> Settings -> Environment Variables.
"""
from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = BASE_DIR / "public" / "static"


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (avoids the python-dotenv dependency)."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key:
            os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _list(name: str, default: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in _env(name, default).split(",") if part.strip())


def _asset_version() -> str:
    """Cache-busting token for /static URLs (changes on every deploy/edit)."""
    for key in ("VERCEL_GIT_COMMIT_SHA", "VERCEL_DEPLOYMENT_ID"):
        value = os.environ.get(key)
        if value:
            return value[:10]
    digest = hashlib.sha1()
    try:
        for path in sorted(STATIC_DIR.glob("*")):
            if path.is_file():
                stat = path.stat()
                digest.update(f"{path.name}:{stat.st_mtime_ns}:{stat.st_size}".encode())
    except OSError:
        pass
    return digest.hexdigest()[:10]


@dataclass(frozen=True)
class Settings:
    # Identity & contact
    github_username: str
    github_token: str
    site_url: str
    contact_email: str
    contact_whatsapp: str
    # Admin
    admin_username: str
    admin_password: str
    admin_password_hash: str
    secret_key: str
    secret_key_source: str
    session_hours: int
    # AI (OpenAI-compatible endpoints)
    ai_primary_url: str
    ai_primary_key: str
    ai_primary_model: str
    ai_fallback_url: str
    ai_fallback_key: str
    ai_fallback_model: str
    ai_connect_timeout: float
    ai_read_timeout: float
    ai_readme_chars: int
    ai_max_tokens: int
    # Storage (the single JSON document)
    storage_backend: str
    data_file: Path
    gist_id: str
    gist_token: str
    gist_filename: str
    # Web
    cors_origins: tuple[str, ...]
    frame_ancestors: str
    rate_limit_storage: str
    trust_proxy: bool
    enable_docs: bool
    # Automation
    auto_sync: bool
    auto_sync_minutes: int
    cron_secret: str
    on_vercel: bool
    asset_version: str


def _load() -> Settings:
    on_vercel = bool(os.environ.get("VERCEL"))
    admin_password = _env("ADMIN_PASSWORD")
    admin_password_hash = _env("ADMIN_PASSWORD_HASH")

    secret_key = _env("SECRET_KEY")
    source = "env"
    if not secret_key:
        material = admin_password_hash or admin_password
        if material:
            # Stable across serverless instances without extra config; rotating
            # the admin password also invalidates every existing session.
            secret_key = hashlib.sha256(f"portfolio-session::{material}".encode()).hexdigest()
            source = "derived"
        else:
            secret_key = secrets.token_hex(32)
            source = "random"

    data_file = Path(_env("DATA_FILE", "data/portfolio.json"))
    if not data_file.is_absolute():
        data_file = BASE_DIR / data_file

    frame_ancestors = _env("FRAME_ANCESTORS", "*") or "*"

    return Settings(
        github_username=_env("GITHUB_USERNAME", "DHURJATI-JS"),
        github_token=_env("GITHUB_TOKEN"),
        site_url=_env("SITE_URL").rstrip("/"),
        contact_email=_env("CONTACT_EMAIL", "dhurjatisharma2010@gmail.com"),
        contact_whatsapp=_env("CONTACT_WHATSAPP", "+917208555047"),
        admin_username=_env("ADMIN_USERNAME", "admin"),
        admin_password=admin_password,
        admin_password_hash=admin_password_hash,
        secret_key=secret_key,
        secret_key_source=source,
        session_hours=max(1, _int("SESSION_HOURS", 8)),
        ai_primary_url=_env("AI_PRIMARY_URL", "http://192.168.0.226:8000/v1").rstrip("/"),
        ai_primary_key=_env("AI_PRIMARY_KEY", "change-me-gateway-key"),
        ai_primary_model=_env("AI_PRIMARY_MODEL", "gemma"),
        ai_fallback_url=_env("AI_FALLBACK_URL", "http://127.0.0.1:11434/v1").rstrip("/"),
        ai_fallback_key=_env("AI_FALLBACK_KEY", "ollama"),
        ai_fallback_model=_env("AI_FALLBACK_MODEL", "gemma3:12b"),
        ai_connect_timeout=_float("AI_CONNECT_TIMEOUT", 4.0),
        ai_read_timeout=_float("AI_READ_TIMEOUT", 50.0 if on_vercel else 150.0),
        ai_readme_chars=max(1000, _int("AI_README_CHARS", 6000)),
        ai_max_tokens=max(300, _int("AI_MAX_TOKENS", 1100)),
        storage_backend=_env("STORAGE_BACKEND", "local").lower(),
        data_file=data_file,
        gist_id=_env("GIST_ID"),
        gist_token=_env("GIST_TOKEN") or _env("GITHUB_TOKEN"),
        gist_filename=_env("GIST_FILENAME", "portfolio.json"),
        cors_origins=_list("CORS_ORIGINS", "*") or ("*",),
        frame_ancestors=frame_ancestors,
        rate_limit_storage=_env("RATELIMIT_STORAGE_URI", "memory://"),
        trust_proxy=_bool("TRUST_PROXY", on_vercel),
        enable_docs=_bool("ENABLE_DOCS", False),
        auto_sync=_bool("AUTO_SYNC", True),
        auto_sync_minutes=max(5, _int("AUTO_SYNC_MINUTES", 60)),
        cron_secret=_env("CRON_SECRET"),
        on_vercel=on_vercel,
        asset_version=_asset_version(),
    )


settings = _load()
