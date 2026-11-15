"""Logging (security plan S3, ASVS V7): JSON lines, request ids, redaction, refused requests logged."""
import json
import logging

import pytest
from fastapi.testclient import TestClient

from laabam.logs import JsonFormatter, Redact, redact
from tests.conftest import login


@pytest.mark.parametrize("raw,secret", [
    ("Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
    ("token eyJhbGciOiJFUzI1NiJ9.eyJzdWIiOiJtZXIifQ.c2lnbmF0dXJl here", "eyJzdWIiOiJtZXIifQ"),
    ("cookie: __Host-laabam_refresh=r3fr3sh-v4lue; Path=/", "r3fr3sh-v4lue"),
    ("api-subscription-key=sk_live_123456789abc", "sk_live_123456789abc"),
    ('{"api_key": "AIzaSyD-secret"}', "AIzaSyD-secret"),
    ("password=hunter2&x=1", "hunter2"),
    ("call 98765 43210 or +91-9876543210", "9876543210"),
    ("mail owner@example.com", "owner@example.com"),
    ("pay ravi.stores@paytm", "ravi.stores@paytm"),
    ("aws AKIAABCDEFGHIJKLMNOP", "AKIAABCDEFGHIJKLMNOP"),
])
def test_redaction(raw, secret):
    out = redact(raw)
    assert secret not in out and secret.replace(" ", "") not in out.replace(" ", "")


def test_amounts_and_ids_are_not_redacted():
    line = "bill bill_0a1b2c3d total 23500 paise at 2026-11-15T10:00:00+05:30"
    assert redact(line) == line


def test_redacted_json_lines_with_the_request_id():
    rec = logging.LogRecord("laabam.x", logging.WARNING, __file__, 1, "sarvam failed: %s",
                            ("api-subscription-key=sk_abcdef123456",), None)
    Redact().filter(rec)
    out = json.loads(JsonFormatter().format(rec))
    assert out["level"] == "WARNING" and out["logger"] == "laabam.x" and "request_id" in out
    assert "sk_abcdef123456" not in out["msg"]


def test_exceptions_are_redacted_too():
    try:
        raise RuntimeError("failed with Bearer abc.def.ghi")
    except RuntimeError:
        import sys
        rec = logging.LogRecord("laabam.x", logging.ERROR, __file__, 1, "boom", (), sys.exc_info())
    Redact().filter(rec)
    assert "abc.def.ghi" not in JsonFormatter().format(rec)


def test_every_response_has_a_request_id(client):
    r = client.get("/api/health")
    assert len(r.headers["x-request-id"]) >= 8
    r = client.get("/api/health", headers={"X-Request-Id": "alb-1234567890"})
    assert r.headers["x-request-id"] == "alb-1234567890"
    r = client.get("/api/health", headers={"X-Request-Id": "bad\nid"})  # not trusted as given
    assert r.headers["x-request-id"] != "bad\nid"


def test_refused_requests_are_logged_without_the_token(client, caplog):
    with caplog.at_level("WARNING", logger="laabam.access"):
        client.get("/v1/me", headers={"Authorization": "Bearer not-a-real-token"})
    rec = next(r for r in caplog.records if r.name == "laabam.access")
    assert rec.status == 401 and rec.path == "/v1/me" and rec.event == "access_denied"
    assert "not-a-real-token" not in caplog.text


def test_another_shops_data_is_logged_as_refused(client, caplog):
    login(client)
    b = client.post("/v1/bills/text", json={"text": "rice 200"}).json()
    login(client)
    with caplog.at_level("WARNING", logger="laabam.access"):
        assert client.get(f"/v1/bills/{b['id']}").status_code == 404
    assert any(r.name == "laabam.access" and r.status == 404 for r in caplog.records)


def test_last_resort_error_gives_only_the_request_id(client, owner, services, caplog):
    def boom(*_a, **_k):
        raise RuntimeError("internal detail with Bearer abc.def.ghi")
    services.bills.create_from_text = boom
    with TestClient(client.app, raise_server_exceptions=False, base_url="https://testserver") as c, \
            caplog.at_level("ERROR", logger="laabam.app"):
        r = c.post("/v1/bills/text", json={"text": "rice 200"}, headers=owner)
    assert r.status_code == 500 and r.json()["request_id"] == r.headers["x-request-id"]
    assert "internal detail" not in r.text
