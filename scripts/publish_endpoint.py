#!/usr/bin/env python3
"""Publish the AI gateway's tunnel address to the data Gist - sealed, so only the website's
backend can read it. The record is encrypted and signed with SECRET_KEY (the PC's .env and
Vercel share it); the Gist only ever shows {"v": 1, "box": "..."}.

start-tunnel.ps1 runs this each time the Cloudflare quick tunnel gets a new address:
    python scripts/publish_endpoint.py --url https://<random>.trycloudflare.com/v1
Check the settings without uploading anything:
    python scripts/publish_endpoint.py --check

Settings come from the environment, then .env in this folder's parent:
    GIST_ID, GIST_TOKEN or GITHUB_TOKEN   where the record goes
    SECRET_KEY                            seals it (or ADMIN_PASSWORD[_HASH], exactly like the site)
    AI_ENDPOINT_FILE                      the Gist file (default ai-endpoint.json)
    AI_GATEWAY_KEYS_FILE                  the gateway's gateway-keys.json (default: ..\\AI Server\\);
                                          its internet_key goes into the sealed record
Prints nothing secret: no address, no key, no token. Standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PURPOSE = "ai-endpoint"
URL_RE = re.compile(r"https://[A-Za-z0-9.-]+(:\d{1,5})?(/[A-Za-z0-9._~/-]*)?")


def load_sealed():
    path = ROOT / "portfolio" / "sealed.py"
    if not path.is_file():
        sys.exit("portfolio/sealed.py is missing - copy it from the repo next to this script's folder.")
    spec = importlib.util.spec_from_file_location("portfolio_sealed", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return values
    for line in lines:
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if m:
            values[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return values


def setting(env: dict[str, str], name: str, default: str = "") -> str:
    return (os.environ.get(name) or "").strip() or env.get(name, "").strip() or default


def secret_key(env: dict[str, str]) -> str:
    """The same key the site uses (portfolio/config.py): SECRET_KEY, else derived from the admin password."""
    key = setting(env, "SECRET_KEY")
    if key:
        return key
    material = setting(env, "ADMIN_PASSWORD_HASH") or setting(env, "ADMIN_PASSWORD")
    if material:
        return hashlib.sha256(f"portfolio-session::{material}".encode()).hexdigest()
    return ""


def internet_key(env: dict[str, str]) -> str:
    key = (os.environ.get("GATEWAY_INTERNET_KEY") or "").strip()
    if key:
        return key
    path = Path(setting(env, "AI_GATEWAY_KEYS_FILE") or ROOT.parent / "AI Server" / "gateway-keys.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return ""
    key = data.get("internet_key") if isinstance(data, dict) else ""
    return key if isinstance(key, str) else ""


def upload(gist_id: str, token: str, filename: str, content: str) -> None:
    body = json.dumps({"files": {filename: {"content": content}}}).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.github.com/gists/{gist_id}", data=body, method="PATCH",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "portfolio-tunnel",
                 "Content-Type": "application/json; charset=utf-8"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=30) as resp:
                resp.read()
            return
        except urllib.error.HTTPError as exc:
            try:
                message = str(json.loads(exc.read() or b"{}").get("message", ""))[:120]
            except ValueError:
                message = ""
            retry = exc.code in (403, 429) or exc.code >= 500
            if not retry or attempt == 3:
                raise RuntimeError(f"GitHub answered HTTP {exc.code} {message}".strip()) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt == 3:
                raise RuntimeError(f"could not reach GitHub ({exc.__class__.__name__})") from None
        time.sleep(5 * (attempt + 1))


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish the gateway's tunnel address, sealed.")
    parser.add_argument("--url", help="the tunnel's API address, e.g. https://<random>.trycloudflare.com/v1")
    parser.add_argument("--check", action="store_true", help="only check the settings; upload nothing")
    parser.add_argument("--env", default=str(ROOT / ".env"), help="path to .env (default: the repo's)")
    args = parser.parse_args()

    env = read_env(Path(args.env))
    gist_id = setting(env, "GIST_ID")
    token = setting(env, "GIST_TOKEN") or setting(env, "GITHUB_TOKEN")
    filename = setting(env, "AI_ENDPOINT_FILE", "ai-endpoint.json")
    secret, key = secret_key(env), internet_key(env)
    sealed = load_sealed()

    problems = []
    if not gist_id or not token:
        problems.append("GIST_ID and GITHUB_TOKEN (or GIST_TOKEN) are needed in .env")
    if not secret:
        problems.append("SECRET_KEY is needed in .env (the same value as on Vercel)")
    if len(key) < 32:
        problems.append("the gateway's internet_key wasn't found - set AI_GATEWAY_KEYS_FILE in .env "
                        "to the gateway-keys.json path (the gateway creates it on its first start)")
    if args.check:
        print(f"gist settings: {'ok' if gist_id and token else 'missing'} | secret: {'ok' if secret else 'missing'} | "
              f"internet key: {'ok' if len(key) >= 32 else 'missing'}")
        return 0 if not problems else 1
    if problems:
        print("Not published: " + "; ".join(problems))
        return 1
    url = (args.url or "").strip().rstrip("/")
    if not URL_RE.fullmatch(url):
        print("Not published: --url must be an https:// address")
        return 1

    # auth "sig1": the website signs every request with the key instead of sending it (see gateway.py)
    payload = {"url": url, "key": key, "auth": "sig1", "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    record = sealed.seal(payload, secret, PURPOSE)
    if sealed.open_sealed(record, secret, PURPOSE) != payload:  # never publish something the site can't open
        print("Not published: the sealed record did not open again")
        return 1
    try:
        upload(gist_id, token, filename, json.dumps(record))
    except RuntimeError as exc:
        print(f"Not published: {exc}")
        return 2
    print("Published the sealed tunnel record (address and key are encrypted).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
