"""Profit maths (BUILD_PLAN §5.3): one method per line, estimates marked, unknown costs never guessed."""
from tests.conftest import login


def paid(client, h, text):
    b = client.post("/v1/bills/text", json={"text": text}, headers=h).json()
    b = client.post(f"/v1/bills/{b['id']}/confirm", headers=h).json()
    assert client.post(f"/sandbox/qr/{b['qr']['order_id']}/pay").json()["result"] == "succeeded"
    return b


def confirmed_printed_bill(client, h):
    """Sri Lakshmi, 8 Nov: oil 1 L pouches at ₹148, sugar loose at ₹42 a kg (and two lines to name)."""
    p = client.post("/v1/purchases/photo", content=b"", headers={**h, "Content-Type": "image/jpeg",
                                                                  "X-Laabam-Sample": "printed"}).json()
    for ln in p["lines"]:
        if "item_ask" in ln["flags"]:
            client.patch(f"/v1/purchases/{p['id']}/lines/{ln['id']}", json={"accept_suggestion": True}, headers=h)
        elif "item_new" in ln["flags"]:
            client.patch(f"/v1/purchases/{p['id']}/lines/{ln['id']}", json={"new_item_name": ln["name_hint"]},
                         headers=h)
    r = client.post(f"/v1/purchases/{p['id']}/confirm", headers=h)
    assert r.status_code == 200, r.text


def test_profit_methods_per_line(client):
    h = login(client)
    paid(client, h, "oil 165, sugar 100")  # teaches the items before the bill names them
    confirmed_printed_bill(client, h)
    paid(client, h, "oil 165, sugar 2 kg 100, tea 50")

    rep = client.get("/v1/profit?period=today", headers=h).json()
    items = {i["name"].lower(): i for i in rep["items"]}
    # oil, said without a quantity: 165 x (1 - 148/165) = 17 a line, an estimate ("about")
    assert items["oil"]["methods"] == {"margin": 2} and items["oil"]["estimated"]
    assert items["oil"]["profit_paise"] == 2 * 1700
    # sugar "2 kg 100": exact, 100 - 2 x 42 = 16. "sugar 100" (no quantity) is estimated from the per-kg
    # price the owner said later (50 a kg): 100 x (1 - 42/50) = 16
    assert items["sugar"]["methods"] == {"margin": 1, "unit": 1}
    assert items["sugar"]["profit_paise"] == 3200 and items["sugar"]["estimated"]
    # tea has no cost from any bill: listed as no cost, never guessed
    assert [i["name"].lower() for i in rep["no_cost_items"]] == ["tea"]
    assert rep["estimated"] and rep["profit_paise"] == 3400 + 3200
    assert rep["sales_paise"] == 26500 + 31500 and rep["coverage_bp"] == 10000


def test_item_report_and_its_sources(client):
    h = login(client)
    paid(client, h, "oil 165")
    confirmed_printed_bill(client, h)
    paid(client, h, "oil 165")
    oil = next(i for i in client.get("/v1/items", headers=h).json() if i["name"].lower() == "oil")
    r = client.get(f"/v1/profit/items/{oil['id']}?period=week", headers=h).json()
    assert r["unit_label"] == "litre" and r["price"]["paise"] == 16500
    assert r["cost"]["rate_paise"] == 14800 and r["cost"]["distributor"] == "Sri Lakshmi Agencies"
    assert r["cost"]["position"] == 1 and r["profit_per_unit_paise"] == 1700
    assert r["margin_bp"] == 1030  # 17 / 165
    assert r["said_prices_paise"] == [16500] and r["sold"]["bills"] == 2
    bills = client.get(f"/v1/profit/items/{oil['id']}/bills?period=week", headers=h).json()
    assert len(bills) == 2 and {b["method"] for b in bills} == {"margin"}
    assert client.get("/v1/profit?period=year", headers=h).status_code == 422
