"""Admin-only backup of every repository: READ-ONLY and secret-free.

* Nothing here can change a repository. Only GET requests are sent to GitHub, and the
  downloadable script only runs `git clone --mirror` (first time) and `git remote update`
  (later runs) - both download-only. It never pushes, deletes or edits anything.
* No secret ever leaves the server. The site's GITHUB_TOKEN is used only on the server, only to
  LIST repositories and branches. ZIP links point at github.com's normal public archive URLs
  (public repositories only), and the script contains no token: for private repositories git
  uses your own GitHub sign-in on your PC.
* Nothing is stored: the list is built for each request and never written to the JSON/Gist.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from . import github
from .config import settings

OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/+-]{1,200}$")


class BackupError(Exception):
    pass


def owner() -> str:
    name = settings.github_username
    if not OWNER_RE.match(name or ""):
        raise BackupError("GITHUB_USERNAME is not a valid GitHub user name.")
    return name


def _safe_branch(name: str) -> bool:
    return bool(BRANCH_RE.match(name)) and ".." not in name and not name.startswith("/") \
        and not name.endswith(("/", ".lock")) and "//" not in name


def _all_repos(user: str) -> tuple[list[dict], bool]:
    """(repos, includes_private). With a token, every repo it can see (private too); else public."""
    if settings.github_token:
        repos: list[dict] = []
        try:
            for page in range(1, 11):
                resp = github._get("/user/repos", params={"per_page": 100, "page": page,
                                                          "affiliation": "owner", "visibility": "all"})
                batch = resp.json() if resp else []
                repos.extend(r for r in batch if (r.get("owner") or {}).get("login", "").lower() == user.lower())
                if len(batch) < 100:
                    break
            if repos:
                return repos, True
        except github.GitHubError:
            pass  # e.g. a token without account access: fall back to the public list
    return github.list_repos(user), False


def _branches(user: str, repo: str) -> list[str]:
    names: list[str] = []
    for page in range(1, 6):
        resp = github._get(f"/repos/{user}/{repo}/branches", params={"per_page": 100, "page": page},
                           allow_404=True)
        batch = resp.json() if resp else []
        names.extend(b.get("name", "") for b in batch if isinstance(b, dict))
        if len(batch) < 100:
            break
    return [n for n in names if _safe_branch(n)]


def list_repos() -> dict[str, Any]:
    user = owner()
    try:
        raw, token_list = _all_repos(user)
    except github.GitHubError as exc:
        raise BackupError(str(exc)) from exc
    repos = [r for r in raw if REPO_RE.match(r.get("name") or "")]

    def detail(r: dict) -> dict[str, Any]:
        try:
            branches = _branches(user, r["name"])
        except github.GitHubError:
            branches = []
        default = r.get("default_branch") or ""
        if default in branches:
            branches = [default] + [b for b in branches if b != default]
        return {
            "name": r["name"], "private": bool(r.get("private")), "fork": bool(r.get("fork")),
            "archived": bool(r.get("archived")), "size_kb": int(r.get("size") or 0),
            "default_branch": default, "branches": branches, "pushed_at": r.get("pushed_at") or "",
        }

    with ThreadPoolExecutor(max_workers=6) as pool:
        items = list(pool.map(detail, repos))
    items.sort(key=lambda x: x["name"].lower())
    return {
        "owner": user,
        "repos": items,
        "private_listed": token_list and any(x["private"] for x in items),
        "token": bool(settings.github_token),
        "total_kb": sum(x["size_kb"] for x in items),
        "branch_count": sum(len(x["branches"]) for x in items),
    }


# ------------------------------------------------------------------ scripts --
def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def script_cmd(data: dict[str, Any]) -> str:
    """Windows: double-click to back up (or update the backup of) every repository with git."""
    user = data["owner"]
    names = [r["name"] for r in data["repos"] if REPO_RE.match(r["name"])]
    lines = [
        "@echo off",
        f"rem GitHub backup for {user} - made by your portfolio site on {_stamp()}.",
        "rem READ-ONLY: it only downloads (git clone --mirror, then git remote update on later runs).",
        "rem It never pushes, deletes or changes anything on GitHub, and it contains no passwords or tokens.",
        "rem Private repositories use your own GitHub sign-in (Git Credential Manager asks the first time).",
        "rem Restore any repo later with:  git clone \"%USERPROFILE%\\GitHub-Backup\\" + user + "\\NAME.git\" NAME",
        "setlocal",
        f"set \"ROOT=%USERPROFILE%\\GitHub-Backup\\{user}\"",
        "where git >nul 2>nul",
        "if errorlevel 1 (",
        "  echo Git is not installed. Get it from https://git-scm.com/download/win and run this again.",
        "  pause",
        "  exit /b 1",
        ")",
        "if not exist \"%ROOT%\" mkdir \"%ROOT%\"",
        "set FAILED=",
        "rem If GitHub CLI is installed and signed in (gh auth login), it lists EVERY repository - private ones too.",
        "where gh >nul 2>nul",
        "if errorlevel 1 goto listed",
        "echo Asking GitHub CLI for your full repository list - private ones too ...",
        f"for /f \"delims=\" %%R in ('gh repo list {user} --limit 1000 --json name --jq \".[].name\" 2^>nul') do call :backup %%R",
        ":listed",
        *[f"call :backup {name}" for name in names],
        "echo.",
        "if defined FAILED (echo Some repositories failed:%FAILED%) else (echo All repositories backed up.)",
        "echo Backups are in %ROOT%",
        "pause",
        "exit /b 0",
        "",
        ":backup",
        "if defined DONE_%~1 exit /b 0",
        "set \"DONE_%~1=1\"",
        "if exist \"%ROOT%\\%~1.git\" (",
        "  echo Updating %~1 ...",
        "  git -C \"%ROOT%\\%~1.git\" remote update --prune",
        ") else (",
        "  echo Downloading %~1 - every branch, tag and the full history ...",
        f"  git clone --mirror \"https://github.com/{user}/%~1.git\" \"%ROOT%\\%~1.git\"",
        ")",
        "if errorlevel 1 set \"FAILED=%FAILED% %~1\"",
        "exit /b 0",
        "",
    ]
    return "\r\n".join(lines)


def script_sh(data: dict[str, Any]) -> str:
    """macOS / Linux: `sh github-backup.sh` backs up (or updates) every repository with git."""
    user = data["owner"]
    names = [r["name"] for r in data["repos"] if REPO_RE.match(r["name"])]
    lines = [
        "#!/bin/sh",
        f"# GitHub backup for {user} - made by your portfolio site on {_stamp()}.",
        "# READ-ONLY: it only downloads (git clone --mirror, then git remote update on later runs).",
        "# It never pushes, deletes or changes anything on GitHub, and it contains no passwords or tokens.",
        "# Private repositories use your own GitHub sign-in (git asks / uses your keychain).",
        f"# Restore any repo later with:  git clone \"$HOME/GitHub-Backup/{user}/NAME.git\" NAME",
        "set -u",
        f"ROOT=\"$HOME/GitHub-Backup/{user}\"",
        "command -v git >/dev/null 2>&1 || { echo 'Git is not installed (https://git-scm.com).'; exit 1; }",
        "mkdir -p \"$ROOT\"",
        "FAILED=''",
        "NAMES='" + " ".join(names) + "'",
        "# If GitHub CLI is installed and signed in (gh auth login), it lists EVERY repository - private ones too.",
        "if command -v gh >/dev/null 2>&1; then",
        "  echo 'Asking GitHub CLI for your full repository list (private ones too) ...'",
        f"  NAMES=\"$NAMES $(gh repo list {user} --limit 1000 --json name --jq '.[].name' 2>/dev/null || true)\"",
        "fi",
        "NAMES=$(printf '%s\\n' $NAMES | grep -E '^[A-Za-z0-9._-]+$' | sort -u)",
        "for NAME in $NAMES",
        "do",
        "  if [ -d \"$ROOT/$NAME.git\" ]; then",
        "    echo \"Updating $NAME ...\"",
        "    git -C \"$ROOT/$NAME.git\" remote update --prune || FAILED=\"$FAILED $NAME\"",
        "  else",
        "    echo \"Downloading $NAME - every branch, tag and the full history ...\"",
        f"    git clone --mirror \"https://github.com/{user}/$NAME.git\" \"$ROOT/$NAME.git\" || FAILED=\"$FAILED $NAME\"",
        "  fi",
        "done",
        "if [ -n \"$FAILED\" ]; then echo \"Some repositories failed:$FAILED\"; else echo 'All repositories backed up.'; fi",
        "echo \"Backups are in $ROOT\"",
        "",
    ]
    return "\n".join(lines)
