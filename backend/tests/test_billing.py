"""Voice billing through the API (text input): checklist, learning, the rules in BUILD_PLAN §1."""
from decimal import Decimal

from sqlalchemy import select

from laabam.billing.parser import ParsedLine
from laabam.billing.verify import price_was_heard
from laabam.models import AuditEvent, PricePoint
from tests.conftest import login


def bill(client, h, text):
    r = client.post("/v1/bills/text", json={"text": text}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def confirm(client, h, b):
    r = client.post(f"/v1/bills/{b['id']}/confirm", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def lines(b):
    return [(ln["item_name"].lower(), ln["qty_milli"], ln["unit"], ln["line_total_paise"], ln["price_source"],
             ln["flags"]) for ln in b["lines"]]


def test_typed_order_gives_checklist_with_total_added_in_code(client, owner):
    b = bill(client, owner, "rice 200, sugar 100, oil 165")
    assert b["status"] == "draft"
    assert lines(b) == [("rice", None, None, 20000, "spoken", []), ("sugar", None, None, 10000, "spoken", []),
                        ("oil", None, None, 16500, "spoken", [])]
    assert b["total_paise"] == 46500 and b["can_confirm"]
    assert b["lines"][0]["heard_text"] == "rice 200"
    assert "parse_ms" in b["timings"]


def test_per_unit_and_quantities(client, owner):
    b = bill(client, owner, "2 kg rice at 60, half kg dal 60, biscuit 10 each 3")
    assert lines(b) == [("rice", 2000, "kg", 12000, "spoken", []), ("dal", 500, "kg", 6000, "spoken", []),
                        ("biscuit", 3000, "pc", 3000, "spoken", [])]
    assert b["lines"][0]["unit_price_paise"] == 6000
    assert b["lines"][1]["unit_price_paise"] == 12000
    assert b["total_paise"] == 21000


def test_confirm_writes_price_points_and_next_order_uses_them(client, owner, services):
    b = bill(client, owner, "rice 200, sugar 100, oil 165")
    assert confirm(client, owner, b)["status"] == "confirmed"
    with services.admin_sessions() as s:
        pts = s.execute(select(PricePoint)).scalars().all()
        assert sorted(p.price_paise for p in pts) == [10000, 16500, 20000]
        assert {p.source for p in pts} == {"voice"} and all(p.sales_line_id for p in pts)

    nxt = bill(client, owner, "rice, oil")
    assert lines(nxt) == [("rice", None, None, 20000, "learned", []), ("oil", None, None, 16500, "learned", [])]
    assert all(ln["learned"] and ln["price_point_id"] for ln in nxt["lines"])  # every figure has a source
    assert nxt["total_paise"] == 36500 and nxt["can_confirm"]


def test_learned_price_scales_to_quantity(client, owner):
    confirm(client, owner, bill(client, owner, "rice 2 kg 120, biscuit 10"))
    nxt = bill(client, owner, "rice 500 g, 3 biscuit, sugar")
    assert lines(nxt) == [("rice", 500, "kg", 3000, "learned", []), ("biscuit", 3000, "pc", 3000, "learned", []),
                          ("sugar", None, None, None, None, ["price_needed"])]
    assert not nxt["can_confirm"]


def test_latest_price_wins(client, owner):
    for text in ("rice 200", "rice 220"):
        confirm(client, owner, bill(client, owner, text))
    assert lines(bill(client, owner, "rice"))[0][3] == 22000


def test_unconfirmed_prices_are_not_learned(client, owner):
    bill(client, owner, "rice 200")  # never confirmed
    assert lines(bill(client, owner, "rice"))[0][5] == ["price_needed"]


def test_no_confirm_with_flagged_lines(client, owner):
    b = bill(client, owner, "sugar, rice 200")
    assert not b["can_confirm"]
    assert client.post(f"/v1/bills/{b['id']}/confirm", headers=owner).status_code == 409
    sugar = b["lines"][0]
    fixed = client.patch(f"/v1/bills/{b['id']}/lines/{sugar['id']}", json={"line_total_paise": 4500},
                         headers=owner).json()
    assert fixed["lines"][0]["flags"] == [] and fixed["lines"][0]["price_source"] == "edit"
    assert fixed["total_paise"] == 24500 and fixed["can_confirm"]
    assert confirm(client, owner, b)["status"] == "confirmed"


class LyingParser:
    """Stands in for a model that invents a price the owner never said."""

    name = "lying-model"

    def parse_bill(self, transcript, language):
        return [ParsedLine("rice", price=Decimal("300"), price_kind="total", start=0, end=len(transcript)),
                ParsedLine("oil", price=Decimal("165"), price_kind="total", start=0, end=len(transcript))]


def test_price_must_appear_in_transcript(client, owner, services):
    services.bills.parser = LyingParser()
    b = bill(client, owner, "rice 200 oil 165")
    assert b["lines"][0]["flags"] == ["price_not_heard"]  # 300 was never said
    assert b["lines"][1]["flags"] == []
    assert client.post(f"/v1/bills/{b['id']}/confirm", headers=owner).status_code == 409


def test_heard_text_must_be_in_transcript():
    assert price_was_heard(20000, "rice 200", "rice 200 oil 165", "en")
    assert not price_was_heard(20000, "rice 200", "rice 250 oil 165", "en")
    assert not price_was_heard(30000, "rice 200", "rice 200", "en")


def test_voice_fix_replaces_a_line(client, owner):
    b = bill(client, owner, "rice 200, sugar 100")
    r = client.post(f"/v1/bills/{b['id']}/say", json={"text": "sugar 120 not 100"}, headers=owner).json()
    assert lines(r) == [("rice", None, None, 20000, "spoken", []), ("sugar", None, None, 12000, "spoken", [])]
    assert r["total_paise"] == 32000
    assert r["lines"][1]["heard_text"] == "sugar 120 not 100"
    added = client.post(f"/v1/bills/{b['id']}/say", json={"text": "and oil 165"}, headers=owner).json()
    assert [ln["item_name"] for ln in added["lines"]] == ["Rice", "Sugar", "Oil"]
    assert added["total_paise"] == 48500


def test_tap_edits(client, owner):
    b = bill(client, owner, "sugar 60 per kg 2 kg, rice 200")
    sugar, rice = b["lines"]
    r = client.patch(f"/v1/bills/{b['id']}/lines/{sugar['id']}", json={"qty_milli": 3000}, headers=owner).json()
    assert r["lines"][0]["line_total_paise"] == 18000  # per-kg price × new quantity
    r = client.patch(f"/v1/bills/{b['id']}/lines/{rice['id']}", json={"item_text": "basmati rice"},
                     headers=owner).json()
    assert r["lines"][1]["item_name"] == "Basmati rice"
    r = client.post(f"/v1/bills/{b['id']}/lines", json={"item_text": "salt", "line_total_paise": 2500},
                    headers=owner).json()
    assert r["lines"][2]["item_name"] == "Salt" and r["total_paise"] == 18000 + 20000 + 2500
    r = client.delete(f"/v1/bills/{b['id']}/lines/{rice['id']}", headers=owner).json()
    assert [ln["item_name"] for ln in r["lines"]] == ["Sugar", "Salt"] and r["total_paise"] == 20500
    assert client.patch(f"/v1/bills/{b['id']}/lines/{sugar['id']}", json={"unit": "tonne"},
                        headers=owner).status_code == 409
    assert client.patch(f"/v1/bills/{b['id']}/lines/{sugar['id']}", json={"line_total_paise": 0},
                        headers=owner).status_code == 422


def test_correction_and_repeat(client, owner):
    b = bill(client, owner, "rice 200, no, 250, rice 100")
    assert [ln["line_total_paise"] for ln in b["lines"]] == [25000, 10000]
    assert b["lines"][0]["item_id"] == b["lines"][1]["item_id"]  # one item, said twice
    assert b["total_paise"] == 35000


def test_empty_transcript(client, owner):
    assert client.post("/v1/bills/text", json={"text": "   "}, headers=owner).status_code == 409
    b = bill(client, owner, "um okay")
    assert b["lines"] == [] and not b["can_confirm"]


def test_fuzzy_item_match(client, owner):
    confirm(client, owner, bill(client, owner, "biscuits 30"))
    b = bill(client, owner, "biscut, biscuit")
    assert b["lines"][0]["item_id"] == b["lines"][1]["item_id"]
    assert b["lines"][0]["line_total_paise"] == 3000


def test_cancel_and_no_edits_after_confirm(client, owner):
    b = bill(client, owner, "rice 200")
    confirm(client, owner, b)
    line = b["lines"][0]["id"]
    assert client.patch(f"/v1/bills/{b['id']}/lines/{line}", json={"line_total_paise": 100},
                        headers=owner).status_code == 409
    assert client.post(f"/v1/bills/{b['id']}/confirm", headers=owner).status_code == 409
    assert client.post(f"/v1/bills/{b['id']}/cancel", headers=owner).json()["status"] == "cancelled"


def test_audit_trail(client, owner, services):
    b = bill(client, owner, "rice 200")
    client.patch(f"/v1/bills/{b['id']}/lines/{b['lines'][0]['id']}", json={"line_total_paise": 21000}, headers=owner)
    confirm(client, owner, b)
    with services.admin_sessions() as s:
        evs = s.execute(select(AuditEvent).where(AuditEvent.subject == b["id"]).order_by(AuditEvent.id)).scalars()
        assert [(e.actor, e.kind) for e in evs] == [("agent", "bill_drafted"), ("owner", "line_edited"),
                                                    ("owner", "bill_confirmed"), ("system", "qr_shown")]


def test_shops_are_separate(client):
    a, b_ = login(client), login(client)
    mine = bill(client, a, "rice 200")
    assert client.get(f"/v1/bills/{mine['id']}", headers=b_).status_code == 404
    assert client.post(f"/v1/bills/{mine['id']}/confirm", headers=b_).status_code == 404
    assert client.get(f"/v1/bills/{mine['id']}", headers=a).status_code == 200


def test_needs_owner_token(client):
    assert client.post("/v1/bills/text", json={"text": "rice 200"}).status_code == 401
    bad = {"Authorization": "Bearer nope"}
    assert client.post("/v1/bills/text", json={"text": "rice 200"}, headers=bad).status_code == 401


def test_language_setting_and_tamil_bill(client, owner):
    me = client.patch("/v1/me", json={"language": "ta"}, headers=owner).json()
    assert me["language"] == "ta" and me["languages"] == ["en", "ta", "hi"]
    b = bill(client, owner, "அரிசி இருநூற்று ஐம்பது, sugar 100")
    assert [ln["line_total_paise"] for ln in b["lines"]] == [25000, 10000] and b["language"] == "ta"
    assert client.patch("/v1/me", json={"language": "fr"}, headers=owner).status_code == 422
    assert client.patch("/v1/me", json={"closing_time": "9pm"}, headers=owner).status_code == 422
    assert client.patch("/v1/me", json={"closing_time": "21:30"}, headers=owner).json()["closing_time"] == "21:30"


def test_hindi_session(client):
    h = login(client, language="hi")
    b = bill(client, h, "चावल दो सौ, चीनी 40 रुपये किलो 2 किलो")
    assert [ln["line_total_paise"] for ln in b["lines"]] == [20000, 8000]


