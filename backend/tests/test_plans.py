"""Phase 5: the festival plan (usual week x a multiplier the owner changes), cash in hand, the shortfall and the
loan offer through the sandbox lender. The seeded month reproduces the deck's card: a ₹40,000 Deepavali order,
₹15,000 in hand, a ₹25,000 loan."""
from datetime import date, timedelta

from sqlalchemy import select

from laabam.clock import ist_day
from laabam.models import AuditEvent, utcnow
from laabam.plans import next_festival
from tests.conftest import login
from tests.test_demo_seed import confirm_balaji_bill, shop  # noqa: F401 (fixture)


def plan(client, h, bp=20000):
    r = client.get(f"/v1/plans/festival?multiplier_bp={bp}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_deepavali_plan(client, services, shop):  # noqa: F811
    confirm_balaji_bill(client, services, shop)
    p = plan(client, shop)
    assert p["festival"] == "deepavali" and p["festival_names"]["en"] == "Deepavali" and p["guess"]
    # the festival date is a setting; the demo's is always 9 days away
    assert p["days_to_go"] == 9 and p["festival_date"] == (ist_day(utcnow()) + timedelta(days=9)).isoformat()
    client.patch("/v1/me", json={"clear_festival_date": True}, headers=shop)  # back to the calendar
    nxt = next_festival("deepavali", ist_day(utcnow()))
    assert plan(client, shop)["festival_date"] == nxt[2].isoformat()
    when = (ist_day(utcnow()) + timedelta(days=20)).isoformat()
    assert client.patch("/v1/me", json={"festival_date": when}, headers=shop).json()["festival_date"] == when
    assert plan(client, shop)["days_to_go"] == 20
    assert p["multiplier_bp"] == 20000 and p["multipliers_bp"] == [15000, 20000, 25000]
    assert p["total_paise"] == 40_000_00 and p["cash_on_hand_paise"] == 15_000_00 and p["shortfall_paise"] == 25_000_00
    assert p["total_paise"] == sum(x["amount_paise"] for x in p["lines"])
    oil = p["lines"][0]
    assert oil["name"] == "Sunflower oil" and oil["qty"] == 92  # 46 a week, twice
    assert oil["distributor"] == "Kumar Traders" and oil["cost_paise"] == 149_00 and oil["cheaper"]  # not Balaji's ₹156
    names = {x["name"] for x in p["lines"]}
    assert "Health drink 500 g" not in names and "Basmati rice 5 kg" not in names  # slow stock isn't stocked up
    assert not names & {"Bread", "Eggs", "Paneer"}  # no cost known: not guessed
    assert all(x["amount_paise"] == x["qty"] * x["cost_paise"] for x in p["lines"])
    # the multiplier changes the order (a guess the owner changes)
    assert plan(client, shop, 15000)["total_paise"] < 40_000_00 < plan(client, shop, 25000)["total_paise"]
    assert client.get("/v1/plans/festival?multiplier_bp=30000", headers=shop).status_code == 422


def test_loan_for_the_shortfall_owner_taps(client, services, shop):  # noqa: F811
    confirm_balaji_bill(client, services, shop)
    saved = client.post("/v1/restock-plans", json={"multiplier_bp": 20000}, headers=shop).json()
    o = client.post(f"/v1/restock-plans/{saved['id']}/loan-offer", headers=shop).json()
    assert o["amount_paise"] == 25_000_00 and o["status"] == "offered" and o["lender"] == "sandbox"
    assert o["terms"] == {}  # the partner sets them; the screen shows its placeholders
    b = o["basis"]
    assert (b["total_paise"], b["cash_on_hand_paise"], b["shortfall_paise"], b["multiplier_bp"]) == \
        (40_000_00, 15_000_00, 25_000_00, 20000) and b["festival"] == "deepavali"
    # asking again gives the same offer, not a second one
    assert client.post(f"/v1/restock-plans/{saved['id']}/loan-offer", headers=shop).json()["id"] == o["id"]
    assert client.get(f"/v1/loan-offers/{o['id']}", headers=shop).json()["status"] == "offered"
    # nothing goes to the lender without the owner's tap
    assert services.adapters.get("lending", "sandbox").applications == []
    a = client.post(f"/v1/loan-offers/{o['id']}/approve", headers=shop).json()
    assert a["status"] == "applied" and a["decided_at"]
    assert services.adapters.get("lending", "sandbox").applications[-1]["ref"] == o["partner_ref"]
    assert client.post(f"/v1/loan-offers/{o['id']}/decline", headers=shop).json()["status"] == "applied"  # final
    with services.admin_sessions() as s:
        kinds = [e.kind for e in s.execute(select(AuditEvent).where(AuditEvent.subject == o["id"])).scalars()]
    assert kinds == ["loan_offered", "loan_approved"]
    assert client.get(f"/v1/loan-offers/{o['id']}", headers=login(client)).status_code == 404


def test_no_loan_without_cash_in_hand_or_a_shortfall(client, services, shop):  # noqa: F811
    client.patch("/v1/me", json={"clear_cash_on_hand": True}, headers=shop)
    p = plan(client, shop)
    assert p["cash_on_hand_paise"] is None and p["shortfall_paise"] is None  # never assumed
    saved = client.post("/v1/restock-plans", json={}, headers=shop).json()
    r = client.post(f"/v1/restock-plans/{saved['id']}/loan-offer", headers=shop)
    assert r.status_code == 409 and r.json()["detail"] == "Enter your cash in hand first."
    client.patch("/v1/me", json={"cash_on_hand_paise": 50_000_00}, headers=shop)
    assert plan(client, shop)["shortfall_paise"] == 0
    saved = client.post("/v1/restock-plans", json={}, headers=shop).json()
    r = client.post(f"/v1/restock-plans/{saved['id']}/loan-offer", headers=shop)
    assert r.status_code == 409 and "covers" in r.json()["detail"]


def test_decline(client, services, shop):  # noqa: F811
    saved = client.post("/v1/restock-plans", json={}, headers=shop).json()
    o = client.post(f"/v1/restock-plans/{saved['id']}/loan-offer", headers=shop).json()
    assert client.post(f"/v1/loan-offers/{o['id']}/decline", headers=shop).json()["status"] == "declined"
    assert services.adapters.get("lending", "sandbox").applications == []


def test_reorder_list(client, shop):  # noqa: F811
    r = client.get("/v1/plans/reorder", headers=shop).json()
    (bis,) = r["lines"]
    assert bis["name"] == "Biscuits" and bis["qty"] > 0 and bis["amount_paise"] == bis["qty"] * 8_50
    assert r["total_paise"] == bis["amount_paise"]


def test_next_festival():
    assert next_festival("deepavali", date(2026, 11, 1))[2] == date(2026, 11, 8)
    assert next_festival("deepavali", date(2026, 11, 14))[2] == date(2027, 10, 29)
    assert next_festival("pongal", date(2026, 11, 14))[2] == date(2027, 1, 14)
    assert next_festival("holi", date(2026, 11, 14)) is None
