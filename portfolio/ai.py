"""AI repo analyst: reads a README, picks the group and writes the summary.

Provider chain (both are OpenAI-compatible /v1/chat/completions endpoints):
  1. gateway - your AI gateway (model "gemma"). On Vercel its current Cloudflare
     quick-tunnel address AND key come from a sealed record in the data Gist (file
     AI_ENDPOINT_FILE, written by scripts/publish_endpoint.py on the PC): encrypted and
     signed with SECRET_KEY, so only this backend can read it. Locally, AI_PRIMARY_URL /
     AI_PRIMARY_KEY in .env can point at the gateway on your LAN instead.
  2. local   - Docker Gemma 3 12B via Ollama (AI_FALLBACK_URL, "gemma3:12b")
Addresses and keys never leave the backend: the admin page, cron output and error
messages only show labels (see redact()).
If both are down, the site shows a keyword-based *preview* (never saved), so
the repo is still treated as "new" and the AI analyses it on the next run.
"""
from __future__ import annotations

import ipaddress
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from . import httpclient, sealed
from .config import settings

CATEGORIES = ("social", "industry")


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    base_url: str = field(repr=False)  # never printed, logged or sent to a browser
    api_key: str = field(repr=False)
    model: str


ENDPOINT_PURPOSE = "ai-endpoint"
endpoint_status = {"problem": ""}  # shown on the admin page when the sealed record can't be used


def published_gateway() -> tuple[str, str]:
    """(address, key) of the gateway's Cloudflare tunnel, from the data Gist; ("", "") if none.

    scripts/start-tunnel.ps1 starts the tunnel on the home PC and, each time its random address
    changes, scripts/publish_endpoint.py seals {"url", "key"} with SECRET_KEY into the Gist file
    AI_ENDPOINT_FILE. Only this backend can open it; a changed or forged record is ignored.
    """
    name = settings.ai_endpoint_file
    if not name or settings.storage_backend != "gist":
        return "", ""
    try:
        from .store import store  # local import: store must not import ai

        raw = store.extra_file(name)
        data = json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001 - never let this break the site
        return "", ""
    if not isinstance(data, dict) or not data:
        return "", ""
    if "box" in data:
        opened = sealed.open_sealed(data, settings.secret_key, ENDPOINT_PURPOSE)
        if not opened:
            endpoint_status["problem"] = ("The gateway's sealed tunnel record can't be opened: SECRET_KEY in the "
                                          "PC's .env must be the same as on Vercel.")
            return "", ""
        url, key = str(opened.get("url") or ""), str(opened.get("key") or "")
    else:  # an older plain record (address only) - replaced by a sealed one when the PC's tunnel restarts
        url, key = str(data.get("url") or ""), settings.ai_primary_key
    url = url.strip().rstrip("/")
    if not url.startswith("https://"):
        return "", ""
    endpoint_status["problem"] = ""
    return url, key


_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s'\"<>()]+", re.I)
_TUNNEL_HOST_RE = re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.trycloudflare\.com\b", re.I)
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b")
_IPV6_CANDIDATE_RE = re.compile(r"\[?[0-9a-f]*:[0-9a-f:]*:[0-9a-f:.%]*\]?(?::\d{1,5})?", re.I)


def _hide_ipv6(match: re.Match) -> str:
    token = match.group(0)
    core = token.split("]")[0].lstrip("[").split("%")[0]
    try:
        ipaddress.IPv6Address(core)
    except ValueError:
        return token
    return "[hidden]"


def redact(text: object, *chain: Provider) -> str:
    """Text for the admin page, cron output and logs with every address and key taken out."""
    out = str(text)
    for provider in chain or tuple(_last_chain):
        parts = urlsplit(provider.base_url)
        for secret in (provider.base_url, parts.netloc, parts.hostname or "", provider.api_key):
            if secret and len(secret) >= 4:
                out = out.replace(secret, "[hidden]")
    out = _URL_RE.sub("[hidden]", out)
    out = _TUNNEL_HOST_RE.sub("[hidden]", out)
    out = _IPV4_RE.sub("[hidden]", out)
    return _IPV6_CANDIDATE_RE.sub(_hide_ipv6, out)


_last_chain: list[Provider] = []


def providers() -> list[Provider]:
    chain = []
    # On Vercel the LAN address is unreachable, so prefer the published tunnel.
    tunnel_url, tunnel_key = (published_gateway() if (settings.on_vercel or not settings.ai_primary_url)
                              else ("", ""))
    if tunnel_url:
        chain.append(Provider("gateway", "OpenAI gateway (Cloudflare tunnel)", tunnel_url,
                              tunnel_key, settings.ai_primary_model))
    elif settings.ai_primary_url:
        chain.append(Provider("gateway", "OpenAI gateway", settings.ai_primary_url,
                              settings.ai_primary_key, settings.ai_primary_model))
    if settings.ai_fallback_url:
        chain.append(Provider("local", "Local Docker Gemma 3 12B", settings.ai_fallback_url,
                              settings.ai_fallback_key, settings.ai_fallback_model))
    _last_chain[:] = chain
    return chain


class AIError(Exception):
    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class AIUnavailable(Exception):
    """Every provider failed."""


SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "confidence": {"type": "number"},
        "title": {"type": "string"},
        "reason": {"type": "string"},
        "summary": {"type": "string"},
        "problem": {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 6},
        "tech_stack": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        "audience": {"type": "string"},
    },
    "required": ["category", "confidence", "title", "reason", "summary", "problem",
                 "key_points", "tech_stack", "audience"],
    "additionalProperties": False,
}

PROMPT = """You are a precise analyst organising a student developer's GitHub portfolio.
Read the repository details and README, then put the project into exactly ONE group:

- "social": mainly solves a SOCIAL problem - wellbeing or mental health, education and
  learning access, healthcare access, community, environment and sustainability,
  accessibility and inclusion, safety, civic or non-profit causes, helping people in
  everyday life without a commercial focus.
- "industry": mainly solves an INDUSTRY / BUSINESS / STARTUP problem - running a
  business, management systems, commerce and payments, finance, cost estimation,
  productivity or automation for companies, developer tools, AI infrastructure, SaaS,
  or a product a startup could sell.
If both apply, choose the project's PRIMARY purpose and main users.

Then summarise it for portfolio visitors. Use only facts from the README and details;
never invent features. Plain English, no markdown, no emojis.

Return ONLY a JSON object with:
- "category": "social" or "industry"
- "confidence": number from 0 to 1
- "title": short human-friendly project name (max 6 words)
- "reason": one sentence on why this group fits (max 30 words)
- "summary": 2-3 sentences on what it does (max 70 words)
- "problem": the problem it solves, one sentence (max 30 words)
- "key_points": 3 to 6 key features or highlights (max 16 words each)
- "tech_stack": up to 8 main technologies, e.g. "Python", "FastAPI"
- "audience": who benefits most (max 12 words)

REPOSITORY
Name: {name}
Description: {description}
Primary language: {language}
Topics: {topics}
Homepage: {homepage}

README (may be truncated):
\"\"\"
{readme}
\"\"\"
"""


def build_prompt(entry: dict[str, Any]) -> str:
    from .sanitize import html_to_text

    readme = entry.get("readme_excerpt") or html_to_text(entry.get("readme_html") or "")
    return PROMPT.format(
        name=entry.get("name", ""),
        description=entry.get("description") or "(none)",
        language=entry.get("language") or "(unknown)",
        topics=", ".join(entry.get("topics") or []) or "(none)",
        homepage=entry.get("homepage") or "(none)",
        readme=(readme or "(no README)")[: settings.ai_readme_chars],
    )


def _chat(provider: Provider, prompt: str, *, structured: bool, schema: dict[str, Any] | None = None,
          name: str = "repo_analysis", max_tokens: int | None = None) -> str:
    payload: dict[str, Any] = {
        "model": provider.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": max_tokens or settings.ai_max_tokens,
        "stream": False,
    }
    if structured:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": name, "strict": True, "schema": schema or SCHEMA},
        }
    headers = {"Authorization": f"Bearer {provider.api_key}"} if provider.api_key else {}
    try:
        resp = httpclient.request("POST", f"{provider.base_url}/chat/completions", headers=headers,
                                  json_body=payload, connect_timeout=settings.ai_connect_timeout,
                                  read_timeout=settings.ai_read_timeout)
    except httpclient.FetchError as exc:
        raise AIError(f"{provider.label} unreachable ({redact(exc, provider)})") from None
    if resp.status >= 400:
        raise AIError(f"{provider.label} HTTP {resp.status}: {redact(resp.text()[:160], provider)}", resp.status)
    try:
        return resp.json()["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AIError(f"{provider.label} returned an unexpected response") from exc


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("model did not return a JSON object")
    return data


def _clip(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _str_list(value: Any, count: int, limit: int) -> list[str]:
    items: list[str] = []
    for item in value if isinstance(value, list) else []:
        text = _clip(item, limit).lstrip("-•* ")
        if text and text.lower() not in {i.lower() for i in items}:
            items.append(text)
        if len(items) >= count:
            break
    return items


def normalize(data: dict[str, Any]) -> dict[str, Any]:
    raw = str(data.get("category", "")).lower()
    if "social" in raw:
        category = "social"
    elif any(word in raw for word in ("industry", "startup", "business")):
        category = "industry"
    else:
        raise ValueError(f"unknown category {raw!r}")
    try:
        confidence = float(data.get("confidence", 0.7))
    except (TypeError, ValueError):
        confidence = 0.7
    if confidence > 1:
        confidence /= 100
    return {
        "category": category,
        "confidence": round(min(max(confidence, 0.0), 1.0), 2),
        "title": _clip(data.get("title"), 60),
        "reason": _clip(data.get("reason"), 260),
        "summary": _clip(data.get("summary"), 600),
        "problem": _clip(data.get("problem"), 260),
        "key_points": _str_list(data.get("key_points"), 6, 160),
        "tech_stack": _str_list(data.get("tech_stack"), 8, 32),
        "audience": _clip(data.get("audience"), 120),
    }


def analyze(entry: dict[str, Any]) -> dict[str, Any]:
    """Run the provider chain. Raises AIUnavailable if every provider fails."""
    prompt = build_prompt(entry)
    errors: list[str] = []
    for provider in providers():
        for structured in (True, False):
            started = time.monotonic()
            try:
                result = normalize(_extract_json(_chat(provider, prompt, structured=structured)))
            except AIError as exc:
                errors.append(str(exc))
                if structured and exc.status in (400, 404, 415, 422, 500):
                    continue  # server may not support json_schema - retry in plain JSON mode
                break  # unreachable / timeout -> next provider
            except ValueError as exc:
                errors.append(f"{provider.label}: invalid JSON ({exc})")
                continue
            result.update(method="ai", provider=provider.key, model=provider.model,
                          seconds=round(time.monotonic() - started, 1))
            return result
    raise AIUnavailable(redact("; ".join(errors)) or "no AI provider is configured")


# ------------------------------------------------------------- translation --
LANGUAGE_NAMES = {
    "hi": "Hindi (Devanagari script)", "mr": "Marathi (Devanagari script)", "es": "Spanish",
    "fr": "French", "de": "German", "ja": "Japanese", "ru": "Russian",
}
TRANSLATE_FIELDS = ("title", "summary", "problem", "reason", "audience", "key_points", "headline", "bio")
_LIMITS = {"title": 80, "summary": 900, "problem": 400, "reason": 400, "audience": 200,
           "headline": 400, "bio": 4500}

TRANSLATE_PROMPT = """Translate the values of this JSON object from English into {language}.
Rules:
- Keep project names, product names, technology names (Python, FastAPI, Docker, ...), code,
  numbers, emails and URLs exactly as they are.
- Natural, simple wording a visitor would read on a portfolio website. No markdown, no emojis.
- Keep blank lines between paragraphs.
- Return ONLY a JSON object with exactly the same keys. A list must stay a list with the same
  number of items, in the same order.

JSON:
{payload}
"""


def _translate_schema(fields: dict[str, Any]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for key, value in fields.items():
        if isinstance(value, list):
            props[key] = {"type": "array", "items": {"type": "string"},
                          "minItems": len(value), "maxItems": len(value)}
        else:
            props[key] = {"type": "string"}
    return {"type": "object", "properties": props, "required": list(fields), "additionalProperties": False}


def translate(fields: dict[str, Any], lang: str) -> dict[str, Any]:
    """Translate {field: text | [text]} into `lang`. Raises AIUnavailable if every provider fails."""
    if lang not in LANGUAGE_NAMES:
        raise ValueError(f"unsupported language {lang!r}")
    clean = {k: v for k, v in fields.items() if k in TRANSLATE_FIELDS and v}
    if not clean:
        return {}
    prompt = TRANSLATE_PROMPT.format(language=LANGUAGE_NAMES[lang],
                                     payload=json.dumps(clean, ensure_ascii=False, indent=1))
    schema = _translate_schema(clean)
    errors: list[str] = []
    for provider in providers():
        for structured in (True, False):
            try:
                raw = _chat(provider, prompt, structured=structured, schema=schema, name="translation",
                            max_tokens=max(settings.ai_max_tokens, 400 + len(prompt) // 2))
                data = _extract_json(raw)
                out: dict[str, Any] = {}
                for key, value in clean.items():
                    got = data.get(key)
                    if isinstance(value, list):
                        items = [_clip(x, 220) for x in got] if isinstance(got, list) else []
                        if len(items) != len(value) or not all(items):
                            raise ValueError(f"{key}: list changed length")
                        out[key] = items
                    else:
                        text = str(got or "").strip()
                        if not text:
                            raise ValueError(f"{key}: empty")
                        if key == "bio":  # keep paragraphs
                            text = "\n\n".join(re.sub(r"[ \t]+", " ", p).strip()
                                                for p in re.split(r"\n\s*\n", text) if p.strip())
                            out[key] = text[: _LIMITS[key]]
                        else:
                            out[key] = _clip(text, _LIMITS.get(key, 400))
                out.update(provider=provider.key, model=provider.model)
                return out
            except AIError as exc:
                errors.append(str(exc))
                if structured and exc.status in (400, 404, 415, 422, 500):
                    continue
                break
            except ValueError as exc:
                errors.append(f"{provider.label}: bad translation ({exc})")
                continue
    raise AIUnavailable(redact("; ".join(errors)) or "no AI provider is configured")


def ping(provider: Provider) -> dict[str, Any]:
    started = time.monotonic()
    headers = {"Authorization": f"Bearer {provider.api_key}"} if provider.api_key else {}
    try:
        resp = httpclient.request("GET", f"{provider.base_url}/models", headers=headers,
                                  connect_timeout=min(3.0, settings.ai_connect_timeout), read_timeout=6)
        ok = resp.ok
        models = []
        if ok:
            try:
                models = [m.get("id") for m in resp.json().get("data", []) if isinstance(m, dict)][:6]
            except (ValueError, AttributeError):
                models = []
        error = "" if ok else f"HTTP {resp.status}"
    except httpclient.FetchError as exc:
        ok, models, error = False, [], redact(exc, provider)
    return {  # no address or key: this goes to the browser
        "key": provider.key, "label": provider.label, "model": provider.model,
        "online": ok, "models": models, "error": error[:200],
        "latency_ms": round((time.monotonic() - started) * 1000),
    }


def is_private_url(url: str) -> bool:
    """True for LAN / loopback endpoints (unreachable from cloud hosts such as Vercel)."""
    host = urlsplit(url).hostname or ""
    if host == "localhost" or host.endswith((".local", ".lan", ".internal")):
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


_STATUS_TTL = 45.0  # seconds; keeps the public badge cheap and never hammers the gateway
_status_lock = threading.Lock()
_status: dict[str, Any] = {"at": -1e9, "data": None}
_TIERS = {"gateway": "primary", "local": "failsafe"}


def public_status() -> dict[str, Any]:
    """Online/offline for the navbar badge. Cached; exposes no URLs or IPs."""
    if _status["data"] is not None and time.monotonic() - _status["at"] < _STATUS_TTL:
        return _status["data"]
    with _status_lock:
        if _status["data"] is not None and time.monotonic() - _status["at"] < _STATUS_TTL:
            return _status["data"]
        chain = providers()

        def check(provider: Provider) -> dict[str, Any]:
            if settings.on_vercel and is_private_url(provider.base_url):
                return {"key": provider.key, "model": provider.model, "online": False, "latency_ms": None}
            return ping(provider)

        # Ping all in parallel, but stop waiting as soon as a higher-priority provider is up.
        pool = ThreadPoolExecutor(max_workers=max(1, len(chain)))
        futures = [pool.submit(check, provider) for provider in chain]
        tiers: list[dict[str, Any]] = []
        for provider, future in zip(chain, futures):
            result = future.result()
            tiers.append({"tier": _TIERS.get(provider.key, provider.key), "model": provider.model,
                          "online": bool(result["online"]),
                          "latency_ms": result["latency_ms"] if result["online"] else None})
            if result["online"]:
                break
        pool.shutdown(wait=False)
        for provider in chain[len(tiers):]:  # not needed - a better provider answered
            tiers.append({"tier": _TIERS.get(provider.key, provider.key), "model": provider.model,
                          "online": None, "latency_ms": None})
        active = next((t for t in tiers if t["online"]), None)
        data = {
            "online": active is not None,
            "tier": active["tier"] if active else None,
            "model": active["model"] if active else None,
            "latency_ms": active["latency_ms"] if active else None,
            "providers": tiers,
            "checked_at": int(time.time()),
        }
        _status.update(at=time.monotonic(), data=data)
        return data


# ---------------------------------------------------------------- heuristics --
# Display-only preview used while the AI has not analysed a repo yet.

_SOCIAL = (
    "social", "society", "community", "citizen", "civic", "ngo", "non-profit", "nonprofit", "charity",
    "donation", "volunteer", "education", "educational", "student", "school", "learning", "tutor",
    "teacher", "health", "healthcare", "medical", "patient", "hospital", "wellbeing", "well-being",
    "wellness", "mental", "mindfulness", "meditation", "stress", "anxiety", "therapy", "accessibility",
    "accessible", "disability", "blind", "deaf", "elderly", "inclusion", "inclusive", "rural",
    "farmer", "agriculture", "environment", "climate", "sustainab", "recycl", "waste", "pollution",
    "hunger", "poverty", "disaster", "emergency", "safety", "awareness", "humanitarian", "equality",
)
_INDUSTRY = (
    "business", "startup", "start-up", "company", "enterprise", "industry", "industrial", "commercial",
    "saas", "b2b", "customer", "client", "revenue", "profit", "sales", "marketing", "e-commerce",
    "ecommerce", "shop", "store", "cart", "checkout", "payment", "invoice", "billing", "subscription",
    "pricing", "finance", "fintech", "banking", "accounting", "payroll", "employee", "staff",
    "management", "inventory", "logistics", "supply chain", "crm", "erp", "analytics", "dashboard",
    "automation", "workflow", "productivity", "operations", "manufacturing", "construction", "estimat",
    "quotation", "gateway", "infrastructure", "deployment", "scalable", "multi-tenant", "admin",
    "owner", "gym", "server", "api",
)
_TECH = (
    "Python", "FastAPI", "Flask", "Django", "Jinja2", "JavaScript", "TypeScript", "React", "Next.js",
    "Vue", "Svelte", "Node.js", "Express", "HTML", "CSS", "Tailwind", "Bootstrap", "PostgreSQL",
    "MySQL", "SQLite", "MongoDB", "Redis", "pgvector", "Docker", "Kubernetes", "Ollama", "llama.cpp",
    "OpenAI", "Gemma", "LangChain", "PyTorch", "TensorFlow", "scikit-learn", "OpenCV", "Tesseract",
    "Pandas", "NumPy", "Streamlit", "Gradio", "WebSocket", "WebRTC", "Stripe", "Razorpay", "Firebase",
    "Supabase", "Vercel", "Nginx", "Celery", "SQLAlchemy", "Pydantic", "Electron", "Flutter",
    "Kotlin", "Java", "C++", "Go", "Rust", "PHP", "Selenium", "Playwright",
)
_TECH_RE = [(name, re.compile(r"(?<![\w.+-])" + re.escape(name) + r"(?![\w+-])", re.I)) for name in _TECH]


def _score(text: str, words: tuple[str, ...]) -> int:
    return sum(len(re.findall(r"\b" + re.escape(word), text)) for word in words)


def heuristic(entry: dict[str, Any]) -> dict[str, Any]:
    head = " ".join([entry.get("name", "").replace("-", " ").replace("_", " "),
                     entry.get("description", ""), " ".join(entry.get("topics") or [])]).lower()
    readme = (entry.get("readme_excerpt") or "").lower()
    social = _score(head, _SOCIAL) * 3 + _score(readme, _SOCIAL)
    industry = _score(head, _INDUSTRY) * 3 + _score(readme, _INDUSTRY)
    category = "social" if social > industry else "industry"
    total = social + industry
    confidence = round(max(social, industry) / total, 2) if total else 0.5

    lines = [ln.strip() for ln in (entry.get("readme_excerpt") or "").splitlines()]
    summary = entry.get("description") or ""
    if not summary:
        for line in lines:
            if line and not line.startswith(("#", "!", "<", "[", "|", "```", "-", "*", ">")) and len(line) > 40:
                summary = re.sub(r"[*_`]", "", line)
                break
    points = []
    for line in lines:
        match = re.match(r"^[-*+]\s+(.*)", line)
        if match:
            point = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", match.group(1))
            point = re.sub(r"[*_`]|<[^>]+>", "", point).strip()
            if 12 <= len(point) <= 160:
                points.append(point)
        if len(points) >= 5:
            break
    source = entry.get("readme_excerpt") or ""
    tech = [name for name, rx in _TECH_RE if rx.search(source)][:8]
    if entry.get("language") and entry["language"] not in tech:
        tech.insert(0, entry["language"])
    return {
        "category": category, "confidence": confidence, "title": "", "reason": "",
        "summary": _clip(summary, 400), "problem": "", "key_points": points,
        "tech_stack": tech[:8], "audience": "", "method": "heuristic", "provider": "", "model": "",
    }
