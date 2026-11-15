"""Append-only audit log. Write through record(); the database rejects updates and deletes.

It never holds raw content (security plan C3): fields that would carry text the owner or a bill said (transcripts,
spoken fixes, printed lines, questions, phone numbers) must be keyed digests from digest(), and record() refuses
anything else, so a later change can't quietly put text back in. Deleting a shop deletes its key (shopkeys.py).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .models import AuditEvent

ACTORS = ("owner", "agent", "system", "partner")
RAW_FIELDS = {"transcript", "text", "raw_text", "question", "heard_text", "phone", "words"}
DIGEST_PREFIX = "hmac-sha256:"

shop_keys = None  # set by build_services: shopkeys.ShopKeys


class RawContentInAudit(ValueError):
    pass


def digest(s: Session, merchant_id: str, text: str | None) -> str | None:
    """A keyed digest of the text, for the audit log (never the text itself)."""
    if text is None:
        return None
    if shop_keys is None:
        raise RuntimeError("audit.shop_keys is not set up")
    return shop_keys.digest(s, merchant_id, text)


def _check(value: Any, key: str | None = None) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            _check(v, k)
    elif isinstance(value, list | tuple):
        for v in value:
            _check(v, key)
    elif key in RAW_FIELDS and value is not None and not (isinstance(value, str) and value.startswith(DIGEST_PREFIX)):
        raise RawContentInAudit(f"audit field {key!r} must be a digest, not raw content")


def record(s: Session, merchant_id: str | None, actor: str, kind: str, subject: str = "", **detail: Any) -> AuditEvent:
    if actor not in ACTORS:
        raise ValueError(f"unknown actor {actor!r}")
    _check(detail)
    ev = AuditEvent(merchant_id=merchant_id, actor=actor, kind=kind, subject=subject, detail=detail)
    s.add(ev)
    return ev
