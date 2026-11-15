"""Bill photos through Sarvam Document AI (mocked): submit -> poll -> fetch on the worker, retries and refusals,
the Gemini fallback, photo conversion, and the same code checks on whatever the model reads."""
import json
from io import BytesIO

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from laabam.adapters.base import AdapterError
from laabam.adapters.vision import (
    SARVAM_SCHEMA,
    OfflineVision,
    SarvamVision,
    VisionChain,
    VisionNotSetUp,
    VisionRejected,
)
from laabam.app import build_services, choose_vision, create_app
from laabam.config import Settings
from laabam.models import AuditEvent, Timer
from laabam.photos import PhotoNotReadable, for_reader
from tests.conftest import login, upload_photo
from tests.test_purchases import FakeGemini, by_text, read

KEY = "sk-sarvam-test-123"

# What Sarvam reads off the "misread" sample photo: channa dal's amount comes back as 484 (the photo says 384).
SARVAM_BILL = {
    "distributor": "Sri Lakshmi Agencies", "bill_no": "SLA/2702", "bill_date": "22/11/2026", "total": "792.00",
    "lines": [
        {"raw_text": "CHANNA DAL 1KG", "qty": "4", "unit": "PKT", "rate": "96.00", "amount": "484.00"},
        {"raw_text": "HAMAM SOAP 100G", "qty": 12, "unit": "PCS", "rate": "34.00", "amount": "408.00"},
        {"raw_text": "TEA DUST 250G", "qty": "", "unit": "", "rate": "", "amount": "120"},
    ],
}


def image_bytes(fmt="JPEG", size=(1200, 900), exif_orientation=None) -> bytes:
    im = Image.new("RGB", size, "white")
    buf = BytesIO()
    kw = {}
    if exif_orientation:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        kw["exif"] = exif.tobytes()
    im.save(buf, fmt, **kw)
    return buf.getvalue()


class FakeSarvam:
    """Document AI's three calls. `script` lists answers for POST extract / GET status / GET results."""

    def __init__(self, result=SARVAM_BILL, statuses=("running", "completed")):
        self.result, self.statuses = result, list(statuses)
        self.requests: list[httpx.Request] = []
        self.extract_errors: list[httpx.Response] = []  # answered before the extract succeeds
        self.status_errors: list[httpx.Response] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/doc-ai/v1/job/extract":
            if self.extract_errors:
                return self.extract_errors.pop(0)
            return httpx.Response(202, json={"job_id": "job_abc", "status": "pending"})
        if path == "/doc-ai/v1/job/job_abc/status":
            if self.status_errors:
                return self.status_errors.pop(0)
            st = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            return httpx.Response(200, json={"job_id": "job_abc", "status": st,
                                             "usage": {"pages_total": 1, "pages_processed": 1}})
        if path == "/doc-ai/v1/job/job_abc/results":
            return httpx.Response(200, json={"job_id": "job_abc", "result": self.result})
        return httpx.Response(404, json={"error": {"code": "not_found"}})

    def paths(self):
        return [f"{r.method} {r.url.path}" for r in self.requests]


class Clock:
    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s

    def __call__(self):
        return self.now


def sarvam(fake, timeout=90.0, language="", attempts=3):
    clock = Clock()
    v = SarvamVision(KEY, language=language, timeout=timeout, poll_every=2.0, attempts=attempts,
                     client=httpx.Client(transport=httpx.MockTransport(fake)), sleep=clock.sleep, clock=clock)
    return v, clock


# ---------------- the adapter ----------------


def test_sarvam_submit_poll_fetch():
    fake = FakeSarvam()
    v, clock = sarvam(fake)
    r = v.read_bill(b"jpeg-bytes", "image/jpeg")
    assert fake.paths() == ["POST /doc-ai/v1/job/extract", "GET /doc-ai/v1/job/job_abc/status",
                            "GET /doc-ai/v1/job/job_abc/status", "GET /doc-ai/v1/job/job_abc/results"]
    sub = fake.requests[0]
    assert str(sub.url).startswith("https://api.sarvam.ai/")
    assert all(q.headers["api-subscription-key"] == KEY for q in fake.requests)
    body = sub.content.decode("latin-1")
    assert 'filename="bill.jpg"' in body and "Content-Type: image/jpeg" in body and "jpeg-bytes" in body
    assert 'name="output_format"\r\n\r\njson' in body and 'name="language"' not in body  # Sarvam detects
    assert json.dumps(SARVAM_SCHEMA) in body  # schema as a JSON string field
    assert clock.sleeps == [2.0, 2.0]
    assert r.provider == "sarvam" and r.request_id == "job_abc" and r.bill_no == "SLA/2702"
    assert r.lines[1].qty == "12"  # numbers kept as printed text, even when sent as JSON numbers
    assert r.lines[2].qty is None and r.lines[2].rate is None  # empty string = couldn't read it


def test_sarvam_schema_follows_the_rules():
    """Root object, every field a type and a description, no null types, depth <= 4."""
    def walk(node, depth):
        assert depth <= 4 and node.get("type") in ("object", "array", "string")
        if depth:
            assert node.get("description")
        for child in node.get("properties", {}).values():
            walk(child, depth + 1)
        if "items" in node:
            walk(node["items"], depth + 1)
    walk(SARVAM_SCHEMA, 0)
    assert set(SARVAM_SCHEMA["properties"]["lines"]["items"]["properties"]) == {"raw_text", "qty", "unit", "rate",
                                                                                "amount"}


def test_sarvam_language_hint_and_png():
    fake = FakeSarvam(statuses=("completed",))
    v, _ = sarvam(fake, language="ta-IN")
    v.read_bill(b"png-bytes", "image/png")
    body = fake.requests[0].content.decode("latin-1")
    assert 'name="language"\r\n\r\nta-IN' in body and 'filename="bill.png"' in body


def test_sarvam_retries_passing_failures():
    fake = FakeSarvam()
    fake.extract_errors = [httpx.Response(503, json={"error": {"code": "model_overloaded"}}),
                           httpx.Response(429, headers={"Retry-After": "7"},
                                          json={"error": {"code": "rate_limit_exceeded_error"}})]
    fake.status_errors = [httpx.Response(500, json={"error": {"code": "internal_server_error"}})]
    v, clock = sarvam(fake)
    assert v.read_bill(b"x", "image/jpeg").lines
    assert clock.sleeps[:2] == [1.0, 7.0]  # backoff, then the server's Retry-After


@pytest.mark.parametrize("status,code", [(402, "insufficient_quota_error"), (400, "invalid_request_error"),
                                         (403, ""), (429, "seat_budget_exhausted_error"), (422, "")])
def test_sarvam_refusals_are_not_retried(status, code):
    fake = FakeSarvam()
    fake.extract_errors = [httpx.Response(status, json={"error": {"code": code}})] * 3
    v, clock = sarvam(fake)
    with pytest.raises(VisionRejected) as e:
        v.read_bill(b"x", "image/jpeg")
    assert len(fake.requests) == 1 and clock.sleeps == []
    assert str(status) in str(e.value) and KEY not in str(e.value)


def test_sarvam_failures():
    v, _ = sarvam(FakeSarvam(statuses=("failed",)))
    with pytest.raises(VisionRejected, match="job failed"):
        v.read_bill(b"x", "image/jpeg")
    v, clock = sarvam(FakeSarvam(statuses=("running",)), timeout=20)  # never finishes
    with pytest.raises(AdapterError, match="still running") as e:
        v.read_bill(b"x", "image/jpeg")
    assert not isinstance(e.value, VisionRejected) and clock.now < 20  # passing: the worker may try again
    fake = FakeSarvam()
    fake.extract_errors = [httpx.Response(503)] * 3
    v, _ = sarvam(fake)
    with pytest.raises(AdapterError) as e:
        v.read_bill(b"x", "image/jpeg")
    assert not isinstance(e.value, VisionRejected) and len(fake.requests) == 3
    with pytest.raises(AdapterError, match="unexpected bill shape"):
        sarvam(FakeSarvam(result={"lines": "nope"}))[0].read_bill(b"x", "image/jpeg")
    with pytest.raises(VisionRejected):
        sarvam(FakeSarvam())[0].read_bill(b"x", "image/webp")  # the service converts first
    with pytest.raises(VisionNotSetUp):
        SarvamVision("").read_bill(b"x", "image/jpeg")
    r = sarvam(FakeSarvam(statuses=("partially_completed",)))[0].read_bill(b"x", "image/jpeg")
    assert r.notes == ["partially_completed"]


def test_chain_falls_back_and_says_why():
    primary, _ = sarvam(FakeSarvam(statuses=("failed",)))
    r = VisionChain(primary, OfflineVision()).read_bill(b"x", "image/jpeg", sample="printed")
    assert r.provider == "offline" and r.notes == ["fallback after sarvam: job failed"]
    empty, _ = sarvam(FakeSarvam(result={"lines": []}, statuses=("completed",)))
    r = VisionChain(empty, OfflineVision()).read_bill(b"x", "image/jpeg", sample="printed")
    assert r.notes == ["fallback after sarvam: no lines"]
    ok, _ = sarvam(FakeSarvam())
    assert VisionChain(ok, OfflineVision()).read_bill(b"x", "image/jpeg").provider == "sarvam"


# ---------------- photo conversion ----------------


def test_photos_converted_for_the_reader():
    accepts = SarvamVision.accepts
    jpeg = image_bytes()
    assert for_reader(jpeg, "image/jpeg", accepts) == (jpeg, "image/jpeg")  # untouched
    png = image_bytes("PNG")
    assert for_reader(png, "image/png", accepts) == (png, "image/png")
    out, ctype = for_reader(image_bytes("WEBP"), "image/webp", accepts)
    assert ctype == "image/jpeg" and Image.open(BytesIO(out)).format == "JPEG"
    # a phone photo stored sideways with an EXIF rotation is turned upright
    out, ctype = for_reader(image_bytes(size=(1200, 900), exif_orientation=6), "image/jpeg", accepts)
    assert ctype == "image/jpeg" and Image.open(BytesIO(out)).size == (900, 1200)
    big, _ = for_reader(image_bytes("WEBP", size=(6000, 4000)), "image/webp", accepts)
    assert max(Image.open(BytesIO(big)).size) == 3000
    with pytest.raises(PhotoNotReadable):
        for_reader(b"not a picture" * 100, "image/webp", accepts)
    assert for_reader(b"webp", "image/webp", ("image/webp",)) == (b"webp", "image/webp")  # Gemini takes WebP


def test_heic_converted():
    pillow_heif = pytest.importorskip("pillow_heif")
    pillow_heif.register_heif_opener()
    buf = BytesIO()
    Image.new("RGB", (640, 480), "white").save(buf, "HEIF")
    out, ctype = for_reader(buf.getvalue(), "image/heic", SarvamVision.accepts)
    assert ctype == "image/jpeg" and Image.open(BytesIO(out)).size == (640, 480)


# ---------------- choosing the reader ----------------


def test_choose_vision(db_url, tmp_path):
    base = {"database_url": db_url, "sandbox": True, "inline_worker": False}
    assert Settings(**base).vision == "sarvam"  # the default
    svc = build_services(Settings(**base, photo_dir=str(tmp_path)))  # no key: offline, sample bills only
    assert svc.purchases.vision.name == "offline" and svc.purchases.mode == "samples"
    v = choose_vision(Settings(**base, sarvam_api_key=KEY), svc.adapters)
    assert isinstance(v, SarvamVision)
    v = choose_vision(Settings(**base, sarvam_api_key=KEY, gemini_api_key="g"), svc.adapters)
    assert isinstance(v, VisionChain) and v.fallback.name == "gemini" and v.accepts == SarvamVision.accepts
    v = choose_vision(Settings(**base, sarvam_api_key=KEY, gemini_api_key="g", vision_fallback="none"), svc.adapters)
    assert isinstance(v, SarvamVision)
    v = choose_vision(Settings(**base, vision="gemini", sarvam_api_key=KEY, gemini_api_key="g"), svc.adapters)
    assert v.name == "gemini" and not isinstance(v, VisionChain)  # fallback is gemini itself: none
    assert choose_vision(Settings(**base, vision="offline", sarvam_api_key=KEY), svc.adapters).name == "offline"
    svc.admin_sessions.kw["bind"].dispose()


# ---------------- end to end: the worker reads, code checks ----------------


@pytest.fixture
def live(db_url, tmp_path):
    fake = FakeSarvam()
    gem = FakeGemini()

    def route(request):
        return gem(request) if "googleapis" in request.url.host else fake(request)

    cfg = Settings(database_url=db_url, sandbox=True, inline_worker=False,
                   sarvam_api_key=KEY, photo_dir=str(tmp_path), vision_poll_seconds=0.0)
    svc = build_services(cfg, http_client=httpx.Client(transport=httpx.MockTransport(route)))
    svc.purchases.vision.sleep = lambda s: None
    with TestClient(create_app(svc)) as client:
        yield svc, client, fake, gem
    svc.admin_sessions.kw["bind"].dispose()


def post_photo(client, h, body, ctype="image/jpeg"):
    r = upload_photo(client, h, body, ctype)
    assert r.status_code == 202, r.text
    return r.json()


def test_photo_read_on_the_worker_and_checked_in_code(live):
    svc, client, fake, _ = live
    h = login(client)
    health = client.get("/api/health").json()
    assert health["vision"] == "live" and health["providers"]["vision"] == "sarvam"
    queued = post_photo(client, h, image_bytes("WEBP"), "image/webp")
    assert queued["status"] == "reading" and queued["reader"] == "sarvam" and not queued["can_confirm"]
    assert fake.requests == []  # nothing sent until the worker runs
    assert [p["status"] for p in client.get("/v1/purchases", headers=h).json()] == ["reading"]

    assert svc.timers.tick() == 1
    photo = client.get(f"/v1/purchases/{queued['id']}/photo", headers=h)
    assert photo.headers["content-type"] == "image/jpeg"  # re-encoded before submitting
    assert 'filename="bill.jpg"' in fake.requests[0].content.decode("latin-1")
    b = client.get(f"/v1/purchases/{queued['id']}", headers=h).json()
    assert b["status"] == "read" and b["reader"] == "sarvam" and b["bill_no"] == "SLA/2702"
    # no figure accepted unchecked: 4 x ₹96 is ₹384, not the ₹484 read
    dal = by_text(b, "CHANNA")
    assert not dal["check_ok"] and "math" in dal["flags"] and dal["expected_paise"] == 38_400
    assert dal["unit_cost_paise"] is None
    assert by_text(b, "HAMAM")["check_ok"]
    tea = by_text(b, "TEA")
    assert "qty_missing" in tea["flags"] and tea["rate_paise"] is None
    assert "total_mismatch" in b["read_issues"]  # 484 + 408 + 120 != 792
    assert not b["can_confirm"]
    assert client.post(f"/v1/purchases/{b['id']}/confirm", headers=h).status_code == 409
    with svc.admin_sessions() as s:
        ev = s.execute(select(AuditEvent).where(AuditEvent.kind == "purchase_read")).scalar_one()
        assert ev.detail["reader"] == "sarvam" and ev.detail["request_id"] == "job_abc"
    me = client.get("/v1/me", headers=h).json()["id"]
    kinds = [e["kind"] for e in client.get(f"/sandbox/trace/{me}").json()]
    assert "purchase.queued" in kinds and "purchase.read" in kinds


def test_passing_failure_retried_once_then_failed_then_try_again(live):
    svc, client, fake, _ = live
    h = login(client)
    fake.extract_errors = [httpx.Response(503)] * 5  # two worker attempts; the circuit opens at the fifth
    b = post_photo(client, h, image_bytes())
    svc.timers.tick()
    with svc.admin_sessions() as s:
        t = s.execute(select(Timer).where(Timer.kind == "purchase_read")).scalar_one()
        assert t.status == "pending" and t.attempts == 1  # tried again shortly
        tid = t.id
    assert client.get(f"/v1/purchases/{b['id']}", headers=h).json()["status"] == "reading"
    svc.timers.run(tid)
    b = client.get(f"/v1/purchases/{b['id']}", headers=h).json()
    assert b["status"] == "failed" and b["read_issues"] == ["read_failed"] and b["can_retry"]
    # five 503s in a row open the circuit for api.sarvam.ai: the sixth call fails fast, never sent
    assert len(fake.requests) == 5
    svc.purchases.vision.client._transport._open_until.clear()  # 30 seconds later
    r = client.post(f"/v1/purchases/{b['id']}/retry", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "reading"
    assert client.post(f"/v1/purchases/{b['id']}/retry", headers=h).status_code == 409  # already reading
    svc.timers.tick()
    b = client.get(f"/v1/purchases/{b['id']}", headers=h).json()
    assert b["status"] == "read" and len(b["lines"]) == 3


def test_refusal_fails_at_once(live):
    svc, client, fake, _ = live
    h = login(client)
    fake.extract_errors = [httpx.Response(402, json={"error": {"code": "insufficient_quota_error"}})]
    b = post_photo(client, h, image_bytes())
    svc.timers.tick()
    b = client.get(f"/v1/purchases/{b['id']}", headers=h).json()
    assert b["status"] == "failed" and len(fake.requests) == 1
    with svc.admin_sessions() as s:
        ev = s.execute(select(AuditEvent).where(AuditEvent.kind == "purchase_read_failed")).scalar_one()
        assert "402" in ev.detail["error"] and KEY not in json.dumps(ev.detail)


def test_discarded_while_reading(live):
    svc, client, fake, _ = live
    h = login(client)
    b = post_photo(client, h, image_bytes())
    assert client.post(f"/v1/purchases/{b['id']}/discard", headers=h).json()["status"] == "discarded"
    svc.timers.tick()
    assert fake.requests == []  # never sent
    assert client.get(f"/v1/purchases/{b['id']}", headers=h).json()["status"] == "discarded"


def test_something_that_is_not_a_photo_is_never_sent(live):
    svc, client, fake, _ = live
    h = login(client)
    b = post_photo(client, h, b"not a picture" * 200, "image/webp")
    svc.timers.tick()
    b = client.get(f"/v1/purchases/{b['id']}", headers=h).json()
    assert b["status"] == "failed" and b["read_issues"] == ["not_a_photo"] and b["has_photo"] is not False
    assert fake.requests == []  # the reader never saw it


def test_falls_back_to_gemini(db_url, tmp_path):
    fake, gem = FakeSarvam(), FakeGemini()
    fake.extract_errors = [httpx.Response(402, json={"error": {"code": "insufficient_quota_error"}})]

    def route(request):
        return gem(request) if "googleapis" in request.url.host else fake(request)

    cfg = Settings(database_url=db_url, sandbox=True, inline_worker=False, sarvam_api_key=KEY,
                   gemini_api_key="gkey-123", photo_dir=str(tmp_path))
    svc = build_services(cfg, http_client=httpx.Client(transport=httpx.MockTransport(route)))
    with TestClient(create_app(svc)) as client:
        h = login(client)
        assert client.get("/api/health").json()["providers"]["vision_fallback"] == "gemini"
        b = post_photo(client, h, image_bytes())
        svc.timers.tick()
        b = client.get(f"/v1/purchases/{b['id']}", headers=h).json()
        assert b["status"] == "read" and b["reader"] == "gemini" and len(gem.requests) == 1
        assert "read_by_fallback" in b["read_issues"]  # C5: the screen says the backup reader was used
        with svc.admin_sessions() as s:
            ev = s.execute(select(AuditEvent).where(AuditEvent.kind == "purchase_read")).scalar_one()
            assert ev.detail["notes"] == ["fallback after sarvam extract: HTTP 402 insufficient_quota_error"]
    svc.admin_sessions.kw["bind"].dispose()


def test_samples_still_read_at_once_without_a_key(client, owner):
    r = read(client, owner, "handwritten")
    assert r.status_code == 200 and r.json()["status"] == "read" and r.json()["reader"] == "offline"


def test_regional_language_bill(client, owner):
    """Tamil item names and units: units are understood, every figure checks out in code, and the owner names
    each item once (nothing is guessed from a script Laabam can't match yet)."""
    b = read(client, owner, "tamil").json()
    assert b["distributor_name"] == "முருகன் மொத்த வியாபாரம்" and b["bill_date"] == "2026-11-12"
    assert b["printed_total_paise"] == b["total_paise"] == 326_800 and b["read_issues"] == []
    oil, sugar, dal, urad = b["lines"]
    assert oil["unit"] == "pack" and oil["base_unit"] == "L" and oil["base_qty_milli"] == 12_000
    assert oil["unit_cost_paise"] == 15_200  # ₹152 a litre
    assert sugar["unit"] == "kg" and sugar["unit_cost_paise"] == 4_400
    assert dal["base_unit"] == "kg" and dal["unit_cost_paise"] == 13_000
    assert urad["unit"] == "kg" and urad["unit_cost_paise"] == 11_800
    assert all(ln["check_ok"] for ln in b["lines"])
    assert all(set(ln["flags"]) <= {"item_new", "item_ask"} for ln in b["lines"])


def test_compare_script_scores_against_the_photos():
    """The scorer behind scripts/compare_vision.py, on the offline readings: right where they match the photo,
    and the planted misread is a wrong amount that the code checks catch."""
    import yaml

    from scripts.compare_vision import BILLS, score

    truth = yaml.safe_load((BILLS / "truth.yaml").read_text(encoding="utf-8"))
    assert sorted(truth) == sorted(p.stem for p in BILLS.glob("*.jpg"))
    offline = OfflineVision()
    for bill in ("printed", "handwritten", "tamil"):
        r = score(truth[bill], offline.read_bill(b"", "image/jpeg", sample=bill))
        assert all(r["header"].values()) and not any(ln["wrong"] for ln in r["lines"]), (bill, r)
    r = score(truth["misread"], offline.read_bill(b"", "image/jpeg", sample="misread"))
    dal = r["lines"][0]
    assert dal["wrong"] == ["amount"] and dal["flags"] == ["math"] and dal["caught"]
    assert r["total_mismatch_flagged"]
