"""Create the secret data Gist for Vercel and prepare the Vercel environment variables.

  python scripts/setup_gist.py                 create the Gist (if GIST_ID is empty) and save GIST_ID to .env
  python scripts/setup_gist.py --vercel-env URL also write .env.vercel for Vercel (SITE_URL = URL)

Uses GITHUB_TOKEN (or GIST_TOKEN) from .env - a fine-grained token with
"Gists: Read and write". The token is only sent to api.github.com and is never printed.
.env.vercel holds your secrets: paste its content into Vercel -> Project -> Settings ->
Environment Variables (paste into the Key field to import all at once), then delete it.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"

# Only needed on your PC (local server, local fallback AI, local file storage).
LOCAL_ONLY = {"DATA_FILE", "AI_FALLBACK_URL", "AI_FALLBACK_KEY", "AI_FALLBACK_MODEL", "AI_READ_TIMEOUT",
              "AUTO_SYNC", "AUTO_SYNC_MINUTES", "RATELIMIT_STORAGE_URI", "GIST_TOKEN"}


def read_env() -> tuple[list[str], dict[str, str]]:
    lines = ENV.read_text(encoding="utf-8").splitlines()
    values = {}
    for line in lines:
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", line)
        if m:
            values[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return lines, values


def set_env(name: str, value: str) -> None:
    lines, _ = read_env()
    for i, line in enumerate(lines):
        if re.match(rf"^\s*{name}\s*=", line):
            lines[i] = f"{name}={value}"
            break
    else:
        lines.append(f"{name}={value}")
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def github(method: str, url: str, token: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "portfolio-setup", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"GitHub API {method} {url} failed: HTTP {exc.code} {exc.read()[:200]!r}") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vercel-env", metavar="SITE_URL", help="also write .env.vercel for this Vercel URL")
    args = parser.parse_args()

    _, env = read_env()
    token = env.get("GIST_TOKEN") or env.get("GITHUB_TOKEN")
    if not token:
        print("Set GITHUB_TOKEN in .env first (fine-grained token with Gists: Read and write).")
        return 1
    filename = env.get("GIST_FILENAME") or "portfolio.json"
    data_file = ROOT / (env.get("DATA_FILE") or "data/portfolio.json")

    gist_id = env.get("GIST_ID", "")
    if gist_id:
        gist = github("GET", f"https://api.github.com/gists/{gist_id}", token)
        print(f"Gist already set: {gist_id} (files: {', '.join(gist.get('files', {}))})")
    else:
        gist = github("POST", "https://api.github.com/gists", token, {
            "description": f"{env.get('GITHUB_USERNAME', '')} portfolio site data (managed by the site)",
            "public": False,
            "files": {filename: {"content": data_file.read_text(encoding="utf-8")}},
        })
        gist_id = gist["id"]
        set_env("GIST_ID", gist_id)
        print(f"Created secret Gist {gist_id} with {filename} and saved GIST_ID to .env")

    if args.vercel_env:
        _, env = read_env()
        out = dict(env)
        for key in LOCAL_ONLY:
            out.pop(key, None)
        out.update(STORAGE_BACKEND="gist", GIST_ID=gist_id, GIST_FILENAME=filename,
                   SITE_URL=args.vercel_env.rstrip("/"), AI_ENDPOINT_FILE=env.get("AI_ENDPOINT_FILE") or "ai-endpoint.json")
        out = {k: v for k, v in out.items() if v != ""}
        target = ROOT / ".env.vercel"
        target.write_text("".join(f"{k}={v}\n" for k, v in out.items()), encoding="utf-8")
        print(f"Wrote {target.name} with {len(out)} variables: {', '.join(sorted(out))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
