"""Calls to partners (security plan §3): one HTTP client for all of them, built here.

- Only allowlisted hosts, only https: a call anywhere else fails before it leaves (no SSRF, no surprise egress).
- TLS verification on (httpx's default, never turned off), timeouts on every call.
- A circuit breaker per host: after 5 failures in a row (network errors or 5xx), calls to that host fail fast for
  30 seconds, so a slow partner can't tie up the workers; then one call is let through to test it.
Adapters still retry within their own budgets (e.g. Sarvam's 90-second read).
"""
from __future__ import annotations

import threading
import time
from urllib.parse import urlparse

import httpx

FAILURES_TO_OPEN = 5
OPEN_SECONDS = 30.0


class NotAllowed(httpx.TransportError):
    """The host isn't on the allowlist (or the URL isn't https)."""


class CircuitOpen(httpx.TransportError):
    """The host failed repeatedly; calls fail fast for a while."""


class GuardedTransport(httpx.BaseTransport):
    def __init__(self, inner: httpx.BaseTransport, allow: set[str], clock=time.monotonic) -> None:
        self.inner, self.allow, self.clock = inner, {h.lower() for h in allow if h}, clock
        self._fails: dict[str, int] = {}
        self._open_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host = (request.url.host or "").lower()
        if request.url.scheme != "https" or host not in self.allow:
            raise NotAllowed(f"outbound call to {request.url.scheme}://{host} is not allowed", request=request)
        with self._lock:
            if self._open_until.get(host, 0) > self.clock():
                raise CircuitOpen(f"{host} is failing; not calling it for a while", request=request)
        try:
            response = self.inner.handle_request(request)
        except httpx.TransportError:
            self._failed(host)
            raise
        if response.status_code >= 500:
            self._failed(host)
        else:
            with self._lock:
                self._fails[host] = 0
        return response

    def _failed(self, host: str) -> None:
        with self._lock:
            n = self._fails.get(host, 0) + 1
            self._fails[host] = n
            if n >= FAILURES_TO_OPEN:
                self._open_until[host] = self.clock() + OPEN_SECONDS
                self._fails[host] = FAILURES_TO_OPEN - 1  # half-open: one more failure re-opens it

    def close(self) -> None:
        self.inner.close()


def hosts_from(*urls: str) -> set[str]:
    return {urlparse(u).hostname or "" for u in urls if u and u.startswith("https://")}


def guarded_client(allow: set[str], inner: httpx.Client | None = None, timeout: float = 30.0) -> httpx.Client:
    """The client every adapter uses. Tests pass `inner` (an httpx.Client with a MockTransport); its transport is
    wrapped the same way, so the guard is under test too."""
    transport = inner._transport if inner is not None else httpx.HTTPTransport(verify=True, retries=0)  # noqa: SLF001
    return httpx.Client(transport=GuardedTransport(transport, allow), timeout=httpx.Timeout(timeout, connect=10.0))
