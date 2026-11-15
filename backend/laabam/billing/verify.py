"""The price check: every spoken price must appear in the words the line came from.

Independent of the parser. Whatever produced the line (rules or a model), the price is accepted
only if read_numbers finds that exact value in the line's heard text, and the heard text is
really part of the transcript. Otherwise the line is flagged and the bill can't be confirmed.
"""
from __future__ import annotations

from decimal import Decimal

from ..lang import active_languages
from ..lang.numbers import normalise, values_in

PRICE_NOT_HEARD = "price_not_heard"


def heard_in_transcript(heard_text: str, transcript: str) -> bool:
    h = " ".join(normalise(heard_text).split())
    t = " ".join(normalise(transcript).split())
    return bool(h) and h in t


def price_was_heard(price_paise: int, heard_text: str, transcript: str, language: str) -> bool:
    if not heard_in_transcript(heard_text, transcript):
        return False
    rupees = Decimal(price_paise) / 100
    return rupees in values_in(heard_text, active_languages(language))
