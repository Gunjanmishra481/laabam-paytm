"""Durable timers: QR expiry, the evening summary, alert scans.

A timer is a row. The worker claims due rows atomically (pending -> running), runs the handler
for the timer's kind, and marks it fired. Failures retry with backoff; after MAX_ATTEMPTS the
timer is marked failed and needs_attention, with an audit event. Rows stuck in "running"
(process died mid-handler) go back to pending after STUCK_AFTER.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from . import audit, tenancy
from .ids import new_id
from .models import Timer, utcnow

log = logging.getLogger("laabam.timers")

MAX_ATTEMPTS = 8
STUCK_AFTER = timedelta(minutes=5)

# A handler runs inside the timer's transaction and gets the session and the timer row.
Handler = Callable[[Session, Timer], None]


@dataclass(frozen=True)
class TimerInfo:
    id: str
    kind: str
    subject: str
    merchant_id: str | None
    attempts: int
    payload: dict


# A prepare step runs before the handler, outside any transaction, for slow work such as a call to a model
# that takes a minute. Its result goes to the handler: handler(session, timer, result).
Prepare = Callable[[TimerInfo], Any]


def backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(3600, 5 * 2**attempts))


def schedule(s: Session, kind: str, due_at: datetime, merchant_id: str | None = None, subject: str = "",
             **payload: Any) -> Timer:
    t = Timer(id=new_id("tmr"), kind=kind, due_at=due_at, merchant_id=merchant_id, subject=subject, payload=payload)
    s.add(t)
    return t


def cancel(s: Session, kind: str, subject: str) -> int:
    return s.execute(update(Timer).where(Timer.kind == kind, Timer.subject == subject, Timer.status == "pending")
                     .values(status="cancelled")).rowcount


class TimerRunner:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions
        self.handlers: dict[str, Handler] = {}
        self.prepares: dict[str, Prepare] = {}

    def register(self, kind: str, handler: Handler, prepare: Prepare | None = None) -> None:
        self.handlers[kind] = handler
        if prepare is not None:
            self.prepares[kind] = prepare

    def run(self, timer_id: str) -> bool:
        """Claim and run one timer. Returns True if it ran successfully."""
        with self.sessions.begin() as s:
            claimed = s.execute(update(Timer).where(Timer.id == timer_id, Timer.status == "pending")
                                .values(status="running", claimed_at=utcnow())).rowcount
            if not claimed:
                return False
            timer = s.get(Timer, timer_id)
            kind, attempts, merchant_id = timer.kind, timer.attempts, timer.merchant_id
            info = TimerInfo(timer.id, kind, timer.subject, merchant_id, attempts, dict(timer.payload or {}))
        try:
            handler = self.handlers.get(kind)
            if handler is None:
                raise LookupError(f"no handler for timer kind {kind!r}")
            prepare = self.prepares.get(kind)
            with tenancy.as_shop(merchant_id):  # a shop's timer runs under that shop's row-level security
                prepared = prepare(info) if prepare else None
                with self.sessions.begin() as s:
                    if prepare:
                        handler(s, s.get(Timer, timer_id), prepared)
                    else:
                        handler(s, s.get(Timer, timer_id))
                    s.execute(update(Timer).where(Timer.id == timer_id).values(status="fired"))
            return True
        except Exception as e:  # noqa: BLE001 - any failure is retried with backoff
            log.warning("timer %s (%s) failed: %s", timer_id, kind, e)
            give_up = attempts + 1 >= MAX_ATTEMPTS
            with tenancy.as_shop(merchant_id), self.sessions.begin() as s:
                s.execute(update(Timer).where(Timer.id == timer_id).values(
                    status="failed" if give_up else "pending", attempts=attempts + 1, last_error=str(e)[:2000],
                    due_at=utcnow() + backoff(attempts), needs_attention=give_up))
                if give_up:
                    audit.record(s, merchant_id, "system", "timer_failed", timer_id, timer_kind=kind,
                                 error=str(e)[:300])
            return False

    def due(self, limit: int = 20) -> list[str]:
        with self.sessions.begin() as s:
            s.execute(update(Timer).where(Timer.status == "running", Timer.claimed_at < utcnow() - STUCK_AFTER)
                      .values(status="pending"))
            return list(s.execute(select(Timer.id).where(Timer.status == "pending", Timer.due_at <= utcnow())
                                  .order_by(Timer.due_at).limit(limit)).scalars())

    def tick(self) -> int:
        return sum(self.run(tid) for tid in self.due())


class Worker:
    """Runs TimerRunner.tick on an interval. One per deployment, or inline in the API."""

    def __init__(self, runner: TimerRunner, interval: float = 1.0) -> None:
        self.runner = runner
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.runner.tick()
            except Exception:  # noqa: BLE001
                log.exception("worker tick failed")
            self._stop.wait(self.interval)

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self.loop, name="laabam-worker", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
