"""Operations shared by the admin panel, the CLI, cron and the auto-sync thread.

Network work (GitHub, AI) always happens OUTSIDE the store lock; only the
quick merge into the JSON runs inside store.mutate(), so public pages are
never blocked while a sync or an AI analysis is running.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from . import ai, github
from .store import now_iso, store

log = logging.getLogger("portfolio")
_auto_lock = threading.Lock()


def find_repo(doc: dict[str, Any], name: str) -> tuple[str, dict[str, Any]]:
    wanted = name.strip().lower()
    for key, entry in doc["repos"].items():
        if entry.get("name", "").lower() == wanted:
            return key, entry
    raise KeyError(name)


def sync_github() -> dict[str, Any]:
    """Refresh repo details, READMEs, issues and activity (no AI)."""
    result = github.collect(store.get())
    return store.mutate(lambda doc: github.apply(doc, result))


def plan(mode: str, include_locked: bool = False) -> list[str]:
    """Repos to analyse. mode="new": only repos NOT yet in the JSON's AI data.
    mode="all": re-verify every repo (manually locked ones only if asked)."""
    doc = store.get()
    include_forks = bool(doc["settings"].get("include_forks"))
    names = []
    for entry in doc["repos"].values():
        if entry.get("gone") or (entry.get("fork") and not include_forks):
            continue
        current = entry.get("ai")
        if mode == "new" and current:
            continue
        if mode == "all" and current and current.get("locked") and not include_locked:
            continue
        names.append(entry["name"])
    return sorted(names, key=str.lower)


def categorize(name: str) -> dict[str, Any]:
    """AI-analyse one repo from its README and store the result permanently."""
    key, entry = find_repo(store.get(), name)
    result = ai.analyze(entry)  # slow network call, outside the lock
    result.update(readme_sha=entry.get("readme_sha", ""), categorized_at=now_iso(), locked=False)

    def save(doc: dict[str, Any]) -> dict[str, Any]:
        if key not in doc["repos"]:
            raise KeyError(name)
        doc["repos"][key]["ai"] = result
        return result

    return store.mutate(save)


def auto_run(budget_seconds: float | None = None) -> dict[str, Any]:
    """Sync GitHub, then categorize NEW repositories only (old ones are kept)."""
    if not _auto_lock.acquire(blocking=False):
        return {"skipped": "another sync is already running"}
    started = time.monotonic()
    report: dict[str, Any] = {"categorized": [], "errors": [], "deferred": []}
    try:
        report["sync"] = sync_github()
        pending = plan("new")
        for index, name in enumerate(pending):
            if budget_seconds and time.monotonic() - started > budget_seconds:
                report["deferred"] = pending[index:]
                break
            try:
                categorize(name)
                report["categorized"].append(name)
            except ai.AIUnavailable as exc:
                report["errors"].append(f"AI unavailable: {exc}")
                report["deferred"] = pending[index:]
                break
            except Exception as exc:  # noqa: BLE001 - keep going with the next repo
                report["errors"].append(f"{name}: {exc}")
    except github.GitHubError as exc:
        report["errors"].append(str(exc))
    finally:
        _auto_lock.release()
    report["seconds"] = round(time.monotonic() - started, 1)
    return report


class AutoSync:
    """Background loop for self-hosted runs (disabled on Vercel)."""

    def __init__(self, interval_minutes: int, first_delay: float = 15.0) -> None:
        self.interval = interval_minutes * 60
        self.first_delay = first_delay
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="portfolio-auto-sync", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        if self._stop.wait(self.first_delay):
            return
        while not self._stop.is_set():
            try:
                report = auto_run()
                log.info("auto-sync: %s", {k: v for k, v in report.items() if k != "sync"})
            except Exception:  # noqa: BLE001
                log.exception("auto-sync failed")
            if self._stop.wait(self.interval):
                return
