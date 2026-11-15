"""Input handling (security plan §3, §8): strict bodies, size caps, content types, query hygiene, response headers,
nothing extra exposed outside development, audio codecs."""
import dataclasses

import pytest
from fastapi.testclient import TestClient

from laabam.app import build_services, create_app
from laabam.config import Settings
from laabam.voice import BadAudio, check_audio
from tests.conftest import login


def test_unknown_fields_are_refused(client, owner):
    r = client.post("/v1/bills/text", json={"text": "rice 200", "total_paise": 1}, headers=owner)
    assert r.status_code == 422  # not silently ignored
    assert client.patch("/v1/me", json={"merchant_id": "mer_other"}, headers=owner).status_code == 422
    assert client.post("/v1/ask", json={"text": "why", "admin": True}, headers=owner).status_code == 422


@pytest.mark.parametrize("path,body", [
    ("/v1/bills/text", {"text": "x" * 2001}),
    ("/v1/ask", {"text": "x" * 501}),
    ("/v1/me", {"language": "x" * 9}),
    ("/v1/restock-plans", {"multiplier_bp": 999_999}),
])
def test_limits_on_every_field(client, owner, path, body):
    method = client.patch if path == "/v1/me" else client.post
    assert method(path, json=body, headers=owner).status_code == 422


def test_json_bodies_are_capped_and_typed(client, owner):
    big = b'{"text": "' + b"x" * 70_000 + b'"}'
    r = client.post("/v1/bills/text", content=big, headers={**owner, "Content-Type": "application/json"})
    assert r.status_code == 413
    r = client.post("/v1/bills/text", content=b'{"text": "rice 200"}', headers={**owner, "Content-Type": "text/plain"})
    assert r.status_code == 415
    form = {**owner, "Content-Type": "application/x-www-form-urlencoded"}
    r = client.post("/v1/bills/text", content=b"text=rice", headers=form)
    assert r.status_code == 415


def test_a_query_key_given_twice_is_refused(client, owner):
    assert client.get("/v1/profit?period=week&period=month", headers=owner).status_code == 400
    assert client.get("/v1/profit?period=week", headers=owner).status_code == 200


def test_api_responses_are_not_cached_or_sniffed(client, owner):
    r = client.get("/v1/me", headers=owner)
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
    assert "server" not in {k.lower() for k in r.headers}


def test_nothing_extra_outside_development(db_url, tmp_path, monkeypatch):
    monkeypatch.delenv("LAABAM_OWNER_DATABASE_URL", raising=False)
    cfg = Settings(database_url=db_url, env="demo", sandbox=True, inline_worker=False, photo_dir=str(tmp_path),
                   token_signer="kms", token_kms_key_id="alias/x", audit_kms_key_id="alias/a",
                   owner_database_url="")
    import laabam.app as app_module
    monkeypatch.setattr(app_module, "make_signer", lambda _cfg: __import__("laabam.tokens").tokens.LocalSigner())
    # the API under test runs on the test database; every other demo check still applies
    real_validate = app_module.validate
    monkeypatch.setattr(app_module, "validate", lambda c: real_validate(dataclasses.replace(
        c, database_url="postgresql+psycopg://db.internal/laabam?sslmode=verify-full&sslrootcert=/ca.pem")))
    monkeypatch.setattr(app_module, "make_keyring", lambda _cfg: __import__("laabam.shopkeys").shopkeys.LocalKeyring())
    svc = build_services(cfg)
    with TestClient(create_app(svc), base_url="https://testserver") as c:
        h = c.get("/api/health").json()
        assert set(h) == {"ok", "sandbox", "voice", "vision", "bill_samples"}  # no versions, no provider names
        assert c.get("/docs").status_code in (404, 200) and "swagger" not in c.get("/docs").text.lower()
        assert c.get("/openapi.json").status_code != 200 or "paths" not in c.get("/openapi.json").text
    svc.sessions.kw["bind"].dispose()


def test_errors_never_carry_internals(client, owner, services):
    def boom(*_a, **_k):
        raise RuntimeError("secret internal detail at /srv/laabam/db.py line 42")
    services.bills.create_from_text = boom
    with TestClient(client.app, raise_server_exceptions=False, base_url="https://testserver") as c:
        r = c.post("/v1/bills/text", json={"text": "rice 200"}, headers=owner)
    assert r.status_code == 500 and "secret" not in r.text and "Traceback" not in r.text


# ---------------- audio ----------------


@pytest.mark.parametrize("ctype,head", [
    ("audio/webm", bytes.fromhex("1a45dfa3")), ("audio/ogg", b"OggS"), ("audio/mp4", b"\x00\x00\x00\x18ftypM4A "),
    ("audio/mpeg", b"ID3\x04"), ("audio/wav", b"RIFF\x24\x00\x00\x00WAVE"),
])
def test_accepted_codecs(ctype, head):
    body = head + b"\x00" * 64
    if ctype == "audio/wav":  # a byte rate in the header: 16 kHz mono 16-bit
        body = body[:28] + (32_000).to_bytes(4, "little") + body[32:]
    check_audio(body, ctype)


@pytest.mark.parametrize("ctype,body", [
    ("audio/webm", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64),  # an image posing as audio
    ("audio/webm", b"<script>alert(1)</script>"),
    ("audio/flac", b"fLaC" + b"\x00" * 64),  # a codec we don't take
    # 50 s of WAV at 8 kB/s: longer than allowed
    ("audio/wav", b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 16 + (8000).to_bytes(4, "little") + b"\x00" * 400_000),
])
def test_refused_audio(ctype, body):
    with pytest.raises(BadAudio):
        check_audio(body, ctype)


def test_voice_route_refuses_what_isnt_a_recording(client, owner):
    r = client.post("/v1/bills/voice", content=b"\x89PNG\r\n\x1a\n" + b"\x00" * 4000,
                    headers={**owner, "Content-Type": "audio/webm"})
    assert r.status_code == 415


def test_costly_routes_are_rate_limited(client):
    h = login(client)
    codes = [client.post("/v1/ask", json={"text": "best item this month?"}, headers=h).status_code for _ in range(41)]
    assert codes[:40] == [200] * 40 and codes[40] == 429


def test_partner_events_unknown_or_malformed(db_url, tmp_path, caplog):
    import json as _json

    from laabam import security
    svc = build_services(Settings(database_url=db_url, sandbox=True, inline_worker=False, photo_dir=str(tmp_path),
                                  partner_secret="p" * 32))
    with TestClient(create_app(svc), base_url="https://testserver") as c:
        def send(body: bytes):
            return c.post("/v1/events", content=body, headers={**security.signed_headers("p" * 32, body),
                                                               "Content-Type": "application/json"})
        with caplog.at_level("INFO", logger="laabam.app"):
            r = send(_json.dumps({"id": "e1", "type": "loan.something_new", "data": {}}).encode())
        assert r.json() == {"result": "ignored"} and "unknown type" in caplog.text
        assert send(b'{"id": "e2"}').status_code == 422  # signed but malformed: refused, not a 500
    svc.sessions.kw["bind"].dispose()
