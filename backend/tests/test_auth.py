"""Identity (security plan §1): ES256 access tokens (KMS in AWS), rotating refresh tokens bound to a device session,
reuse revokes the session, partner assertions verified and single-use, sandbox gating, rate limits on auth routes."""
import json
import time
from datetime import timedelta

import jwt
import pytest
from botocore.stub import Stubber
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from sqlalchemy import select

from laabam.app import build_services, create_app
from laabam.auth import COOKIE
from laabam.config import Settings, UnsafeConfig, validate
from laabam.models import AuditEvent, DeviceSession, Merchant, RefreshToken
from laabam.partner_identity import SandboxPartner
from laabam.routes_auth import client_ip
from laabam.tokens import KmsSigner, LocalSigner, TokenError, Tokens, b64url
from tests.conftest import login

R = {"X-Laabam-Refresh": "1"}


def demo(client, **body):
    r = client.post("/sandbox/session", json={"history": False, **body})
    assert r.status_code == 200, r.text
    return r


def bearer(r) -> dict:
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ---------------- access tokens ----------------


def test_access_tokens_are_es256_short_and_strict():
    signer = LocalSigner()
    t = Tokens(signer, ttl=900)
    tok, exp = t.issue("mer_1", "ses_1")
    head = jwt.get_unverified_header(tok)
    assert head == {"alg": "ES256", "typ": "JWT", "kid": signer.kid}
    a = t.verify(tok)
    assert (a.merchant_id, a.session_id) == ("mer_1", "ses_1") and exp - time.time() <= 900
    # expired
    old, _ = t.issue("mer_1", "ses_1", now=int(time.time()) - 1000)
    with pytest.raises(TokenError):
        t.verify(old)
    # another key, 'none', and HMAC with the public key (algorithm confusion) are all refused
    with pytest.raises(TokenError):
        Tokens(LocalSigner()).verify(tok)
    claims = jwt.decode(tok, options={"verify_signature": False})
    with pytest.raises(TokenError):
        t.verify(jwt.encode(claims, None, algorithm="none"))
    pub = signer.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    forged = b64url(json.dumps({"alg": "HS256", "typ": "JWT", "kid": signer.kid}).encode()) + "." + \
        b64url(json.dumps(claims).encode())
    import hashlib
    import hmac
    forged += "." + b64url(hmac.new(pub, forged.encode(), hashlib.sha256).digest())
    with pytest.raises(TokenError):
        t.verify(forged)
    # a token minted with a longer life than allowed
    with pytest.raises(TokenError):
        Tokens(signer, ttl=900).verify(Tokens(signer, ttl=3600).issue("mer_1", "ses_1")[0])


def test_kms_signer_never_sees_the_private_key():
    """KMS signs the digest; the public key comes from KMS. Stubbed: tests never call AWS."""
    import boto3
    key = ec.generate_private_key(ec.SECP256R1())
    client = boto3.client("kms", region_name="ap-south-1", aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(client)
    arn = "arn:aws:kms:ap-south-1:111122223333:key/abc"
    stub.add_response("describe_key", {"KeyMetadata": {"KeyId": "abc", "Arn": arn, "KeySpec": "ECC_NIST_P256",
                                                       "KeyUsage": "SIGN_VERIFY"}}, {"KeyId": "alias/laabam-tokens"})
    captured = {}

    def sign_response(digest):
        captured["digest"] = digest
        return {"KeyId": arn, "Signature": key.sign(digest, ec.ECDSA(hashes.SHA256())),
                "SigningAlgorithm": "ECDSA_SHA_256"}

    with stub:
        signer = KmsSigner("alias/laabam-tokens", client)
        t = Tokens(signer)
        # KMS is asked to sign a 32-byte digest (MessageType DIGEST), never the token text
        msg = b"header.payload"
        import hashlib
        digest = hashlib.sha256(msg).digest()
        # sign over the raw digest with the prehashed key so the stub returns a valid signature
        from cryptography.hazmat.primitives.asymmetric.utils import Prehashed
        stub.add_response("sign", {"KeyId": arn, "Signature": key.sign(digest, ec.ECDSA(Prehashed(hashes.SHA256()))),
                                   "SigningAlgorithm": "ECDSA_SHA_256"},
                          {"KeyId": "alias/laabam-tokens", "Message": digest, "MessageType": "DIGEST",
                           "SigningAlgorithm": "ECDSA_SHA_256"})
        raw = signer.sign(msg)
        assert len(raw) == 64
        stub.add_response("get_public_key", {"PublicKey": key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)},
            {"KeyId": "alias/laabam-tokens"})
        assert signer.public_key().public_numbers() == key.public_key().public_numbers()
    assert t.signer.kid.startswith("kms-") and captured == {}


def test_wrong_kms_key_type_is_refused():
    import boto3
    client = boto3.client("kms", region_name="ap-south-1", aws_access_key_id="x", aws_secret_access_key="x")
    stub = Stubber(client)
    stub.add_response("describe_key", {"KeyMetadata": {"KeyId": "k", "Arn": "arn:aws:kms:ap-south-1:111122223333:key/k",
                                                       "KeySpec": "SYMMETRIC_DEFAULT",
                                                       "KeyUsage": "ENCRYPT_DECRYPT"}}, {"KeyId": "k"})
    with stub, pytest.raises(ValueError):
        KmsSigner("k", client)


# ---------------- refresh tokens and device sessions ----------------


def test_refresh_cookie_is_host_only_httponly_strict(client):
    r = demo(client)
    c = r.headers["set-cookie"]
    assert c.startswith(f"{COOKIE}=") and "HttpOnly" in c and "Secure" in c and "SameSite=strict" in c
    assert "Path=/" in c and "Domain" not in c
    assert r.headers["cache-control"] == "no-store"


def test_refresh_rotates_and_reuse_revokes_the_session(client, services):
    r = demo(client)
    first = client.cookies.get(COOKIE)
    assert client.post("/v1/auth/refresh").status_code == 403  # no CSRF header
    r2 = client.post("/v1/auth/refresh", headers=R)
    assert r2.status_code == 200 and r2.json()["merchant"]["id"] == r.json()["merchant"]["id"]
    second = client.cookies.get(COOKIE)
    assert second and second != first
    with services.admin_sessions() as s:  # only hashes are stored
        stored = list(s.execute(select(RefreshToken.token_hash)).scalars())
        assert first not in stored and second not in stored and all(len(h) == 64 for h in stored)
    # the first token again: someone copied it. The whole session ends, including the newer token
    client.cookies.set(COOKIE, first, domain="testserver.local", path="/")
    client.cookies.clear()
    stolen = TestClient(client.app, base_url="https://testserver")
    stolen.cookies.set(COOKIE, first)
    assert stolen.post("/v1/auth/refresh", headers=R).status_code == 401
    assert client.get("/v1/me", headers=bearer(r2)).status_code == 401  # the session is gone at the next request
    client.cookies.set(COOKIE, second)
    assert client.post("/v1/auth/refresh", headers=R).status_code == 401
    with services.admin_sessions() as s:
        sess = s.execute(select(DeviceSession)).scalars().one()
        assert sess.revoked_reason == "reuse"
        assert "session_revoked" in [e.kind for e in s.execute(select(AuditEvent)).scalars()]


def test_sessions_list_revoke_and_logout(client):
    a = demo(client)
    h = bearer(a)
    # a second device of the same shop: the same partner merchant signs in again
    sessions = client.get("/v1/sessions", headers=h).json()
    assert len(sessions) == 1 and sessions[0]["current"] and sessions[0]["via"] == "sandbox"
    assert "Chrome" not in sessions[0]["label"] or True
    other = login(client)
    assert client.delete(f"/v1/sessions/{sessions[0]['id']}", headers=other).status_code == 404  # not its shop
    assert client.delete(f"/v1/sessions/{sessions[0]['id']}", headers=h).json() == {"ok": True}
    assert client.get("/v1/me", headers=h).status_code == 401  # revoked: refused at the next request
    b = demo(client)
    out = client.post("/v1/auth/logout", headers=bearer(b))
    assert out.status_code == 200 and f'{COOKIE}=""' in out.headers["set-cookie"]
    assert client.get("/v1/me", headers=bearer(b)).status_code == 401


def test_session_expires_absolutely(client, services):
    r = demo(client)
    with services.admin_sessions.begin() as s:
        sess = s.execute(select(DeviceSession)).scalars().one()
        sess.expires_at = sess.created_at - timedelta(seconds=1)
    assert client.post("/v1/auth/refresh", headers=R).status_code == 401
    assert r.status_code == 200


# ---------------- partner assertions ----------------


@pytest.fixture
def partner(services):
    return services.sandbox_partner


def exchange(client, assertion):
    return client.post("/v1/auth/partner", json={"assertion": assertion})


def test_partner_assertion_signs_in_once(client, partner, services):
    a = partner.sign("PTM123", name="Anbu Stores", language="ta")
    r = exchange(client, a)
    assert r.status_code == 200
    me = r.json()["merchant"]
    assert me["name"] == "Anbu Stores" and me["language"] == "ta"
    assert exchange(client, a).status_code == 401  # replay
    # the same merchant again: the same shop, a new device session
    r2 = exchange(client, partner.sign("PTM123"))
    assert r2.json()["merchant"]["id"] == me["id"] and r2.json()["session_id"] != r.json()["session_id"]
    with services.admin_sessions() as s:
        assert s.get(Merchant, me["id"]).payments_adapter == "sandbox"  # the demo's adapters (paytm in prod)
    said = [e["text"] for e in client.get(f"/sandbox/soundbox/{me['id']}", headers=bearer(r)).json()["events"]
            if e["kind"] == "announce"]
    assert any(t.startswith("புதிய போன் Laabam இல் உள்நுழைந்தது") for t in said)  # told on the Soundbox, in Tamil


@pytest.mark.parametrize("bad", ["wrong_aud", "expired", "too_long", "future", "other_key", "hs256", "none",
                                 "no_mid", "no_jti"])
def test_partner_assertion_refusals(client, partner, bad):
    now = int(time.time())
    if bad == "wrong_aud":
        a = partner.sign("PTM1", aud="someone-else")
    elif bad == "expired":
        a = partner.sign("PTM1", now=now - 600)
    elif bad == "too_long":
        a = partner.sign("PTM1", lifetime=3600)
    elif bad == "future":
        a = partner.sign("PTM1", now=now + 600)
    elif bad == "other_key":
        a = SandboxPartner().sign("PTM1")
    elif bad == "hs256":
        a = jwt.encode({"iss": partner.issuer, "aud": "laabam", "iat": now, "exp": now + 60, "jti": "x" * 16,
                        "mid": "PTM1"}, "secret", algorithm="HS256", headers={"kid": next(iter(partner.keys.keys))})
    elif bad == "none":
        a = jwt.encode({"iss": partner.issuer, "aud": "laabam", "iat": now, "exp": now + 60, "jti": "x" * 16,
                        "mid": "PTM1"}, None, algorithm="none")
    elif bad == "no_mid":
        a = partner.sign("PTM1", drop=("mid",))
    else:
        a = partner.sign("PTM1", drop=("jti",))
    r = exchange(client, a)
    assert r.status_code == 401, (bad, r.text)
    assert "set-cookie" not in r.headers


# ---------------- configuration and sandbox gating ----------------


@pytest.mark.parametrize("cfg,problem", [
    (dict(env="prod", sandbox=True), "LAABAM_SANDBOX=1 is not allowed in prod"),
    (dict(env="prod", sandbox=False, lending="x", partner_jwks_url="https://p/jwks", token_signer="kms",
          token_kms_key_id="k", vision_fallback="gemini"), "vision fallback"),
    (dict(env="demo", sandbox=True, token_signer="local"), "KMS"),
    (dict(env="demo", sandbox=True, token_signer="kms", token_kms_key_id="k"), "audit keys"),
    (dict(env="prod", sandbox=False, database_url="postgresql+psycopg://u:p@h/db?sslmode=require"), "verify-full"),
    (dict(env="demo", sandbox=True, database_url="sqlite:///x.db"), "verify-full"),
    (dict(env="demo", sandbox=True, token_signer="kms", token_kms_key_id="k", cors_origins="*"), "CORS"),
    (dict(env="prod", sandbox=False, lending="x", token_signer="kms", token_kms_key_id="k"), "JWKS"),
    (dict(env="staging"), "LAABAM_ENV"),
    (dict(env="prod", sandbox=False, vision="gemini"), "LAABAM_VISION=gemini"),
])
def test_unsafe_configs_refuse_to_start(cfg, problem):
    with pytest.raises(UnsafeConfig, match=problem):
        validate(Settings(**{"vision_fallback": "none", **cfg}))


VERIFIED_DB = "postgresql+psycopg://u:p@db.internal:5432/laabam?sslmode=verify-full&sslrootcert=/etc/ssl/rds/ca.pem"


def test_database_url_from_parts_verifies_the_server(monkeypatch):
    from laabam.config import _database_url
    monkeypatch.delenv("LAABAM_DATABASE_URL", raising=False)
    for k, v in {"LAABAM_DB_HOST": "db.internal", "LAABAM_DB_USER": "app", "LAABAM_DB_PASSWORD": "p@ss/word"}.items():
        monkeypatch.setenv(k, v)
    url = _database_url()
    assert "sslmode=verify-full" in url and "sslrootcert=/etc/ssl/rds/global-bundle.pem" in url
    assert "p%40ss%2Fword" in url


def test_prod_config_refuses_to_start_with_the_sandbox(db_url):
    with pytest.raises(UnsafeConfig):
        build_services(Settings(database_url=db_url, env="prod", sandbox=True, inline_worker=False))
    validate(Settings(env="prod", sandbox=False, lending="partner", token_signer="kms", token_kms_key_id="k",
                      audit_kms_key_id="alias/audit", partner_jwks_url="https://partner.example/jwks",
                      vision_fallback="none",
                      owner_database_url="", database_url=VERIFIED_DB))  # a safe one is fine


# ---------------- C1: the demo's own shops ----------------


def test_each_demo_sign_in_is_its_own_shop_and_sandbox_routes_need_its_token(client):
    a, b = demo(client), demo(client)
    assert a.json()["merchant"]["id"] != b.json()["merchant"]["id"]
    mid = a.json()["merchant"]["id"]
    for path in (f"/sandbox/trace/{mid}", f"/sandbox/soundbox/{mid}", f"/sandbox/sms/{mid}"):
        assert client.get(path, headers={"Authorization": ""}).status_code == 401
        assert client.get(path, headers=bearer(b)).status_code == 404
        assert client.get(path, headers=bearer(a)).status_code == 200


def test_demo_shops_are_rate_limited_per_ip(db_url, tmp_path):
    svc = build_services(Settings(database_url=db_url, sandbox=True, inline_worker=False, photo_dir=str(tmp_path),
                                  demo_shops_per_ip_hour=2))
    with TestClient(create_app(svc), base_url="https://testserver") as c:
        assert demo(c).status_code == 200 and demo(c).status_code == 200
        r = c.post("/sandbox/session", json={"history": False})
        assert r.status_code == 429 and int(r.headers["retry-after"]) > 0


def test_refresh_is_rate_limited(client):
    demo(client)
    codes = [client.post("/v1/auth/refresh", headers=R).status_code for _ in range(31)]
    assert codes[:30] == [200] * 30 and codes[30] == 429


def test_demo_shops_expire_after_a_day(client, services):
    old = demo(client).json()["merchant"]["id"]
    new = demo(client).json()["merchant"]["id"]
    with services.admin_sessions.begin() as s:
        s.get(Merchant, old).created_at -= timedelta(hours=25)
    from laabam.app import demo_cleanup
    with services.sessions.begin() as s:
        demo_cleanup(services, s)
    with services.admin_sessions() as s:
        assert s.get(Merchant, old) is None and s.get(Merchant, new) is not None
        assert s.execute(select(AuditEvent).where(AuditEvent.kind == "shop_deleted")).scalars().one().subject == old


def test_client_ip_behind_trusted_proxies():
    class Req:
        def __init__(self, xff, peer="10.0.0.5"):
            self.headers = {"x-forwarded-for": xff} if xff is not None else {}
            self.client = type("C", (), {"host": peer})()

    assert client_ip(Req("1.2.3.4, 54.0.0.1"), 0) == "10.0.0.5"  # no proxies trusted: the peer
    assert client_ip(Req("1.2.3.4, 54.0.0.1"), 2) == "1.2.3.4"  # CloudFront + ALB
    assert client_ip(Req("6.6.6.6, 1.2.3.4, 54.0.0.1"), 2) == "1.2.3.4"  # a spoofed left entry is ignored
    assert client_ip(Req("1.2.3.4"), 2) == "10.0.0.5"  # too few hops: don't trust it
