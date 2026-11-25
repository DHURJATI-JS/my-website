"""Clean GitHub-rendered README / issue HTML before it is stored in the JSON.

* nh3 (Rust html sanitizer) removes scripts, event handlers, styles, iframes...
* relative links/images are rewritten to absolute GitHub URLs
* camo-proxied images use their original https URL (much smaller HTML)
* GitHub's syntax-highlighting classes (pl-*) and alert boxes are kept, so the
  site needs no client-side highlighter at all.
"""
from __future__ import annotations

import html as htmllib
import posixpath
import re
from urllib.parse import quote, urlsplit

import nh3

TAGS = {
    "a", "abbr", "b", "blockquote", "br", "caption", "code", "dd", "del", "details", "div",
    "dl", "dt", "em", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i",
    "img", "input", "ins", "kbd", "li", "mark", "ol", "p", "picture", "pre", "q", "rp", "rt",
    "ruby", "s", "samp", "small", "source", "span", "strike", "strong", "sub", "summary", "sup",
    "table", "tbody", "td", "tfoot", "th", "thead", "tr", "tt", "u", "ul", "var", "video",
}

ATTRIBUTES = {
    "*": {"align", "title"},
    "a": {"href"},
    "img": {"src", "alt", "width", "height"},
    "source": {"srcset", "media", "type"},
    "video": {"src", "poster", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
    "ol": {"start", "type"},
    "input": {"type", "checked"},
    "details": {"open"},
}

_PL = (
    "pl-c pl-c1 pl-c2 pl-s pl-s1 pl-v pl-e pl-en pl-smi pl-ent pl-k pl-pds pl-pse pl-sr pl-cce "
    "pl-sre pl-sra pl-bu pl-ii pl-ml pl-mh pl-ms pl-mi pl-mb pl-md pl-mi1 pl-mc pl-mi2 pl-mdr "
    "pl-ba pl-sg pl-corl"
).split()
CLASSES = {
    "span": set(_PL),
    "div": {
        "highlight", "markdown-alert", "markdown-alert-note", "markdown-alert-tip",
        "markdown-alert-important", "markdown-alert-warning", "markdown-alert-caution",
    },
    "p": {"markdown-alert-title"},
    "li": {"task-list-item"},
    "ul": {"contains-task-list"},
}

FORCED_ATTRS = {
    "a": {"target": "_blank"},
    "img": {"loading": "lazy", "decoding": "async", "referrerpolicy": "no-referrer"},
    "input": {"disabled": "disabled"},
    "video": {"controls": "controls", "preload": "metadata", "playsinline": "playsinline"},
}

_ATTR_RE = re.compile(r'([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*"([^"]*)"')
_IMG_RE = re.compile(r"<img\b[^>]*>", re.I)
_ANCHOR_RE = re.compile(r'<a\b[^>]*\bclass="[^"]*\banchor\b[^"]*"[^>]*>.*?</a>', re.I | re.S)
_SVG_RE = re.compile(r"<svg\b.*?</svg>", re.I | re.S)
_EMPTY_A_RE = re.compile(r"<a\b[^>]*>\s*</a>", re.I)
_DATA_PATH_RE = re.compile(r'<div\s+id="readme"[^>]*\bdata-path="([^"]*)"', re.I)
_NUM_RE = re.compile(r"\d{1,4}%?")


def _uncamo(raw: str) -> str:
    """Replace camo.githubusercontent.com proxies with the original https URL."""
    mapping: dict[str, str] = {}
    for tag in _IMG_RE.findall(raw):
        attrs = dict(_ATTR_RE.findall(tag))
        src, canonical = attrs.get("src", ""), attrs.get("data-canonical-src", "")
        if src.startswith("https://camo.githubusercontent.com/") and htmllib.unescape(canonical).startswith("https://"):
            mapping[src] = canonical
    for camo, canonical in mapping.items():
        raw = raw.replace(f'"{camo}"', f'"{canonical}"')
    return raw


class _UrlResolver:
    def __init__(self, owner: str, repo: str, branch: str, base_dir: str) -> None:
        self.repo_url = f"https://github.com/{owner}/{repo}"
        self.blob = f"https://github.com/{owner}/{repo}/blob/{branch}/"
        self.raw = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/"
        self.base_dir = base_dir.strip("/")

    def __call__(self, value: str, *, image: bool) -> str | None:
        v = value.strip()
        if not v:
            return None
        if v.startswith("#"):
            return f"{self.repo_url}{v}"  # README anchors open on GitHub
        if v.startswith("//"):
            return "https:" + v
        scheme = urlsplit(v).scheme.lower()
        if scheme in ("http", "https", "mailto"):
            return v
        if scheme:
            return None  # javascript:, data:, vbscript: ...
        if v.startswith("/"):
            return "https://github.com" + v
        path, _, fragment = v.partition("#")
        path, _, query = path.partition("?")
        joined = posixpath.normpath(posixpath.join(self.base_dir, path)) if path else self.base_dir
        joined = joined.lstrip("./") if joined.startswith("..") else joined
        joined = "" if joined == "." else joined
        url = (self.raw if image else self.blob) + quote(joined, safe="/%@:+-_.~!$&'()*,;=")
        if query:
            url += "?" + query
        if fragment:
            url += "#" + fragment
        return url


def clean_html(raw: str, owner: str, repo: str, branch: str = "HEAD", base_dir: str = "") -> str:
    """Sanitize GitHub-rendered HTML. Safe to inject with innerHTML afterwards."""
    if not raw:
        return ""
    if not base_dir:
        match = _DATA_PATH_RE.search(raw)
        if match:
            base_dir = posixpath.dirname(htmllib.unescape(match.group(1)))
    resolve = _UrlResolver(owner, repo, branch, base_dir)

    raw = _ANCHOR_RE.sub("", raw)
    raw = _SVG_RE.sub("", raw)
    raw = _uncamo(raw)

    def attribute_filter(tag: str, attr: str, value: str) -> str | None:
        if attr == "href":
            return resolve(value, image=False)
        if attr in ("src", "poster"):
            return resolve(value, image=True)
        if attr == "srcset":
            parts = []
            for candidate in value.split(","):
                bits = candidate.strip().split()
                if not bits:
                    continue
                url = resolve(bits[0], image=True)
                if url and url.startswith(("https://", "http://")):
                    parts.append(" ".join([url, *bits[1:2]]))
            return ", ".join(parts) or None
        if attr in ("width", "height"):
            return value if _NUM_RE.fullmatch(value.strip()) else None
        if attr == "align":
            return value if value.lower() in {"left", "right", "center", "justify"} else None
        if attr in ("colspan", "rowspan", "start"):
            return value if value.isdigit() and len(value) < 4 else None
        if attr == "type" and tag == "input":
            return "checkbox" if value == "checkbox" else None
        if attr == "type" and tag == "ol":
            return value if value in {"1", "a", "A", "i", "I"} else None
        return value

    cleaned = nh3.clean(
        raw,
        tags=TAGS,
        attributes=ATTRIBUTES,
        allowed_classes=CLASSES,
        attribute_filter=attribute_filter,
        set_tag_attribute_values=FORCED_ATTRS,
        link_rel="noopener noreferrer nofollow",
        url_schemes={"http", "https", "mailto"},
        strip_comments=True,
    )
    cleaned = _EMPTY_A_RE.sub("", cleaned)
    cleaned = cleaned.replace(' class=""', "")
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


_STATS_HINT = re.compile(
    r"ghstats|readme-stats|github-readme|streak|trophy|top-langs|/langs|activity-graph|"
    r"metrics|profile-summary|contribution|wakatime|leetcard|leetcode|stats",
    re.I,
)
_STATS_EXCLUDE = re.compile(r"komarev|visitor|views|hits\.|count", re.I)


def extract_stats_images(cleaned_html: str, limit: int = 8) -> list[dict[str, str]]:
    """Find GitHub-stats style cards (e.g. from the profile README)."""
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for tag in _IMG_RE.findall(cleaned_html):
        attrs = {k.lower(): htmllib.unescape(v) for k, v in _ATTR_RE.findall(tag)}
        src = attrs.get("src", "")
        if not src.startswith("https://") or src in seen:
            continue
        if _STATS_HINT.search(src) and not _STATS_EXCLUDE.search(src):
            seen.add(src)
            found.append({"src": src, "alt": attrs.get("alt", "")[:120]})
        if len(found) >= limit:
            break
    return found


_TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(fragment: str, limit: int = 4000) -> str:
    """Rough plain-text version of sanitized HTML (used as AI input fallback)."""
    text = re.sub(r"</(p|div|h\d|li|tr|pre|blockquote)>|<br\s*/?>", "\n", fragment, flags=re.I)
    text = re.sub(r"<li\b[^>]*>", "- ", text, flags=re.I)
    text = htmllib.unescape(_TAG_RE.sub("", text))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()[:limit]


# --------------------------------------------------------------- banners --
# Small or decorative images that are never a project's banner.
_BANNER_SKIP = re.compile(
    r"shields\.io|badgen|forthebadge|badge|skillicons|komarev|visitor|hits\.|typing-svg|readme-typing|"
    r"ghstats|readme-stats|streak|wakatime|icons8|/icons?/|favicon|avatar|logo|wordmark|app-icon|"
    r"\.gif(\?|$)|\.mp4(\?|$)",
    re.I,
)
_BANNER_HINT = re.compile(r"banner|hero|cover|social|preview|screenshot|demo", re.I)


def extract_banner(cleaned_html: str, scan: int = 20) -> str:
    """The README's top picture, if it looks like a banner or screenshot (not a badge, logo or GIF)."""
    for tag in _IMG_RE.findall(cleaned_html or "")[:scan]:
        attrs = {k.lower(): htmllib.unescape(v) for k, v in _ATTR_RE.findall(tag)}
        src = attrs.get("src", "")
        if not src.startswith("https://") or len(src) > 500:
            continue
        if _BANNER_SKIP.search(src) and not _BANNER_HINT.search(src.rsplit("/", 1)[-1]):
            continue
        width = attrs.get("width", "").rstrip("px").strip()
        if width.isdigit() and int(width) < 300:
            continue
        return src
    return ""
