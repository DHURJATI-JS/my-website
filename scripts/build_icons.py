"""Build public/static/icons.svg from Line Awesome (Icons8's open-source icon set).

One small, cached SVG sprite instead of an icon font = lighter pages.
Usage:  .venv\\Scripts\\python scripts\\build_icons.py
Add an icon: put  "my-id": "line-awesome-file-name"  in ICONS and re-run;
use it in templates as {{ icon('my-id') }}.
"""
from __future__ import annotations

import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

VERSION = "1.3.0"
BASE = f"https://cdn.jsdelivr.net/npm/line-awesome@{VERSION}/svg/"
OUT = Path(__file__).resolve().parent.parent / "public" / "static" / "icons.svg"

ICONS = {
    "github": "github", "envelope": "envelope-solid", "whatsapp": "whatsapp", "star": "star-solid",
    "code-branch": "code-branch-solid", "dot-circle": "dot-circle", "check-circle": "check-circle",
    "sun": "sun-solid", "moon": "moon-solid", "language": "language-solid",
    "external-link": "external-link-alt-solid", "times": "times-solid", "search": "search-solid",
    "arrow-up": "arrow-up-solid", "arrow-right": "arrow-right-solid", "lock": "lock-solid",
    "sign-out": "sign-out-alt-solid", "sync": "sync-solid", "robot": "robot-solid", "save": "save-solid",
    "rss": "rss-solid", "bug": "bug-solid", "comment": "comment-solid", "tag": "tag-solid",
    "clock": "clock-solid", "shield": "shield-alt-solid", "check": "check-solid", "thumbtack": "thumbtack-solid",
    "map-marker": "map-marker-alt-solid", "birthday-cake": "birthday-cake-solid",
    "graduation-cap": "graduation-cap-solid", "briefcase": "briefcase-solid", "linkedin": "linkedin",
    "twitter": "twitter", "instagram": "instagram", "youtube": "youtube", "link": "link-solid",
    "rocket": "rocket-solid", "layer-group": "layer-group-solid", "download": "download-solid",
    "magic": "magic-solid", "brain": "brain-solid", "book-open": "book-open-solid", "code": "code-solid",
    "user": "user-solid", "home": "home-solid", "warning": "exclamation-triangle-solid",
    "info": "info-circle-solid", "server": "server-solid", "lightbulb": "lightbulb-solid",
    "users": "users-solid", "list": "list-ul-solid", "copy": "copy-solid", "bars": "bars-solid",
    "balance": "balance-scale-solid", "sort": "sort-amount-down-solid", "file": "file-alt-solid",
    "globe": "globe-solid", "plus": "plus-solid", "undo": "undo-solid", "bolt": "bolt-solid",
}

_PATH = re.compile(r'<path[^>]*\sd="([^"]+)"', re.I)
_VIEWBOX = re.compile(r'viewBox="([\d.\s-]+)"', re.I)
_SAFE_D = re.compile(r"^[MmZzLlHhVvCcSsQqTtAa0-9eE\s,.+-]+$")


def fetch(item: tuple[str, str]) -> tuple[str, str | None, str]:
    key, name = item
    try:
        with urllib.request.urlopen(BASE + name + ".svg", timeout=20) as resp:  # noqa: S310 - fixed https CDN
            svg = resp.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return key, None, f"{name}: {exc}"
    paths = [d for d in _PATH.findall(svg) if _SAFE_D.match(d)]  # keep path data only
    # 32x32 icons need no more than 2 decimals - roughly halves the sprite size
    paths = [re.sub(r"(\d+\.\d{2})\d+", r"\1", re.sub(r"\s+", " ", d).strip()) for d in paths]
    if not paths:
        return key, None, f"{name}: no usable <path>"
    match = _VIEWBOX.search(svg)
    view_box = match.group(1) if match else "0 0 32 32"
    body = "".join(f'<path d="{d}"/>' for d in paths)
    return key, f'<symbol id="i-{key}" viewBox="{view_box}">{body}</symbol>', ""


def main() -> int:
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(fetch, ICONS.items()))
    errors = [err for _, sym, err in results if sym is None]
    symbols = [sym for _, sym, _ in results if sym]
    OUT.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg">'
        f"<!-- Line Awesome {VERSION} by Icons8 (https://icons8.com/line-awesome) -->"
        + "".join(symbols) + "</svg>\n",
        encoding="utf-8",
    )
    print(f"wrote {OUT} - {len(symbols)} icons, {OUT.stat().st_size:,} bytes")
    for err in errors:
        print("  MISSING", err)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
