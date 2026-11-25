"""Generated preview pictures (1280x640 SVG) for projects that have no picture of their own.

Card pictures are picked in this order (views.banner):
  1. the repo's GitHub social preview (Settings -> Social preview), read during sync
  2. the first real picture in its README
  3. this generated card: project name, short summary, tech and group colour
Only presentation attributes are used (no <style>, no scripts, no external fonts), and every
piece of text is XML-escaped, so the SVG is inert wherever it is shown (site, README, social).
"""
from __future__ import annotations

import hashlib
from typing import Any
from xml.sax.saxutils import escape

W, H = 1280, 640
_DEFAULT_ACCENTS = {"social": "#1baf7a", "industry": "#eb6834", "other": "#3d6fd6"}
_FONT = "Segoe UI, Helvetica Neue, Arial, Noto Sans, Noto Sans Devanagari, sans-serif"


def _wrap(text: str, width: int, max_lines: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if len(candidate) <= width:
            line = candidate
            continue
        if line:
            lines.append(line)
        line = word[:width]
        if len(lines) == max_lines:
            break
    if line and len(lines) < max_lines:
        lines.append(line)
    if len(lines) == max_lines and len(" ".join(lines)) < len(" ".join(words)):
        lines[-1] = lines[-1][: width - 1].rstrip(" ,.;:") + "…"
    return lines


def _short_summary(text: str) -> str:
    text = " ".join((text or "").split())
    cut = text.find(". ", 80)
    return text[: cut + 1] if 0 < cut < 220 else text[:220]


def render(card: dict[str, Any], owner: str, accent: str = "") -> str:
    accent = accent or _DEFAULT_ACCENTS.get(card.get("category", ""), "#7c3aed")
    # a stable second colour per project so neighbouring cards don't look identical
    hue = int(hashlib.sha1(card.get("name", "").encode()).hexdigest()[:2], 16) * 360 // 256
    second = f"hsl({hue},70%,55%)"
    title = (card.get("title") or card.get("name") or "").split(":")[0].strip() or card.get("name", "")
    title_lines = _wrap(title, 24, 2)
    summary_lines = _wrap(_short_summary(card.get("summary") or card.get("description") or ""), 58, 3)
    tech = [t for t in (card.get("tech") or []) if t][:5]

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
        f'role="img" aria-label="{escape(title, {chr(34): "&quot;"})}">',
        "<defs>",
        '<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#0b1020"/><stop offset="1" stop-color="#161b33"/></linearGradient>',
        f'<radialGradient id="g1" cx="0.12" cy="0.1" r="0.7"><stop offset="0" stop-color="{accent}" stop-opacity="0.55"/>'
        f'<stop offset="1" stop-color="{accent}" stop-opacity="0"/></radialGradient>',
        f'<radialGradient id="g2" cx="0.95" cy="0.95" r="0.65"><stop offset="0" stop-color="{second}" stop-opacity="0.45"/>'
        f'<stop offset="1" stop-color="{second}" stop-opacity="0"/></radialGradient>',
        "</defs>",
        f'<rect width="{W}" height="{H}" fill="url(#bg)"/>',
        f'<rect width="{W}" height="{H}" fill="url(#g1)"/>',
        f'<rect width="{W}" height="{H}" fill="url(#g2)"/>',
    ]
    # faint grid
    for x in range(0, W, 64):
        out.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{H}" stroke="#ffffff" stroke-opacity="0.035"/>')
    for y in range(0, H, 64):
        out.append(f'<line x1="0" y1="{y}" x2="{W}" y2="{y}" stroke="#ffffff" stroke-opacity="0.035"/>')
    out.append(f'<rect x="72" y="72" width="10" height="{70 + 74 * len(title_lines)}" rx="5" fill="{accent}"/>')
    out.append(f'<text x="110" y="104" font-family="{_FONT}" font-size="26" font-weight="600" fill="#9aa4c7">'
               f'{escape(owner)} / {escape(card.get("name", ""))}</text>')
    y = 184
    for line in title_lines:
        out.append(f'<text x="108" y="{y}" font-family="{_FONT}" font-size="66" font-weight="800" fill="#ffffff">'
                   f"{escape(line)}</text>")
        y += 76
    y += 18
    for line in summary_lines:
        out.append(f'<text x="110" y="{y}" font-family="{_FONT}" font-size="30" fill="#c9d1ee">{escape(line)}</text>')
        y += 44
    x, chip_y = 110, H - 104
    for name in tech:
        w = 34 + int(len(name) * 14.5)
        if x + w > W - 90:
            break
        out.append(f'<rect x="{x}" y="{chip_y}" width="{w}" height="46" rx="23" fill="#ffffff" fill-opacity="0.08" '
                   f'stroke="{accent}" stroke-opacity="0.6"/>')
        out.append(f'<text x="{x + w / 2:.0f}" y="{chip_y + 31}" text-anchor="middle" font-family="{_FONT}" '
                   f'font-size="22" font-weight="600" fill="#e7ebff">{escape(name)}</text>')
        x += w + 14
    stars = int(card.get("stars") or 0)
    if stars:
        out.append(f'<text x="{W - 90}" y="104" text-anchor="end" font-family="{_FONT}" font-size="28" '
                   f'font-weight="700" fill="#ffd166">★ {stars}</text>')
    out.append("</svg>")
    return "\n".join(out)
