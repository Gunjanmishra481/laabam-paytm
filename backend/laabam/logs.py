"""Logging (security plan S3, ASVS V7): one JSON object per line, a request id on every line, secrets and personal
data redacted before anything is written, and every access denial logged.

- configure(): JSON lines on stderr for the laabam.* loggers (CloudWatch reads them as they are).
- RequestIds: an ASGI middleware. Takes a well-formed X-Request-Id from the load balancer or makes one, returns it
  on the response, and logs refused requests (401, 403, 404 on an API path, 429) with the method, path template
  and client IP, never the token.
- Redact: a filter that masks bearer tokens, JWTs, cookies, API keys, key=value secrets, phone numbers, emails
  and UPI ids in the message and arguments.
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import secrets
import time
from datetime import UTC, datetime

request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_ID_OK = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")

_PATTERNS = [
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [redacted]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*"), "[jwt]"),
    (re.compile(r"(?i)(__Host-laabam_refresh|refresh_token|cookie)\s*[=:]\s*[^\s;,]+"), r"\1=[redacted]"),
    (re.compile(r"(?i)\b(api[-_]?key|api-subscription-key|x-goog-api-key|secret|password|passwd|token|signature"
                r"|authorization)\b(\"?\s*[=:]\s*)(\"?)[^\s\",;&]+"), r"\1\2\3[redacted]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{10,}"), "[key]"),
    (re.compile(r"\bsk_[A-Za-z0-9_]{8,}"), "[key]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[aws-key]"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[email]"),
    (re.compile(r"\b[A-Za-z0-9._-]{2,}@(?:paytm|ok[a-z]+|ybl|upi|ibl|axl|apl)\b"), "[upi]"),
    (re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)"), "[phone]"),
]


def redact(text: str) -> str:
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text


class Redact(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 - a bad format string still gets logged, redacted
            msg = str(record.msg)
        record.msg, record.args = redact(msg), None
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
               "level": record.levelname, "logger": record.name, "request_id": request_id.get(),
               "msg": record.getMessage()}
        for k in ("event", "status", "method", "path", "ip", "shop", "ms"):
            if hasattr(record, k):
                out[k] = getattr(record, k)
        if record.exc_text:
            out["error"] = record.exc_text
        elif record.exc_info:
            out["error"] = redact(self.formatException(record.exc_info))
        return json.dumps(out, ensure_ascii=False)


def configure(level: int = logging.INFO) -> None:
    root = logging.getLogger("laabam")
    if any(getattr(h, "_laabam", False) for h in root.handlers):
        return
    h = logging.StreamHandler()
    h._laabam = True  # type: ignore[attr-defined]
    h.setFormatter(JsonFormatter())
    h.addFilter(Redact())
    root.addHandler(h)
    root.setLevel(level)
    root.propagate = True
    for handler in root.handlers:  # every laabam handler redacts, including ones added earlier
        if not any(isinstance(f, Redact) for f in handler.filters):
            handler.addFilter(Redact())


access_log = logging.getLogger("laabam.access")
DENIED = {401, 403, 429}


class RequestIds:
    """Request ids, and a log line for every refused request (ASVS V7.2.2)."""

    def __init__(self, app, trusted_proxies: int = 0) -> None:
        self.app, self.trusted_proxies = app, trusted_proxies

    def _ip(self, scope) -> str:
        if self.trusted_proxies:
            for k, v in scope.get("headers", []):
                if k == b"x-forwarded-for":
                    hops = [h.strip() for h in v.decode("latin-1").split(",") if h.strip()]
                    if len(hops) >= self.trusted_proxies:
                        return hops[-self.trusted_proxies]
        client = scope.get("client")
        return client[0] if client else "-"

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        given = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"x-request-id"), "")
        rid = given if _ID_OK.match(given) else secrets.token_hex(8)
        token = request_id.set(rid)
        scope["laabam.request_id"] = rid  # for the last-resort handler, which runs outside this middleware
        start = time.perf_counter()
        status = 0

        async def send_wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = {**message, "headers": [*message.get("headers", []), (b"x-request-id", rid.encode())]}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            path = scope["path"]
            api = path.startswith(("/v1/", "/sandbox/"))
            if status in DENIED or (status == 404 and api):
                route = scope.get("route")
                access_log.warning("refused", extra={
                    "event": "access_denied", "status": status, "method": scope["method"],
                    "path": getattr(route, "path", None) or path[:120], "ip": self._ip(scope),
                    "ms": round((time.perf_counter() - start) * 1000, 1)})
            request_id.reset(token)
