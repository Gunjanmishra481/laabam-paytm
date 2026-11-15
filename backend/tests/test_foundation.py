"""Phase 0: the pieces copied from Undo, adapted for Laabam."""
import time
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime

import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from laabam import audit, llm, security, timers
from laabam.adapters import AdapterRegistry, AdapterUnavailable
from laabam.app import build_services, create_app
from laabam.clock import IST, ist_date, ist_day, ist_day_bounds, ist_time, next_ist_time
from laabam.config import Settings
from laabam.idempotency import IdempotencyConflict, first_time, once, outbound_key
from laabam.ids import new_id, new_readable_id
from laabam.models import AuditEvent, Timer, utcnow
from laabam.money import mul_paise, parse_amount_paise, rupees, to_paise, upi_amount
from tests.conftest import login

# ---------------- money (rule 6: integer paise) ----------------


def test_rupees_formats_indian_grouping():
    assert rupees(500000) == "₹5,000"
    assert rupees(12345678900) == "₹12,34,56,789"
    assert rupees(35050) == "₹350.50"
    assert rupees(-1250) == "-₹12.50"


@pytest.mark.parametrize("raw,paise", [("12.50", 1250), ("1,200", 120000), (200, 20000), ("₹465", 46500),
                                       ("0.005", 1)])
def test_to_paise(raw, paise):
    assert to_paise(raw) == paise


def test_money_is_integer_paise_never_float():
    with pytest.raises(TypeError):
        to_paise(12.5)
    with pytest.raises(TypeError):
        rupees(12.5)
    assert isinstance(mul_paise(6000, 2500), int)
    assert mul_paise(6000, 2500) == 15000  # 2.5 kg at ₹60
    assert mul_paise(3333, 1000) == 3333
    assert upi_amount(46500) == "465.00"
    assert upi_amount(1205) == "12.05"


def test_parse_amount():
    assert parse_amount_paise("paid ₹5,000 today") == 500000
    assert parse_amount_paise("5000 rupees") == 500000
    assert parse_amount_paise("rice 200") is None  # bare numbers are the bill parser's job


# ---------------- IST (rule 6: fixed +05:30) ----------------


def test_ist_fixed_offset():
    assert IST.utcoffset(None) == timedelta(hours=5, minutes=30)
    dt = datetime(2026, 11, 8, 18, 45, tzinfo=UTC)  # 00:15 IST next day
    assert ist_time(dt) == "12:15 AM"
    assert ist_date(dt) == "9 Nov"
    assert ist_day(dt) == date(2026, 11, 9)
    start, end = ist_day_bounds(date(2026, 11, 9))
    assert start.astimezone(UTC) == datetime(2026, 11, 8, 18, 30, tzinfo=UTC)
    assert end - start == timedelta(days=1)


def test_next_closing_time():
    before = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)  # 15:30 IST
    after = datetime(2026, 11, 9, 16, 0, tzinfo=UTC)  # 21:30 IST
    assert next_ist_time(before, dtime(21, 0)) == datetime(2026, 11, 9, 15, 30, tzinfo=UTC)
    assert next_ist_time(after, dtime(21, 0)) == datetime(2026, 11, 10, 15, 30, tzinfo=UTC)


def test_naive_datetimes_are_refused():
    with pytest.raises(ValueError):
        ist_time(datetime(2026, 1, 1))


# ---------------- ids, signatures, tokens ----------------


def test_ids():
    assert new_id("bill").startswith("bill_") and len(new_id("bill")) <= 32
    rid = new_readable_id("LB")
    assert rid.startswith("LB-") and not set(rid[3:]) & set("01OI")


def test_signatures():
    body = b'{"a":1}'
    h = security.signed_headers("s3cret", body)
    ts, sig = h[security.TIMESTAMP_HEADER], h[security.SIGNATURE_HEADER]
    assert security.verify_signature("s3cret", ts, sig, body)
    assert not security.verify_signature("other", ts, sig, body)
    assert not security.verify_signature("s3cret", ts, sig, b'{"a":2}')
    old = security.signed_headers("s3cret", body, now=time.time() - 3600)
    assert not security.verify_signature("s3cret", old[security.TIMESTAMP_HEADER], old[security.SIGNATURE_HEADER], body)
    assert not security.verify_signature("", ts, sig, body)





# ---------------- model guard (rule 2: no figure invented by a model) ----------------

VALUES = {"PROFIT_TODAY": "₹1,240", "TOP_ITEM": "rice"}
FALLBACK = "Profit today: {PROFIT_TODAY}. Best seller: {TOP_ITEM}."


def test_fill():
    assert llm.fill(FALLBACK, VALUES) == "Profit today: ₹1,240. Best seller: rice."
    with pytest.raises(llm.MissingValue):
        llm.fill("{UNKNOWN}", VALUES)


@pytest.mark.parametrize("model_text", [
    "You made 1240 rupees today.",          # ASCII digit
    "आज का मुनाफ़ा ९०० रुपये",                # Devanagari digit
    "இன்று லாபம் ௫௦௦",                       # Tamil digit
    "Profit was {PROFIT_TODAY}, up {GROWTH}.",  # placeholder code did not provide
    "",
    None,
])
def test_digit_in_model_output_falls_back_to_template(model_text):
    out, source = llm.fill_guarded(model_text, FALLBACK, VALUES)
    assert source == "template"
    assert out == "Profit today: ₹1,240. Best seller: rice."


def test_clean_model_text_is_used():
    out, source = llm.fill_guarded("Good day! You earned {PROFIT_TODAY}; {TOP_ITEM} sold best.", FALLBACK, VALUES)
    assert source == "model"
    assert out == "Good day! You earned ₹1,240; rice sold best."


# ---------------- adapter registry ----------------


def test_sandbox_adapters_need_sandbox_flag():
    for sandbox in (True, False):
        reg = AdapterRegistry(sandbox=sandbox)
        reg.register("payments", "sandbox", object, sandbox_only=True)
        reg.register("payments", "paytm", object)
        assert reg.get("payments", "paytm") is reg.get("payments", "paytm")  # cached
        if sandbox:
            reg.get("payments", "sandbox")
        else:
            with pytest.raises(AdapterUnavailable):
                reg.get("payments", "sandbox")
    with pytest.raises(AdapterUnavailable):
        reg.get("payments", "nope")
    with pytest.raises(ValueError):
        reg.register("teleport", "x", object)


# ---------------- database: audit log (rule 8), timers, idempotency ----------------


def test_audit_log_is_append_only(services):
    with services.admin_sessions.begin() as s:
        audit.record(s, "mer_1", "owner", "bill_confirmed", "bill_1", total_paise=46500)
    with services.admin_sessions() as s:
        ev = s.query(AuditEvent).one()
        assert ev.detail == {"total_paise": 46500} and ev.ts.tzinfo is not None
        with pytest.raises((IntegrityError, DBAPIError)):
            s.execute(update(AuditEvent).values(kind="rewritten"))
            s.commit()
        s.rollback()
        with pytest.raises((IntegrityError, DBAPIError)):
            s.execute(text("DELETE FROM audit_events"))
            s.commit()
        s.rollback()
        assert s.query(AuditEvent).count() == 1
    with pytest.raises(ValueError):
        audit.record(None, None, "robot", "x")  # type: ignore[arg-type]


def test_timer_fires_once(services):
    fired = []
    services.timers.register("qr_expiry", lambda s, t: fired.append(t.payload["bill_id"]))
    with services.admin_sessions.begin() as s:
        tid = timers.schedule(s, "qr_expiry", utcnow() - timedelta(seconds=1), "mer_1", "bill_1", bill_id="bill_1").id
        timers.schedule(s, "qr_expiry", utcnow() + timedelta(hours=1), "mer_1", "bill_2", bill_id="bill_2")
    assert services.timers.tick() == 1
    assert services.timers.tick() == 0
    assert not services.timers.run(tid)  # already fired: the claim fails
    assert fired == ["bill_1"]


def test_timer_retries_then_needs_attention(services):
    def boom(s, t):
        raise RuntimeError("soundbox offline")

    services.timers.register("evening_summary", boom)
    with services.admin_sessions.begin() as s:
        tid = timers.schedule(s, "evening_summary", utcnow(), "mer_1").id
    for _ in range(timers.MAX_ATTEMPTS):
        assert not services.timers.run(tid)
        with services.admin_sessions.begin() as s:
            t = s.get(Timer, tid)
            if t.status == "pending":
                assert t.due_at > utcnow()  # backed off
                t.due_at = utcnow()
    with services.admin_sessions() as s:
        t = s.get(Timer, tid)
        assert t.status == "failed" and t.needs_attention and t.attempts == timers.MAX_ATTEMPTS
        assert "soundbox offline" in t.last_error
        assert s.query(AuditEvent).filter_by(kind="timer_failed", subject=tid).count() == 1


def test_stuck_timer_is_reclaimed(services):
    services.timers.register("alert_scan", lambda s, t: None)
    with services.admin_sessions.begin() as s:
        t = timers.schedule(s, "alert_scan", utcnow())
        t.status, t.claimed_at = "running", utcnow() - timedelta(minutes=10)
        tid = t.id
    assert services.timers.tick() == 1
    with services.admin_sessions() as s:
        assert s.get(Timer, tid).status == "fired"


def test_cancelled_timer_does_not_fire(services):
    services.timers.register("qr_expiry", lambda s, t: pytest.fail("should not fire"))
    with services.admin_sessions.begin() as s:
        timers.schedule(s, "qr_expiry", utcnow(), subject="bill_9")
    with services.admin_sessions.begin() as s:
        assert timers.cancel(s, "qr_expiry", "bill_9") == 1
    assert services.timers.tick() == 0


def test_inbound_event_applied_once(services):
    assert first_time(services.admin_sessions, "paytm", "evt_1", "payment.succeeded")
    assert not first_time(services.admin_sessions, "paytm", "evt_1", "payment.succeeded")
    assert first_time(services.admin_sessions, "lending", "evt_1", "loan.decided")


def test_idempotent_requests(services):
    calls = []

    def make():
        calls.append(1)
        return {"bill": "bill_1", "n": len(calls)}

    assert once(services.admin_sessions, "mer_1", "k1", {"a": 1}, make) == {"bill": "bill_1", "n": 1}
    assert once(services.admin_sessions, "mer_1", "k1", {"a": 1}, make) == {"bill": "bill_1", "n": 1}
    assert len(calls) == 1
    with pytest.raises(IdempotencyConflict):
        once(services.admin_sessions, "mer_1", "k1", {"a": 2}, make)
    once(services.admin_sessions, "mer_1", None, {"a": 1}, make)
    assert len(calls) == 2
    assert outbound_key("qr", "bill_1") == outbound_key("qr", "bill_1") != outbound_key("qr", "bill_2")


# ---------------- HTTP ----------------


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] and r.json()["sandbox"] is True


def test_sandbox_fire_timer(client, services):
    services.timers.register("qr_expiry", lambda s, t: None)
    h = login(client)
    mid = client.get("/v1/me", headers=h).json()["id"]
    with services.admin_sessions.begin() as s:
        tid = timers.schedule(s, "qr_expiry", utcnow() + timedelta(minutes=5), mid).id
    # another shop can't fire this shop's timer
    assert client.post(f"/sandbox/timers/{tid}/fire", headers=login(client)).status_code == 404
    r = client.post(f"/sandbox/timers/{tid}/fire", headers=h)
    assert r.json() == {"ok": True, "status": "fired", "last_error": None}
    assert client.post(f"/sandbox/timers/{tid}/fire", headers=h).status_code == 409


def test_sandbox_routes_absent_without_flag(db_url):
    from fastapi.testclient import TestClient

    svc = build_services(Settings(database_url=db_url, sandbox=False, inline_worker=False))
    with TestClient(create_app(svc)) as c:
        assert c.get("/api/health").json()["sandbox"] is False
        assert c.post("/sandbox/timers/tmr_x/fire").status_code == 404
    svc.admin_sessions.kw["bind"].dispose()
