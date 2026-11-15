"""Outbound calls (security plan §3): allowlisted https hosts only, and a circuit breaker per host."""
import httpx
import pytest

from laabam.outbound import CircuitOpen, GuardedTransport, NotAllowed, guarded_client, hosts_from


def mock(status=200):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(status, json={})

    return calls, httpx.Client(transport=httpx.MockTransport(handler))


def test_only_allowlisted_https_hosts():
    calls, inner = mock()
    c = guarded_client(hosts_from("https://api.sarvam.ai", "https://securegw-stage.paytm.in"), inner=inner)
    assert c.get("https://api.sarvam.ai/x").status_code == 200
    for url in ("https://evil.example/x", "http://api.sarvam.ai/x", "https://169.254.169.254/latest/meta-data",
                "https://api.sarvam.ai.evil.example/x"):
        with pytest.raises(NotAllowed):
            c.get(url)
    assert calls == ["https://api.sarvam.ai/x"]  # nothing else left the process
    assert hosts_from("http://insecure.example", "https://ok.example/p") == {"ok.example"}


def test_circuit_opens_after_repeated_failures_then_half_opens():
    now = [0.0]
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503)

    t = GuardedTransport(httpx.MockTransport(handler), {"api.sarvam.ai"}, clock=lambda: now[0])
    c = httpx.Client(transport=t)
    for _ in range(5):
        assert c.get("https://api.sarvam.ai/x").status_code == 503
    with pytest.raises(CircuitOpen):  # fails fast: not sent
        c.get("https://api.sarvam.ai/x")
    assert len(calls) == 5
    now[0] = 31.0  # after the cool-off, one call goes through; another failure re-opens at once
    assert c.get("https://api.sarvam.ai/x").status_code == 503
    with pytest.raises(CircuitOpen):
        c.get("https://api.sarvam.ai/x")
    assert len(calls) == 6


def test_every_adapter_uses_the_guarded_client(services):
    from laabam.outbound import GuardedTransport as G
    sarvam = services.adapters.get("vision", "sarvam")
    assert isinstance(sarvam.client._transport, G)
    assert isinstance(services.adapters.get("speech", "sarvam").client._transport, G)
