"""Beta sign-up by phone (Firebase Phone Auth proves the number; we issue our own sessions). Runs on SQLite and on
Postgres as the app role, so row-level security applies to everything here."""
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from laabam.app import build_services, create_app
from laabam.config import Settings, UnsafeConfig, validate
from laabam.firebase_identity import FirebaseTokenError, FirebaseVerifier
from laabam.models import AuditEvent, Merchant
from laabam.partner_identity import StaticKeys
from laabam.phonehash import KmsPhoneHasher, LocalPhoneHasher
from laabam.routes_auth import PRIVACY_VERSION

PROJECT = "laabam-beta-test"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KID = "test-kid-1"


def id_token(phone="+919876543210", *, key=KEY, kid=KID, alg="RS256", aud=PROJECT, iss=None, age=30, exp_in=3600,
             provider="phone", drop=(), **extra):
    now = int(time.time())
    claims = {"iss": iss or f"https://securetoken.google.com/{PROJECT}", "aud": aud, "sub": "uid-" + phone[-4:],
              "iat": now - age, "auth_time": now - age, "exp": now + exp_in, "phone_number": phone,
              "firebase": {"sign_in_provider": provider, "identities": {"phone": [phone]}}, **extra}
    for k in drop:
        claims.pop(k, None)
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": kid})


def verifier():
    return FirebaseVerifier(PROJECT, StaticKeys({KID: KEY.public_key()}))


@pytest.fixture
def beta(db_url, tmp_path):
    svc = build_services(Settings(database_url=db_url, beta=True, firebase_project_id=PROJECT, inline_worker=False,
                                  photo_dir=str(tmp_path / "photos"), lending="sandbox"))
    svc.firebase_verifier.keys = StaticKeys({KID: KEY.public_key()})  # Google's key server, played by a test key
    with TestClient(create_app(svc), base_url="https://testserver") as c:
        yield c, svc
    svc.sessions.kw["bind"].dispose()


def sign_in(c, phone="+919876543210", consent=True, **kw):
    body = {"id_token": id_token(phone, **kw), "consent": consent,
            "consent_version": PRIVACY_VERSION if consent else None}
    return c.post("/v1/auth/firebase", json=body)


def auth(r):
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ---------------- the token checks ----------------


def test_a_good_phone_token_passes(services):
    with services.sessions.begin() as s:
        claims = verifier().verify(s, id_token())
    assert claims.phone == "+919876543210" and claims.uid == "uid-3210"


@pytest.mark.parametrize("bad,why", [
    (dict(key=OTHER_KEY), "invalid"),  # signed by someone else
    (dict(exp_in=-120), "expired"),
    (dict(aud="someone-elses-project"), "wrong audience"),
    (dict(iss="https://securetoken.google.com/someone-elses-project"), "wrong issuer"),
    (dict(iss="https://accounts.google.com"), "wrong issuer"),
    (dict(kid="unknown"), "unknown signing key"),
    (dict(provider="password"), "not a phone sign-in"),
    (dict(phone="+14155550100"), "country not allowed"),  # India only
    (dict(age=3600), "too old"),  # the OTP wasn't just entered
    (dict(age=-600), "future"),
    (dict(drop=("phone_number",)), "phone number refused"),
    (dict(drop=("auth_time",)), "invalid"),
    (dict(drop=("sub",)), "invalid"),
])
def test_bad_tokens_are_refused(services, bad, why):
    with services.sessions.begin() as s, pytest.raises(FirebaseTokenError, match=why):
        verifier().verify(s, id_token(**bad))


def test_only_rs256_is_accepted(services):
    hs = jwt.encode({"aud": PROJECT, "iss": f"https://securetoken.google.com/{PROJECT}", "sub": "x", "iat": 1,
                     "exp": 2**31, "auth_time": int(time.time()), "phone_number": "+919876543210"},
                    "a-guessed-secret-key-of-32-bytes!", algorithm="HS256", headers={"kid": KID})
    with services.sessions.begin() as s, pytest.raises(FirebaseTokenError, match="alg"):
        verifier().verify(s, hs)
    with services.sessions.begin() as s, pytest.raises(FirebaseTokenError):
        verifier().verify(s, "not.a.token")


def test_a_token_is_good_once(services):
    token = id_token()
    with services.sessions.begin() as s:
        verifier().verify(s, token)
    with services.sessions.begin() as s, pytest.raises(FirebaseTokenError, match="already used"):
        verifier().verify(s, token)


# ---------------- sign-up and sign-in ----------------


def test_config_tells_the_app_to_sign_in_by_phone(beta):
    c, _ = beta
    cfg = c.get("/api/config").json()
    assert cfg["signin"] == "phone" and cfg["beta"] is True and cfg["firebase"]["projectId"] == PROJECT
    assert cfg["privacy_version"] == PRIVACY_VERSION


def test_no_demo_sign_in_in_the_beta(beta):
    c, _ = beta
    assert c.post("/sandbox/session", json={"reset": True}).status_code == 404
    assert c.post("/sandbox/reset", json={"reset": True}).status_code == 404


def test_a_new_number_needs_consent_then_gets_a_new_shop(beta):
    c, svc = beta
    assert sign_in(c, consent=False).status_code == 428
    token = id_token()  # the same code can be sent again once the box is ticked: the 428 didn't use it up
    assert c.post("/v1/auth/firebase", json={"id_token": token, "consent": False}).status_code == 428
    r = c.post("/v1/auth/firebase", json={"id_token": token, "consent": True, "consent_version": PRIVACY_VERSION})
    assert r.status_code == 201
    me = r.json()["merchant"]
    assert me["needs"] == ["language", "shop"]  # onboarding next
    assert r.cookies.get("__Host-laabam_refresh")
    with svc.admin_sessions() as s:
        m = s.get(Merchant, me["id"])
        assert m.consent_version == PRIVACY_VERSION and m.consent_at is not None and m.payments_adapter == "sandbox"
        assert m.phone_hash == LocalPhoneHasher().hash("+919876543210")


def test_an_old_consent_version_is_not_enough(beta):
    c, _ = beta
    r = c.post("/v1/auth/firebase", json={"id_token": id_token(), "consent": True, "consent_version": "2020-01-01"})
    assert r.status_code == 428


def test_the_same_number_comes_back_to_the_same_shop(beta):
    c, _ = beta
    first = sign_in(c)
    again = sign_in(c, consent=False, age=45)  # a later sign-in (a new token); an existing shop doesn't ask again
    assert again.status_code == 200 and again.json()["merchant"]["id"] == first.json()["merchant"]["id"]


def test_the_phone_number_is_never_stored(beta, db_url):
    c, svc = beta
    mid = sign_in(c, "+919812345678").json()["merchant"]["id"]
    with svc.admin_sessions() as s:
        row = s.execute(select(Merchant).where(Merchant.id == mid)).scalar_one()
        stored = " ".join(str(getattr(row, col.key)) for col in Merchant.__mapper__.column_attrs)
        events = " ".join(str(e.detail) for e in s.execute(select(AuditEvent)).scalars())
        dump = stored + events
        if db_url.startswith("postgresql"):  # and nowhere else in the database either
            for (table,) in s.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")):
                dump += " ".join(str(r) for r in s.execute(text(f'SELECT * FROM "{table}"')))
    assert "9812345678" not in dump and "12345678" not in dump


def test_two_phones_two_shops_kept_apart(beta):
    c, _ = beta
    a = sign_in(c, "+919800000001")
    b = sign_in(c, "+919800000002")
    assert a.json()["merchant"]["id"] != b.json()["merchant"]["id"]
    ha, hb = auth(a), auth(b)
    c.patch("/v1/me", json={"language": "en"}, headers=ha)
    c.patch("/v1/me", json={"name": "Anbu Stores", "upi_vpa": "anbu@okaxis"}, headers=ha)
    bill = c.post("/v1/bills/text", json={"text": "rice 200"}, headers=ha).json()
    assert c.get(f"/v1/bills/{bill['id']}", headers=hb).status_code == 404
    assert c.get("/v1/bills", headers=hb).json() == []
    assert c.get("/v1/me", headers=hb).json()["name"] == ""
    assert len(c.get("/v1/bills", headers=ha).json()) == 1


def test_simulated_payment_only_on_your_own_qr(beta):
    c, _ = beta
    a, b = auth(sign_in(c, "+919800000011")), auth(sign_in(c, "+919800000012"))
    c.patch("/v1/me", json={"language": "en"}, headers=a)
    c.patch("/v1/me", json={"name": "Anbu Stores", "upi_vpa": "anbu@okaxis"}, headers=a)
    bill = c.post("/v1/bills/text", json={"text": "rice 200"}, headers=a).json()
    qr = c.post(f"/v1/bills/{bill['id']}/confirm", headers=a).json()["qr"]
    assert c.post(f"/v1/beta/qr/{qr['order_id']}/pay", headers=b).status_code == 404  # not B's QR
    assert c.post(f"/v1/beta/qr/{qr['order_id']}/pay", headers=a).json()["result"] == "succeeded"


def test_otp_requests_are_rate_limited_per_number_and_per_ip(beta):
    c, _ = beta
    codes = [c.post("/v1/auth/phone/start", json={"phone": "+91 98765 43210"}).status_code for _ in range(6)]
    assert codes[:5] == [204] * 5 and codes[5] == 429  # 5 an hour for one number
    more = [c.post("/v1/auth/phone/start", json={"phone": f"+9198765000{i:02d}"}).status_code for i in range(10)]
    assert more[:4] == [204] * 4 and 429 in more  # 10 an hour from one IP
    assert c.post("/v1/auth/phone/start", json={"phone": "+14155550100"}).status_code in (422, 429)


def test_export_and_delete_work_for_a_phone_shop(beta):
    c, svc = beta
    r = sign_in(c, "+919800000021")
    h = auth(r)
    c.patch("/v1/me", json={"language": "en"}, headers=h)
    c.patch("/v1/me", json={"name": "Chola Stores", "upi_vpa": "chola@okaxis"}, headers=h)
    assert c.get("/v1/me/export", headers=h).headers["content-type"] == "application/zip"
    d = c.request("DELETE", "/v1/me", json={"shop_name": "Chola Stores"}, headers=h)
    assert d.status_code == 200 and d.json()["deletion_due_at"]


# ---------------- configuration ----------------

PROD_BETA = dict(env="prod", beta=True, sandbox=False, lending="sandbox", firebase_project_id=PROJECT,
                 phone_hash_kms_key_id="alias/p", token_signer="kms", token_kms_key_id="alias/t",
                 audit_kms_key_id="alias/a", vision_fallback="none", owner_database_url="",
                 database_url="postgresql+psycopg://u:p@db.internal/laabam?sslmode=verify-full&sslrootcert=/ca.pem")


def test_prod_allows_the_beta_with_every_other_control():
    validate(Settings(**PROD_BETA))


@pytest.mark.parametrize("change,problem", [
    (dict(firebase_project_id=""), "Firebase"),
    (dict(phone_hash_kms_key_id=""), "hashes phone numbers"),
    (dict(sandbox=True), "LAABAM_SANDBOX=1 is not allowed in prod"),
    (dict(database_url="sqlite:////tmp/beta.db"), "verify-full"),  # the demo's SQLite exception doesn't apply
    (dict(token_signer="local"), "KMS"),
    (dict(vision_fallback="gemini"), "outside India"),
    (dict(beta=False), "lending adapter"),  # without the beta switch, prod still refuses the sandbox lender
])
def test_prod_beta_still_refuses_the_rest(change, problem):
    with pytest.raises(UnsafeConfig, match=problem):
        validate(Settings(**{**PROD_BETA, **change}))


def test_beta_is_not_a_demo_mode():
    with pytest.raises(UnsafeConfig, match="separate modes"):
        validate(Settings(beta=True, sandbox=True, firebase_project_id=PROJECT))


def test_kms_phone_hash_uses_hmac_sha256():
    calls = []

    class Kms:
        def generate_mac(self, **kw):
            calls.append(kw)
            return {"Mac": b"\x01" * 32}

    assert KmsPhoneHasher("alias/p", client=Kms()).hash("+919876543210") == "01" * 32
    assert calls == [{"KeyId": "alias/p", "Message": b"+919876543210", "MacAlgorithm": "HMAC_SHA_256"}]
