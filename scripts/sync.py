"""Sync GitHub + AI-categorise from the command line (no server needed).

  .venv\\Scripts\\python scripts\\sync.py           sync, then AI-categorise NEW repos only
  .venv\\Scripts\\python scripts\\sync.py --all     sync, then re-verify & re-categorise ALL repos
  .venv\\Scripts\\python scripts\\sync.py --no-ai   only refresh GitHub details

Typical Vercel workflow: run this on your PC (where the LAN AI gateway is reachable),
then commit data/portfolio.json and push - the site reads everything from that file.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from portfolio import ai, jobs  # noqa: E402
from portfolio.config import settings  # noqa: E402
from portfolio.github import GitHubError  # noqa: E402

LABEL = {"social": "Social impact", "industry": "Industry & startup"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--all", action="store_true", help="re-verify and re-categorise ALL repositories")
    mode.add_argument("--no-ai", action="store_true", help="only sync GitHub details")
    parser.add_argument("--include-locked", action="store_true", help="with --all: also override manual choices")
    args = parser.parse_args()

    print(f"GitHub user: {settings.github_username}  |  JSON: {settings.data_file}")
    try:
        report = jobs.sync_github()
    except GitHubError as exc:
        print(f"GitHub sync failed: {exc}")
        return 1
    print(f"Synced {report['repos']} repos, {report['readmes_refreshed']} README(s) refreshed"
          + (f", new: {', '.join(report['added'])}" if report["added"] else ""))
    for error in report["errors"]:
        print("  !", error)
    if args.no_ai:
        return 0

    names = jobs.plan("all" if args.all else "new", include_locked=args.include_locked)
    if not names:
        print("Nothing to categorise - every repository already has an AI group.")
        return 0
    print(f"AI will read {len(names)} README(s) via: " + " -> ".join(p.label for p in ai.providers()))
    failures = 0
    for index, name in enumerate(names, 1):
        started = time.monotonic()
        try:
            result = jobs.categorize(name)
        except ai.AIUnavailable as exc:
            print(f"[{index}/{len(names)}] {name}: AI unavailable - {exc}")
            return 2
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"[{index}/{len(names)}] {name}: failed - {exc}")
            continue
        print(f"[{index}/{len(names)}] {name} -> {LABEL[result['category']]} "
              f"({result['confidence']:.0%}, {result['provider']}, {time.monotonic() - started:.1f}s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
