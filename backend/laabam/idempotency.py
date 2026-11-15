"""Idempotency: inbound partner events are applied once; retried requests get the same answer;
outbound partner calls carry a stable key so a retry never acts twice."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .models import IdempotencyKey, InboundEvent


class IdempotencyConflict(Exception):
    """The same key was reused with a different request."""


def outbound_key(*parts: str) -> str:
    """Stable key for a call to a partner, e.g. outbound_key("qr", bill_id). Same inputs, same key."""
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:40]


def first_time(sessions: sessionmaker[Session], source: str, event_id: str, event_type: str) -> bool:
    """Record an inbound event. False if it was already seen (the caller should skip it)."""
    try:
        with sessions.begin() as s:
            s.add(InboundEvent(source=source, event_id=event_id, event_type=event_type))
        return True
    except IntegrityError:
        return False


def once(sessions: sessionmaker[Session], scope: str, key: str | None, request: Any,
         fn: Callable[[], dict]) -> dict:
    """Run fn() once per (scope, key); a retry with the same key and body returns the stored response."""
    if not key:
        return fn()
    req_hash = hashlib.sha256(json.dumps(request, sort_keys=True, default=str).encode()).hexdigest()
    with sessions() as s:
        prior = s.get(IdempotencyKey, (scope, key))
    if prior:
        if prior.request_hash != req_hash:
            raise IdempotencyConflict("Idempotency-Key reused with a different request")
        return prior.response
    result = fn()
    stored = json.loads(json.dumps(result, default=str))
    with sessions.begin() as s:
        s.merge(IdempotencyKey(scope=scope, key=key, request_hash=req_hash, response=stored))
    return stored
