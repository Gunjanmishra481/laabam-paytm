"""Signatures on partner webhooks, in and out. (Owner tokens: tokens.py.)"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

SIGNATURE_TOLERANCE_SECONDS = 300
TIMESTAMP_HEADER = "X-Laabam-Timestamp"
SIGNATURE_HEADER = "X-Laabam-Signature"
OWNER_AUDIENCE = "laabam"
ISSUER = "laabam"


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def signed_headers(secret: str, body: bytes, now: float | None = None) -> dict[str, str]:
    ts = str(int(now if now is not None else time.time()))
    return {TIMESTAMP_HEADER: ts, SIGNATURE_HEADER: sign(secret, ts, body)}


def verify_signature(secret: str, timestamp: str | None, signature: str | None, body: bytes) -> bool:
    if not secret or not timestamp or not signature:
        return False
    try:
        if abs(time.time() - int(timestamp)) > SIGNATURE_TOLERANCE_SECONDS:
            return False  # replayed or badly skewed
    except ValueError:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), signature)


def request_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()
