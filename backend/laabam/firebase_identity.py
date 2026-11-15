"""Firebase Phone Auth, used only to prove the owner holds a phone number (beta sign-up).

The phone sends the OTP flow to Firebase and gets a Firebase ID token; the API checks it here and then issues its own
KMS-signed session (auth.py). Firebase's session is not kept. Checks, per Firebase's "verify ID tokens" rules:
- RS256 only, signed by one of Google's securetoken keys (published JWKS, fetched through the outbound allowlist);
- aud = our project id; iss = https://securetoken.google.com/<project id>;
- exp in the future, iat and auth_time in the past; sub (the Firebase uid) present;
- signed in with the phone provider, and a phone_number claim under an allowed country prefix;
- auth_time recent (the OTP was just entered): an old token can't open a new session;
- single use: a token exchanged once can't be exchanged again (its hash is kept until it expires).
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import jwt
from sqlalchemy.orm import Session

from .models import UsedAssertion
from .phonehash import BadPhone, normalise

ISSUER_PREFIX = "https://securetoken.google.com/"
MAX_AUTH_AGE = 600  # seconds since the OTP was entered
LEEWAY = 60  # clock skew allowed on exp/iat


class FirebaseTokenError(ValueError):
    pass


@dataclass(frozen=True)
class PhoneClaims:
    phone: str  # E.164
    uid: str
    auth_time: int
    jti: str = ""  # this token's single-use key
    exp: int = 0


class FirebaseVerifier:
    def __init__(self, project_id: str, keys, prefixes: tuple[str, ...] = ("+91",),
                 max_auth_age: int = MAX_AUTH_AGE) -> None:
        if not project_id:
            raise ValueError("Firebase project id is required")
        self.project_id, self.keys, self.prefixes, self.max_auth_age = project_id, keys, prefixes, max_auth_age

    def verify(self, s: Session, token: str, now: float | None = None) -> PhoneClaims:
        """check() and consume() together."""
        claims = self.check(token, now)
        self.consume(s, claims)
        return claims

    def consume(self, s: Session, claims: PhoneClaims) -> None:
        """Marks the token used (single use); in the caller's transaction, so a refusal after it rolls it back."""
        if s.get(UsedAssertion, claims.jti) is not None:
            raise FirebaseTokenError("already used")
        s.add(UsedAssertion(jti=claims.jti, issuer="firebase", expires_at=datetime.fromtimestamp(claims.exp, UTC)))
        s.flush()

    def check(self, token: str, now: float | None = None) -> PhoneClaims:
        """Every check that needs no database."""
        now = time.time() if now is None else now
        if not token or len(token) > 4096:
            raise FirebaseTokenError("missing or too long")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as e:
            raise FirebaseTokenError("not a JWT") from e
        if header.get("alg") != "RS256":
            raise FirebaseTokenError(f"alg {header.get('alg')!r} refused")
        key = self.keys.get(header.get("kid") or "")
        if key is None:
            raise FirebaseTokenError("unknown signing key")
        try:
            claims = jwt.decode(
                token, key, algorithms=["RS256"], audience=self.project_id,
                issuer=ISSUER_PREFIX + self.project_id, leeway=LEEWAY,
                options={"require": ["exp", "iat", "aud", "iss", "sub", "auth_time"], "verify_iat": False},
            )
        except jwt.ExpiredSignatureError as e:
            raise FirebaseTokenError("expired") from e
        except jwt.InvalidAudienceError as e:
            raise FirebaseTokenError("wrong audience") from e
        except jwt.InvalidIssuerError as e:
            raise FirebaseTokenError("wrong issuer") from e
        except jwt.PyJWTError as e:
            raise FirebaseTokenError(f"invalid: {type(e).__name__}") from e
        iat, auth_time, exp = claims["iat"], claims["auth_time"], claims["exp"]
        if not all(isinstance(v, int | float) for v in (iat, auth_time, exp)):
            raise FirebaseTokenError("bad time claims")
        if iat > now + LEEWAY or auth_time > now + LEEWAY:
            raise FirebaseTokenError("issued in the future")
        if now - auth_time > self.max_auth_age:
            raise FirebaseTokenError("sign-in too old; enter a new code")
        uid = claims.get("sub")
        if not isinstance(uid, str) or not 0 < len(uid) <= 128:
            raise FirebaseTokenError("bad subject")
        if (claims.get("firebase") or {}).get("sign_in_provider") != "phone":
            raise FirebaseTokenError("not a phone sign-in")
        try:
            phone = normalise(claims.get("phone_number") or "", self.prefixes)
        except BadPhone as e:
            raise FirebaseTokenError(f"phone number refused: {e}") from e
        jti = "fb:" + hashlib.sha256(token.encode()).hexdigest()[:60]
        return PhoneClaims(phone=phone, uid=uid, auth_time=int(auth_time), jti=jti, exp=int(exp))
