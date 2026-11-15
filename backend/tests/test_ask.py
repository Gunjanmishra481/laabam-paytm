"""Ask (BUILD_PLAN §5.7): answers computed in code, worded by templates (or a model, guarded), with sources.
Drafted messages are for the owner to send. Runs on the seeded Demo Kirana month with Sri Balaji's bill."""
from datetime import timedelta

from laabam.ask import Asker, classify, day_text
from laabam.clock import ist_day
from laabam.models import Merchant, utcnow
from tests.test_demo_seed import confirm_balaji_bill, shop  # noqa: F401 (fixture)


def ask(client, h, text="", **kw):
    r = client.post("/v1/ask", json={"text": text, **kw}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def oil_id(client, h):
    return next(i["id"] for i in client.get("/v1/items", headers=h).json() if i["name"] == "Sunflower oil")


def test_why_is_profit_down(client, services, shop):  # noqa: F811
    confirm_balaji_bill(client, services, shop)
    a = ask(client, shop, "Why is profit down this week?")
    assert a["intent"] == "why_down" and a["phrased_by"] == "template"
    assert a["text"] == ("Mostly sunflower oil. It now costs ₹156 a litre, up from ₹148, so you made about ₹368 "
                         "less on it "
                         "this week.")
    assert [x["t"] for x in a["segments"] if x["b"]] == ["₹156", "₹368"]  # bold as drawn
    assert "".join(x["t"] for x in a["segments"]) == a["text"]
    line, voice = a["sources"]
    assert line["kind"] == "bill_line" and line["bill_no"] == "4471" and line["position"] == 4
    assert line["distributor"] == "Sri Balaji Agencies"
    assert voice == {"kind": "voice_bills", "item_id": a["item_id"], "count": 46}


def test_draft_to_the_distributor(client, services, shop):  # noqa: F811
    confirm_balaji_bill(client, services, shop)
    oil = oil_id(client, shop)
    a = ask(client, shop, "Write to Sri Balaji about it", item_id=oil)
    old_day = day_text(ist_day(utcnow()) - timedelta(days=15), "en")
    assert a["intent"] == "draft" and a["draft"]["to"] == "Sri Balaji Agencies"
    assert a["draft"]["text"] == (f"Namaste. Sunflower oil 1 L is ₹156 on bill 4471, up from ₹148 on {old_day}. "
                                  "Another distributor billed ₹149. Can you do ₹149?")
    assert a["text"] == "Here’s a message to Sri Balaji Agencies. You send it."
    assert {s["bill_no"] for s in a["sources"]} == {"4471", "4302", "K-0918"}
    # from Item detail's "Draft a message": the intent and the item, no words needed
    assert ask(client, shop, intent="draft", item_id=oil)["draft"]["text"] == a["draft"]["text"]


def test_reorder_and_best_item(client, shop):  # noqa: F811
    a = ask(client, shop, "What should I reorder?")
    assert a["intent"] == "reorder" and a["text"].startswith("Reorder Biscuits soon. Biscuits will run out by ")
    assert a["sources"][0]["name"] == "Biscuits"
    b = ask(client, shop, "Best item this month?")
    rep = client.get("/v1/profit?period=month", headers=shop).json()
    assert b["intent"] == "best" and b["text"].startswith(f"{rep['items'][0]['name']} earned the most this month")
    assert b["sources"][0]["kind"] == "voice_bills" and b["sources"][0]["count"] > 0


def test_no_draft_without_a_rise_and_unknown_questions(client, shop):  # noqa: F811
    assert ask(client, shop, "Write to them about it")["draft"] is None  # no rise yet
    a = ask(client, shop, "Hello there")
    assert a["intent"] == "unknown" and "what to reorder" in a["text"]


def test_questions_in_tamil_and_hindi(client, services, shop):  # noqa: F811
    confirm_balaji_bill(client, services, shop)
    client.patch("/v1/me", json={"language": "ta"}, headers=shop)
    a = ask(client, shop, "இந்த வாரம் லாபம் ஏன் குறைந்தது?")
    assert a["intent"] == "why_down" and a["language"] == "ta" and "₹368" in a["text"] and "லிட்டருக்கு" in a["text"]
    client.patch("/v1/me", json={"language": "hi"}, headers=shop)
    a = ask(client, shop, "क्या मँगाना है?")
    assert a["intent"] == "reorder" and a["text"].endswith("तक खत्म हो जाएगा।")


def test_classify():
    assert classify("why is profit down") == "why_down"
    assert classify("what should I reorder?") == "reorder"
    assert classify("Write to Sri Balaji about it") == "draft"
    assert classify("best item this month?") == "best"
    assert classify("ஏன் லாபம் குறைவு") == "why_down" and classify("सबसे अच्छा सामान") == "best"
    assert classify("namaste") is None


def test_model_text_with_a_digit_falls_back_to_the_template(client, services, shop):  # noqa: F811
    """No figure invented by a model: model text is a template; any digit in it means the template is used."""
    confirm_balaji_bill(client, services, shop)
    mid = client.get("/v1/me", headers=shop).json()["id"]
    seen = []

    def phraser(intent, lang, template, names):
        seen.append((intent, names))
        return phrasing

    asker = Asker(services.alerts.th, phraser)
    with services.admin_sessions() as s:
        m = s.get(Merchant, mid)
        phrasing = "Oil went up by 8 rupees, so profit fell."  # a digit: dropped
        a = asker.ask(s, m, "why is profit down")
        assert a["phrased_by"] == "template" and a["text"].startswith("Mostly sunflower oil.")
        phrasing = "{ITEM} costs more now: {NEW}, was {OLD}. That took {LOST} this week."
        a = asker.ask(s, m, "why is profit down")
        assert a["phrased_by"] == "model" and a["text"] == "sunflower oil costs more now: ₹156, was ₹148. That " \
                                                           "took ₹368 this week."
        phrasing = "{ITEM} is up {PERCENT}."  # a placeholder code didn't provide: dropped
        assert asker.ask(s, m, "why is profit down")["phrased_by"] == "template"
    assert seen[0] == ("why_down", {"ITEM", "NEW", "PER", "OLD", "LOST", "ABOUT"})


def test_listen_and_spoken_questions_without_a_speech_service(client, shop):  # noqa: F811
    assert client.post("/v1/speak", json={"text": "Mostly sunflower oil."}, headers=shop).status_code == 204
    r = client.post("/v1/ask/voice", content=b"\x1a\x45\xdf\xa3" + b"\x00" * 4000,
                    headers={**shop, "Content-Type": "audio/webm"})
    assert r.status_code == 503  # no speech service: never a guess
