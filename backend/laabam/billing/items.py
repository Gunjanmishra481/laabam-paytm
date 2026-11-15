"""Matching what the owner said to items: exact alias -> normalised name -> fuzzy -> new item."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..ids import new_id
from ..lang.numbers import normalise
from ..models import Item

FUZZY_THRESHOLD = 0.85
FUZZY_MIN_LENGTH = 4
_PUNCT = re.compile(r"[^\w\sऀ-ॿ஀-௿]")


def item_key(text: str) -> str:
    """'Biscuits ' -> 'biscuit'; 'Toor  Dal' -> 'toor dal'. Plurals only for Latin words."""
    words = _PUNCT.sub(" ", normalise(text)).split()
    out = []
    for w in words:
        if w.isascii() and len(w) > 3:
            if w.endswith("oes"):
                w = w[:-2]
            elif w.endswith("s") and not w.endswith("ss"):
                w = w[:-1]
        out.append(w)
    return " ".join(out)


def display_name(text: str) -> str:
    text = " ".join(text.split())
    return text[:1].upper() + text[1:] if text.isascii() else text


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def find_item(items: list[Item], text: str) -> tuple[Item | None, str]:
    """Returns (item, how) where how is exact | fuzzy | none."""
    key = item_key(text)
    if not key:
        return None, "none"
    for it in items:
        if key == it.key or key in (it.aliases or []):
            return it, "exact"
    if len(key) >= FUZZY_MIN_LENGTH:
        best, score = None, 0.0
        for it in items:
            for alias in {it.key, *(it.aliases or [])}:
                r = _ratio(key, alias)
                if r > score:
                    best, score = it, r
        if best is not None and score >= FUZZY_THRESHOLD:
            return best, "fuzzy"
    return None, "none"


def merchant_items(s: Session, merchant_id: str) -> list[Item]:
    return list(s.execute(select(Item).where(Item.merchant_id == merchant_id).order_by(Item.created_at)).scalars())


def resolve_item(s: Session, merchant_id: str, text: str, items: list[Item] | None = None) -> Item | None:
    """The item for `text`, creating it on first mention. None if the text is empty."""
    items = merchant_items(s, merchant_id) if items is None else items
    found, _ = find_item(items, text)
    if found is not None:
        return found
    key = item_key(text)
    if not key:
        return None
    item = Item(id=new_id("item"), merchant_id=merchant_id, name=display_name(text), key=key, aliases=[key])
    try:
        with s.begin_nested():
            s.add(item)
    except IntegrityError:  # created by a concurrent request
        return s.execute(select(Item).where(Item.merchant_id == merchant_id, Item.key == key)).scalar_one()
    items.append(item)
    return item
