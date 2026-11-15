"""Privacy and data (security plan S3): keyed audit digests with per-shop keys, retention, export, delete."""
import io
import json
import re
import zipfile
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from laabam import audit, privacy, tenancy
from laabam.models import (
    AuditEvent,
    DeviceSession,
    Merchant,
    PurchaseBill,
    SalesBill,
    SalesLine,
    ShopKey,
    Timer,
    utcnow,
)
from laabam.shopkeys import KmsKeyring, LocalKeyring
from tests.conftest import login


def me(client):
    return client.get("/v1/me").json()


def events(services, mid, kind):
    with services.admin_sessions() as s:
        return [e.detail for e in s.execute(select(AuditEvent).where(AuditEvent.merchant_id == mid,
                                                                     AuditEvent.kind == kind)).scalars()]


# ---------------- C3: the audit log holds keyed digests, never the words ----------------


def test_audit_holds_digests_not_the_words(client, services):
    login(client)
    mid = me(client)["id"]
    b = client.post("/v1/bills/text", json={"text": "rice 200 sugar 100"}).json()
    client.post(f"/v1/bills/{b['id']}/say", json={"text": "rice 250"})
    drafted, = events(services, mid, "bill_drafted")
    said, = events(services, mid, "bill_said")
    for d, words in ((drafted, "transcript"), (said, "text")):
        assert d[words].startswith("hmac-sha256:") and len(d[words]) == len("hmac-sha256:") + 64
    blob = json.dumps(drafted) + json.dumps(said)
    assert not re.search(r"(rice|sugar)", blob)
    # the same words give the same digest, so the log can still be checked against the bill
    with services.sessions.begin() as s, tenancy.as_shop(mid):
        tenancy.set_shop(s, mid)
        assert audit.digest(s, mid, "rice 200 sugar 100") == drafted["transcript"]


def test_each_shop_has_its_own_key(client, services):
    login(client)
    a = me(client)["id"]
    login(client)
    b = me(client)["id"]
    with services.sessions.begin() as s:
        tenancy.set_shop(s, a)
        da = audit.digest(s, a, "rice 200")
        tenancy.set_shop(s, b)
        db_ = audit.digest(s, b, "rice 200")
    assert da != db_  # the same words in two shops can't be linked


def test_raw_text_is_refused_by_the_audit_log(client, services):
    login(client)
    mid = me(client)["id"]
    with services.sessions.begin() as s:
        tenancy.set_shop(s, mid)
        for field in ("transcript", "text", "raw_text", "question", "phone"):
            with pytest.raises(audit.RawContentInAudit):
                audit.record(s, mid, "owner", "x", mid, **{field: "rice 200"})
        with pytest.raises(audit.RawContentInAudit):  # nested too
            audit.record(s, mid, "agent", "x", mid, lines=[{"raw_text": "SUNFLOWER OIL"}])
        audit.record(s, mid, "owner", "x", mid, text=audit.digest(s, mid, "fine"))


def test_purchase_lines_in_the_audit_are_digests(client, services):
    login(client)
    mid = me(client)["id"]
    r = client.post("/v1/purchases/photo", content=b"", headers={"Content-Type": "image/jpeg",
                                                                 "X-Laabam-Sample": "printed"})
    assert r.status_code in (200, 202), r.text
    bill = client.get(f"/v1/purchases/{r.json()['id']}").json()
    read, = events(services, mid, "purchase_read")
    assert read["lines"] and all(ln["raw_text"].startswith("hmac-sha256:") for ln in read["lines"])
    line = bill["lines"][0]
    client.delete(f"/v1/purchases/{bill['id']}/lines/{line['id']}")
    deleted, = events(services, mid, "purchase_line_deleted")
    assert deleted["raw_text"].startswith("hmac-sha256:") and line["raw_text"] not in json.dumps(deleted)


def test_keys_are_stored_wrapped_and_bound_to_the_shop(client, services):
    login(client)
    mid = me(client)["id"]
    client.post("/v1/bills/text", json={"text": "rice 200"})
    with services.admin_sessions() as s:
        row = s.get(ShopKey, mid)
    assert row.keyring == "local" and len(row.wrapped) == 12 + 32 + 16  # nonce + key + tag: never the bare key
    ring = LocalKeyring()
    key = ring.unwrap(mid, row.wrapped)
    assert len(key) == 32
    with pytest.raises(Exception):  # noqa: B017 - the shop id is bound in: another shop can't unwrap it
        ring.unwrap("mer_someone_else", row.wrapped)


def test_kms_keyring_uses_the_shop_as_encryption_context():
    calls = []

    class Kms:
        def generate_data_key(self, **kw):
            calls.append(("gen", kw))
            return {"Plaintext": b"k" * 32, "CiphertextBlob": b"wrapped"}

        def decrypt(self, **kw):
            calls.append(("dec", kw))
            return {"Plaintext": b"k" * 32}

    ring = KmsKeyring("alias/laabam-audit", client=Kms())
    assert ring.new_key("mer_1") == (b"k" * 32, b"wrapped")
    assert ring.unwrap("mer_1", b"wrapped") == b"k" * 32
    assert calls[0][1] == {"KeyId": "alias/laabam-audit", "KeySpec": "AES_256", "EncryptionContext": {"shop": "mer_1"}}
    assert calls[1][1]["EncryptionContext"] == {"shop": "mer_1"}


def test_deleting_a_shop_deletes_its_key(client, services):
    from laabam.shopdata import delete_shop
    login(client)
    mid = me(client)["id"]
    client.post("/v1/bills/text", json={"text": "rice 200"})
    before, = events(services, mid, "bill_drafted")
    with services.sessions.begin() as s:
        delete_shop(s, mid, services.purchases.photos, reason="test")
    with services.admin_sessions() as s:
        assert s.get(ShopKey, mid) is None
        assert s.execute(select(AuditEvent).where(AuditEvent.merchant_id == mid)).first()  # the log stays
    assert before["transcript"].startswith("hmac-sha256:")
    assert mid not in audit.shop_keys._cache  # no copy left in memory either: the digests can't be checked again


# ---------------- retention ----------------


def test_retention_clears_old_words_and_photos(client, services):
    login(client)
    mid = me(client)["id"]
    b = client.post("/v1/bills/text", json={"text": "rice 200"}).json()
    r = client.post("/v1/purchases/photo", content=b"", headers={"Content-Type": "image/jpeg",
                                                                 "X-Laabam-Sample": "printed"})
    pid = r.json()["id"]
    with services.admin_sessions.begin() as s:
        old = utcnow() - timedelta(days=400)
        s.execute(update(SalesBill).where(SalesBill.id == b["id"]).values(created_at=old))
        s.execute(update(PurchaseBill).where(PurchaseBill.id == pid).values(created_at=old, photo_key=f"{mid}/x.jpg"))
    services.purchases.photos.put(f"{mid}/x.jpg", b"photo")
    with services.sessions.begin() as s:
        counts = privacy.sweep(s, mid, services.purchases.photos, days=365)
    assert counts == {"bills_cleared": 1, "lines_cleared": 1, "photos": 1}
    with services.admin_sessions() as s:
        assert s.get(SalesBill, b["id"]).transcript == ""
        assert all(ln.heard_text == "" for ln in s.execute(select(SalesLine).where(SalesLine.bill_id == b["id"]))
                   .scalars())
        assert s.get(SalesBill, b["id"]).total_paise == b["total_paise"]  # amounts stay
        assert s.get(PurchaseBill, pid).photo_key is None
    assert services.purchases.photos.get(f"{mid}/x.jpg") is None
    assert events(services, mid, "retention_sweep")[0]["photos"] == 1


def test_recent_data_is_kept(client, services):
    login(client)
    mid = me(client)["id"]
    client.post("/v1/bills/text", json={"text": "rice 200"})
    with services.sessions.begin() as s:
        assert privacy.sweep(s, mid, services.purchases.photos, days=365) == {"bills_cleared": 0, "lines_cleared": 0,
                                                                             "photos": 0}


def test_retention_runs_daily(client, services):
    with services.admin_sessions() as s:
        t = s.execute(select(Timer).where(Timer.kind == "retention", Timer.status == "pending")).scalar_one()
    assert timedelta(hours=23) < t.due_at - utcnow() <= timedelta(days=1)


# ---------------- export ----------------


def test_export_has_the_shops_data_and_is_audited(client, services):
    login(client)
    other = me(client)["id"]
    client.post("/v1/bills/text", json={"text": "other shop sugar 100"})
    login(client)
    mid = me(client)["id"]
    client.post("/v1/bills/text", json={"text": "rice 200"})
    r = client.get("/v1/me/export")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert r.headers["cache-control"] == "no-store"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    assert {"shop.json", "sales_bills.json", "items.json", "audit_log.json", "README.txt"} <= names
    assert json.loads(z.read("shop.json"))[0]["id"] == mid
    assert json.loads(z.read("sales_bills.json"))[0]["transcript"] == "rice 200"  # the owner's own words
    blob = b"".join(z.read(n) for n in names)
    assert other.encode() not in blob and b"sugar" not in blob  # nothing of another shop
    assert b"token_hash" not in blob and b"wrapped" not in blob  # no secrets
    assert events(services, mid, "data_exported")


def test_export_is_rate_limited(client):
    login(client)
    codes = [client.get("/v1/me/export").status_code for _ in range(6)]
    assert codes[:5] == [200] * 5 and codes[5] == 429


# ---------------- delete ----------------


def test_delete_needs_the_name_and_a_fresh_sign_in(client, services):
    login(client)
    m = me(client)
    assert client.request("DELETE", "/v1/me", json={"shop_name": "not my shop"}).status_code == 422
    with services.admin_sessions.begin() as s:  # signed in 11 minutes ago
        s.execute(update(DeviceSession).where(DeviceSession.merchant_id == m["id"])
                  .values(created_at=utcnow() - timedelta(minutes=11)))
    r = client.request("DELETE", "/v1/me", json={"shop_name": m["name"]})
    assert r.status_code == 403 and "sign in again" in r.json()["detail"]
    assert me(client)["deletion_due_at"] is None


def test_delete_after_grace_and_cancel(client, services):
    login(client)
    m = me(client)
    r = client.request("DELETE", "/v1/me", json={"shop_name": m["name"].upper()})
    assert r.status_code == 200
    assert me(client)["deletion_due_at"] == r.json()["deletion_due_at"]
    assert client.delete("/v1/me/deletion").json()["deletion_due_at"] is None
    kinds = [e for e in ("deletion_requested", "deletion_cancelled") if events(services, m["id"], e)]
    assert kinds == ["deletion_requested", "deletion_cancelled"]
    with services.admin_sessions() as s:
        assert s.execute(select(Timer.status).where(Timer.kind == "shop_delete")).scalar_one() == "cancelled"


def test_the_shop_is_deleted_when_the_grace_ends(client, services):
    login(client)
    m = me(client)
    client.post("/v1/bills/text", json={"text": "rice 200"})
    client.request("DELETE", "/v1/me", json={"shop_name": m["name"]})
    with services.admin_sessions.begin() as s:
        past = utcnow() - timedelta(seconds=1)
        s.execute(update(Merchant).where(Merchant.id == m["id"]).values(deletion_due_at=past))
        s.execute(update(Timer).where(Timer.kind == "shop_delete").values(due_at=past))
    services.timers.tick()
    with services.admin_sessions() as s:
        assert s.get(Merchant, m["id"]) is None and s.get(ShopKey, m["id"]) is None
    assert events(services, m["id"], "shop_deleted")[0]["reason"] == "owner_request"
    assert client.get("/v1/me").status_code == 401  # its sessions went with it


def test_a_cancelled_deletion_does_not_run(client, services):
    login(client)
    m = me(client)
    client.request("DELETE", "/v1/me", json={"shop_name": m["name"]})
    with services.sessions.begin() as s:
        t = s.execute(select(Timer).where(Timer.kind == "shop_delete")).scalar_one()
        tenancy.set_shop(s, m["id"])
        s.get(Merchant, m["id"]).deletion_due_at = None
        privacy.on_shop_delete(services, s, t)  # the timer fires late, after a cancel
    with services.admin_sessions() as s:
        assert s.get(Merchant, m["id"]) is not None
