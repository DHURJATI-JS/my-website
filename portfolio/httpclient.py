"""A tiny HTTP/JSON client built on the standard library (no requests/httpx).

Separate connect/read timeouts let the AI fail over fast when a server is down
while still giving a slow generation enough time to finish.
"""
from __future__ import annotations

import http.client
import json
import ssl
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit

USER_AGENT = "portfolio-site/1.0"
_SSL_CONTEXT = ssl.create_default_context()


class FetchError(Exception):
    """Network-level failure (DNS, refused, timeout, TLS, oversize, ...)."""


class Response:
    __slots__ = ("status", "headers", "body")

    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self) -> Any:
        return json.loads(self.body)


def request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
    connect_timeout: float = 5.0,
    read_timeout: float = 20.0,
    max_bytes: int = 12_000_000,
    max_redirects: int = 3,
    follow_redirects: bool = True,
) -> Response:
    hdrs = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity", "Connection": "close"}
    if headers:
        hdrs.update(headers)
    body = None
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    if params:
        url += ("&" if "?" in url else "?") + urlencode(params)

    for _ in range(max_redirects + 1):
        resp = _send(method, url, hdrs, body, connect_timeout, read_timeout, max_bytes)
        location = resp.headers.get("location")
        if resp.status in (301, 302, 303, 307, 308) and location and follow_redirects:
            new_url = urljoin(url, location)
            if urlsplit(new_url).netloc != urlsplit(url).netloc:
                hdrs.pop("Authorization", None)  # never leak tokens to another host
            if resp.status == 303:
                method, body = "GET", None
            url = new_url
            continue
        return resp
    raise FetchError("too many redirects")


def _send(method, url, headers, body, connect_timeout, read_timeout, max_bytes) -> Response:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError(f"unsupported URL: {url!r}")
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    if parts.scheme == "https":
        conn: http.client.HTTPConnection = http.client.HTTPSConnection(
            parts.hostname, parts.port, timeout=connect_timeout, context=_SSL_CONTEXT
        )
    else:
        conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=connect_timeout)
    try:
        conn.connect()
        if conn.sock is not None:
            conn.sock.settimeout(read_timeout)
        conn.request(method, target, body=body, headers=headers)
        resp = conn.getresponse()
        data = resp.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise FetchError(f"response from {parts.hostname} is too large")
        return Response(resp.status, {k.lower(): v for k, v in resp.getheaders()}, data)
    except (OSError, http.client.HTTPException) as exc:
        raise FetchError(f"{parts.hostname}: {exc.__class__.__name__}: {exc}") from exc
    finally:
        conn.close()
