"""Settings, read once from environment variables (all prefixed LAABAM_)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import quote


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def _flag(name: str, default: bool) -> bool:
    return _env(name, "1" if default else "0").lower() in ("1", "true", "yes", "on")


def _database_url() -> str:
    """LAABAM_DATABASE_URL, or a URL built from the LAABAM_DB_* parts ECS injects from the RDS secret."""
    url, host = _env("LAABAM_DATABASE_URL", ""), _env("LAABAM_DB_HOST", "")
    if url or not host:
        return url or "sqlite:///laabam.db"
    user, password = quote(_env("LAABAM_DB_USER", ""), safe=""), quote(_env("LAABAM_DB_PASSWORD", ""), safe="")
    port, name = _env("LAABAM_DB_PORT", "5432"), _env("LAABAM_DB_NAME", "laabam")
    # verify-full: TLS, and the server's certificate must chain to the RDS CA and match the host name
    ca = quote(_env("LAABAM_DB_CA", "/etc/ssl/rds/global-bundle.pem"), safe="/")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{name}?sslmode=verify-full&sslrootcert={ca}"


@dataclass(frozen=True)
class Settings:
    # Postgres in production, e.g. postgresql+psycopg://user:pass@host:5432/laabam
    database_url: str = field(default_factory=_database_url)

    # Model providers. "offline" uses rules, fixtures and templates (tests and demos).
    llm: str = field(default_factory=lambda: _env("LAABAM_LLM", "offline"))
    speech: str = field(default_factory=lambda: _env("LAABAM_SPEECH", "offline"))
    # Bill photos: sarvam (Document AI) | gemini | offline. A provider with no API key falls back to offline.
    vision: str = field(default_factory=lambda: _env("LAABAM_VISION", "sarvam"))
    # A second reader, tried when the chosen one fails or reads nothing. Off by default ("none"); the only one
    # available is "gemini" (needs LAABAM_GEMINI_API_KEY; sends photos outside India, so prod refuses it).
    vision_fallback: str = field(default_factory=lambda: _env("LAABAM_VISION_FALLBACK", "none"))
    vision_timeout_seconds: float = field(default_factory=lambda: float(_env("LAABAM_VISION_TIMEOUT_SECONDS", "90")))
    vision_poll_seconds: float = field(default_factory=lambda: float(_env("LAABAM_VISION_POLL_SECONDS", "2")))
    # Sarvam (speech and Document AI share the key). Model names change; check https://docs.sarvam.ai.
    sarvam_api_key: str = field(default_factory=lambda: _env("LAABAM_SARVAM_API_KEY", ""))
    sarvam_base_url: str = field(default_factory=lambda: _env("LAABAM_SARVAM_BASE_URL", "https://api.sarvam.ai"))
    sarvam_stt_model: str = field(default_factory=lambda: _env("LAABAM_SARVAM_STT_MODEL", "saaras:v4"))
    sarvam_tts_model: str = field(default_factory=lambda: _env("LAABAM_SARVAM_TTS_MODEL", "bulbul:v3"))
    # Document AI language hint (BCP-47, e.g. ta-IN). "" lets Sarvam detect it: bills mix English and Indic.
    sarvam_vision_language: str = field(default_factory=lambda: _env("LAABAM_SARVAM_VISION_LANGUAGE", ""))
    # Gemini reads bill photos with LAABAM_VISION=gemini, or as the fallback. Check model names at ai.google.dev.
    gemini_api_key: str = field(default_factory=lambda: _env("LAABAM_GEMINI_API_KEY", ""))
    gemini_model: str = field(default_factory=lambda: _env("LAABAM_GEMINI_MODEL", "gemini-3.8-flash"))
    gemini_base_url: str = field(
        default_factory=lambda: _env("LAABAM_GEMINI_BASE_URL", "https://generativelanguage.googleapis.com"))
    # Bill photos on local disk (development). Production uses S3 (Phase 7).
    photo_dir: str = field(default_factory=lambda: _env("LAABAM_PHOTO_DIR", "var/photos"))
    sarvam_speaker: str = field(default_factory=lambda: _env("LAABAM_SARVAM_SPEAKER", ""))  # "" = Sarvam's default

    # Where this runs: dev (a laptop), demo (laabam-demo: sandbox adapters, synthetic data) or prod (laabam-prod).
    # validate() refuses unsafe combinations, e.g. the sandbox in prod.
    env: str = field(default_factory=lambda: _env("LAABAM_ENV", "dev"))

    # Owner access tokens (ES256, 15 minutes). In AWS the key lives in KMS (LAABAM_TOKEN_KMS_KEY_ID); development
    # signs with a local P-256 key (LAABAM_TOKEN_KEY_PEM, or a new one per process).
    token_signer: str = field(default_factory=lambda: _env("LAABAM_TOKEN_SIGNER", "local"))  # local | kms
    token_kms_key_id: str = field(default_factory=lambda: _env("LAABAM_TOKEN_KMS_KEY_ID", ""))
    token_key_pem: str = field(default_factory=lambda: _env("LAABAM_TOKEN_KEY_PEM", ""))
    access_token_seconds: int = field(default_factory=lambda: int(_env("LAABAM_ACCESS_TOKEN_SECONDS", "900")))
    refresh_idle_days: int = field(default_factory=lambda: int(_env("LAABAM_REFRESH_IDLE_DAYS", "30")))
    session_max_days: int = field(default_factory=lambda: int(_env("LAABAM_SESSION_MAX_DAYS", "90")))
    # Per-shop audit keys (security plan C3) are wrapped by this KMS key in AWS; development wraps them locally.
    audit_kms_key_id: str = field(default_factory=lambda: _env("LAABAM_AUDIT_KMS_KEY_ID", ""))
    # Partner sign-in (docs/partner-identity.md): who signs the assertions and where their keys are published.
    partner_issuer: str = field(default_factory=lambda: _env("LAABAM_PARTNER_ISSUER", "paytm-for-business"))
    partner_jwks_url: str = field(default_factory=lambda: _env("LAABAM_PARTNER_JWKS_URL", ""))
    # How many proxies in front of the API add to X-Forwarded-For (CloudFront + ALB = 2). 0: use the peer address.
    trusted_proxies: int = field(default_factory=lambda: int(_env("LAABAM_TRUSTED_PROXIES", "0")))
    # Demo shops a single IP may start per hour (each demo sign-in starts its own shop).
    demo_shops_per_ip_hour: int = field(default_factory=lambda: int(_env("LAABAM_DEMO_SHOPS_PER_IP_HOUR", "20")))
    # Tests only: a connection that owns the tables (bypasses row-level security) for set-up and inspection.
    owner_database_url: str = field(default_factory=lambda: _env("LAABAM_OWNER_DATABASE_URL", ""))

    # Retention: transcripts, heard text and bill photos older than this are cleared by a daily sweep.
    retention_days: int = field(default_factory=lambda: int(_env("LAABAM_RETENTION_DAYS", "365")))
    # "Delete my shop" runs after this many days; the owner can cancel until then.
    deletion_grace_days: int = field(default_factory=lambda: int(_env("LAABAM_DELETION_GRACE_DAYS", "7")))

    # Run the timer worker inside the API process (fine for one instance).
    inline_worker: bool = field(default_factory=lambda: _flag("LAABAM_INLINE_WORKER", True))
    worker_interval: float = field(default_factory=lambda: float(_env("LAABAM_WORKER_INTERVAL", "1.0")))

    # Payments. The QR is valid this long before the bill expires (the owner can show it again).
    qr_ttl_seconds: int = field(default_factory=lambda: int(_env("LAABAM_QR_TTL_SECONDS", "300")))
    payment_poll_seconds: int = field(default_factory=lambda: int(_env("LAABAM_PAYMENT_POLL_SECONDS", "5")))
    paytm_base_url: str = field(default_factory=lambda: _env("LAABAM_PAYTM_BASE_URL", "https://securegw-stage.paytm.in"))
    paytm_merchant_key: str = field(default_factory=lambda: _env("LAABAM_PAYTM_MERCHANT_KEY", ""))
    paytm_client_id: str = field(default_factory=lambda: _env("LAABAM_PAYTM_CLIENT_ID", "C11"))
    # Signs partner events to POST /v1/events (HMAC, X-Laabam-Signature).
    partner_secret: str = field(default_factory=lambda: _env("LAABAM_PARTNER_SECRET", ""))

    # Alerts (BUILD_PLAN §5.4). A cost rise counts at either threshold; slow = no sale for this many days.
    alert_rise_bp: int = field(default_factory=lambda: int(_env("LAABAM_ALERT_RISE_BP", "500")))  # 5%
    alert_rise_paise: int = field(default_factory=lambda: int(_env("LAABAM_ALERT_RISE_PAISE", "200")))  # ₹2
    alert_low_margin_bp: int = field(default_factory=lambda: int(_env("LAABAM_ALERT_LOW_MARGIN_BP", "800")))  # 8%
    alert_slow_days: int = field(default_factory=lambda: int(_env("LAABAM_ALERT_SLOW_DAYS", "14")))
    # Real sales, for the low-margin alert: at least this many bills with the item in the last 14 days.
    alert_min_bills: int = field(default_factory=lambda: int(_env("LAABAM_ALERT_MIN_BILLS", "3")))
    # Nightly scan time (IST, HH:MM); scans also run after every confirmed distributor bill.
    alert_scan_at: str = field(default_factory=lambda: _env("LAABAM_ALERT_SCAN_AT", "02:00"))

    # Plans and loans (BUILD_PLAN §5.5, §5.6): the festival the plan is for (laabam/festivals.yaml), the lender.
    plan_festival: str = field(default_factory=lambda: _env("LAABAM_PLAN_FESTIVAL", "deepavali"))
    lending: str = field(default_factory=lambda: _env("LAABAM_LENDING", "sandbox"))

    # Sandbox adapters and demo routes. Never enable in production.
    sandbox: bool = field(default_factory=lambda: _flag("LAABAM_SANDBOX", False))

    # Other browser origins allowed to call the API (comma-separated). None by default: the PWA is served from the
    # same origin (CloudFront in AWS, the Vite proxy in development).
    cors_origins: str = field(default_factory=lambda: _env("LAABAM_CORS_ORIGINS", ""))


def _partner_adapters(self) -> str:
    """The adapters a new shop gets: the sandbox ones in the demo, Paytm's otherwise."""
    return "sandbox" if self.sandbox else "paytm"


Settings.partner_adapters = property(_partner_adapters)


class UnsafeConfig(RuntimeError):
    """The service refuses to start with this configuration."""


def validate(cfg: Settings) -> None:
    """Fail closed at start-up: the deployed stacks must have every control on."""
    problems = []
    if cfg.env not in ("dev", "demo", "prod"):
        problems.append(f"LAABAM_ENV must be dev, demo or prod, not {cfg.env!r}")
    if cfg.env == "prod":
        if cfg.sandbox:
            problems.append("LAABAM_SANDBOX=1 is not allowed in prod")
        if cfg.lending == "sandbox":
            problems.append("prod needs a real lending adapter (LAABAM_LENDING)")
        if cfg.vision_fallback not in ("", "none"):
            problems.append("the vision fallback sends bill photos outside India: not allowed in prod")
        if cfg.vision == "gemini":
            problems.append("LAABAM_VISION=gemini sends bill photos outside India: not allowed in prod")
        if not cfg.partner_jwks_url.startswith("https://"):
            problems.append("prod needs the partner's JWKS URL (https)")
    if cfg.env in ("demo", "prod"):
        if cfg.token_signer != "kms" or not cfg.token_kms_key_id:
            problems.append("deployed stacks sign tokens with KMS (LAABAM_TOKEN_SIGNER=kms, LAABAM_TOKEN_KMS_KEY_ID)")
        url = cfg.database_url
        # laabam-demo may keep its synthetic data in a local SQLite file inside the container: it never crosses a
        # network, so there is no TLS to verify. prod always needs Postgres over verified TLS.
        local_demo_file = cfg.env == "demo" and url.startswith("sqlite:///") and not url.startswith("sqlite:////:")
        if not local_demo_file and (not url.startswith("postgresql") or "sslmode=verify-full" not in url
                                    or "sslrootcert=" not in url):
            problems.append("deployed stacks use Postgres with TLS verify-full and the RDS CA (sslmode=verify-full)")
        if not cfg.audit_kms_key_id:
            problems.append("deployed stacks wrap the shops' audit keys with KMS (LAABAM_AUDIT_KMS_KEY_ID)")
        if "*" in cfg.cors_origins:
            problems.append("CORS '*' is not allowed outside development")
        if cfg.owner_database_url:
            problems.append("LAABAM_OWNER_DATABASE_URL is for tests only")
    if cfg.env == "demo" and not cfg.sandbox:
        problems.append("laabam-demo runs with LAABAM_SANDBOX=1")
    if problems:
        raise UnsafeConfig("; ".join(problems))


settings = Settings()
