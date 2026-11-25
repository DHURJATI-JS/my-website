"""Sealed records: small secrets kept in the data Gist that only this site's backend can read.

The home PC's tunnel script (scripts/publish_endpoint.py) seals {"url", "key", "at"} for the
AI gateway; the backend opens it with SECRET_KEY, which the PC's .env and Vercel share.
Anyone who can read the Gist sees only {"v": 1, "box": "<random-looking text>"} - no address,
no key, nothing about the PC - and nobody without SECRET_KEY can forge or change a record.

Encrypt-then-MAC with the standard library only (so the PC can run it without the site's
packages):
  keys   : HMAC-SHA256(secret, label | purpose) -> separate encryption and signing keys
  cipher : keystream = HMAC-SHA256(enc, nonce | counter) XOR plaintext  (CTR mode over a PRF)
  tag    : HMAC-SHA256(mac, version | nonce | ciphertext), checked in constant time first
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from typing import Any

VERSION = 1
LABEL = b"portfolio/sealed-record/v1"
NONCE_BYTES = 16
TAG_BYTES = 32
MAX_PLAINTEXT = 16 * 1024


def _keys(secret: str, purpose: str) -> tuple[bytes, bytes]:
    if not secret or not purpose:
        raise ValueError("a secret and a purpose are needed")
    root = hmac.new(secret.encode("utf-8"), LABEL + b"|" + purpose.encode("utf-8"), hashlib.sha256).digest()
    return (hmac.new(root, b"encrypt", hashlib.sha256).digest(),
            hmac.new(root, b"sign", hashlib.sha256).digest())


def _xor_stream(key: bytes, nonce: bytes, data: bytes) -> bytes:
    stream = bytearray()
    counter = 0
    while len(stream) < len(data):
        stream += hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(a ^ b for a, b in zip(data, stream))


def _tag(key: bytes, nonce: bytes, body: bytes) -> bytes:
    return hmac.new(key, bytes([VERSION]) + nonce + body, hashlib.sha256).digest()


def seal(data: dict[str, Any], secret: str, purpose: str) -> dict[str, Any]:
    plain = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(plain) > MAX_PLAINTEXT:
        raise ValueError("record too large")
    enc, sign = _keys(secret, purpose)
    nonce = secrets.token_bytes(NONCE_BYTES)
    body = _xor_stream(enc, nonce, plain)
    box = nonce + body + _tag(sign, nonce, body)
    return {"v": VERSION, "box": base64.urlsafe_b64encode(box).decode("ascii")}


def open_sealed(record: Any, secret: str, purpose: str) -> dict[str, Any] | None:
    """The sealed dict - or None if the record is missing, damaged, forged or sealed with another secret."""
    if not isinstance(record, dict) or record.get("v") != VERSION or not isinstance(record.get("box"), str):
        return None
    try:
        raw = base64.urlsafe_b64decode(record["box"].encode("ascii"))
        enc, sign = _keys(secret, purpose)
    except (ValueError, UnicodeError):  # binascii.Error is a ValueError
        return None
    if not NONCE_BYTES + TAG_BYTES <= len(raw) <= NONCE_BYTES + TAG_BYTES + MAX_PLAINTEXT:
        return None
    nonce, body, tag = raw[:NONCE_BYTES], raw[NONCE_BYTES:-TAG_BYTES], raw[-TAG_BYTES:]
    if not hmac.compare_digest(tag, _tag(sign, nonce, body)):
        return None
    try:
        data = json.loads(_xor_stream(enc, nonce, body).decode("utf-8"))
    except (ValueError, UnicodeError):
        return None
    return data if isinstance(data, dict) else None
