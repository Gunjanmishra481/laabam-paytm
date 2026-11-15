"""Phase 2: QR, payments, expiry, Soundbox, SMS, partner events, and the Paytm adapter."""
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from laabam import security
from laabam.adapters.base import AdapterError, Shop
from laabam.adapters.paytm import PaytmPayments, generate_signature, verify_signature
from laabam.app import build_services, create_app
from laabam.config import Settings
from laabam.merchants import create_merchant
from laabam.models import AuditEvent, Payment
from tests.conftest import login, owner_headers

PAYTM = Path(__file__).parent / "data" / "paytm"
KEY = "kbzk1DSbJiV_O3p5"  # 16 characters, like a Paytm merchant key
PARTNER_SECRET = "partner-secret-" + "y" * 24


def recorded(name: str, **subs) -> dict:
    text = (PAYTM / name).read_text()
    for k, v in subs.items():
        text = text.replace(k, v)
    return json.loads(text)


def confirmed_bill(client, h, text="rice 200, sugar 100, oil 165"):
    b = client.post("/v1/bills/text", json={"text": text}, headers=h).json()
    r = client.post(f"/v1/bills/{b['id']}/confirm", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def merchant_id(client, h):
    return client.get("/v1/me", headers=h).json()["id"]


# ---------------- QR on confirm ----------------


def test_confirm_shows_upi_qr_for_exact_amount(client, owner):
    b = confirmed_bill(client, owner)
    assert b["status"] == "confirmed" and b["total_paise"] == 46500
    qr = b["qr"]
    assert qr["order_id"] == f"{b['id']}-1" and qr["expires_at"]
    url = urlparse(qr["payload"])
    q = parse_qs(url.query)
    assert url.scheme == "upi" and url.netloc == "pay"
    assert q["pa"] == ["demo.kirana@sandbox"] and q["am"] == ["465.00"] and q["cu"] == ["INR"]
    assert q["tr"] == [qr["order_id"]] and q["pn"] == ["Demo Kirana"]
    box = client.get(f"/sandbox/soundbox/{merchant_id(client, owner)}").json()
    assert box["screen"]["kind"] == "qr" and box["screen"]["amount_paise"] == 46500
    assert box["screen"]["text"] == "Scan to pay ₹465"


def test_no_qr_until_owner_confirms(client, owner):
    b = client.post("/v1/bills/text", json={"text": "rice 200"}, headers=owner).json()
    assert b["qr"] is None
    assert client.get(f"/sandbox/soundbox/{merchant_id(client, owner)}").json()["screen"]["kind"] == "idle"


def test_qr_failure_keeps_the_draft_and_the_soundbox_quiet(client, owner, services):
    mid = merchant_id(client, owner)
    from laabam.models import Merchant

    with services.admin_sessions.begin() as s:
        s.get(Merchant, mid).upi_vpa = ""
    b = client.post("/v1/bills/text", json={"text": "rice 200"}, headers=owner).json()
    r = client.post(f"/v1/bills/{b['id']}/confirm", headers=owner)
    assert r.status_code == 502
    assert client.get(f"/v1/bills/{b['id']}", headers=owner).json()["status"] == "draft"
    assert client.get(f"/sandbox/soundbox/{mid}").json()["events"] == []


# ---------------- paying ----------------


def test_customer_pays_and_soundbox_announces(client, owner, services):
    b = confirmed_bill(client, owner)
    r = client.post(f"/sandbox/qr/{b['qr']['order_id']}/pay").json()
    assert r["result"] == "succeeded"
    paid = client.get(f"/v1/bills/{b['id']}", headers=owner).json()
    assert paid["status"] == "paid" and paid["payment_ref"] == r["txn_id"] and paid["paid_at"]
    mid = merchant_id(client, owner)
    box = client.get(f"/sandbox/soundbox/{mid}").json()
    assert box["screen"] == {"kind": "message", "text": "₹465 received."}
    assert [e["kind"] for e in box["events"]] == ["show_qr", "announce"]
    with services.admin_sessions() as s:
        p = s.execute(select(Payment)).scalar_one()
        assert (p.amount_paise, p.status, p.bill_id) == (46500, "succeeded", b["id"])
    assert client.get("/v1/today", headers=owner).json() | {"date": None} == {
        "date": None, "paid_bills": 1, "sales_paise": 46500, "payment_issues": 0}
    kinds = [e["kind"] for e in client.get(f"/sandbox/trace/{mid}").json()]
    assert kinds == ["bill_drafted", "payments.create_qr", "soundbox.show_qr", "payment.received",
                     "soundbox.announce"]


def test_short_payment_is_flagged_not_paid(client, owner):
    b = confirmed_bill(client, owner)
    assert client.post(f"/sandbox/qr/{b['qr']['order_id']}/pay", json={"amount_paise": 40000}).json()["result"] \
        == "mismatch"
    got = client.get(f"/v1/bills/{b['id']}", headers=owner).json()
    assert got["status"] == "confirmed" and got["payment_issue"] == "short_payment"
    assert client.get("/v1/today", headers=owner).json()["payment_issues"] == 1
    box = client.get(f"/sandbox/soundbox/{merchant_id(client, owner)}").json()
    assert box["screen"]["text"] == "₹400 received."  # the Soundbox says what actually arrived


def test_paid_twice_and_paid_after_cancel(client, owner):
    b = confirmed_bill(client, owner)
    order = b["qr"]["order_id"]
    client.post(f"/sandbox/qr/{order}/pay")
    assert client.post(f"/sandbox/qr/{order}/pay").json()["result"] == "duplicate"
    assert client.get(f"/v1/bills/{b['id']}", headers=owner).json()["payment_issue"] == "paid_twice"
    assert client.post(f"/v1/bills/{b['id']}/cancel", headers=owner).status_code == 409  # paid: can't cancel

    c = confirmed_bill(client, owner, "rice 200")
    client.post(f"/v1/bills/{c['id']}/cancel", headers=owner)
    assert client.post(f"/sandbox/qr/{c['qr']['order_id']}/pay").json()["result"] == "unmatched"
    got = client.get(f"/v1/bills/{c['id']}", headers=owner).json()
    assert got["status"] == "cancelled" and got["payment_issue"] == "paid_after_cancel"


def test_two_bills_open_at_once(client, owner):
    a = confirmed_bill(client, owner, "rice 200")
    b = confirmed_bill(client, owner, "sugar 100")
    assert a["qr"]["payload"] != b["qr"]["payload"]
    client.post(f"/sandbox/qr/{b['qr']['order_id']}/pay")
    assert client.get(f"/v1/bills/{a['id']}", headers=owner).json()["status"] == "confirmed"
    assert client.get(f"/v1/bills/{b['id']}", headers=owner).json()["status"] == "paid"


def test_edit_bill_withdraws_the_qr_and_reopens_the_draft(client, owner, services):
    from laabam.models import PricePoint, Timer

    b = confirmed_bill(client, owner)
    mid = merchant_id(client, owner)
    r = client.post(f"/v1/bills/{b['id']}/edit", headers=owner)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["status"] == "draft" and got["qr"] is None and got["can_confirm"]
    assert client.get(f"/sandbox/soundbox/{mid}").json()["screen"]["kind"] == "idle"
    with services.admin_sessions() as s:
        assert s.execute(select(Timer).where(Timer.subject == b["id"], Timer.status == "pending")).all() == []
        assert s.execute(select(PricePoint)).all() == []  # confirm's prices taken back
        assert s.execute(select(AuditEvent).where(AuditEvent.kind == "bill_reopened")).scalar_one()

    # the old QR is paid anyway: flagged, the draft isn't marked paid
    assert client.post(f"/sandbox/qr/{b['qr']['order_id']}/pay").json()["result"] == "unmatched"
    assert client.get(f"/v1/bills/{b['id']}", headers=owner).json()["payment_issue"] == "paid_after_cancel"

    # fix a line and confirm again: a new QR for the new amount, prices written once
    line = got["lines"][1]
    client.patch(f"/v1/bills/{b['id']}/lines/{line['id']}", json={"line_total_paise": 12000}, headers=owner)
    again = client.post(f"/v1/bills/{b['id']}/confirm", headers=owner).json()
    assert again["status"] == "confirmed" and again["total_paise"] == 48500
    assert again["qr"]["order_id"] == f"{b['id']}-2"
    with services.admin_sessions() as s:
        assert len(s.execute(select(PricePoint)).all()) == 3


def test_only_an_unpaid_confirmed_bill_can_be_edited(client, owner):
    draft = client.post("/v1/bills/text", json={"text": "rice 200"}, headers=owner).json()
    assert client.post(f"/v1/bills/{draft['id']}/edit", headers=owner).status_code == 409
    b = confirmed_bill(client, owner)
    client.post(f"/sandbox/qr/{b['qr']['order_id']}/pay")
    assert client.post(f"/v1/bills/{b['id']}/edit", headers=owner).status_code == 409


# ---------------- expiry ----------------


def test_qr_expires_and_can_be_shown_again(client, owner):
    b = confirmed_bill(client, owner)
    assert client.post(f"/sandbox/bills/{b['id']}/expire").json()["status"] == "fired"
    got = client.get(f"/v1/bills/{b['id']}", headers=owner).json()
    assert got["status"] == "expired"
    mid = merchant_id(client, owner)
    assert client.get(f"/sandbox/soundbox/{mid}").json()["screen"]["kind"] == "idle"
    again = client.post(f"/v1/bills/{b['id']}/show-qr", headers=owner).json()
    assert again["status"] == "confirmed" and again["qr"]["order_id"] == f"{b['id']}-2"
    # the customer pays the old QR anyway: the money arrived, so the bill is paid
    assert client.post(f"/sandbox/qr/{b['qr']['order_id']}/pay").json()["result"] == "succeeded"
    assert client.get(f"/v1/bills/{b['id']}", headers=owner).json()["status"] == "paid"
    assert client.post(f"/v1/bills/{b['id']}/show-qr", headers=owner).status_code == 409


def test_expiry_of_an_old_qr_does_not_expire_the_new_one(client, owner, services):
    b = confirmed_bill(client, owner)
    client.post(f"/v1/bills/{b['id']}/show-qr", headers=owner)
    assert client.post(f"/sandbox/bills/{b['id']}/expire").json()["status"] == "fired"
    # only the newest QR's timer was pending; the first was cancelled when the QR was shown again
    assert client.get(f"/v1/bills/{b['id']}", headers=owner).json()["status"] == "expired"
    assert client.post(f"/sandbox/bills/{b['id']}/expire").status_code == 409


# ---------------- SMS bill ----------------


def test_no_customer_phone_numbers_are_taken_or_kept(client, owner):
    """C2: the SMS bill (which needed the customer's number) is gone, and so is the column."""
    from laabam.models import SalesBill
    b = client.post("/v1/bills/text", json={"text": "rice 200"}, headers=owner).json()
    assert client.patch(f"/v1/bills/{b['id']}", json={"customer_phone": "9876543210"}, headers=owner).status_code == 405
    assert "sms_phone" not in b and "sms_phone" not in SalesBill.__table__.c


# ---------------- partner events ----------------


@pytest.fixture
def partner_client(db_url):
    svc = build_services(Settings(database_url=db_url, sandbox=True, inline_worker=False,
                                  partner_secret=PARTNER_SECRET))
    with TestClient(create_app(svc)) as c:
        yield c
    svc.admin_sessions.kw["bind"].dispose()


def signed_event(client, evt: dict, secret=PARTNER_SECRET):
    body = json.dumps(evt).encode()
    return client.post("/v1/events", content=body,
                       headers={**security.signed_headers(secret, body), "Content-Type": "application/json"})


def test_partner_payment_event_signed_and_applied_once(partner_client):
    h = login(partner_client)
    b = confirmed_bill(partner_client, h)
    evt = {"id": "paytm_evt_1", "type": "payment.succeeded",
           "data": {"order_id": b["qr"]["order_id"], "amount_paise": 46500, "txn_id": "T1"}}
    assert signed_event(partner_client, evt, secret="wrong-" + "z" * 30).status_code == 401
    assert signed_event(partner_client, evt).json() == {"result": "succeeded"}
    assert signed_event(partner_client, evt).json() == {"result": "already_applied"}
    assert partner_client.get(f"/v1/bills/{b['id']}", headers=h).json()["status"] == "paid"
    assert signed_event(partner_client, {"id": "e2", "type": "soundbox.ack", "data": {}}).json() == \
        {"result": "ignored"}
    assert signed_event(partner_client, {"id": "e3", "type": "payment.succeeded", "data": {}}).status_code == 422
    assert signed_event(partner_client, {"id": "e4", "type": "payment.succeeded",
                                         "data": {"order_id": "bill_nope-1", "amount_paise": 1}}).json() == \
        {"result": "unknown_order"}


def test_partner_events_refused_without_a_secret(client):
    assert signed_event(client, {"id": "x", "type": "payment.succeeded", "data": {}}, secret="").status_code == 401


# ---------------- Paytm adapter (recorded shapes, never live) ----------------


def test_paytm_checksum_round_trip():
    body = '{"mid":"M1","orderId":"bill_1-1"}'
    sig = generate_signature(body, KEY)
    assert verify_signature(body, KEY, sig)
    assert not verify_signature(body + " ", KEY, sig)
    assert not verify_signature(body, "x" * 16, sig)
    assert not verify_signature(body, KEY, "not-base64!")
    assert generate_signature(body, KEY, salt="abcd") == generate_signature(body, KEY, salt="abcd")


SHOP = Shop("mer_1", "Demo Kirana", "en", "demo.kirana@sandbox", "LAABAM12345678901234", "SB-1")


def mock_paytm(routes: dict[str, dict], seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=routes[request.url.path])
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_paytm_create_qr_request_shape():
    seen = []
    p = PaytmPayments(KEY, http=mock_paytm({"/paymentservices/qr/create": recorded("qr_create_success.json")}, seen))
    qr = p.create_qr(SHOP, "bill_0001-1", 46500, "Demo Kirana bill", "k1")
    assert qr.payload.startswith("upi://pay?") and qr.provider_ref == "281005040101ZXQ7NBYKQ6F0"
    req = seen[0]
    assert str(req.url) == "https://securegw-stage.paytm.in/paymentservices/qr/create"
    raw = req.content.decode()
    sent = json.loads(raw)
    assert sent["body"] == {"mid": "LAABAM12345678901234", "orderId": "bill_0001-1", "amount": "465.00",
                            "businessType": "UPI_QR_CODE", "posId": "SB-1"}
    assert sent["head"]["clientId"] == "C11" and sent["head"]["version"] == "v1"
    # the signature covers the exact body bytes sent
    body_bytes = raw[len('{"body":'):raw.index(',"head":')]
    assert verify_signature(body_bytes, KEY, sent["head"]["signature"])


def test_paytm_create_qr_failure():
    p = PaytmPayments(KEY, http=mock_paytm({"/paymentservices/qr/create": recorded("qr_create_failure.json")}, []))
    with pytest.raises(AdapterError, match="QR_1020"):
        p.create_qr(SHOP, "bill_0001-1", 46500, "n", "k")
    with pytest.raises(AdapterError):
        PaytmPayments("")


def test_paytm_status():
    ok = PaytmPayments(KEY, http=mock_paytm({"/v3/order/status": recorded("order_status_success.json")}, []))
    st = ok.payment_status(SHOP, "bill_0001-1")
    assert (st.status, st.amount_paise, st.txn_id) == ("succeeded", 46500, "20261109011650000123456789")
    assert st.paid_at.isoformat() == "2026-11-09T14:03:22+05:30"
    pend = PaytmPayments(KEY, http=mock_paytm({"/v3/order/status": recorded("order_status_pending.json")}, []))
    assert pend.payment_status(SHOP, "x").status == "pending"


def test_paytm_merchant_end_to_end_with_status_poll(db_url):
    """A shop on real Paytm: QR from the Create QR API, payment found by the status poll."""
    state = {"status": "order_status_pending.json"}
    order = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/paymentservices/qr/create":
            order["id"] = json.loads(request.content)["body"]["orderId"]
            return httpx.Response(200, json=recorded("qr_create_success.json"))
        return httpx.Response(200, json=recorded(state["status"], ORDER_ID=order["id"]))

    cfg = Settings(database_url=db_url, sandbox=True, inline_worker=False,
                   paytm_merchant_key=KEY)
    svc = build_services(cfg, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with svc.admin_sessions.begin() as s:
        m = create_merchant(s, "Kumar Stores", "en", "kumar@paytm", paytm_mid="LAABAM12345678901234",
                            adapters="paytm")
        mid = m.id
    h = owner_headers(svc, mid)
    with TestClient(create_app(svc)) as c:
        b = confirmed_bill(c, h, "rice 200, sugar 100, oil 165")
        assert b["qr"]["payload"].startswith("upi://pay?pa=paytmqr")
        assert svc.timers.tick() == 0  # the first check is a few seconds out
        from laabam.models import Timer

        with svc.admin_sessions.begin() as s:
            t = s.execute(select(Timer).where(Timer.kind == "payment_check", Timer.status == "pending")).scalar_one()
            t.due_at = t.created_at
        assert svc.timers.tick() == 1  # still pending at Paytm: schedules the next check
        state["status"] = "order_status_success.json"
        with svc.admin_sessions.begin() as s:
            t = s.execute(select(Timer).where(Timer.kind == "payment_check", Timer.status == "pending")).scalar_one()
            t.due_at = t.created_at
        assert svc.timers.tick() == 1
        got = c.get(f"/v1/bills/{b['id']}", headers=h).json()
        assert got["status"] == "paid" and got["payment_ref"] == "20261109011650000123456789"
    svc.admin_sessions.kw["bind"].dispose()


def test_cancel_takes_the_qr_off_the_soundbox(client, owner, services):
    b = confirmed_bill(client, owner)
    mid = merchant_id(client, owner)
    assert client.get(f"/sandbox/soundbox/{mid}").json()["screen"]["kind"] == "qr"
    client.post(f"/v1/bills/{b['id']}/cancel", headers=owner)
    assert client.get(f"/sandbox/soundbox/{mid}").json()["screen"]["kind"] == "idle"
    from laabam.models import Timer

    with services.admin_sessions() as s:
        pending = s.execute(select(Timer).where(Timer.subject == b["id"], Timer.status == "pending")).all()
        assert pending == []
