"""Rate limits (security plan §3): token buckets in Postgres (SQLite in development), one row per key.

A bucket holds `capacity` tokens and refills `capacity` per `period` seconds. take() spends one or refuses.
Keys look like "auth.refresh:ip:203.0.113.7" or "voice:shop:mer_...". The row is locked for update, so
concurrent requests can't both spend the last token. AWS WAF rate rules sit in front of this at the edge.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .models import RateBucket, utcnow


@dataclass(frozen=True)
class Limit:
    capacity: int
    period: float  # seconds to refill fully

    @property
    def rate(self) -> float:
        return self.capacity / self.period


class RateLimited(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__("Too many requests")
        self.retry_after = retry_after


class Limiter:
    def __init__(self, sessions: sessionmaker[Session], limits: dict[str, Limit]) -> None:
        self.sessions, self.limits = sessions, limits

    def take(self, action: str, *subjects: tuple[str, str], now: datetime | None = None) -> None:
        """Spend one token from each (kind, value) bucket for `action`; raise RateLimited if any is empty."""
        limit = self.limits.get(action)
        if limit is None:
            return
        now = now or utcnow()
        for kind, value in subjects:
            if value:
                self._take(f"{action}:{kind}:{value}"[:160], limit, now)

    def _take(self, key: str, limit: Limit, now: datetime) -> None:
        for _ in range(2):  # a concurrent first insert: retry once as an update
            try:
                with self.sessions.begin() as s:
                    q = select(RateBucket).where(RateBucket.key == key).with_for_update()
                    b = s.execute(q).scalar_one_or_none()
                    if b is None:
                        s.add(RateBucket(key=key, tokens=limit.capacity - 1, updated_at=now))
                        return
                    elapsed = max(0.0, (now - b.updated_at).total_seconds())
                    tokens = min(float(limit.capacity), b.tokens + elapsed * limit.rate)
                    if tokens < 1:
                        raise RateLimited(max(1, int((1 - tokens) / limit.rate) + 1))
                    b.tokens, b.updated_at = tokens - 1, now
                    return
            except IntegrityError:
                continue
