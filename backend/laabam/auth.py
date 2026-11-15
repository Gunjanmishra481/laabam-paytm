"""Device sessions and refresh tokens (security plan §1).

Signing in (partner assertion, or the sandbox in the demo) starts a device session: a 15-minute access token
(tokens.py) and a refresh token (256-bit random; only its SHA-256 is stored) in a __Host- cookie. Each refresh
uses up the presented token and issues the next. Presenting a used token again means it was copied: the whole
session is revoked. Sessions end after `session_max_days` and when idle for `refresh_idle_days`.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import audit, tenancy
from .ids import new_id
from .models import DeviceSession, RefreshToken, utcnow
from .tokens import Access, TokenError, Tokens

COOKIE = "__Host-laabam_refresh"
REFRESH_HEADER = "X-Laabam-Refresh"  # CSRF: a cross-site form or image can't set it


class SessionEnded(Exception):
    """Sign in again. `reason` is for the audit log."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class Issued:
    access_token: str
    access_expires: int
    refresh_token: str
    refresh_max_age: int
    session_id: str
    merchant_id: str


class AuthService:
    def __init__(self, tokens: Tokens, idle_days: int = 30, max_days: int = 90) -> None:
        self.tokens, self.idle, self.max = tokens, timedelta(days=idle_days), timedelta(days=max_days)

    def _refresh(self, s: Session, sess: DeviceSession) -> tuple[str, int]:
        now = utcnow()
        raw = secrets.token_urlsafe(32)
        expires = min(now + self.idle, sess.expires_at)
        s.add(RefreshToken(id=new_id("rft"), merchant_id=sess.merchant_id, session_id=sess.id,
                           token_hash=token_hash(raw), expires_at=expires))
        return raw, max(1, int((expires - now).total_seconds()))

    def start(self, s: Session, merchant_id: str, label: str, via: str) -> Issued:
        """A new device session for a shop the caller has just proven it owns. The shop must be in context."""
        tenancy.set_shop(s, merchant_id)
        now = utcnow()
        sess = DeviceSession(id=new_id("ses"), merchant_id=merchant_id, label=label[:80], via=via,
                             created_at=now, last_seen_at=now, expires_at=now + self.max)
        s.add(sess)
        s.flush()
        raw, max_age = self._refresh(s, sess)
        access, exp = self.tokens.issue(merchant_id, sess.id)
        audit.record(s, merchant_id, "owner", "signed_in", sess.id, via=via, device=sess.label)
        return Issued(access, exp, raw, max_age, sess.id, merchant_id)

    def refresh(self, s: Session, presented: str) -> Issued | SessionEnded:
        """The next tokens, or SessionEnded (returned, not raised, when the session was just revoked: the caller
        commits that before answering 401)."""
        h = token_hash(presented)
        mid = tenancy.shop_for_refresh(s, h)
        if mid is None:
            raise SessionEnded("unknown")
        tenancy.set_shop(s, mid)
        rt = s.execute(select(RefreshToken).where(RefreshToken.token_hash == h).with_for_update()).scalar_one()
        sess = s.execute(select(DeviceSession).where(DeviceSession.id == rt.session_id).with_for_update()).scalar_one()
        now = utcnow()
        if sess.revoked_at is not None:
            raise SessionEnded("revoked")
        if rt.used_at is not None:  # a copy of a token already spent: someone else has it
            self.revoke(s, mid, sess.id, "reuse")
            return SessionEnded("reuse")
        if rt.expires_at <= now or sess.expires_at <= now:
            self.revoke(s, mid, sess.id, "expired")
            return SessionEnded("expired")
        rt.used_at = now
        sess.last_seen_at = now
        raw, max_age = self._refresh(s, sess)
        access, exp = self.tokens.issue(mid, sess.id)
        return Issued(access, exp, raw, max_age, sess.id, mid)

    def revoke(self, s: Session, merchant_id: str, session_id: str, reason: str = "owner") -> bool:
        tenancy.set_shop(s, merchant_id)
        n = s.execute(update(DeviceSession).where(DeviceSession.id == session_id,
                                                  DeviceSession.merchant_id == merchant_id,
                                                  DeviceSession.revoked_at.is_(None))
                      .values(revoked_at=utcnow(), revoked_reason=reason)).rowcount
        if n:
            audit.record(s, merchant_id, "owner" if reason == "owner" else "system", "session_revoked", session_id,
                         reason=reason)
        return bool(n)

    def revoke_all(self, s: Session, merchant_id: str, reason: str) -> int:
        tenancy.set_shop(s, merchant_id)
        return s.execute(update(DeviceSession).where(DeviceSession.merchant_id == merchant_id,
                                                     DeviceSession.revoked_at.is_(None))
                         .values(revoked_at=utcnow(), revoked_reason=reason)).rowcount

    def check(self, s: Session, token: str) -> Access:
        """The access token, and its session still live. Raises TokenError otherwise."""
        a = self.tokens.verify(token)
        tenancy.set_shop(s, a.merchant_id)
        sess = s.get(DeviceSession, a.session_id)
        if sess is None or sess.merchant_id != a.merchant_id or sess.revoked_at is not None:
            raise TokenError("session ended")
        return a

    def list(self, s: Session, merchant_id: str, current: str | None) -> list[dict]:
        tenancy.set_shop(s, merchant_id)
        rows = s.execute(select(DeviceSession).where(DeviceSession.merchant_id == merchant_id,
                                                     DeviceSession.revoked_at.is_(None))
                         .order_by(DeviceSession.last_seen_at.desc())).scalars()
        return [{"id": r.id, "label": r.label, "via": r.via, "created_at": r.created_at.isoformat(),
                 "last_seen_at": r.last_seen_at.isoformat(), "current": r.id == current} for r in rows]


def device_label(user_agent: str | None) -> str:
    ua = (user_agent or "").lower()
    browser = "Chrome" if "chrome" in ua and "edg" not in ua else "Edge" if "edg" in ua else \
        "Firefox" if "firefox" in ua else "Safari" if "safari" in ua else "Browser"
    os_ = "Android" if "android" in ua else "iPhone" if "iphone" in ua else "Windows" if "windows" in ua else \
        "Mac" if "mac os" in ua else "Linux" if "linux" in ua else "a device"
    return f"{browser} on {os_}"
