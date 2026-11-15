"""The phrase corpus (tests/data/phrases.yaml) and number reading."""
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from laabam.billing.parser import parse_order
from laabam.billing.verify import price_was_heard
from laabam.lang import active_languages
from laabam.lang.numbers import values_in

PHRASES = yaml.safe_load((Path(__file__).parent / "data" / "phrases.yaml").read_text(encoding="utf-8"))


def test_corpus_is_big_enough():
    assert len(PHRASES) >= 30
    assert {p["lang"] for p in PHRASES} == {"en", "ta", "hi"}


def _dec(v):
    return None if v is None else Decimal(str(v))


@pytest.mark.parametrize("case", PHRASES, ids=[p["text"] for p in PHRASES])
def test_phrase(case):
    got = parse_order(case["text"], case["lang"])
    want = case["lines"]
    assert len(got) == len(want), [g.item_text for g in got]
    for g, w in zip(got, want, strict=True):
        assert g.item_text.lower() == w.get("item", ""), g
        assert _dec(g.qty) == _dec(w.get("qty")), g
        assert g.unit == w.get("unit"), g
        assert _dec(g.price) == _dec(w.get("price")), g
        assert g.price_kind == (w.get("kind", "total") if "price" in w else None), g
        assert g.flags == w.get("flags", []), g
        # every price the parser read is found again by the independent check
        if g.price is not None:
            paise = int(g.price * 100)
            assert price_was_heard(paise, g.heard(case["text"]), case["text"], case["lang"]), g


@pytest.mark.parametrize("text,lang,values", [
    ("two hundred and fifty", "en", {250}),
    ("three thirty", "en", {3, 30}),
    ("two thousand five hundred", "en", {2500}),
    ("a hundred", "en", {100}),
    ("साढ़े तीन सौ", "hi", {350}),
    ("सवा सौ", "hi", {125}),
    ("पौने दो सौ", "hi", {175}),
    ("डेढ़ हज़ार", "hi", {1500}),
    ("चीनी दे दो", "hi", set()),
    ("ஆயிரத்து இருநூறு", "ta", {1200}),
    ("தொண்ணூற்று ஒன்பது", "ta", {99}),
    ("௫௦ ரூபாய்", "ta", {50}),
    ("sugar 12.50", "en", {Decimal("12.50")}),
])
def test_number_reading(text, lang, values):
    assert values_in(text, active_languages(lang)) == {Decimal(v) for v in values}


def test_languages_are_scoped():
    # romanised Hindi "do" is a number only when the shop speaks Hindi
    assert values_in("do", active_languages("en")) == set()
    assert values_in("do", active_languages("hi")) == {Decimal(2)}
    with pytest.raises(ValueError):
        active_languages("fr")
