"""AI repo analyst: reads a README, picks the group and writes the summary.

Provider chain (both are OpenAI-compatible /v1/chat/completions endpoints):
  1. gateway - your LAN gateway  (AI_PRIMARY_URL,  model "gemma")
  2. local   - Docker Gemma 3 12B via Ollama (AI_FALLBACK_URL, "gemma3:12b")
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
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from . import httpclient
from .config import settings

CATEGORIES = ("social", "industry")


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    base_url: str
    api_key: str
    model: str


def providers() -> list[Provider]:
    chain = []
    if settings.ai_primary_url:
        chain.append(Provider("gateway", "OpenAI gateway", settings.ai_primary_url,
                              settings.ai_primary_key, settings.ai_primary_model))
    if settings.ai_fallback_url:
        chain.append(Provider("local", "Local Docker Gemma 3 12B", settings.ai_fallback_url,
                              settings.ai_fallback_key, settings.ai_fallback_model))
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


def _chat(provider: Provider, prompt: str, *, structured: bool) -> str:
    payload: dict[str, Any] = {
        "model": provider.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": settings.ai_max_tokens,
        "stream": False,
    }
    if structured:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "repo_analysis", "strict": True, "schema": SCHEMA},
        }
    headers = {"Authorization": f"Bearer {provider.api_key}"} if provider.api_key else {}
    try:
        resp = httpclient.request("POST", f"{provider.base_url}/chat/completions", headers=headers,
                                  json_body=payload, connect_timeout=settings.ai_connect_timeout,
                                  read_timeout=settings.ai_read_timeout)
    except httpclient.FetchError as exc:
        raise AIError(f"{provider.label} unreachable ({exc})") from exc
    if resp.status >= 400:
        raise AIError(f"{provider.label} HTTP {resp.status}: {resp.text()[:160]}", resp.status)
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
    raise AIUnavailable("; ".join(errors) or "no AI provider is configured")


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
        ok, models, error = False, [], str(exc)
    return {
        "key": provider.key, "label": provider.label, "model": provider.model,
        "url": provider.base_url, "online": ok, "models": models, "error": error[:200],
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
