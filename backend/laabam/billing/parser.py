"""Reading an order: transcript -> structured lines. Structure only; no prices are invented here.

The offline parser below is rule-based and handles the counter phrases in tests/data/phrases.yaml.
A model-backed parser returns the same ParsedLine shape; either way the price check in verify.py
runs afterwards, in code, against the transcript.

Forms handled: "rice 200", "rice 2 kg 120", "3 biscuit 30", "2 kg rice at 60", "oil 165 rupees",
"sugar 60 per kg 2 kg", "rice, oil" (no prices: learned later), corrections ("rice 200, no, 250",
"sugar 120 not 100"), fillers ("and", "then", "और", "அப்புறம்"), number words and paise.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ..lang import active_languages
from ..lang import lexicon as lx
from ..lang.numbers import NumberSpan, Token, currency_word, in_class, read_numbers, tokenize, unit_word

SEPARATOR_SYMBOLS = {",", ".", ";", ":", "!", "?", "+", "&", "|", "-", "।", "॥"}
PER_SYMBOLS = {"@", "/"}
SMALL_COUNT = Decimal(20)  # "biscuit 3 30": a first number this small, then another, is a count


@dataclass
class ParsedLine:
    item_text: str
    qty: Decimal | None = None  # in `unit`; None = not said
    unit: str | None = None  # kg | L | pc | pack
    price: Decimal | None = None  # rupees, as heard
    price_kind: str | None = None  # total | per_unit
    start: int = 0
    end: int = 0
    flags: list[str] = field(default_factory=list)

    def heard(self, transcript: str) -> str:
        return transcript[self.start:self.end].strip(" ,.;")


@dataclass
class _Item:
    """Tokens are classified into: num, unit, cur, per, fix (replace), not (keep previous), sep, word."""

    kind: str
    tok: Token | None = None
    num: NumberSpan | None = None
    unit: tuple[str, Decimal] | None = None

    @property
    def start(self) -> int:
        return self.num.start if self.num else self.tok.start

    @property
    def end(self) -> int:
        return self.num.end if self.num else self.tok.end


def _classify(transcript: str, language: str) -> list[_Item]:
    langs = active_languages(language)
    tokens = tokenize(transcript)
    numbers = {s.first: s for s in read_numbers(tokens, langs)}
    items: list[_Item] = []
    i = 0
    while i < len(tokens):
        if i in numbers:
            span = numbers[i]
            items.append(_Item("num", num=span))
            i = span.last + 1
            continue
        t = tokens[i]
        w = t.text
        if t.kind == "sym":
            if w == "₹":
                items.append(_Item("cur", t))
            elif w in PER_SYMBOLS:
                items.append(_Item("per", t))
            else:
                items.append(_Item("sep", t))
        elif (u := unit_word(w, langs)) is not None:
            items.append(_Item("unit", t, unit=u))
        elif currency_word(w, langs):
            items.append(_Item("cur", t))
        elif in_class(lx.PER_WORDS, w, langs):
            items.append(_Item("per", t))
        elif in_class(lx.NOT_WORDS, w, langs):
            items.append(_Item("not", t))
        elif in_class(lx.CORRECTION_WORDS, w, langs):
            items.append(_Item("fix", t))
        elif in_class(lx.FILLER_WORDS, w, langs) or in_class(lx.PAISE_WORDS, w, langs):
            items.append(_Item("sep", t))
        else:
            items.append(_Item("word", t))
        i += 1
    return items


@dataclass
class _Builder:
    words: list[str] = field(default_factory=list)
    qty: Decimal | None = None
    unit: str | None = None
    price: Decimal | None = None
    price_kind: str | None = None
    price_from_digits_small: bool = False
    start: int | None = None
    end: int = 0
    flags: list[str] = field(default_factory=list)
    replace_next: bool = False  # after "no": the next number replaces
    skip_next: bool = False  # after "not": the next number is the wrong one
    per_next: bool = False  # after "at"/"per": the next number is a rate
    cur_next: bool = False  # after "₹"/"rs": the next number is a price

    def touch(self, it: _Item) -> None:
        self.start = it.start if self.start is None else min(self.start, it.start)
        self.end = max(self.end, it.end)

    @property
    def empty(self) -> bool:
        return not self.words and self.qty is None and self.price is None

    @property
    def pending(self) -> bool:
        return self.replace_next or self.skip_next

    def line(self) -> ParsedLine:
        flags = list(self.flags)
        if not self.words:
            flags.append("item_needed")
        return ParsedLine(" ".join(self.words), self.qty, self.unit, self.price, self.price_kind,
                          self.start or 0, self.end, flags)


def parse_order(transcript: str, language: str = "en") -> list[ParsedLine]:
    items = _classify(transcript, language)
    lines: list[ParsedLine] = []
    builders: list[_Builder] = []
    cur = _Builder()

    def finish() -> None:
        nonlocal cur
        if not cur.empty:
            builders.append(cur)
        cur = _Builder()

    def reopen() -> None:
        nonlocal cur
        if cur.empty and builders:
            cur = builders.pop()

    def kind_at(k: int) -> str | None:
        return items[k].kind if 0 <= k < len(items) else None

    def next_real(k: int) -> int:
        while kind_at(k) == "sep":
            k += 1
        return k

    def set_price(value: Decimal, kind: str, it: _Item, small_digits: bool) -> None:
        if cur.skip_next:
            cur.skip_next = False
            cur.touch(it)
            return
        if cur.price is None or cur.replace_next:
            cur.price, cur.price_kind = value, kind
            cur.price_from_digits_small = small_digits
        elif cur.qty is None and cur.price_kind == "per_unit" and kind == "total" and value <= SMALL_COUNT:
            cur.qty, cur.unit = value, cur.unit or "pc"  # "biscuit 10 each 3"
        elif cur.qty is None and cur.price_from_digits_small and kind == "total" and cur.price_kind == "total":
            # "biscuit 3 30": the first number was a count
            cur.qty, cur.unit = cur.price, "pc"
            cur.price, cur.price_from_digits_small = value, False
        else:
            cur.price, cur.price_kind = value, kind
            if "two_prices" not in cur.flags:
                cur.flags.append("two_prices")
        cur.replace_next = False
        cur.touch(it)

    i = 0
    while i < len(items):
        it = items[i]
        k = it.kind
        if k == "sep":
            if cur.pending or cur.per_next or cur.cur_next:
                i += 1
                continue
            if cur.words and cur.price is None and cur.qty is None:
                # "rice, 200": a lone price after a comma still belongs to this item
                j = next_real(i + 1)
                if kind_at(j) == "num" and kind_at(j + 1) not in ("word", "unit"):
                    i += 1
                    continue
                if kind_at(j) == "cur" and kind_at(j + 1) == "num":
                    i += 1
                    continue
            if cur.words or cur.price is not None:
                finish()
            i += 1
        elif k == "fix":
            reopen()
            cur.replace_next = True
            i += 1
        elif k == "not":
            reopen()
            cur.skip_next = True
            i += 1
        elif k == "per":
            cur.per_next = True
            cur.touch(it)
            i += 1
        elif k == "cur":
            if cur.price is not None and not cur.pending and kind_at(i - 1) == "num":
                cur.touch(it)  # "200 rupees": marks the number just read
                if kind_at(i + 1) == "unit" and kind_at(i + 2) != "word":  # "60 rupees a kilo", "60 रुपये किलो"
                    cur.price_kind = "per_unit"
                    if cur.unit is None:
                        cur.unit = items[i + 1].unit[0]
                    cur.touch(items[i + 1])
                    i += 1
            else:
                cur.cur_next = True
                cur.touch(it)
            i += 1
        elif k == "unit":
            # a unit with no number before it: "sugar kg 50", "ஒரு கிலோ" handled as num + unit
            canon, factor = it.unit
            if cur.qty is None:
                cur.qty, cur.unit = factor, canon
            cur.touch(it)
            i += 1
        elif k == "num":
            value = it.num.value
            nxt = kind_at(i + 1)
            if cur.per_next or cur.cur_next:
                kind = "per_unit" if cur.per_next else "total"
                cur.per_next = cur.cur_next = False
                set_price(value, kind, it, False)
                if kind_at(i + 1) == "per" and kind_at(i + 2) == "unit":  # "at 60 per kg"
                    cur.price_kind = "per_unit"
                    cur.unit = cur.unit or items[i + 2].unit[0]
                    cur.touch(items[i + 2])
                    i += 3
                    continue
                if kind_at(i + 1) == "unit" and kind == "per_unit" and cur.qty is not None:
                    cur.touch(items[i + 1])
                    i += 2
                    continue
                i += 1
                continue
            if nxt == "unit":
                canon, factor = items[i + 1].unit
                qty = value * factor
                if cur.words and cur.price is not None and not cur.pending and kind_at(i + 2) == "word":
                    finish()  # "rice 200 2 kg sugar": the quantity starts the next line
                if cur.skip_next:
                    cur.skip_next = False
                elif cur.qty is None or cur.replace_next:
                    cur.qty, cur.unit = qty, canon
                    cur.replace_next = False
                elif cur.price is None:
                    # "sugar 1 kg 50 per kg"? a second quantity with no price: keep the first, flag it
                    cur.flags.append("two_quantities")
                cur.touch(it)
                cur.touch(items[i + 1])
                i += 2
                continue
            if nxt == "per" and kind_at(i + 2) == "unit":  # "60 per kg"
                set_price(value, "per_unit", it, False)
                cur.unit = cur.unit or items[i + 2].unit[0]
                cur.touch(items[i + 2])
                i += 3
                continue
            if nxt == "per" and not cur.per_next:  # "10 each"
                set_price(value, "per_unit", it, False)
                cur.touch(items[i + 1])
                i += 2
                continue
            if not cur.words and cur.price is None and nxt == "word" and value <= SMALL_COUNT and cur.qty is None:
                cur.qty, cur.unit = value, "pc"  # "3 biscuit 30"
                cur.touch(it)
                i += 1
                continue
            if cur.words and cur.price is not None and not cur.pending and nxt == "word":
                finish()  # "rice 200 3 biscuit 30"
                continue
            small = (not it.num.words) and value <= SMALL_COUNT and value == value.to_integral()
            set_price(value, "total", it, small)
            i += 1
        else:  # word
            if cur.words and (cur.price is not None or cur.qty is not None) and not cur.pending:
                finish()
            cur.words.append(transcript[it.tok.start:it.tok.end])
            cur.touch(it)
            i += 1
    finish()

    for b in builders:
        if b.price_kind == "per_unit" and b.qty is None and b.unit is not None:
            b.qty = Decimal(1)
        lines.append(b.line())
    return lines
