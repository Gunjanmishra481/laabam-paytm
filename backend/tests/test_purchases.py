"""Phase 3: distributor bill photos -> checked cost lines -> owner confirms -> costs linked to items."""
import base64
import json
from datetime import date
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from laabam.adapters.base import AdapterError
from laabam.adapters.vision import SCHEMA, GeminiVision, OfflineVision, VisionNotSetUp
from laabam.app import build_services, create_app
from laabam.config import Settings
from laabam.models import AuditEvent, Item
from laabam.purchases.reading import (
    BillReading,
    ReadLine,
    base_quantity,
    check_line,
    date_of,
    decimal_of,
    match_key,
    pack_size,
)
from tests.conftest import jpeg, login, upload_photo

PHOTO = jpeg()  # a real photo: the worker re-encodes every upload
JPEG = {"Content-Type": "image/jpeg"}


def read(client, h, sample=None, body=b"", headers=None):
    extra = {"X-Laabam-Sample": sample} if sample else {}
    return client.post("/v1/purchases/photo", content=body, headers={**h, **(headers or JPEG), **extra})


def read_live(client, h, services, body=PHOTO, ctype="image/jpeg"):
    """A live reader: the photo goes to storage, the bill is "reading"; the worker re-encodes and reads it."""
    r = upload_photo(client, h, body, ctype)
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "reading" and r.json()["lines"] == []
    services.timers.tick()
    return client.get(f"/v1/purchases/{r.json()['id']}", headers=h).json()


def by_text(bill, start):
    return next(ln for ln in bill["lines"] if ln["raw_text"].lower().startswith(start.lower()))


def sell(client, h, text="rice 200, sugar 100, oil 165"):
    b = client.post("/v1/bills/text", json={"text": text}, headers=h).json()
    assert client.post(f"/v1/bills/{b['id']}/confirm", headers=h).status_code == 200


def fix(client, h, bill, line, **edit):
    r = client.patch(f"/v1/purchases/{bill['id']}/lines/{line['id']}", json=edit, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------- reading helpers (code, no model) ----------------


@pytest.mark.parametrize("text,value", [
    ("1,480.00", "1480.00"), ("₹ 148", "148"), ("148/-", "148"), ("Rs. 42", "42"), ("१२०", "120"),
    ("12.5", "12.5"), ("abc", None), ("", None), (None, None), ("0", None), ("-5", None), ("1.2.3", None),
])
def test_decimal_of(text, value):
    assert decimal_of(text) == (None if value is None else Decimal(value))


@pytest.mark.parametrize("text,value", [
    ("08/11/2026", date(2026, 11, 8)), ("15-11-26", date(2026, 11, 15)), ("2026-11-08", date(2026, 11, 8)),
    ("Date: 8.11.2026", date(2026, 11, 8)), ("31/02/2026", None), ("yesterday", None), ("", None),
])
def test_date_of(text, value):
    assert date_of(text) == value


@pytest.mark.parametrize("text,size", [
    ("SUNFLWR OIL 1LTR POUCH", ("L", 1000)), ("Milk 500ml", ("L", 500)), ("HAMAM SOAP 100G", ("kg", 100)),
    ("PONNI RICE 25KG BAG", ("kg", 25000)), ("Atta 10kg", ("kg", 10000)), ("Parle biscuit box", None),
    ("TEA 250 GM", ("kg", 250)), ("OIL 1.5 L", ("L", 1500)),
])
def test_pack_size(text, size):
    assert pack_size(text) == size


def test_base_quantity():
    assert base_quantity("SUNFLWR OIL 1LTR POUCH", 10_000, "pc") == ("L", 10_000)
    assert base_quantity("SUGAR LOOSE", 25_000, "kg") == ("kg", 25_000)
    assert base_quantity("HAMAM SOAP 100G", 12_000, "pc") == ("kg", 1_200)
    assert base_quantity("Parle biscuit box", 2_000, "pack") == ("pack", 2_000)
    assert base_quantity("x", None, "pc") == (None, None)


@pytest.mark.parametrize("text,key", [
    ("SUNFLWR OIL 1LTR POUCH", "sunflwr oil"), ("SUGAR LOOSE", "sugar"), ("TOOR DAL 1KG PKT", "toor dal"),
    ("PONNI RICE 25KG BAG", "ponni rice"), ("Parle biscuits box", "parle biscuit"), ("Milk 500ml", "milk"),
])
def test_match_key(text, key):
    assert match_key(text) == key


def test_check_line():
    ok = check_line(10_000, 14_800, 148_000, 10_000)
    assert ok.check_ok and ok.flags == [] and ok.unit_cost_paise == 14_800
    assert check_line(10_000, 14_800, 148_050, 10_000).check_ok  # within ₹1 (rounding)
    bad = check_line(4_000, 9_600, 48_400, 4_000)
    assert not bad.check_ok and bad.flags == ["math"] and bad.unit_cost_paise is None
    no_rate = check_line(2_000, None, 96_000, 2_000)
    assert no_rate.check_ok and no_rate.unit_cost_paise == 48_000  # many bills print only the amount
    assert check_line(None, 100, 100, None).flags == ["qty_missing"]
    assert check_line(1_000, 100, None, 1_000).flags == ["amount_missing"]


# ---------------- photo -> lines ----------------


def test_new_shop_every_line_asks_for_an_item(client, owner):
    r = read(client, owner, "printed")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["status"] == "read" and b["distributor_name"] == "Sri Lakshmi Agencies"
    assert b["bill_no"] == "SLA/2611" and b["bill_date"] == "2026-11-08"
    assert b["total_paise"] == 452_000 and b["printed_total_paise"] == 452_000 and b["read_issues"] == []
    assert all(ln["check_ok"] and ln["flags"] == ["item_new"] for ln in b["lines"])
    assert not b["can_confirm"]
    oil = by_text(b, "SUNFLWR")
    assert (oil["qty_milli"], oil["unit"], oil["rate_paise"]) == (10_000, "pc", 14_800)
    assert oil["line_total_paise"] == 148_000
    assert (oil["base_unit"], oil["base_qty_milli"], oil["unit_cost_paise"]) == ("L", 10_000, 14_800)
    rice = by_text(b, "PONNI")
    assert (rice["base_unit"], rice["base_qty_milli"], rice["unit_cost_paise"]) == ("kg", 25_000, 5_400)
    assert client.post(f"/v1/purchases/{b['id']}/confirm", headers=owner).status_code == 409


def test_known_items_matched_or_asked_once_then_learned(client, owner, services):
    sell(client, owner)  # the owner has sold Rice, Sugar and Oil by voice
    b = read(client, owner, "printed").json()
    sugar, oil, rice, dal = (by_text(b, t) for t in ("SUGAR", "SUNFLWR", "PONNI", "TOOR"))
    assert sugar["item_name"] == "Sugar" and sugar["match"] == "alias" and sugar["flags"] == []
    assert oil["item_id"] is None and oil["suggested_item_name"] == "Oil" and oil["flags"] == ["item_ask"]
    assert rice["suggested_item_name"] == "Rice" and rice["flags"] == ["item_ask"]
    assert dal["flags"] == ["item_new"] and dal["suggested_item_id"] is None and dal["name_hint"] == "Toor dal"

    b = fix(client, owner, b, oil, accept_suggestion=True)
    b = fix(client, owner, b, rice, accept_suggestion=True)
    b = fix(client, owner, b, dal, new_item_name="Toor dal")
    assert b["can_confirm"]
    b = client.post(f"/v1/purchases/{b['id']}/confirm", headers=owner).json()
    assert b["status"] == "confirmed" and b["confirmed_at"]

    with services.admin_sessions() as s:
        oil_item = s.execute(select(Item).where(Item.key == "oil")).scalar_one()
        assert "sunflwr oil" in oil_item.aliases and oil_item.unit == "L"
        ev = s.execute(select(AuditEvent).where(AuditEvent.kind == "purchase_confirmed")).scalar_one()
        assert {"item": "Oil", "alias": "sunflwr oil"} in ev.detail["aliases_learned"]

    # the next bill from another distributor spells it differently: matched without asking
    h2 = read(client, owner, "handwritten").json()
    oil2 = by_text(h2, "Sunflower oil")
    assert oil2["item_name"] == "Oil" and oil2["match"] == "fuzzy" and oil2["unit_cost_paise"] == 15_600
    milk = by_text(h2, "Milk")
    assert (milk["base_unit"], milk["base_qty_milli"], milk["unit_cost_paise"]) == ("L", 20_000, 5_400)

    items = {i["name"]: i for i in client.get("/v1/items", headers=owner).json()}
    assert items["Oil"]["cost"]["unit_cost_paise"] == 14_800 and items["Oil"]["cost"]["per_unit"] == "L"
    assert items["Oil"]["price"]["price_paise"] == 16_500  # what the owner sells at, from the voice bill
    assert items["Toor dal"]["cost"]["unit_cost_paise"] == 12_800
    assert items["Oil"]["cost"]["purchase_id"] == b["id"]  # every figure links to its source


def test_misread_line_is_flagged_until_fixed(client, owner):
    b = read(client, owner, "misread").json()
    dal = by_text(b, "CHANNA")
    assert not dal["check_ok"] and "math" in dal["flags"] and dal["unit_cost_paise"] is None
    assert b["total_paise"] == 89_200 and b["printed_total_paise"] == 79_200
    assert b["read_issues"] == ["total_mismatch"]
    b = fix(client, owner, b, dal, line_total_paise=38_400, new_item_name="Channa dal")
    dal = by_text(b, "CHANNA")
    assert dal["check_ok"] and dal["flags"] == [] and dal["unit_cost_paise"] == 9_600
    assert b["read_issues"] == []
    b = fix(client, owner, b, by_text(b, "HAMAM"), new_item_name="Soap")
    assert by_text(b, "HAMAM")["unit_cost_paise"] == 34_000  # ₹408 for 1.2 kg -> ₹340 a kg
    assert client.post(f"/v1/purchases/{b['id']}/confirm", headers=owner).status_code == 200


def test_owner_can_pick_an_item_delete_and_add_lines(client, owner):
    sell(client, owner)
    b = read(client, owner, "printed").json()
    items = {i["name"]: i["id"] for i in client.get("/v1/items", headers=owner).json()}
    b = fix(client, owner, b, by_text(b, "PONNI"), item_id=items["Rice"])
    assert by_text(b, "PONNI")["match"] == "owner"
    b = client.delete(f"/v1/purchases/{b['id']}/lines/{by_text(b, 'TOOR')['id']}", headers=owner).json()
    assert len(b["lines"]) == 3 and b["total_paise"] == 388_000 and b["read_issues"] == ["total_mismatch"]
    r = client.post(f"/v1/purchases/{b['id']}/lines", headers=owner,
                    json={"raw_text": "TOOR DAL 1KG PKT", "qty_milli": 5000, "unit": "pack", "rate_paise": 12800,
                          "line_total_paise": 64000, "new_item_name": "Toor dal"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["total_paise"] == 452_000 and b["read_issues"] == []
    assert by_text(b, "TOOR")["unit_cost_paise"] == 12_800 and by_text(b, "TOOR")["base_unit"] == "kg"
    other = client.get("/v1/items", headers=login(client)).json()
    assert other == []  # another shop sees none of it


def test_header_fixes_and_duplicates(client, owner):
    b = read(client, owner, "printed").json()
    r = client.patch(f"/v1/purchases/{b['id']}", headers=owner,
                     json={"distributor_name": "  Sri  Lakshmi Agencies ", "bill_date": "2026-11-09"})
    assert r.json()["distributor_name"] == "Sri Lakshmi Agencies" and r.json()["bill_date"] == "2026-11-09"
    for ln in b["lines"]:
        b = fix(client, owner, b, ln, new_item_name=match_key(ln["raw_text"]))
    client.post(f"/v1/purchases/{b['id']}/confirm", headers=owner)
    again = read(client, owner, "printed").json()
    assert "maybe_duplicate" in again["read_issues"]
    assert client.post(f"/v1/purchases/{again['id']}/discard", headers=owner).json()["status"] == "discarded"
    assert [p["id"] for p in client.get("/v1/purchases", headers=owner).json()] == [b["id"]]
    # a confirmed bill is final
    assert client.patch(f"/v1/purchases/{b['id']}", json={"bill_no": "x"}, headers=owner).status_code == 409


def test_photo_is_stored_and_private(client, owner, services):
    reading = BillReading(lines=[ReadLine("SUGAR LOOSE", "5", "kg", "42", "210")], distributor="Test", provider="stub")
    services.purchases.vision = type("Stub", (), {"name": "stub", "read_bill": lambda *a: reading})()
    b = read_live(client, owner, services)
    assert b["status"] == "read" and b["has_photo"] and b["reader"] == "stub"
    got = client.get(f"/v1/purchases/{b['id']}/photo", headers=owner)
    assert got.status_code == 200 and got.headers["content-type"] == "image/jpeg"
    assert got.content[:2] == b"\xff\xd8" and got.content != PHOTO  # the re-encoded copy, not what was sent
    assert client.get(f"/v1/purchases/{b['id']}/photo", headers=login(client)).status_code == 404
    assert client.get(f"/v1/purchases/{b['id']}/photo", headers={"Authorization": ""}).status_code == 401


def test_photo_checks(client, owner, services):
    def start(ctype, size):
        return client.post("/v1/purchases/uploads", json={"content_type": ctype, "size": size}, headers=owner)
    assert start("text/plain", 5000).status_code == 422
    assert start("image/jpeg", 10).status_code == 422  # empty photo
    assert start("image/jpeg", 10_000_001).status_code == 422  # too large
    assert read(client, owner, body=PHOTO).status_code == 422  # photos never come through the API body
    # the demo has no vision service: real photos are refused, sample bills work
    r = start("image/jpeg", len(PHOTO))
    assert r.status_code == 503 and r.json()["detail"] == "Reading bill photos is not set up yet."
    h = client.get("/api/health").json()
    assert h["vision"] == "samples" and h["bill_samples"] == ["balaji", "handwritten", "misread", "printed", "tamil"]
    services.purchases.vision = type("Blank", (), {"name": "blank", "read_bill": lambda *a: BillReading(lines=[])})()
    b = read_live(client, owner, services)  # not a bill
    assert b["status"] == "failed" and b["read_issues"] == ["no_lines"] and b["can_retry"] and not b["lines"]


def test_vision_off_outside_sandbox(db_url, tmp_path):
    cfg = Settings(database_url=db_url, sandbox=False, inline_worker=False,
                   photo_dir=str(tmp_path))
    svc = build_services(cfg)
    assert svc.purchases.mode == "off" and svc.purchases.samples() == []
    with pytest.raises(VisionNotSetUp):
        OfflineVision().read_bill(PHOTO, "image/jpeg")
    svc.admin_sessions.kw["bind"].dispose()


# ---------------- Gemini (mocked) ----------------

GEMINI_BILL = {
    "distributor": "Sri Lakshmi Agencies", "bill_no": "SLA/2611", "bill_date": "08/11/2026",
    "lines": [{"raw_text": "SUNFLWR OIL 1LTR POUCH", "qty": 10, "unit": "PCS", "rate": "148.00", "amount": "1,480.00"},
              {"raw_text": "SUGAR LOOSE", "qty": "25", "unit": "KG", "rate": None, "amount": "1050"}],
    "total": "2530",
}


class FakeGemini:
    def __init__(self, answer=GEMINI_BILL, status=200, raw=None):
        self.answer, self.status, self.raw = answer, status, raw
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"code": self.status, "status": "RESOURCE_EXHAUSTED"}})
        text = self.raw if self.raw is not None else json.dumps(self.answer)
        return httpx.Response(200, json={"responseId": "resp_1", "candidates": [{"content": {"parts": [
            {"text": "thinking...", "thought": True}, {"text": text}]}}]})


def gemini(fake, model="gemini-test"):
    return GeminiVision("gkey-123", model, client=httpx.Client(transport=httpx.MockTransport(fake)))


def test_gemini_request_and_reading():
    fake = FakeGemini()
    r = gemini(fake).read_bill(PHOTO, "image/jpeg")
    req = fake.requests[0]
    assert str(req.url) == "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent"
    assert req.headers["x-goog-api-key"] == "gkey-123"
    body = json.loads(req.content)
    parts = body["contents"][0]["parts"]
    assert "Do not calculate" in parts[0]["text"]
    assert parts[1]["inlineData"] == {"mimeType": "image/jpeg", "data": base64.b64encode(PHOTO).decode()}
    cfg = body["generationConfig"]
    assert cfg["responseMimeType"] == "application/json" and cfg["responseJsonSchema"] == SCHEMA
    assert cfg["temperature"] == 0
    assert r.provider == "gemini" and r.request_id == "resp_1" and r.distributor == "Sri Lakshmi Agencies"
    assert r.lines[0] == ReadLine("SUNFLWR OIL 1LTR POUCH", "10", "PCS", "148.00", "1,480.00")  # numbers kept as text
    assert r.lines[1].rate is None


def test_gemini_failures_do_not_leak_the_key():
    with pytest.raises(AdapterError) as e:
        gemini(FakeGemini(status=429)).read_bill(PHOTO, "image/jpeg")
    assert "429" in str(e.value) and "gkey-123" not in str(e.value)
    with pytest.raises(AdapterError):
        gemini(FakeGemini(raw="not json")).read_bill(PHOTO, "image/jpeg")
    with pytest.raises(AdapterError):
        gemini(FakeGemini(answer={"lines": "nope"})).read_bill(PHOTO, "image/jpeg")
    with pytest.raises(AdapterError):
        gemini(FakeGemini()).read_bill(PHOTO, "image/gif")
    with pytest.raises(VisionNotSetUp):
        GeminiVision("", "m").read_bill(PHOTO, "image/jpeg")


def test_photo_through_gemini(db_url, tmp_path):
    fake = FakeGemini()
    cfg = Settings(database_url=db_url, sandbox=True, inline_worker=False, vision="gemini",
                   gemini_api_key="gkey-123", photo_dir=str(tmp_path))
    svc = build_services(cfg, http_client=httpx.Client(transport=httpx.MockTransport(fake)))
    with TestClient(create_app(svc)) as client:
        h = login(client)
        assert client.get("/api/health").json()["vision"] == "live"
        b = read_live(client, h, svc)
        assert b["reader"] == "gemini" and b["has_photo"] and b["total_paise"] == 253_000
        assert by_text(b, "SUGAR")["check_ok"] and by_text(b, "SUGAR")["unit_cost_paise"] == 4_200
        fake.status = 403  # refused: no second try
        b = read_live(client, h, svc)
        assert b["status"] == "failed" and b["read_issues"] == ["read_failed"]
        kinds = [e["kind"] for e in client.get(f"/sandbox/trace/{client.get('/v1/me', headers=h).json()['id']}").json()]
        assert "purchase.read" in kinds and "vision.failed" in kinds
    svc.admin_sessions.kw["bind"].dispose()
