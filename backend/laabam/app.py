"""HTTP API. Run: uvicorn laabam.app:app

Audiences:
  /api/health    load balancers
  /v1/...        the owner's app (owner token) and partners (signed events)
  /sandbox/...   demos and tests only (LAABAM_SANDBOX=1)
"""
from __future__ import annotations

import logging
import os
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from . import alerts, audit, logs, merchants, plans, privacy, profit, security, tenancy
from .adapters import AdapterRegistry, AdapterUnavailable
from .adapters.base import AdapterError
from .adapters.lending import SandboxLending
from .adapters.paytm import PaytmPayments
from .adapters.sandbox import SandboxPayments, SandboxSms, SandboxSoundbox, Trace
from .adapters.speech import OfflineSpeech, SarvamSpeech
from .adapters.vision import GeminiVision, OfflineVision, SarvamVision, VisionChain, sample_date
from .ask import Asker
from .auth import AuthService, device_label
from .billing.service import BillService, LineEdit, NotAllowed, NotFound, OfflineParser
from .config import Settings, validate
from .config import settings as default_settings
from .db import after_commit, create_schema, make_engine, session_factory
from .demo_seed import seed_demo_month
from .http_guard import RequestGuard
from .idempotency import IdempotencyConflict
from .ids import new_id
from .models import LoanOffer, Merchant, RestockPlan, SalesBill, Timer, utcnow
from .outbound import guarded_client, hosts_from
from .partner_identity import JwksKeys, PartnerVerifier, SandboxPartner
from .payments import PaymentService, QRFailed, shop_of
from .photos import LocalPhotoStore
from .purchases.service import (
    MAX_PHOTO_BYTES,
    PurchaseLineEdit,
    PurchaseService,
    UnreadablePhoto,
    VisionUnavailable,
)
from .ratelimit import Limit, Limiter, RateLimited
from .routes_auth import auth_router, client_ip, make_owner, session_response
from .shopkeys import ShopKeys, make_keyring
from .summary import Summaries, latest, summary_json
from .summary import build as build_summary
from .timers import TimerRunner, Worker
from .tokens import Tokens, make_signer
from .voice import MAX_AUDIO_BYTES, BadAudio, HeardNothing, VoiceService, VoiceUnavailable, check_audio

log = logging.getLogger("laabam.app")
auth_log = logging.getLogger("laabam.auth")

VERSION = "0.1.0"
# The seeded demo shop. A made-up name, and a UPI handle no real app can pay ("@sandbox" isn't a PSP).
DEMO_SHOP = {"name": "Demo Kirana", "upi_vpa": "demo.kirana@sandbox"}
LEGACY_DEMO_NAMES = ("Murugan Stores",)  # earlier demo shops, relabelled on sign-in


# ---------------- request bodies ----------------


class Strict(BaseModel):
    """Request bodies: unknown fields are refused (422), not ignored."""

    model_config = ConfigDict(extra="forbid")


class TextBillIn(Strict):
    text: str = Field(min_length=1, max_length=2000)


class LineIn(Strict):
    item_text: str | None = Field(default=None, max_length=120)
    qty_milli: int | None = Field(default=None, gt=0, le=10_000_000)
    unit: str | None = Field(default=None, max_length=8)
    line_total_paise: int | None = Field(default=None, gt=0, le=100_000_000)
    clear_qty: bool = False

    def edit(self) -> LineEdit:
        return LineEdit(**self.model_dump())


class MeIn(Strict):
    name: str | None = Field(default=None, max_length=120)
    language: str | None = Field(default=None, max_length=8)
    closing_time: str | None = Field(default=None, max_length=5)
    upi_vpa: str | None = Field(default=None, max_length=128)
    cash_on_hand_paise: int | None = Field(default=None, ge=0, le=10**12)
    clear_cash_on_hand: bool = False
    keep_recordings: bool | None = None
    festival_date: date | None = None  # the next festival the plan is for
    clear_festival_date: bool = False


class DeleteShopIn(Strict):
    shop_name: str = Field(max_length=120)  # typed by the owner, to confirm


class SandboxSessionIn(Strict):
    reset: bool = False
    language: str | None = Field(default=None, max_length=8)
    fresh: bool = False  # a new, empty shop: no language, name or UPI ID, so onboarding runs
    history: bool = True  # a reset demo shop comes with its sample month (tests turn it off)


class AskIn(Strict):
    text: str = Field(default="", max_length=500)
    intent: str | None = Field(default=None, max_length=16)  # e.g. "draft" from Item detail's Draft a message
    item_id: str | None = Field(default=None, max_length=32)  # what the previous answer was about


class PlanIn(Strict):
    multiplier_bp: int = Field(default=plans.DEFAULT_MULTIPLIER_BP, ge=10000, le=50000)
    festival: str | None = Field(default=None, max_length=32)


class SpeakIn(Strict):
    text: str = Field(min_length=1, max_length=1500)


class PurchaseLineIn(Strict):
    item_id: str | None = Field(default=None, max_length=32)
    accept_suggestion: bool = False
    new_item_name: str | None = Field(default=None, max_length=120)
    raw_text: str | None = Field(default=None, max_length=200)
    qty_milli: int | None = Field(default=None, gt=0, le=100_000_000)
    unit: str | None = Field(default=None, max_length=8)
    rate_paise: int | None = Field(default=None, gt=0, le=1_000_000_000)
    clear_rate: bool = False
    line_total_paise: int | None = Field(default=None, gt=0, le=1_000_000_000)

    def edit(self) -> PurchaseLineEdit:
        return PurchaseLineEdit(**self.model_dump())


class PurchasePatchIn(Strict):
    distributor_name: str | None = Field(default=None, max_length=120)
    bill_no: str | None = Field(default=None, max_length=64)
    bill_date: date | None = None
    clear_date: bool = False


class PartnerEventIn(Strict):
    id: str = Field(min_length=1, max_length=128)
    type: str = Field(min_length=1, max_length=64)
    source: str = Field(default="paytm", max_length=16)
    data: dict = Field(default_factory=dict)


class UploadIn(Strict):
    content_type: str = Field(min_length=6, max_length=32)
    size: int = Field(gt=0, le=10_000_000)


class SandboxPayIn(Strict):
    amount_paise: int | None = Field(default=None, gt=0, le=10**9)


# ---------------- wiring ----------------


@dataclass
class Sandbox:
    payments: SandboxPayments
    soundbox: SandboxSoundbox
    sms: SandboxSms
    trace: Trace


@dataclass
class Services:
    settings: Settings
    sessions: sessionmaker[Session]
    adapters: AdapterRegistry
    timers: TimerRunner
    worker: Worker
    bills: BillService
    payments: PaymentService
    voice: VoiceService
    purchases: PurchaseService
    sandbox: Sandbox | None
    alerts: alerts.AlertScanner | None = None
    asker: Asker | None = None
    summaries: Summaries | None = None
    auth: AuthService | None = None
    limiter: Limiter | None = None
    partner_verifier: PartnerVerifier | None = None
    sandbox_partner: SandboxPartner | None = None
    admin_sessions: sessionmaker[Session] | None = None  # tests: the table owner (no row-level security)

    def log_auth(self, event: str, **fields) -> None:
        """Authentication decisions, without tokens (ASVS V7.2.1)."""
        auth_log.info("%s %s", event, " ".join(f"{k}={v}" for k, v in fields.items()))


def choose_vision(cfg: Settings, adapters: AdapterRegistry):
    """LAABAM_VISION, with LAABAM_VISION_FALLBACK behind it. A reader without its API key can't read
    anything, so it is replaced by offline (sample bills only) with a warning."""
    keys = {"sarvam": cfg.sarvam_api_key, "gemini": cfg.gemini_api_key}
    name = cfg.vision
    if name in keys and not keys[name]:
        log.warning("LAABAM_VISION=%s but its API key is empty: bill photos can't be read (offline, samples "
                    "only). Set LAABAM_%s_API_KEY.", name, name.upper())
        name = "offline"
    primary = adapters.get("vision", name)
    fb = cfg.vision_fallback
    if name != "offline" and fb not in ("", "none", "offline", name) and keys.get(fb):
        return VisionChain(primary, adapters.get("vision", fb))
    return primary


def build_services(cfg: Settings, schema: bool = True, http_client=None) -> Services:
    validate(cfg)  # refuses unsafe configurations (e.g. the sandbox in prod) before anything starts
    # every partner call goes through one client: allowlisted https hosts, timeouts, a circuit breaker per host
    http_client = guarded_client(hosts_from(cfg.sarvam_base_url, cfg.gemini_base_url, cfg.paytm_base_url,
                                            cfg.partner_jwks_url), inner=http_client)
    engine = make_engine(cfg.database_url)
    if schema:
        create_schema(engine)  # no-op on tables that exist; production runs Alembic first
    sessions = session_factory(engine)
    tenancy.install_session_hook(sessions)
    adapters = AdapterRegistry(sandbox=cfg.sandbox)
    adapters.register("text", "offline", OfflineParser)
    adapters.register("speech", "offline", OfflineSpeech)
    adapters.register("vision", "offline", OfflineVision)
    adapters.register("vision", "gemini", lambda: GeminiVision(cfg.gemini_api_key, cfg.gemini_model,
                                                               cfg.gemini_base_url, client=http_client))
    adapters.register("vision", "sarvam", lambda: SarvamVision(
        cfg.sarvam_api_key, cfg.sarvam_base_url, cfg.sarvam_vision_language, cfg.vision_timeout_seconds,
        cfg.vision_poll_seconds, client=http_client))
    adapters.register("speech", "sarvam", lambda: SarvamSpeech(cfg.sarvam_api_key, cfg.sarvam_base_url,
                                                               cfg.sarvam_stt_model, cfg.sarvam_tts_model,
                                                               cfg.sarvam_speaker, client=http_client))
    adapters.register("payments", "paytm", lambda: PaytmPayments(cfg.paytm_merchant_key, cfg.paytm_base_url,
                                                                 cfg.paytm_client_id, http_client))
    sandbox = None
    if cfg.sandbox:
        sandbox = Sandbox(SandboxPayments(), SandboxSoundbox(), SandboxSms(), Trace())
        adapters.register("payments", "sandbox", lambda: sandbox.payments, sandbox_only=True)
        adapters.register("soundbox", "sandbox", lambda: sandbox.soundbox, sandbox_only=True)
        adapters.register("sms", "sandbox", lambda: sandbox.sms, sandbox_only=True)
        lending = SandboxLending()
        adapters.register("lending", "sandbox", lambda: lending, sandbox_only=True)
    trace = sandbox.trace.record if sandbox else (lambda _m, _k, _d: None)
    timers = TimerRunner(sessions)
    bills = BillService(sessions, adapters.get("text", cfg.llm if cfg.llm in adapters.names("text") else "offline"),
                        trace)
    payments = PaymentService(sessions, adapters, cfg, trace)
    speech = adapters.get("speech", cfg.speech)
    payments.speech = speech
    voice = VoiceService(sessions, bills, speech, demo_samples=cfg.sandbox, trace=trace)
    if speech.name == "offline":
        log.warning("speech is offline (LAABAM_SPEECH=%s): recordings can't be heard; voice mode is %r. "
                    "Set LAABAM_SPEECH=sarvam and LAABAM_SARVAM_API_KEY for live voice.", cfg.speech, voice.mode)
    elif cfg.speech == "sarvam" and not cfg.sarvam_api_key:
        log.warning("LAABAM_SPEECH=sarvam but LAABAM_SARVAM_API_KEY is empty: every recording will fail.")
    purchases = PurchaseService(sessions, choose_vision(cfg, adapters), LocalPhotoStore(cfg.photo_dir),
                                demo_samples=cfg.sandbox, trace=trace)
    bills.on_confirmed.append(payments.on_confirmed)
    bills.on_cancelled.append(payments.on_cancelled)
    timers.register("qr_expiry", payments.on_qr_expiry)
    timers.register("payment_check", payments.on_payment_check)
    timers.register("purchase_read", purchases.on_read, prepare=purchases.read_queued)
    scanner = alerts.AlertScanner(cfg, trace)
    purchases.on_confirmed.append(scanner.on_purchase_confirmed)
    timers.register("alert_scan", scanner.on_scan)
    timers.register("alert_nightly", scanner.on_nightly)

    def announce(s, m: Merchant, text: str) -> None:  # the Soundbox speaks it once the summary is saved
        shop, lang = shop_of(m), m.language
        after_commit(s, lambda: payments._announce(shop, text, lang))

    summaries = Summaries(announce, trace)
    timers.register("evening_summary", summaries.on_timer)
    if sandbox:
        sandbox.payments.on_event = lambda evt, order, amount, txn, status: payments.handle_payment(
            "sandbox", evt, order, amount, txn, status)
    svc = Services(cfg, sessions, adapters, timers, Worker(timers, cfg.worker_interval), bills, payments,
                   voice, purchases, sandbox, scanner, Asker(scanner.th), summaries)
    audit.shop_keys = ShopKeys(make_keyring(cfg))
    timers.register("retention", lambda s, t: privacy.on_retention(svc, s, t))
    timers.register("shop_delete", lambda s, t: privacy.on_shop_delete(svc, s, t))
    svc.auth = AuthService(Tokens(make_signer(cfg), cfg.access_token_seconds), cfg.refresh_idle_days,
                           cfg.session_max_days)
    svc.limiter = Limiter(sessions, {
        "auth.partner": Limit(10, 60), "auth.refresh": Limit(30, 60),
        # the costly calls (speech, models, uploads): generous for a busy counter, a wall for a script
        "voice": Limit(120, 600), "ask": Limit(40, 600), "speak": Limit(40, 600), "photo.upload": Limit(30, 3600),
        "summary.now": Limit(10, 600), "export": Limit(5, 3600),
        "demo.shop": Limit(cfg.demo_shops_per_ip_hour, 3600),
    })
    if cfg.sandbox:
        svc.sandbox_partner = SandboxPartner()
        svc.partner_verifier = PartnerVerifier(SandboxPartner.issuer, svc.sandbox_partner.keys)
        timers.register("demo_cleanup", lambda s, t: demo_cleanup(svc, s))
    elif cfg.partner_jwks_url:
        import httpx
        svc.partner_verifier = PartnerVerifier(cfg.partner_issuer, JwksKeys(cfg.partner_jwks_url,
                                                                            http_client or httpx.Client()))
    if cfg.owner_database_url:
        svc.admin_sessions = session_factory(make_engine(cfg.owner_database_url))
    else:
        svc.admin_sessions = sessions
    return svc


DEMO_SHOP_HOURS = 24


def demo_cleanup(svc: Services, s: Session) -> None:
    """Demo shops last a day: older ones are deleted (their audit rows stay), and the job runs again in an hour."""
    from .shopdata import delete_shop
    cutoff = utcnow() - timedelta(hours=DEMO_SHOP_HOURS)
    for mid in tenancy.shop_ids(s):
        tenancy.set_shop(s, mid)
        m = s.get(Merchant, mid)
        if m is not None and (m.paytm_mid or "").startswith(DEMO_MID_PREFIX) and m.created_at < cutoff:
            delete_shop(s, mid, svc.purchases.photos, reason="demo_expired")
    ensure_demo_cleanup(s)


def ensure_demo_cleanup(s: Session) -> None:
    from . import timers as timers_mod
    if not s.execute(select(Timer.id).where(Timer.kind == "demo_cleanup", Timer.status == "pending")).first():
        timers_mod.schedule(s, "demo_cleanup", utcnow() + timedelta(hours=1))


DEMO_MID_PREFIX = "SBX"  # the sandbox partner's merchant ids


def new_demo_shop(svc: Services, s: Session, language: str | None, fresh: bool, history: bool) -> Merchant:
    """A demo shop of its own for each demo sign-in (security plan C1): visitors never share one."""
    mid = new_id("mer")
    tenancy.set_shop(s, mid)
    paytm_mid = f"{DEMO_MID_PREFIX}{secrets.token_hex(8).upper()}"
    if fresh:  # "Start as a new shop": nothing set, nothing seeded
        m = merchants.create_merchant(s, "", "en", "", id=mid, paytm_mid=paytm_mid, soundbox_id="SB-DEMO",
                                      language_set=False, sample_data=False)
    else:
        m = merchants.create_merchant(s, DEMO_SHOP["name"], language or "en", DEMO_SHOP["upi_vpa"], id=mid,
                                      paytm_mid=paytm_mid, soundbox_id="SB-DEMO", sample_data=True)
    s.flush()
    svc.summaries.schedule(s, m)
    if history and not fresh:
        seed_demo_month(s, m)
        alerts.refresh(s, m.id, svc.alerts.th)
    return m


def sandbox_router(svc: Services, owner) -> APIRouter:
    """Demo only (LAABAM_SANDBOX=1; never registered otherwise). Signing in starts a new demo shop; every other
    route needs that shop's token and acts only on its own data."""
    r = APIRouter(prefix="/sandbox", tags=["sandbox"])
    cfg = svc.settings

    @r.post("/session")
    def session(body: SandboxSessionIn, request: Request):
        """Stands in for Paytm for Business opening Laabam: a new demo shop, then the sandbox partner signs an
        assertion for it, exchanged exactly like a real one."""
        svc.limiter.take("demo.shop", ("ip", client_ip(request, cfg.trusted_proxies)))
        with svc.sessions.begin() as s:
            m = new_demo_shop(svc, s, body.language, body.fresh, body.history)
            assertion = svc.sandbox_partner.sign(m.paytm_mid)
            claims = svc.partner_verifier.verify(s, assertion)
            assert claims.mid == m.paytm_mid
            issued = svc.auth.start(s, m.id, device_label(request.headers.get("user-agent")), "sandbox")
            ensure_demo_cleanup(s)
            out = session_response(svc, issued, m)
        svc.log_auth("signed_in", shop=m.id, via="sandbox")
        return out

    @r.post("/reset")
    def reset(body: SandboxSessionIn, request: Request):
        return session(body, request)

    def fire(timer_id: str, mid: str) -> dict:
        with svc.sessions.begin() as s:
            if s.execute(select(Timer.id).where(Timer.id == timer_id, Timer.merchant_id == mid)).first() is None:
                raise HTTPException(404, "Not found")  # not this shop's
            moved = s.execute(update(Timer).where(Timer.id == timer_id, Timer.status == "pending",
                                                  Timer.merchant_id == mid)
                              .values(due_at=utcnow() - timedelta(seconds=1))).rowcount
        if not moved:
            raise HTTPException(409, "No pending timer with that id")
        ok = svc.timers.run(timer_id)
        with svc.sessions() as s:
            t = s.get(Timer, timer_id)
            return {"ok": ok, "status": t.status, "last_error": t.last_error}

    def own(merchant_id: str, mid: str) -> None:
        if merchant_id != mid:
            raise HTTPException(404, "Not found")

    @r.post("/timers/{timer_id}/fire")
    def fire_timer(timer_id: str, mid: str = Depends(owner)):
        """Runs one of your shop's pending timers now (demo: expire a QR, play the summary)."""
        return fire(timer_id, mid)

    @r.post("/summary/{merchant_id}")
    def play_summary(merchant_id: str, mid: str = Depends(owner)):
        """Demo: play the closing-time summary now (built, saved, spoken on the Soundbox)."""
        own(merchant_id, mid)
        with svc.sessions.begin() as s:
            return summary_json(svc.summaries.play(s, s.get(Merchant, mid)))

    @r.post("/bills/{bill_id}/expire")
    def expire_bill(bill_id: str, mid: str = Depends(owner)):
        """Demo: let the bill's QR run out now."""
        with svc.sessions() as s:
            own_bill = s.execute(select(SalesBill.id).where(SalesBill.id == bill_id,
                                                             SalesBill.merchant_id == mid)).first()
            if own_bill is None:
                raise HTTPException(404, "Not found")
            tid = s.execute(select(Timer.id).where(Timer.kind == "qr_expiry", Timer.subject == bill_id,
                                                   Timer.merchant_id == mid, Timer.status == "pending")
                            ).scalar_one_or_none()
        if tid is None:
            raise HTTPException(409, "This bill has no QR waiting")
        return fire(tid, mid)

    sb = svc.sandbox

    @r.post("/qr/{order_id}/pay")
    def pay(order_id: str, body: SandboxPayIn | None = None, mid: str = Depends(owner)):
        """Plays the customer scanning one of your QRs and paying (optionally a different amount)."""
        with svc.sessions() as s:
            bill_id = order_id.rsplit("-", 1)[0]  # an older QR of the bill can still be paid
            if s.execute(select(SalesBill.id).where(SalesBill.id == bill_id,
                                                    SalesBill.merchant_id == mid)).first() is None:
                raise HTTPException(404, "No such QR")
        try:
            return sb.payments.pay(order_id, body.amount_paise if body else None)
        except KeyError:
            raise HTTPException(404, "No such QR") from None

    @r.get("/soundbox/{merchant_id}")
    def soundbox(merchant_id: str, after: int = 0, mid: str = Depends(owner)):
        own(merchant_id, mid)
        return sb.soundbox.state(mid, after)

    @r.get("/soundbox/audio/{seq}")
    def soundbox_audio(seq: int, mid: str = Depends(owner)):
        if sb.soundbox.audio_owner.get(seq) != mid:
            raise HTTPException(404, "No audio")
        return Response(sb.soundbox.audio[seq], media_type="audio/mpeg")

    @r.get("/trace/{merchant_id}")
    def trace(merchant_id: str, after: int = 0, mid: str = Depends(owner)):
        own(merchant_id, mid)
        return sb.trace.feed(mid, after)

    @r.get("/sms/{merchant_id}")
    def sms(merchant_id: str, mid: str = Depends(owner)):
        own(merchant_id, mid)
        return sb.sms.sent.get(mid, [])

    return r


def create_app(services: Services | None = None) -> FastAPI:
    svc = services or build_services(default_settings)
    cfg, bills = svc.settings, svc.bills

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        with svc.sessions.begin() as s:
            svc.alerts.ensure_nightly(s)
            svc.summaries.ensure_all(s)
            privacy.ensure_retention(s)
            if cfg.sandbox:
                ensure_demo_cleanup(s)
        if cfg.inline_worker:
            svc.worker.start()
        yield
        svc.worker.stop()

    logs.configure()  # JSON lines with request ids, redacted; uvicorn doesn't configure app loggers

    dev = cfg.env == "dev"
    app = FastAPI(title="Laabam API", version=VERSION, lifespan=lifespan, docs_url="/docs" if dev else None,
                  redoc_url=None, openapi_url="/openapi.json" if dev else None)  # no API map outside development
    app.add_middleware(RequestGuard)
    app.add_middleware(logs.RequestIds, trusted_proxies=cfg.trusted_proxies)  # outermost: ids on every line
    app.state.services = svc
    origins = [o.strip() for o in cfg.cors_origins.split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(RateLimited)
    async def _limited(_r, e):
        return JSONResponse({"detail": "Too many requests. Please wait a moment and try again."}, status_code=429,
                            headers={"Retry-After": str(e.retry_after)})

    @app.exception_handler(NotFound)
    async def _nf(_r, e):
        return JSONResponse({"detail": "Not found"}, status_code=404)

    @app.exception_handler(NotAllowed)
    async def _na(_r, e):
        return JSONResponse({"detail": str(e)}, status_code=409)

    @app.exception_handler(Exception)
    async def _last_resort(request: Request, e):
        """Anything unhandled: logged in full (redacted) with the request id; the caller gets only the id."""
        rid = request.scope.get("laabam.request_id", "-")
        token = logs.request_id.set(rid)
        try:
            log.error("unhandled error", exc_info=e)
        finally:
            logs.request_id.reset(token)
        return JSONResponse({"detail": "Something went wrong. Please try again.", "request_id": rid},
                            status_code=500, headers={"X-Request-Id": rid, "Cache-Control": "no-store"})

    @app.exception_handler(QRFailed)
    async def _qr(_r, e):
        log.warning("QR failed: %s", e)
        return JSONResponse({"detail": "Couldn't make the QR. Please try again."}, status_code=502)

    @app.exception_handler(VoiceUnavailable)
    async def _no_voice(_r, e):
        log.info("voice unavailable: %s", e)
        return JSONResponse({"detail": "No speech service is set up, so Laabam can't hear recordings. "
                                       "Please type the order."}, status_code=503)

    @app.exception_handler(VisionUnavailable)
    async def _no_vision(_r, e):
        log.info("vision unavailable: %s", e)
        return JSONResponse({"detail": "Reading bill photos is not set up yet."}, status_code=503)

    @app.exception_handler(UnreadablePhoto)
    async def _bad_photo(_r, e):
        return JSONResponse({"detail": str(e)}, status_code=422)

    @app.exception_handler(HeardNothing)
    async def _nothing(_r, e):
        return JSONResponse({"detail": str(e)}, status_code=422)

    @app.exception_handler(AdapterError)
    async def _partner(_r, e):
        log.warning("adapter error: %s", e)
        return JSONResponse({"detail": "That didn't work. Please try again."}, status_code=502)

    @app.exception_handler(merchants.InvalidSetting)
    async def _bad_setting(_r, e):
        return JSONResponse({"detail": str(e)}, status_code=422)

    @app.exception_handler(IdempotencyConflict)
    async def _idem(_r, e):
        return JSONResponse({"detail": str(e)}, status_code=422)

    @app.exception_handler(AdapterUnavailable)
    async def _adapter(_r, e):
        log.warning("adapter unavailable: %s", e)
        return JSONResponse({"detail": "This service is not set up for your shop yet."}, status_code=503)

    # ---------------- auth ----------------

    owner, owner_access = make_owner(svc)
    app.include_router(auth_router(svc, owner_access))

    # ---------------- health ----------------

    @app.get("/api/health")
    def health():
        if cfg.env != "dev":  # the app needs the modes; nobody else needs versions or provider names
            return {"ok": True, "sandbox": cfg.sandbox, "voice": svc.voice.mode, "vision": svc.purchases.mode,
                    "bill_samples": svc.purchases.samples()}
        return {"ok": True, "version": VERSION, "sandbox": cfg.sandbox,
                "providers": {"llm": cfg.llm, "speech": cfg.speech, "vision": svc.purchases.vision.name,
                              "vision_fallback": getattr(getattr(svc.purchases.vision, "fallback", None), "name",
                                                         None)},
                "voice": svc.voice.mode, "vision": svc.purchases.mode,
                "bill_samples": svc.purchases.samples()}

    # ---------------- the shop ----------------

    @app.get("/v1/me")
    def get_me(mid: str = Depends(owner)):
        with svc.sessions() as s:
            return merchants.me_json(s.get(Merchant, mid))

    @app.get("/v1/me/export")
    def export_me(mid: str = Depends(owner)):
        """Everything the shop has, as a zip (JSON files and bill photos)."""
        svc.limiter.take("export", ("shop", mid))
        with svc.sessions.begin() as s:
            data = privacy.export(s, mid, svc.purchases.photos)
        stamp = utcnow().strftime("%Y%m%d")
        return Response(data, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="laabam-{stamp}.zip"'})

    @app.delete("/v1/me")
    def delete_me(body: DeleteShopIn, access=Depends(owner_access)):
        """Delete the shop, after the grace period. Needs the shop's name typed and a sign-in in the last 10 min."""
        with svc.sessions.begin() as s:
            try:
                due = privacy.request_delete(s, access.merchant_id, access.session_id, body.shop_name,
                                             cfg.deletion_grace_days)
            except privacy.NeedsFreshSignIn:
                raise HTTPException(403, "For your safety, please sign in again, then delete.") from None
            except privacy.WrongName:
                raise HTTPException(422, "Type your shop's name exactly to confirm.") from None
        return {"deletion_due_at": due.isoformat()}

    @app.delete("/v1/me/deletion")
    def keep_me(mid: str = Depends(owner)):
        """Cancel a deletion that hasn't run yet."""
        with svc.sessions.begin() as s:
            privacy.cancel_delete(s, mid)
            return merchants.me_json(s.get(Merchant, mid))

    @app.patch("/v1/me")
    def patch_me(body: MeIn, mid: str = Depends(owner)):
        with svc.sessions.begin() as s:
            m = merchants.update(s.get(Merchant, mid), **body.model_dump())
            if body.closing_time is not None:
                svc.summaries.schedule(s, m)  # the evening summary moves with the closing time
            return merchants.me_json(m)

    # ---------------- bills ----------------

    @app.post("/v1/bills/text")
    def bill_from_text(body: TextBillIn, mid: str = Depends(owner)):
        return bills.create_from_text(mid, body.text)

    async def audio_body(request: Request) -> tuple[bytes, str]:
        ctype = request.headers.get("content-type", "")
        if not ctype.startswith("audio/"):
            raise HTTPException(415, "Send the recording as audio.")
        size = int(request.headers.get("content-length") or 0)
        if size > MAX_AUDIO_BYTES:
            raise HTTPException(413, "That recording is too long. Please say a shorter order.")
        chunks, total = [], 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_AUDIO_BYTES:
                raise HTTPException(413, "That recording is too long. Please say a shorter order.")
            chunks.append(chunk)
        audio = b"".join(chunks)
        if audio:  # an empty body names a sample clip (tests, demo); a real recording must be one
            try:
                check_audio(audio, ctype)
            except BadAudio:
                raise HTTPException(415, "That isn't a recording Laabam can use. Please try again.") from None
        return audio, ctype

    @app.post("/v1/bills/voice")
    async def bill_from_voice(request: Request, mid: str = Depends(owner),
                              x_laabam_clip: str | None = Header(None, max_length=64),
                              x_laabam_sample_order: str | None = Header(None, max_length=8)):
        """Push-to-talk: the recording in the body (audio/webm, audio/mp4, ...). The audio is not stored.
        X-Laabam-Clip names an offline sample clip (tests). X-Laabam-Sample-Order: next plays the demo's next
        sample order (sandbox, no speech service only); without it a recording is never replaced by a sample."""
        svc.limiter.take("voice", ("shop", mid), ("ip", client_ip(request, cfg.trusted_proxies)))
        audio, ctype = await audio_body(request)
        sample = x_laabam_sample_order == "next"
        try:
            return await run_in_threadpool(svc.voice.bill, mid, audio, ctype, x_laabam_clip, sample)
        except AdapterError as e:
            raise HTTPException(502, "Couldn't hear that. Please try again, or type the order.") from e

    @app.post("/v1/bills/{bill_id}/say/voice")
    async def say_voice(bill_id: str, request: Request, mid: str = Depends(owner),
                        x_laabam_clip: str | None = Header(None, max_length=64)):
        """A spoken fix ("sugar 120 not 100") as a recording."""
        svc.limiter.take("voice", ("shop", mid), ("ip", client_ip(request, cfg.trusted_proxies)))
        audio, ctype = await audio_body(request)
        try:
            return await run_in_threadpool(svc.voice.say, mid, bill_id, audio, ctype, x_laabam_clip)
        except AdapterError as e:
            raise HTTPException(502, "Couldn't hear that. Please try again, or type the fix.") from e

    @app.get("/v1/bills")
    def list_bills(mid: str = Depends(owner)):
        return bills.list(mid)

    @app.get("/v1/bills/{bill_id}")
    def get_bill(bill_id: str, mid: str = Depends(owner)):
        return bills.get(mid, bill_id)

    @app.post("/v1/bills/{bill_id}/lines")
    def add_line(bill_id: str, body: LineIn, mid: str = Depends(owner)):
        return bills.add_line(mid, bill_id, body.edit())

    @app.patch("/v1/bills/{bill_id}/lines/{line_id}")
    def edit_line(bill_id: str, line_id: str, body: LineIn, mid: str = Depends(owner)):
        return bills.edit_line(mid, bill_id, line_id, body.edit())

    @app.delete("/v1/bills/{bill_id}/lines/{line_id}")
    def delete_line(bill_id: str, line_id: str, mid: str = Depends(owner)):
        return bills.delete_line(mid, bill_id, line_id)

    @app.post("/v1/bills/{bill_id}/say")
    def say(bill_id: str, body: TextBillIn, mid: str = Depends(owner)):
        """A spoken fix ("sugar 120 not 100") or addition, as text."""
        return bills.say(mid, bill_id, body.text)

    @app.post("/v1/bills/{bill_id}/confirm")
    def confirm(bill_id: str, mid: str = Depends(owner)):
        return bills.confirm(mid, bill_id)

    @app.post("/v1/bills/{bill_id}/cancel")
    def cancel(bill_id: str, mid: str = Depends(owner)):
        return bills.cancel(mid, bill_id)

    @app.post("/v1/bills/{bill_id}/edit")
    def reopen(bill_id: str, mid: str = Depends(owner)):
        """Back to the checklist from the QR: withdraws the QR; the bill is a draft again."""
        return bills.reopen(mid, bill_id)

    @app.post("/v1/bills/{bill_id}/show-qr")
    def show_qr(bill_id: str, mid: str = Depends(owner)):
        """Show the QR again (after it expired, or for a new attempt)."""
        return svc.payments.show_again(mid, bill_id)

    @app.post("/v1/purchases/photo")
    async def purchase_photo(request: Request, mid: str = Depends(owner),
                             x_laabam_sample: str | None = Header(None, max_length=64)):
        """Demo and tests only: X-Laabam-Sample names a sample bill, read at once. Real photos are uploaded
        straight to storage: POST /v1/purchases/uploads, PUT the photo, POST /v1/purchases/{id}/uploaded."""
        ctype = request.headers.get("content-type", "")
        chunks, total = [], 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_PHOTO_BYTES:
                raise HTTPException(413, "That photo is too large. Please try again.")
            chunks.append(chunk)
        try:
            out = await run_in_threadpool(svc.purchases.create_from_photo, mid, b"".join(chunks), ctype,
                                          x_laabam_sample)
            return JSONResponse(out, status_code=202) if out["status"] == "reading" else out
        except AdapterError as e:
            log.warning("vision failed: %s", e)
            raise HTTPException(502, "Couldn't read the photo. Please try again.") from e

    @app.post("/v1/purchases/uploads")
    def start_upload(body: UploadIn, request: Request, mid: str = Depends(owner)):
        """A presigned PUT for one bill photo: 5 minutes, this content type and size, this shop's key."""
        svc.limiter.take("photo.upload", ("shop", mid), ("ip", client_ip(request, cfg.trusted_proxies)))
        return svc.purchases.start_upload(mid, body.content_type, body.size)

    @app.post("/v1/purchases/{bill_id}/uploaded")
    def finish_upload(bill_id: str, mid: str = Depends(owner)):
        """The photo is up: the worker re-encodes it and reads it (202, the bill is "reading")."""
        return JSONResponse(svc.purchases.finish_upload(mid, bill_id), status_code=202)

    if hasattr(svc.purchases.photos, "check_upload"):  # local storage only; in AWS the phone PUTs to S3
        @app.put("/v1/uploads/{token}")
        async def local_upload(token: str, request: Request):
            """Stands in for S3's presigned PUT: the signed token is the permission (key, type, size, expiry)."""
            claims = svc.purchases.photos.check_upload(token)
            if claims is None:
                raise HTTPException(403, "This upload link isn't valid.")
            ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
            if ctype != claims["t"] or request.headers.get("content-length") != str(claims["n"]):
                raise HTTPException(403, "This upload doesn't match its link.")
            chunks, total = [], 0
            async for chunk in request.stream():
                total += len(chunk)
                if total > claims["n"]:
                    raise HTTPException(413, "That photo is too large.")
                chunks.append(chunk)
            if total != claims["n"]:
                raise HTTPException(400, "The upload was cut short.")
            await run_in_threadpool(svc.purchases.photos.put, claims["k"], b"".join(chunks))
            return Response(status_code=200)

    @app.get("/v1/purchases")
    def list_purchases(mid: str = Depends(owner)):
        return svc.purchases.list(mid)

    @app.get("/v1/purchases/{bill_id}")
    def get_purchase(bill_id: str, mid: str = Depends(owner)):
        return svc.purchases.get(mid, bill_id)

    @app.get("/v1/purchases/{bill_id}/photo")
    def purchase_photo_file(bill_id: str, mid: str = Depends(owner)):
        found = svc.purchases.photo(mid, bill_id)
        if found is None:
            raise HTTPException(404, "No photo")
        return Response(found[0], media_type=found[1], headers={"Cache-Control": "private, max-age=3600"})

    @app.patch("/v1/purchases/{bill_id}")
    def patch_purchase(bill_id: str, body: PurchasePatchIn, mid: str = Depends(owner)):
        return svc.purchases.patch(mid, bill_id, **body.model_dump())

    @app.post("/v1/purchases/{bill_id}/lines")
    def add_purchase_line(bill_id: str, body: PurchaseLineIn, mid: str = Depends(owner)):
        return svc.purchases.add_line(mid, bill_id, body.edit())

    @app.patch("/v1/purchases/{bill_id}/lines/{line_id}")
    def edit_purchase_line(bill_id: str, line_id: str, body: PurchaseLineIn, mid: str = Depends(owner)):
        return svc.purchases.edit_line(mid, bill_id, line_id, body.edit())

    @app.delete("/v1/purchases/{bill_id}/lines/{line_id}")
    def delete_purchase_line(bill_id: str, line_id: str, mid: str = Depends(owner)):
        return svc.purchases.delete_line(mid, bill_id, line_id)

    @app.post("/v1/purchases/{bill_id}/confirm")
    def confirm_purchase(bill_id: str, mid: str = Depends(owner)):
        return svc.purchases.confirm(mid, bill_id)

    @app.post("/v1/purchases/{bill_id}/retry")
    def retry_purchase(bill_id: str, mid: str = Depends(owner)):
        """Read a bill again after a failed read (same photo)."""
        return svc.purchases.retry(mid, bill_id)

    @app.post("/v1/purchases/{bill_id}/discard")
    def discard_purchase(bill_id: str, mid: str = Depends(owner)):
        return svc.purchases.discard(mid, bill_id)

    @app.get("/v1/items")
    def list_items(mid: str = Depends(owner)):
        return svc.purchases.items(mid)

    # ---------------- profit (BUILD_PLAN §5.3) ----------------

    @app.get("/v1/profit")
    def profit_report(period: str = "today", mid: str = Depends(owner)):
        if period not in profit.PERIOD_DAYS:
            raise HTTPException(422, "period must be today, week or month")
        with svc.sessions() as s:
            return profit.profit_report(s, mid, period)

    @app.get("/v1/profit/items/{item_id}")
    def profit_item(item_id: str, period: str = "week", mid: str = Depends(owner)):
        if period not in profit.PERIOD_DAYS:
            raise HTTPException(422, "period must be today, week or month")
        with svc.sessions() as s:
            try:
                return profit.item_report(s, mid, item_id, period)
            except KeyError:
                raise HTTPException(404, "No such item.") from None

    @app.get("/v1/profit/items/{item_id}/bills")
    def profit_item_bills(item_id: str, period: str = "week", mid: str = Depends(owner)):
        if period not in profit.PERIOD_DAYS:
            raise HTTPException(422, "period must be today, week or month")
        with svc.sessions() as s:
            try:
                return profit.item_bills(s, mid, item_id, period)
            except KeyError:
                raise HTTPException(404, "No such item.") from None

    # ---------------- questions (BUILD_PLAN §5.7) ----------------

    def _answer(mid: str, text: str, intent: str | None, item_id: str | None) -> dict:
        with svc.sessions() as s:
            m = s.get(Merchant, mid)
            out = svc.asker.ask(s, m, text, intent, item_id)
        svc.purchases.trace(mid, "ask", {"question": text, "intent": out["intent"], "phrased_by": out["phrased_by"],
                                       "sources": len(out["sources"])})
        return out

    @app.post("/v1/ask")
    def ask(body: AskIn, request: Request, mid: str = Depends(owner)):
        """A typed question. Figures come from code; the words from the strings table (or a guarded model)."""
        svc.limiter.take("ask", ("shop", mid), ("ip", client_ip(request, cfg.trusted_proxies)))
        return _answer(mid, body.text.strip(), body.intent, body.item_id)

    @app.post("/v1/ask/voice")
    async def ask_voice(request: Request, mid: str = Depends(owner),
                        x_laabam_clip: str | None = Header(None, max_length=64)):
        """A spoken question (push-to-talk). The audio is not stored."""
        svc.limiter.take("voice", ("shop", mid), ("ip", client_ip(request, cfg.trusted_proxies)))
        audio, ctype = await audio_body(request)
        try:
            text = await run_in_threadpool(svc.voice.question, mid, audio, ctype, x_laabam_clip)
        except AdapterError as e:
            raise HTTPException(502, "Couldn't hear that. Please try again, or type the question.") from e
        return _answer(mid, text, None, None)

    @app.post("/v1/speak")
    def speak(body: SpeakIn, request: Request, mid: str = Depends(owner)):
        """Listen: the answer read aloud in the shop's language. 204 when no speech service is set up (the app
        then uses the phone's own voice)."""
        svc.limiter.take("speak", ("shop", mid), ("ip", client_ip(request, cfg.trusted_proxies)))
        with svc.sessions() as s:
            lang = s.get(Merchant, mid).language
        try:
            audio = svc.payments.speech.speak(body.text, lang)
        except AdapterError as e:
            raise HTTPException(502, "Couldn't read it aloud just now.") from e
        if not audio:
            return Response(status_code=204)
        return Response(audio, media_type="audio/mpeg")

    # ---------------- plans and loans (BUILD_PLAN §5.5, §5.6) ----------------

    def _lender():
        try:
            return svc.adapters.get("lending", cfg.lending)
        except AdapterUnavailable:
            raise HTTPException(503, "No lending partner is set up yet.") from None

    @app.get("/v1/plans/festival")
    def festival_plan(multiplier_bp: int = plans.DEFAULT_MULTIPLIER_BP, festival: str | None = None,
                      mid: str = Depends(owner)):
        """The festival stock plan: the usual week x the multiplier (a guess the owner changes), cash in hand and
        the shortfall. Not saved; POST /v1/restock-plans keeps a copy when the owner asks for a loan."""
        with svc.sessions() as s:
            try:
                return plans.festival_plan(s, s.get(Merchant, mid), multiplier_bp, festival or cfg.plan_festival)
            except plans.PlanError as e:
                raise HTTPException(422, str(e)) from None

    @app.get("/v1/plans/reorder")
    def reorder_plan(mid: str = Depends(owner)):
        """This week's reorder list: what runs out before its usual next bill."""
        with svc.sessions() as s:
            return plans.reorder_list(s, s.get(Merchant, mid), svc.alerts.th)

    @app.post("/v1/restock-plans")
    def save_restock_plan(body: PlanIn, mid: str = Depends(owner)):
        """Keeps a copy of the plan as it is now: the basis a loan offer is sized from."""
        with svc.sessions.begin() as s:
            m = s.get(Merchant, mid)
            try:
                plan = plans.festival_plan(s, m, body.multiplier_bp, body.festival or cfg.plan_festival)
            except plans.PlanError as e:
                raise HTTPException(422, str(e)) from None
            p = plans.save_plan(s, m, plan)
            s.flush()
            return {**plan, "id": p.id}

    @app.post("/v1/restock-plans/{plan_id}/loan-offer")
    def loan_offer(plan_id: str, mid: str = Depends(owner)):
        """A loan for the plan's shortfall (total - cash in hand), from the lending partner."""
        lender = _lender()
        with svc.sessions.begin() as s:
            m, p = s.get(Merchant, mid), s.get(RestockPlan, plan_id)
            if p is None or p.merchant_id != mid:
                raise HTTPException(404, "No such plan.")
            try:
                o = plans.create_offer(s, m, p, lender, shop_of(m))
            except plans.PlanError as e:
                raise HTTPException(409, str(e)) from None
            return plans.offer_json(o, p)

    @app.get("/v1/loan-offers/{offer_id}")
    def get_loan_offer(offer_id: str, mid: str = Depends(owner)):
        with svc.sessions() as s:
            o = s.get(LoanOffer, offer_id)
            if o is None or o.merchant_id != mid:
                raise HTTPException(404, "No such loan offer.")
            return plans.offer_json(o, s.get(RestockPlan, o.restock_plan_id))

    def _decide(offer_id: str, mid: str, approve: bool):
        lender = _lender()
        with svc.sessions.begin() as s:
            o = s.get(LoanOffer, offer_id, with_for_update=True)
            if o is None or o.merchant_id != mid:
                raise HTTPException(404, "No such loan offer.")
            m = s.get(Merchant, mid)
            plans.decide(s, m, o, approve, lender, shop_of(m))
            return plans.offer_json(o, s.get(RestockPlan, o.restock_plan_id))

    @app.post("/v1/loan-offers/{offer_id}/approve")
    def approve_loan(offer_id: str, mid: str = Depends(owner)):
        """The owner's tap: send the application. The lending partner makes the decision."""
        return _decide(offer_id, mid, True)

    @app.post("/v1/loan-offers/{offer_id}/decline")
    def decline_loan(offer_id: str, mid: str = Depends(owner)):
        return _decide(offer_id, mid, False)

    # ---------------- evening summary (BUILD_PLAN §5.7) ----------------

    @app.get("/v1/summaries/latest")
    def latest_summary(mid: str = Depends(owner)):
        with svc.sessions() as s:
            row = latest(s, mid)
            if row is None:
                raise HTTPException(404, "No summary yet. It comes at closing time.")
            return summary_json(row)

    @app.post("/v1/summaries")
    def summary_now(request: Request, mid: str = Depends(owner)):
        """Today's summary so far, on the phone (the Soundbox speaks only the closing-time one)."""
        svc.limiter.take("summary.now", ("shop", mid), ("ip", client_ip(request, cfg.trusted_proxies)))
        with svc.sessions.begin() as s:
            return summary_json(build_summary(s, s.get(Merchant, mid)))

    # ---------------- alerts (BUILD_PLAN §5.4) ----------------

    @app.get("/v1/alerts")
    def list_alerts(mid: str = Depends(owner)):
        with svc.sessions() as s:
            return alerts.open_alerts(s, mid)

    def _mark(alert_id: str, mid: str, status: str):
        with svc.sessions.begin() as s:
            try:
                return alerts.mark(s, mid, alert_id, status)
            except KeyError:
                raise HTTPException(404, "No such alert.") from None

    @app.post("/v1/alerts/{alert_id}/seen")
    def alert_seen(alert_id: str, mid: str = Depends(owner)):
        """Got it: the owner has seen it; it stays closed until the finding changes."""
        return _mark(alert_id, mid, "seen")

    @app.post("/v1/alerts/{alert_id}/acted")
    def alert_acted(alert_id: str, mid: str = Depends(owner)):
        """The owner took the alert's action (e.g. added the item to the reorder list)."""
        return _mark(alert_id, mid, "acted")

    @app.get("/v1/today")
    def today(mid: str = Depends(owner)):
        return svc.payments.today(mid)

    # ---------------- partners (signed) ----------------

    @app.post("/v1/events")
    async def partner_event(request: Request, x_laabam_timestamp: str | None = Header(None),
                            x_laabam_signature: str | None = Header(None)):
        raw = await request.body()
        if not security.verify_signature(cfg.partner_secret, x_laabam_timestamp, x_laabam_signature, raw):
            raise HTTPException(401, "Bad signature")
        try:
            evt = PartnerEventIn.model_validate_json(raw)
        except ValueError:
            raise HTTPException(422, "Malformed event") from None
        if evt.type in ("payment.succeeded", "payment.failed"):
            d = evt.data
            try:
                order_id, amount = str(d["order_id"]), int(d.get("amount_paise", 0))
            except (KeyError, TypeError, ValueError):
                raise HTTPException(422, "payment events need order_id and amount_paise") from None
            result = svc.payments.handle_payment(evt.source, evt.id, order_id, amount, d.get("txn_id"),
                                                 "succeeded" if evt.type == "payment.succeeded" else "failed")
            return {"result": result}
        log.info("partner event of unknown type %r from %s ignored (id %s)", evt.type[:64], evt.source, evt.id[:64])
        return {"result": "ignored"}

    if cfg.sandbox:  # demo only: absent from the app (not just refused) everywhere else
        app.include_router(sandbox_router(svc, owner))

    # The built web app, served from the same origin in production.
    dist = Path(os.environ.get("LAABAM_WEB_DIST", Path(__file__).resolve().parents[2] / "frontend" / "dist"))
    if dist.is_dir() and (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith(("v1/", "api/", "sandbox/")):
                raise HTTPException(404, "Not found")
            target = (dist / path).resolve()
            if path and target.is_file() and dist.resolve() in target.parents:
                if path.startswith("samples/bills/") and path.endswith(".svg"):
                    # a sample bill dated relative to today ("six days ago") shows the date it is read as
                    svg = target.read_text(encoding="utf-8")
                    name = path.rsplit("/", 1)[-1][:-4]
                    when = (svc.purchases.vision.samples.get(name, {}) if hasattr(svc.purchases.vision, "samples")
                            else {}).get("bill_date")
                    if "{{DATE}}" in svg:
                        svg = svg.replace("{{DATE}}", sample_date(when) or "")
                    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "no-cache"})
                return FileResponse(target)
            return FileResponse(dist / "index.html")

    return app


app = create_app() if os.environ.get("LAABAM_NO_APP") != "1" else None
