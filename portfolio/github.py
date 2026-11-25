"""GitHub sync: repos, READMEs, issues and activity -> the JSON document.

Only the admin panel, the CLI (scripts/sync.py), the Vercel cron job and the
local auto-sync thread call this module. Public page views never touch the
GitHub API, so the site stays fast and never hits GitHub rate limits.
"""
from __future__ import annotations

import base64
import json
import posixpath
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from . import httpclient
from .config import settings
from .sanitize import clean_html, extract_stats_images
from .store import now_iso

API = "https://api.github.com"
_HEX = re.compile(r"[0-9a-fA-F]{6}")


class GitHubError(Exception):
    pass


def _headers(accept: str) -> dict[str, str]:
    headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    return headers


def _get(path: str, *, accept: str = "application/vnd.github+json", params: dict | None = None,
         allow_404: bool = False) -> httpclient.Response | None:
    try:
        resp = httpclient.request("GET", API + path, headers=_headers(accept), params=params,
                                  connect_timeout=6, read_timeout=30)
    except httpclient.FetchError as exc:
        raise GitHubError(f"GitHub unreachable: {exc}") from exc
    if resp.status == 404 and allow_404:
        return None
    if resp.status == 429 or (resp.status == 403 and resp.headers.get("x-ratelimit-remaining") == "0"):
        reset = resp.headers.get("x-ratelimit-reset", "")
        when = (datetime.fromtimestamp(int(reset), timezone.utc).strftime("%H:%M UTC")
                if reset.isdigit() else "soon")
        hint = "" if settings.github_token else " Add GITHUB_TOKEN to raise the limit from 60 to 5,000 requests/hour."
        raise GitHubError(f"GitHub API rate limit reached (resets {when}).{hint}")
    if resp.status >= 400:
        try:
            message = resp.json().get("message", "")
        except ValueError:
            message = resp.text()[:200]
        raise GitHubError(f"GitHub API error {resp.status} on {path}: {message}")
    return resp


def safe_url(value: Any) -> str:
    """Only allow http(s) links (blocks javascript: and friends)."""
    url = str(value or "").strip()
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.I):
        if re.match(r"^[\w.-]+\.[a-z]{2,}(/.*)?$", url, re.I):
            url = "https://" + url
        else:
            return ""
    return url[:500]


def _compact_user(user: dict) -> dict[str, Any]:
    keys = ("login", "name", "avatar_url", "html_url", "bio", "company", "blog", "location",
            "followers", "following", "public_repos", "created_at")
    return {key: user.get(key) for key in keys}


def _details(repo: dict) -> dict[str, Any]:
    lic = repo.get("license") or {}
    spdx = lic.get("spdx_id")
    return {
        "id": repo["id"],
        "name": repo["name"],
        "full_name": repo["full_name"],
        "html_url": repo["html_url"],
        "homepage": safe_url(repo.get("homepage")),
        "description": (repo.get("description") or "").strip(),
        "language": repo.get("language") or "",
        "topics": list(repo.get("topics") or [])[:12],
        "stars": repo.get("stargazers_count", 0),
        "forks": repo.get("forks_count", 0),
        "open_issues": repo.get("open_issues_count", 0),
        "license": spdx if spdx and spdx != "NOASSERTION" else (lic.get("name") or ""),
        "default_branch": repo.get("default_branch") or "main",
        "created_at": repo.get("created_at"),
        "updated_at": repo.get("updated_at"),
        "pushed_at": repo.get("pushed_at"),
        "size_kb": repo.get("size", 0),
        "fork": bool(repo.get("fork")),
        "archived": bool(repo.get("archived")),
        "has_issues": bool(repo.get("has_issues")),
    }


def is_public(repo: dict) -> bool:
    """Only public repositories are ever shown on the site."""
    return not repo.get("private") and (repo.get("visibility") or "public") == "public"


def list_repos(username: str) -> list[dict]:
    repos: list[dict] = []
    for page in range(1, 11):
        resp = _get(f"/users/{username}/repos",
                    params={"per_page": 100, "page": page, "type": "owner", "sort": "pushed"})
        batch = resp.json() if resp else []
        repos.extend(batch)
        if len(batch) < 100:
            break
    return repos


def fetch_readme(full_name: str) -> dict[str, str] | None:
    resp = _get(f"/repos/{full_name}/readme", allow_404=True)
    if resp is None:
        return None
    meta = resp.json()
    text = ""
    if meta.get("encoding") == "base64" and meta.get("content"):
        text = base64.b64decode(meta["content"]).decode("utf-8", "replace")
    return {"sha": meta.get("sha", ""), "path": meta.get("path", "README.md"), "text": text}


def fetch_readme_html(full_name: str) -> str:
    resp = _get(f"/repos/{full_name}/readme", accept="application/vnd.github.html+json", allow_404=True)
    return resp.text() if resp else ""


_OG_IMAGE_RE = re.compile(r'<meta\s+property="og:image"\s+content="([^"]+)"', re.I)
_CUSTOM_OG_HOST = "https://repository-images.githubusercontent.com/"


def fetch_social_image(full_name: str) -> str | None:
    """The repo's custom social-preview image (Settings -> Social preview), read from its page.

    Returns "" when the repo has none, or None when GitHub could not be read (keep the old value).
    The REST API does not expose this image, and GraphQL needs a token, so the public page is used.
    """
    try:
        resp = httpclient.request("GET", f"https://github.com/{full_name}", headers={"Accept": "text/html"},
                                  connect_timeout=6, read_timeout=20, max_bytes=3_000_000)
    except httpclient.FetchError:
        return None
    if resp.status != 200:
        return None
    match = _OG_IMAGE_RE.search(resp.text()[:200_000])
    url = match.group(1).replace("&amp;", "&") if match else ""
    return url if url.startswith(_CUSTOM_OG_HOST) and len(url) <= 500 else ""


def fetch_social_images_graphql(full_names: list[str]) -> dict[str, str]:
    """Custom social-preview URLs for many repos in one GraphQL call (needs GITHUB_TOKEN).

    Returns {full_name: url or ""}; repos missing from the answer are left out (caller falls back).
    """
    if not settings.github_token or not full_names:
        return {}
    out: dict[str, str] = {}
    for start in range(0, len(full_names), 40):
        chunk = full_names[start:start + 40]
        parts = []
        for i, full_name in enumerate(chunk):
            owner, _, name = full_name.partition("/")
            parts.append(f'r{i}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) '
                         "{ openGraphImageUrl usesCustomOpenGraphImage }")
        try:
            resp = httpclient.request("POST", API + "/graphql", headers=_headers("application/json"),
                                      json_body={"query": "query { " + " ".join(parts) + " }"},
                                      connect_timeout=6, read_timeout=20)
            data = (resp.json() or {}).get("data") or {} if resp.ok else {}
        except (httpclient.FetchError, ValueError):
            continue
        for i, full_name in enumerate(chunk):
            node = data.get(f"r{i}")
            if not isinstance(node, dict):
                continue
            url = str(node.get("openGraphImageUrl") or "")
            out[full_name] = url if node.get("usesCustomOpenGraphImage") and url.startswith("https://") \
                and len(url) <= 500 else ""
    return out


def _excerpt(markdown: str) -> str:
    text = re.sub(r"<!--.*?-->", "", markdown, flags=re.S)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[: settings.ai_readme_chars]


def _readme_bundle(owner: str, repo: dict, previous: dict) -> dict[str, Any]:
    bundle: dict[str, Any] = {"readme_pushed_at": repo.get("pushed_at")}
    readme = fetch_readme(repo["full_name"])
    if readme is None:
        bundle.update(readme_sha="", readme_path="", readme_html="", readme_excerpt="")
        return bundle
    if readme["sha"] == previous.get("readme_sha") and previous.get("readme_html"):
        return bundle  # README unchanged - keep the stored HTML
    html = fetch_readme_html(repo["full_name"])
    bundle.update(
        readme_sha=readme["sha"],
        readme_path=readme["path"],
        readme_html=clean_html(html, owner, repo["name"], repo.get("default_branch") or "HEAD",
                               posixpath.dirname(readme["path"])),
        readme_excerpt=_excerpt(readme["text"]),
    )
    return bundle


def fetch_issues(owner: str) -> list[dict[str, Any]]:
    resp = _get("/search/issues", accept="application/vnd.github.html+json",
                params={"q": f"user:{owner} is:issue is:public", "sort": "updated", "order": "desc", "per_page": 60})
    items = (resp.json() if resp else {}).get("items") or []
    issues = []
    for item in items:
        repo = (item.get("repository_url") or "").rstrip("/").rsplit("/", 1)[-1]
        user = item.get("user") or {}
        labels = [
            {"name": str(label.get("name", ""))[:50],
             "color": label["color"] if _HEX.fullmatch(str(label.get("color") or "")) else ""}
            for label in item.get("labels") or [] if isinstance(label, dict)
        ]
        issues.append({
            "repo": repo,
            "number": item.get("number"),
            "title": (item.get("title") or "")[:300],
            "state": item.get("state") or "open",
            "state_reason": item.get("state_reason") or "",
            "url": item.get("html_url"),
            "user": user.get("login", ""),
            "avatar": user.get("avatar_url", ""),
            "labels": labels[:8],
            "comments": item.get("comments", 0),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "closed_at": item.get("closed_at"),
            "body_html": clean_html(item.get("body_html") or "", owner, repo),
        })
    return issues


_EVENT_TYPES = {
    "PushEvent": "push", "CreateEvent": "create", "WatchEvent": "star", "ForkEvent": "fork",
    "IssuesEvent": "issue", "PullRequestEvent": "pr", "PullRequestReviewEvent": "pr",
    "IssueCommentEvent": "comment", "ReleaseEvent": "release", "PublicEvent": "public",
}


def fetch_events(owner: str, limit: int = 10) -> list[dict[str, Any]]:
    resp = _get(f"/users/{owner}/events/public", params={"per_page": 60})
    events: list[dict[str, Any]] = []
    last = None
    for event in resp.json() if resp else []:
        kind = _EVENT_TYPES.get(event.get("type"), "other")
        repo = (event.get("repo") or {}).get("name", "")
        payload = event.get("payload") or {}
        item: dict[str, Any] = {"type": kind, "repo": repo, "at": event.get("created_at"),
                                "url": f"https://github.com/{repo}" if repo else f"https://github.com/{owner}"}
        if kind == "create":
            ref_type = payload.get("ref_type") or "repository"
            if ref_type == "repository":
                item["type"] = "create_repo"
            else:
                item.update(type="create_ref", ref_type=ref_type, ref=str(payload.get("ref") or "")[:60])
        target = payload.get("issue") or payload.get("pull_request") or payload.get("release") or {}
        if isinstance(target, dict) and str(target.get("html_url", "")).startswith("https://github.com/"):
            item["url"] = target["html_url"]
        signature = (item["type"], repo)
        if signature == last:
            continue  # collapse bursts (e.g. many pushes to one repo)
        last = signature
        events.append(item)
        if len(events) >= limit:
            break
    return events


def collect(doc: dict[str, Any]) -> dict[str, Any]:
    """Fetch everything from GitHub. Network only: `doc` is just read."""
    owner = settings.github_username
    user_resp = _get(f"/users/{owner}")
    repos = list_repos(owner)
    previous = doc.get("repos", {})
    gh_prev = doc.get("github", {})
    include_forks = bool(doc.get("settings", {}).get("include_forks"))
    result: dict[str, Any] = {"user": _compact_user(user_resp.json() if user_resp else {}),
                              "repos": {}, "errors": [], "profile": None, "issues": None,
                              "events": None, "readmes": 0}
    profile_repo = None
    jobs = []
    for repo in repos:
        if not is_public(repo):
            continue  # never show private/internal repos, whatever the token can see
        if repo["name"].lower() == owner.lower():
            profile_repo = repo  # the special profile README repo (holds GitHub stats)
            continue
        key = str(repo["id"])
        prev = previous.get(key, {})
        result["repos"][key] = _details(repo)
        if repo.get("fork") and not include_forks:
            continue
        if prev.get("readme_pushed_at") != repo.get("pushed_at") or "readme_html" not in prev:
            jobs.append((key, repo, prev))

    def work(job: tuple) -> tuple[str, dict | None, str]:
        key, repo, prev = job
        try:
            return key, _readme_bundle(owner, repo, prev), ""
        except GitHubError as exc:
            return key, None, f"{repo['name']}: {exc}"

    shown = [(key, result["repos"][key]["full_name"]) for key in result["repos"]
             if include_forks or not result["repos"][key]["fork"]]

    # Social previews: one GraphQL call when a token is set, else (or for anything it missed) the repo page.
    via_graphql = fetch_social_images_graphql([full_name for _, full_name in shown])

    def social(item: tuple[str, str]) -> tuple[str, str | None]:
        key, full_name = item
        if full_name in via_graphql:
            return key, via_graphql[full_name]
        return key, fetch_social_image(full_name)

    with ThreadPoolExecutor(max_workers=6) as pool:
        for key, bundle, error in pool.map(work, jobs):
            if error:
                result["errors"].append(error)
            elif bundle:
                result["repos"][key].update(bundle)
                result["readmes"] += 1
        for key, image in pool.map(social, shown):
            if image is not None:  # None = GitHub unreachable: keep what we had
                result["repos"][key]["social_image"] = image

    if profile_repo is None:
        result["profile"] = {"profile_readme_html": "", "stats_images": [], "profile_pushed_at": ""}
    elif profile_repo.get("pushed_at") != gh_prev.get("profile_pushed_at") or not gh_prev.get("profile_readme_html"):
        try:
            html = fetch_readme_html(profile_repo["full_name"])
            cleaned = clean_html(html, owner, profile_repo["name"], profile_repo.get("default_branch") or "HEAD")
            result["profile"] = {"profile_readme_html": cleaned,
                                 "stats_images": extract_stats_images(cleaned),
                                 "profile_pushed_at": profile_repo.get("pushed_at")}
        except GitHubError as exc:
            result["errors"].append(f"profile README: {exc}")

    for field, fetch in (("issues", fetch_issues), ("events", fetch_events)):
        try:
            result[field] = fetch(owner)
        except GitHubError as exc:
            result["errors"].append(f"{field}: {exc}")
    return result


def apply(doc: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Merge a collect() result into the document (runs inside store.mutate)."""
    repos = doc["repos"]
    added, gone = [], []
    for key, details in result["repos"].items():
        entry = repos.get(key)
        if entry is None:
            entry = repos[key] = {"ai": None}
            added.append(details["name"])
        entry.update(details)
        entry.pop("gone", None)
    for key, entry in repos.items():
        if key not in result["repos"]:
            if not entry.get("gone"):
                entry["gone"] = True  # keep its AI data in case it comes back
                gone.append(entry.get("name", key))
            # Deleted or made private: drop its README copy so it can't leak through the
            # data file or Gist. It is fetched again if the repo becomes public again.
            for field in ("readme_html", "readme_excerpt", "readme_sha", "readme_path", "readme_pushed_at",
                          "social_image"):
                entry.pop(field, None)
    github = doc["github"]
    github["user"] = result["user"]
    if result.get("profile") is not None:
        github.update(result["profile"])
    for field in ("issues", "events"):
        if result.get(field) is not None:
            github[field] = result[field]
    doc["synced_at"] = now_iso()
    return {"repos": len(result["repos"]), "added": added, "gone": gone,
            "readmes_refreshed": result["readmes"], "errors": result["errors"]}
