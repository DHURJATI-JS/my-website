"""Unblock admin logins from the command line (for when you have locked yourself out).

  .venv\\Scripts\\python scripts\\unblock_ip.py            list blocked networks
  .venv\\Scripts\\python scripts\\unblock_ip.py --all      unblock every network
  .venv\\Scripts\\python scripts\\unblock_ip.py 1.2.3.4    unblock one address (or an IPv6 /64)

It uses the same .env as the site, so with STORAGE_BACKEND=gist it edits the live data.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from portfolio import lockout  # noqa: E402
from portfolio.store import store  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ip", nargs="?", help="address or IPv6 /64 network to unblock")
    parser.add_argument("--all", action="store_true", help="unblock every network")
    args = parser.parse_args()

    if not args.ip and not args.all:
        blocked = lockout.admin_view(store.get())["blocked"]
        if not blocked:
            print("Nothing is blocked.")
        for item in blocked:
            print(f"{item['ip']:<45} blocked {item.get('at', '')}  ({item.get('count', '?')} wrong passwords)")
        return 0
    target = "*" if args.all else lockout.network_key(args.ip)
    removed = lockout.unblock(target)
    print(f"Unblocked {removed} network(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
