"""The ONE JSON document that powers the whole site.

Everything the public pages show - repository details, rendered README HTML,
AI categories + summaries, issues, activity feed and the editable profile -
lives in a single JSON file (``data/portfolio.json`` by default). Public
requests never call GitHub or the AI: they only read this file (cached in
memory and re-read only when it changes).

Backends
  local  data/portfolio.json on disk. If the disk is read-only (Vercel) writes
         go to /tmp instead and are TEMPORARY - see README "Deploying to Vercel".
  gist   a file inside a GitHub Gist, so admin changes persist on serverless.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import httpclient
from .config import BASE_DIR, settings

SCHEMA = 1


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_profile() -> dict[str, Any]:
    return {
        "name": "",
        "headline": "",
        "bio": "",
        "age": "",
        "birth_date": "",
        "location": "",
        "education": "",
        "role": "",
        "availability": "",
        "skills": [],
        "interests": [],
        "email": settings.contact_email,
        "whatsapp": settings.contact_whatsapp,
        "avatar_url": "",
        "resume_url": "",
        "website": "",
        "linkedin": "",
        "twitter": "",
        "instagram": "",
        "youtube": "",
        "show_age": True,
        "show_stats": True,
        "show_activity": True,
    }


def empty_doc() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "updated_at": None,
        "synced_at": None,
        "profile": default_profile(),
        "settings": {"hidden": [], "featured": [], "include_forks": False},
        # Research-grade projects: off until switched on in /admin (see portfolio/research.py).
        "research": {"enabled": False, "entries": []},
        "github": {
            "user": {},
            "profile_readme_html": "",
            "profile_readme_sha": "",
            "profile_pushed_at": "",
            "stats_images": [],
            "events": [],
            "issues": [],
        },
        "repos": {},
    }


def normalize(doc: Any) -> dict[str, Any]:
    """Fill in any keys missing from an older/hand-edited document."""
    base = empty_doc()
    if not isinstance(doc, dict):
        return base
    for key, default in base.items():
        value = doc.get(key)
        if isinstance(default, dict):
            merged = dict(default)
            if isinstance(value, dict):
                merged.update(value)
            doc[key] = merged
        elif key not in doc:
            doc[key] = default
    return doc


class StoreError(Exception):
    pass


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # Windows: a reader may briefly hold the file
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


class LocalBackend:
    name = "local"
    interval = 1.0  # seconds between cheap "did the file change?" checks

    def __init__(self, path: Path) -> None:
        self.path = path
        self.fallback = Path(tempfile.gettempdir()) / "portfolio-site" / path.name
        self.ephemeral = False

    def _active(self) -> Path:
        return self.fallback if self.ephemeral else self.path

    def stamp(self) -> Any:
        try:
            st = self._active().stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def changed(self, stamp: Any) -> bool:
        return self.stamp() != stamp

    def read(self) -> str | None:
        try:
            return self._active().read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise StoreError(f"cannot read {self._active()}: {exc}") from exc

    def write(self, text: str) -> None:
        if not self.ephemeral:
            try:
                _atomic_write(self.path, text)
                return
            except OSError:
                self.ephemeral = True  # read-only filesystem (e.g. Vercel)
        _atomic_write(self.fallback, text)

    def describe(self) -> dict[str, Any]:
        try:
            location = str(self.path.relative_to(BASE_DIR)).replace("\\", "/")
        except ValueError:
            location = str(self.path)
        persistent = not (self.ephemeral or settings.on_vercel)
        return {"backend": "local", "location": location, "persistent": persistent}


class GistBackend:
    name = "gist"
    interval = 20.0  # re-fetch the gist at most every 20 s per instance

    def __init__(self, gist_id: str, token: str, filename: str, seed: LocalBackend) -> None:
        self.gist_id = gist_id
        self.token = token
        self.filename = filename
        self.seed = seed
        # Other small files in the same Gist (e.g. ai-endpoint.json written by
        # scripts/start-tunnel.ps1). Refreshed on every read of the Gist.
        self.extras: dict[str, str] = {}

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def stamp(self) -> Any:
        return None

    def changed(self, stamp: Any) -> bool:
        return True

    def read(self) -> str | None:
        url = f"https://api.github.com/gists/{self.gist_id}"
        try:
            resp = httpclient.request("GET", url, headers=self._headers(), read_timeout=15)
        except httpclient.FetchError as exc:
            raise StoreError(f"Gist unreachable: {exc}") from exc
        if resp.status != 200:
            raise StoreError(f"Gist read failed (HTTP {resp.status})")
        files = resp.json().get("files") or {}
        self.extras = {
            name: f.get("content") or ""
            for name, f in files.items()
            if name != self.filename and isinstance(f, dict) and not f.get("truncated")
        }
        file = files.get(self.filename)
        if not file:
            return self.seed.read()  # first run: start from the bundled JSON
        if file.get("truncated") and file.get("raw_url"):
            raw = httpclient.request("GET", file["raw_url"], headers={"Authorization": f"Bearer {self.token}"})
            if not raw.ok:
                raise StoreError(f"Gist raw read failed (HTTP {raw.status})")
            return raw.text()
        return file.get("content")

    def write(self, text: str) -> None:
        url = f"https://api.github.com/gists/{self.gist_id}"
        payload = {"files": {self.filename: {"content": text}}}
        try:
            resp = httpclient.request("PATCH", url, headers=self._headers(), json_body=payload, read_timeout=30)
        except httpclient.FetchError as exc:
            raise StoreError(f"Gist unreachable: {exc}") from exc
        if not resp.ok:
            raise StoreError(f"Gist write failed (HTTP {resp.status})")

    def describe(self) -> dict[str, Any]:
        return {"backend": "gist", "location": f"gist {self.gist_id[:7]}… / {self.filename}", "persistent": True}


class Store:
    def __init__(self, backend: LocalBackend | GistBackend, warning: str = "") -> None:
        self.backend = backend
        self.warning = warning
        self._lock = threading.RLock()
        self._doc: dict[str, Any] | None = None
        self._stamp: Any = None
        self._checked = 0.0
        self.revision = 0
        self.last_error = ""

    def get(self) -> dict[str, Any]:
        """The cached document. Treat it as READ-ONLY - use mutate() to change it."""
        with self._lock:
            now = time.monotonic()
            if self._doc is None:
                self._reload(strict=False)
            elif now - self._checked >= self.backend.interval:
                self._checked = now
                if self.backend.changed(self._stamp):
                    self._reload(strict=False)
            return self._doc  # type: ignore[return-value]

    def _reload(self, *, strict: bool) -> None:
        stamp = self.backend.stamp()
        try:
            text = self.backend.read()
            doc = normalize(json.loads(text) if text else None)
        except (StoreError, ValueError) as exc:
            self.last_error = str(exc)
            if strict:
                raise StoreError(self.last_error) from exc
            if self._doc is None:
                self._doc = empty_doc()
                self.revision += 1
            return  # keep serving the last good copy
        self.last_error = ""
        self._doc, self._stamp = doc, stamp
        self._checked = time.monotonic()
        self.revision += 1

    def mutate(self, fn: Callable[[dict[str, Any]], Any]) -> Any:
        """Read-modify-write the document atomically; returns fn's result."""
        with self._lock:
            self._reload(strict=True)  # start from the freshest copy
            doc = copy.deepcopy(self._doc)
            result = fn(doc)
            doc["schema"] = SCHEMA
            doc["updated_at"] = now_iso()
            text = json.dumps(doc, ensure_ascii=False, indent=2)
            self.backend.write(text)
            self._doc = doc
            self._stamp = self.backend.stamp()
            self._checked = time.monotonic()
            self.revision += 1
            return result

    def extra_file(self, name: str) -> str:
        """Content of another small file in the Gist ('' for the local backend)."""
        self.get()  # refreshes the Gist (and its extra files) at most every `interval` s
        return getattr(self.backend, "extras", {}).get(name, "")

    def describe(self) -> dict[str, Any]:
        info = self.backend.describe()
        info.update(error=self.last_error, warning=self.warning)
        return info


def _make_store() -> Store:
    local = LocalBackend(settings.data_file)
    if settings.storage_backend == "gist":
        if settings.gist_id and settings.gist_token:
            return Store(GistBackend(settings.gist_id, settings.gist_token, settings.gist_filename, local))
        return Store(local, "STORAGE_BACKEND=gist needs GIST_ID and GIST_TOKEN (or GITHUB_TOKEN); using the local file.")
    return Store(local)


store = _make_store()
