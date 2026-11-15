"""Sign-in, refresh, sign-out and device sessions (security plan §1), and the owner dependency every /v1 route uses.

- POST /v1/auth/partner   {assertion}: a partner-signed assertion (docs/partner-identity.md) -> a device session
- POST /v1/auth/phone/start {phone}: before the phone asks Firebase for an OTP; rate limits per IP and per number
- POST /v1/auth/firebase  {id_token, consent}: a Firebase phone ID token -> our own device session (beta sign-up)
- POST /v1/auth/refresh   cookie __Host-laabam_refresh + header X-Laabam-Refresh: 1 -> new access token, rotated cookie
- POST /v1/auth/logout    ends this device's session
- GET  /v1/sessions, DELETE /v1/sessions/{id}: the shop's signed-in devices, and signing one out
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import audit, merchants, tenancy
from .auth import COOKIE, Issued, SessionEnded, device_label
from .db import after_commit
from .firebase_identity import FirebaseTokenError
from .ids import new_id
from .models import Merchant, utcnow
from .partner_identity import AssertionError_, PartnerClaims
from .payments import shop_of
from .phonehash import BadPhone, normalise
from .strings import say
from .tokens import Access, TokenError


class AssertionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assertion: str = Field(min_length=20, max_length=4096)


PRIVACY_VERSION = "2026-11-15"  # the privacy notice a new owner agrees to (frontend /privacy)


class PhoneStartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone: str = Field(min_length=8, max_length=20)


class FirebaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id_token: str = Field(min_length=20, max_length=4096)
    consent: bool = False  # ticked "I agree" to the privacy notice; needed to create a shop
    consent_version: str | None = Field(default=None, max_length=16)


def client_ip(request: Request, trusted_proxies: int) -> str:
    """The caller's address. Behind N trusted proxies (CloudFront, ALB) it is the N-th entry from the right of
    X-Forwarded-For; anything a client puts further left is ignored."""
    peer = request.client.host if request.client else ""
    if trusted_proxies <= 0:
        return peer
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    return hops[-trusted_proxies] if len(hops) >= trusted_proxies else peer


def session_response(svc, issued: Issued, merchant: Merchant, status: int = 200) -> JSONResponse:
    res = JSONResponse({"access_token": issued.access_token, "expires_at": issued.access_expires,
                        "session_id": issued.session_id, "merchant": merchants.me_json(merchant)}, status_code=status)
    # __Host-: Secure, Path=/, no Domain (this host only). HttpOnly: scripts can't read it. Strict: never sent
    # with a request another site starts.
    res.set_cookie(COOKIE, issued.refresh_token, max_age=issued.refresh_max_age, path="/", secure=True,
                   httponly=True, samesite="strict")
    res.headers["Cache-Control"] = "no-store"
    return res


def clear_cookie(res: JSONResponse) -> JSONResponse:
    res.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    return res


def shop_for_claims(svc, s, claims: PartnerClaims) -> Merchant:
    """The shop the partner vouched for; a first sign-in creates it (onboarding asks the rest)."""
    mid = tenancy.shop_for_paytm_mid(s, claims.mid)
    if mid is not None:
        tenancy.set_shop(s, mid)
        return s.get(Merchant, mid)
    new = new_id("mer")
    tenancy.set_shop(s, new)
    m = merchants.create_merchant(s, claims.name or "", claims.language or "en", "", id=new, paytm_mid=claims.mid,
                                  language_set=claims.language is not None, adapters=svc.settings.partner_adapters)
    s.flush()
    svc.summaries.schedule(s, m)
    return m


def make_owner(svc):
    """FastAPI dependencies: `owner` gives the shop id, `owner_access` the token's claims. Both put the shop in
    context, so every transaction in the request runs under that shop's row-level security."""

    def _check(token: str) -> Access:
        with svc.sessions.begin() as s:
            return svc.auth.check(s, token)

    async def owner_access(authorization: str | None = Header(None)) -> Access:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "Please sign in again.")
        try:
            access = await run_in_threadpool(_check, authorization.removeprefix("Bearer ").strip())
        except TokenError:
            raise HTTPException(401, "Please sign in again.") from None
        tenancy.use_shop(access.merchant_id)
        return access

    async def owner(access: Access = Depends(owner_access)) -> str:
        return access.merchant_id

    return owner, owner_access


def auth_router(svc, owner_access) -> APIRouter:
    r = APIRouter(prefix="/v1", tags=["auth"])
    cfg = svc.settings

    @r.post("/auth/partner")
    def partner_sign_in(body: AssertionIn, request: Request):
        """Sign in with a partner assertion (Paytm for Business opens Laabam with one)."""
        svc.limiter.take("auth.partner", ("ip", client_ip(request, cfg.trusted_proxies)))
        if svc.partner_verifier is None:
            raise HTTPException(503, "Signing in through the partner isn't set up yet.")
        with svc.sessions.begin() as s:
            try:
                claims = svc.partner_verifier.verify(s, body.assertion)
            except AssertionError_ as e:
                svc.log_auth("partner_refused", reason=str(e)[:120])
                raise HTTPException(401, "Couldn't sign you in. Please open Laabam from Paytm for Business again.") \
                    from None
            m = shop_for_claims(svc, s, claims)
            label = device_label(request.headers.get("user-agent"))
            issued = svc.auth.start(s, m.id, label, "partner")
            out = session_response(svc, issued, m)
            # tell the owner on the Soundbox: a sign-in they didn't make shows up at the counter (ASVS V2.5.5)
            shop, text = shop_of(m), say(m.language, "new_device", DEVICE=label)
            after_commit(s, lambda: svc.payments._announce(shop, text, m.language))
        svc.log_auth("signed_in", shop=m.id, via="partner")
        return out

    @r.post("/auth/phone/start", status_code=204)
    def phone_start(body: PhoneStartIn, request: Request):
        """Asked before the phone requests an OTP from Firebase: at most 10 an hour per IP and 5 an hour per number.
        (Firebase enforces its own limits too: an app can't stop calls made to Firebase directly.)"""
        if svc.firebase_verifier is None:
            raise HTTPException(404, "Not found")
        svc.limiter.take("otp.ip", ("ip", client_ip(request, cfg.trusted_proxies)))
        try:
            phone = normalise(body.phone, svc.phone_prefixes)
        except BadPhone:
            raise HTTPException(422, "Enter a 10-digit Indian mobile number.") from None
        svc.limiter.take("otp.number", ("phone", svc.phone_hasher.hash(phone)))
        return None

    @r.post("/auth/firebase")
    def firebase_sign_in(body: FirebaseIn, request: Request):
        """A Firebase phone ID token proves the number; the shop is found by the number's keyed hash, or created
        (only with consent to the privacy notice). Our own session follows; Firebase's isn't kept."""
        if svc.firebase_verifier is None:
            raise HTTPException(404, "Not found")
        ip = client_ip(request, cfg.trusted_proxies)
        svc.limiter.take("auth.firebase", ("ip", ip))

        def refused(e: FirebaseTokenError):
            svc.log_auth("firebase_refused", reason=str(e)[:120])
            return HTTPException(401, "Couldn't sign you in. Please enter a new code.")

        try:
            claims = svc.firebase_verifier.check(body.id_token)
        except FirebaseTokenError as e:
            raise refused(e) from None
        phone_hash = svc.phone_hasher.hash(claims.phone)
        svc.limiter.take("auth.firebase", ("phone", phone_hash))
        with svc.sessions.begin() as s:
            try:
                svc.firebase_verifier.consume(s, claims)
            except FirebaseTokenError as e:
                raise refused(e) from None
            mid = tenancy.shop_for_phone(s, phone_hash)
            created = mid is None
            if created:
                if not body.consent or body.consent_version != PRIVACY_VERSION:
                    # raising rolls the transaction back, so the token stays unused: tick the box and send it again
                    raise HTTPException(428, "Please read and agree to the privacy notice first.")
                mid = new_id("mer")
                tenancy.set_shop(s, mid)
                m = merchants.create_merchant(s, "", "en", "", id=mid, language_set=False, phone_hash=phone_hash,
                                              consent_version=PRIVACY_VERSION, consent_at=utcnow(),
                                              adapters=cfg.partner_adapters)
                s.flush()
                svc.summaries.schedule(s, m)
                audit.record(s, mid, "owner", "signed_up", mid, via="phone", consent_version=PRIVACY_VERSION)
            else:
                tenancy.set_shop(s, mid)
                m = s.get(Merchant, mid)
            label = device_label(request.headers.get("user-agent"))
            issued = svc.auth.start(s, m.id, label, "phone")
            out = session_response(svc, issued, m, status=201 if created else 200)
            if not created:  # a new device on an existing shop is announced on its Soundbox (ASVS V2.5.5)
                shop, text = shop_of(m), say(m.language, "new_device", DEVICE=label)
                after_commit(s, lambda: svc.payments._announce(shop, text, m.language))
        svc.log_auth("signed_in", shop=m.id, via="phone", new=created)
        return out

    @r.post("/auth/refresh")
    def refresh(request: Request, x_laabam_refresh: str | None = Header(None)):
        """A new access token and a rotated refresh cookie. The custom header is CSRF protection on top of
        SameSite=Strict: a page on another site can't add it."""
        if x_laabam_refresh != "1":
            raise HTTPException(403, "Missing refresh header.")
        svc.limiter.take("auth.refresh", ("ip", client_ip(request, cfg.trusted_proxies)))
        raw = request.cookies.get(COOKIE)
        if not raw or len(raw) > 100:
            return clear_cookie(JSONResponse({"detail": "Please sign in again."}, status_code=401))
        with svc.sessions.begin() as s:
            try:
                res = svc.auth.refresh(s, raw)
            except SessionEnded as e:
                res = e
            if isinstance(res, Issued):
                out = session_response(svc, res, s.get(Merchant, res.merchant_id))
        if isinstance(res, SessionEnded):  # committed: a reused token has already revoked its session
            svc.log_auth("refresh_refused", reason=res.reason)
            return clear_cookie(JSONResponse({"detail": "Please sign in again."}, status_code=401))
        return out

    @r.post("/auth/logout")
    def logout(access: Access = Depends(owner_access)):
        with svc.sessions.begin() as s:
            svc.auth.revoke(s, access.merchant_id, access.session_id, "owner")
        return clear_cookie(JSONResponse({"ok": True}))

    @r.get("/sessions")
    def sessions(access: Access = Depends(owner_access)):
        with svc.sessions() as s:
            return svc.auth.list(s, access.merchant_id, access.session_id)

    @r.delete("/sessions/{session_id}")
    def revoke(session_id: str, access: Access = Depends(owner_access)):
        with svc.sessions.begin() as s:
            if not svc.auth.revoke(s, access.merchant_id, session_id, "owner"):
                raise HTTPException(404, "Not found")
        return {"ok": True}

    return r
