"""Evening summary (BUILD_PLAN §5.7): figures in code, words from a template (a model's draft only if it has no
digits), spoken on the Soundbox at closing time, rescheduled daily and moved with the closing time."""
from datetime import timedelta

from sqlalchemy import select

from laabam.ask import WEEKDAYS
from laabam.clock import IST, ist_day
from laabam.models import Merchant, Timer, utcnow
from laabam.money import rupees
from laabam.summary import build
from tests.test_demo_seed import confirm_balaji_bill, shop  # noqa: F401 (fixture)


def summary_timer(services, mid):
    with services.admin_sessions() as s:
        return s.execute(select(Timer).where(Timer.kind == "evening_summary", Timer.subject == mid,
                                             Timer.status == "pending")).scalar_one()


def test_summary_words_and_figures(client, services, shop):  # noqa: F811
    confirm_balaji_bill(client, services, shop)
    assert client.get("/v1/summaries/latest", headers=shop).status_code == 404
    rep = client.get("/v1/profit?period=today", headers=shop).json()
    r = client.post("/v1/summaries", headers=shop).json()
    f = r["figures"]
    profit = (rep["profit_paise"] + 50) // 100 * 100
    assert f["sales_paise"] == rep["sales_paise"] and f["profit_paise"] == profit and f["estimated"]
    assert f["top"]["name"] == rep["items"][0]["name"]
    assert [w["kind"] for w in f["watch"]] == ["price_rise", "running_out"]
    out_day = ist_day(utcnow()) + timedelta(days=2)
    assert r["text"] == (f"Today {rupees(rep['sales_paise'])} came in and profit was about {rupees(profit)}. "
                         f"{rep['items'][0]['name']} earned the most. Sunflower oil now costs ₹8 more a litre. "
                         f"Biscuits will run out by {WEEKDAYS['en'][out_day.weekday()]}, so reorder tomorrow.")
    assert "".join(x["t"] for x in f["segments"]) == r["text"] and r["phrased_by"] == "template"
    assert client.get("/v1/summaries/latest", headers=shop).json()["id"] == r["id"]
    # asking again rebuilds today's, it doesn't add another
    assert client.post("/v1/summaries", headers=shop).json()["id"] == r["id"]


def test_closing_time_plays_it_on_the_soundbox(client, services, shop):  # noqa: F811
    mid = client.get("/v1/me", headers=shop).json()["id"]
    t = summary_timer(services, mid)
    assert t.due_at.astimezone(IST).strftime("%H:%M") == "21:00"
    assert services.timers.run(t.id)
    latest = client.get("/v1/summaries/latest", headers=shop).json()
    assert latest["played_at"] and latest["text"].startswith("Today ₹")
    said = [e for e in client.get(f"/sandbox/soundbox/{mid}").json()["events"] if e["kind"] == "announce"]
    assert said[-1]["text"] == latest["text"]
    nxt = summary_timer(services, mid)  # tomorrow's
    assert nxt.id != t.id and nxt.due_at.astimezone(IST).strftime("%H:%M") == "21:00"
    # the closing time moves it
    client.patch("/v1/me", json={"closing_time": "20:30"}, headers=shop)
    assert summary_timer(services, mid).due_at.astimezone(IST).strftime("%H:%M") == "20:30"


def test_model_text_with_a_digit_falls_back(client, services, shop):  # noqa: F811
    mid = client.get("/v1/me", headers=shop).json()["id"]
    with services.admin_sessions.begin() as s:
        m = s.get(Merchant, mid)
        row = build(s, m, phraser=lambda intent, lang, tpl, names: "Sales were 8400 today.")
        assert row.phrased_by == "template" and row.text.startswith("Today ₹")
        names = []
        row = build(s, m, phraser=lambda intent, lang, tpl, n: names.append(n)
                    or "Takings {SALES_0}, about {PROFIT_0}.")
        assert row.phrased_by == "model" and row.text.startswith("Takings ₹") and "SALES_0" in names[0]


def test_summary_in_tamil_and_an_empty_day(client, services, shop):  # noqa: F811
    client.patch("/v1/me", json={"language": "ta"}, headers=shop)
    r = client.post("/v1/summaries", headers=shop).json()
    assert r["language"] == "ta" and r["text"].startswith("இன்று ₹") and "லாபம் சுமார்" in r["text"]
    from tests.conftest import login
    h = login(client)  # a shop with nothing sold
    assert client.post("/v1/summaries", headers=h).json()["text"] == "No paid bills today."


def test_demo_plays_the_summary_now(client, shop):  # noqa: F811
    mid = client.get("/v1/me", headers=shop).json()["id"]
    r = client.post(f"/sandbox/summary/{mid}").json()
    assert r["played_at"] and client.get("/v1/summaries/latest", headers=shop).json()["id"] == r["id"]
    assert client.post("/sandbox/summary/mer_nope").status_code == 404
