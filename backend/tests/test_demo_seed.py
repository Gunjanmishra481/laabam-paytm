"""The Demo Kirana month (BUILD_PLAN Phase 4): Laabam finds every planted finding, and the oil story's numbers
come out of the data: ₹148 -> ₹156, profit a litre ₹17 -> ₹9, ₹368 less this week, Kumar Traders at ₹149."""
from datetime import datetime, time, timedelta

import pytest
from sqlalchemy import select

from laabam import alerts
from laabam.clock import IST, ist_day
from laabam.demo_seed import seed_demo_month
from laabam.models import Merchant, Timer, utcnow
from tests.conftest import login


def end_of_today() -> datetime:
    return datetime.combine(ist_day(utcnow()), time(23, 59), IST)


@pytest.fixture
def shop(client, services):
    """The demo shop with its month, seeded as if the day were over (so every figure is fixed)."""
    h = login(client)
    mid = client.get("/v1/me", headers=h).json()["id"]
    with services.admin_sessions.begin() as s:
        seed_demo_month(s, s.get(Merchant, mid), now=end_of_today())
        alerts.refresh(s, mid, services.alerts.th)
    return h


def by_kind(client, h):
    out = {}
    for a in client.get("/v1/alerts", headers=h).json():
        out.setdefault(a["kind"], []).append(a)
    return out


def confirm_balaji_bill(client, services, h):
    """The demo's step 3: the owner reads Sri Balaji's new bill (sample photo) and confirms it."""
    p = client.post("/v1/purchases/photo", content=b"", headers={**h, "Content-Type": "image/jpeg",
                                                                  "X-Laabam-Sample": "balaji"}).json()
    assert p["bill_no"] == "4471" and p["distributor_name"] == "Sri Balaji Agencies"
    assert p["bill_date"] == (ist_day(utcnow()) - timedelta(days=6)).isoformat()
    # every line matched by itself: the names were learned from the earlier bills
    assert all(not ln["flags"] for ln in p["lines"]) and p["can_confirm"], p["lines"]
    assert client.post(f"/v1/purchases/{p['id']}/confirm", headers=h).status_code == 200
    with services.admin_sessions() as s:  # the scan was queued for the worker
        assert s.execute(select(Timer).where(Timer.kind == "alert_scan", Timer.status == "pending")).first()
    services.timers.tick()
    return p


def test_planted_findings_before_the_new_bill(client, shop):
    found = by_kind(client, shop)
    assert set(found) == {"running_out", "slow", "low_margin"}  # nothing else, and no oil rise yet

    (bis,) = found["running_out"]
    f = bis["figures"]
    assert bis["item_name"] == "Biscuits" and f["unit_label"] == "pack"
    assert f["per_day_milli"] == 14_000 and f["left_milli"] == 40_000  # about 14 a day, about 40 left
    assert f["out_day"] == (ist_day(utcnow()) + timedelta(days=2)).isoformat()

    slow = {a["item_name"]: a["figures"] for a in found["slow"]}
    assert slow["Health drink 500 g"]["days_quiet"] == 21 and slow["Health drink 500 g"]["value_paise"] == 1_840_00
    assert slow["Basmati rice 5 kg"]["value_paise"] == 4_360_00 and slow["Basmati rice 5 kg"]["last_sale_day"] is None
    assert sum(f["value_paise"] for f in slow.values()) == 6_200_00  # ₹6,200 of slow stock

    (milk,) = found["low_margin"]
    assert milk["item_name"] == "Milk" and milk["figures"]["margin_bp"] == 370  # 3.7%
    assert milk["figures"]["price_paise"] == 27_00 and milk["figures"]["cost"]["usual_cost_paise"] == 26_00


def test_profit_shows_what_it_cannot_know(client, shop):
    rep = client.get("/v1/profit?period=week", headers=shop).json()
    assert sorted(i["name"] for i in rep["no_cost_items"]) == ["Bread", "Chips", "Curd", "Eggs", "Maggi", "Paneer",
                                                               "Salt"]
    assert rep["estimated"] and 8000 <= rep["coverage_bp"] < 10000  # UPI payments with no voice bill
    assert len(rep["bars"]) == 7 and all(b["profit_paise"] > 0 for b in rep["bars"])
    oil = next(i for i in rep["items"] if i["name"] == "Sunflower oil")
    assert oil["margin_bp"] == 1030 and oil["trend"] is None  # ₹17 on ₹165, before the new bill


def test_the_oil_story(client, services, shop):
    """A price rise, caught: the owner confirms Sri Balaji's bill and Laabam finds what it costs them."""
    confirm_balaji_bill(client, services, shop)
    found = by_kind(client, shop)
    (rise,) = found["price_rise"]
    f = rise["figures"]
    assert rise["item_name"] == "Sunflower oil" and f["unit_label"] == "litre"
    assert f["old"]["rate_paise"] == 148_00 and f["old"]["bill_no"] == "4302" and f["old"]["position"] == 6
    assert f["new"]["rate_paise"] == 156_00 and f["new"]["bill_no"] == "4471" and f["new"]["position"] == 4
    assert f["rise_paise"] == 8_00 and f["rise_bp"] == 541
    assert f["prev_profit_per_unit_paise"] == 17_00 and f["profit_per_unit_paise"] == 9_00
    assert f["sold_this_week_milli"] == 46_000 and f["lost_this_week_paise"] == 368_00
    assert "low_margin" not in {a["kind"] for a in found.get("low_margin", []) if a["item_name"] == "Sunflower oil"}
    assert [a["item_name"] for a in found["low_margin"]] == ["Milk"]  # oil's fall is told once, as the rise

    oil = client.get(f"/v1/profit/items/{rise['item_id']}?period=week", headers=shop).json()
    assert oil["profit_per_unit_paise"] == 9_00 and oil["prev_profit_per_unit_paise"] == 17_00
    assert oil["margin_bp"] == 545 and oil["lost_to_rise_paise"] == 368_00  # 5.5%
    assert oil["sold"]["qty_milli"] == 46_000 and oil["sold"]["bills"] == 46 and oil["said_prices_paise"] == [165_00]
    assert oil["cheaper"]["distributor"] == "Kumar Traders" and oil["cheaper"]["rate_paise"] == 149_00
    week = client.get("/v1/profit?period=week", headers=shop).json()
    assert next(i for i in week["items"] if i["name"] == "Sunflower oil")["trend"] == "down"


def test_alerts_got_it_and_resolved(client, services, shop):
    a = by_kind(client, shop)["low_margin"][0]
    assert client.post(f"/v1/alerts/{a['id']}/seen", headers=shop).json()["status"] == "seen"
    assert "low_margin" not in by_kind(client, shop)
    # a later scan doesn't bring a seen alert back
    with services.admin_sessions.begin() as s:
        alerts.refresh(s, client.get("/v1/me", headers=shop).json()["id"], services.alerts.th)
    assert "low_margin" not in by_kind(client, shop)
    assert client.post("/v1/alerts/alt_nope/seen", headers=shop).status_code == 404
    other = login(client)
    assert client.post(f"/v1/alerts/{a['id']}/seen", headers=other).status_code == 404


def test_sandbox_reset_seeds_the_month(client):
    h = login(client, history=True)
    me = client.get("/v1/me", headers=h).json()
    assert me["sample_data"] and me["cash_on_hand_paise"] == 15_000_00
    assert {a["kind"] for a in client.get("/v1/alerts", headers=h).json()} >= {"slow", "low_margin"}
    assert len(client.get("/v1/purchases", headers=h).json()) >= 10


def test_nightly_scan_reschedules_itself(client, services):
    with services.admin_sessions() as s:
        t = s.execute(select(Timer).where(Timer.kind == "alert_nightly", Timer.status == "pending")).scalar_one()
        tid, due = t.id, t.due_at
    assert due.astimezone(IST).strftime("%H:%M") == "02:00"
    assert services.timers.run(tid)
    with services.admin_sessions() as s:
        nxt = s.execute(select(Timer).where(Timer.kind == "alert_nightly", Timer.status == "pending")).scalar_one()
        assert nxt.id != tid and nxt.due_at > utcnow()
