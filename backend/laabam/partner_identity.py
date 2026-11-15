"""Signing in through the partner (security plan §1; format in docs/partner-identity.md).

Paytm for Business signs a short-lived assertion (ES256 JWS) that says which merchant the owner is; Laabam
verifies it and exchanges it for a device session. Checked: alg ES256 only, kid in the partner's key set,
issuer, audience, expiry, issued-at, lifetime <= 5 minutes, and jti unused (stored until expiry, so a replay
fails). The real Paytm integration is pending their spec; the sandbox partner signs assertions in the same
format for the demo, with its own key.
"""
from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import jwt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import UsedAssertion
from .tokens import LocalSigner, b64url

AUDIENCE = "laabam"
MAX_LIFETIME = 300  # seconds
LEEWAY = 30


class AssertionError_(Exception):
    """The assertion was refused. The message is for logs; the owner sees a plain sign-in error."""


@dataclass(frozen=True)
class PartnerClaims:
    issuer: str
    mid: str  # the partner's merchant id
    name: str | None
    language: str | None
    jti: str
    expires_at: datetime


class StaticKeys:
    """A key set given directly (the sandbox partner's, or tests')."""

    def __init__(self, keys: dict) -> None:
        self.keys = keys

    def get(self, kid: str):
        return self.keys.get(kid)


class JwksKeys:
    """The partner's published JWKS, fetched through the outbound allowlist and cached for an hour."""

    def __init__(self, url: str, client, ttl: int = 3600, kty: str = "EC") -> None:
        self.url, self.client, self.ttl, self.kty = url, client, ttl, kty  # EC P-256 (partner) or RSA (Firebase)
        self._keys: dict = {}
        self._at = 0.0

    def get(self, kid: str):
        if kid not in self._keys or time.time() - self._at > self.ttl:
            r = self.client.get(self.url, timeout=5.0)
            r.raise_for_status()
            keys = {}
            for k in r.json().get("keys", []):
                if not k.get("kid") or k.get("kty") != self.kty:
                    continue
                if self.kty == "EC" and k.get("crv") == "P-256":
                    keys[k["kid"]] = jwt.algorithms.ECAlgorithm.from_jwk(json.dumps(k))
                elif self.kty == "RSA" and k.get("alg", "RS256") == "RS256":
                    keys[k["kid"]] = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(k))
            self._keys, self._at = keys, time.time()
        return self._keys.get(kid)


class PartnerVerifier:
    def __init__(self, issuer: str, keys, audience: str = AUDIENCE) -> None:
        self.issuer, self.keys, self.audience = issuer, keys, audience

    def verify(self, s: Session, assertion: str) -> PartnerClaims:
        try:
            header = jwt.get_unverified_header(assertion)
        except jwt.PyJWTError as e:
            raise AssertionError_("not a JWS") from e
        if header.get("alg") != "ES256":
            raise AssertionError_(f"alg {header.get('alg')!r} refused")
        key = self.keys.get(str(header.get("kid", "")))
        if key is None:
            raise AssertionError_("unknown kid")
        try:
            c = jwt.decode(assertion, key, algorithms=["ES256"], audience=self.audience, issuer=self.issuer,
                           leeway=LEEWAY, options={"require": ["iss", "aud", "exp", "iat", "jti", "mid"]})
        except jwt.PyJWTError as e:
            raise AssertionError_(str(e)) from e
        if int(c["exp"]) - int(c["iat"]) > MAX_LIFETIME:
            raise AssertionError_("lifetime longer than 5 minutes")
        if int(c["iat"]) > time.time() + LEEWAY:
            raise AssertionError_("issued in the future")
        jti, mid = str(c["jti"]), str(c["mid"])
        if not (8 <= len(jti) <= 128) or not (1 <= len(mid) <= 64):
            raise AssertionError_("bad jti or mid")
        expires = datetime.fromtimestamp(int(c["exp"]) + LEEWAY, UTC)
        try:
            with s.begin_nested():
                s.add(UsedAssertion(jti=jti, issuer=self.issuer, expires_at=expires))
        except IntegrityError:
            raise AssertionError_("assertion already used") from None
        lang = c.get("language")
        return PartnerClaims(self.issuer, mid, (str(c.get("name"))[:120] if c.get("name") else None),
                             str(lang) if lang in ("en", "ta", "hi") else None, jti, expires)


class SandboxPartner:
    """Stands in for Paytm for Business in the demo: signs assertions in the documented format."""

    issuer = "sandbox-partner"

    def __init__(self) -> None:
        self._signer = LocalSigner()

    @property
    def keys(self) -> StaticKeys:
        return StaticKeys({self._signer.kid: self._signer.public_key()})

    def sign(self, mid: str, name: str | None = None, language: str | None = None, lifetime: int = 120,
             now: int | None = None, drop: tuple[str, ...] = (), **extra) -> str:
        """An assertion in the documented format. `extra` overrides claims and `drop` removes them (tests)."""
        now = int(now if now is not None else time.time())
        header = {"alg": "ES256", "typ": "JWT", "kid": self._signer.kid}
        claims = {"iss": self.issuer, "aud": AUDIENCE, "iat": now, "exp": now + lifetime,
                  "jti": secrets.token_hex(16), "mid": mid, **({"name": name} if name else {}),
                  **({"language": language} if language else {}), **extra}
        for k in drop:
            claims.pop(k, None)
        si = b64url(json.dumps(header, separators=(",", ":")).encode()) + "." + \
            b64url(json.dumps(claims, separators=(",", ":")).encode())
        return si + "." + b64url(self._signer.sign(si.encode()))
