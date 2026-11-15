"""Tokenising a transcript and reading the numbers in it (digits, or words in English, Tamil, Hindi).

The bill parser and the price check both use this, so "a price appears in the transcript" means
exactly "read_numbers finds that value in the words the line came from".
Spans are character offsets into the original text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from . import lexicon as lx

# Devanagari and Tamil digits -> ASCII. One code point each, so offsets are unchanged.
_NATIVE_DIGITS = {**{0x0966 + i: str(i) for i in range(10)}, **{0x0BE6 + i: str(i) for i in range(10)}}

# Letters plus the Devanagari/Tamil vowel signs and viramas (Python's \w leaves the marks out).
_LETTER = r"[^\W\d_]|[ऀ-ॣ॰-ॿ஀-௥௰-௿‌‍]"
_TOKEN = re.compile(
    rf"(?P<num>\d+(?:,\d{{2,3}})*(?:\.\d+)?)|(?P<word>(?:{_LETTER})+)|(?P<sym>[₹@/,.;:!?+&|\-।॥])"
)


@dataclass(frozen=True)
class Token:
    kind: str  # num | word | sym
    text: str  # lower-cased; digits normalised to ASCII
    start: int
    end: int


@dataclass(frozen=True)
class NumberSpan:
    value: Decimal
    start: int
    end: int
    words: bool  # said in words rather than digits
    first: int  # index of the first token in the token list
    last: int  # index of the last token


def normalise(text: str) -> str:
    """Lower-case Latin letters and map native digits to ASCII, keeping every offset the same."""
    out = []
    for ch in text.translate(_NATIVE_DIGITS):
        low = ch.lower()
        out.append(low if len(low) == 1 else ch)
    return "".join(out)


def tokenize(text: str) -> list[Token]:
    norm = normalise(text)
    return [Token(m.lastgroup, m.group(), m.start(), m.end()) for m in _TOKEN.finditer(norm)]


def _lookup(table: dict[str, dict], word: str, langs: tuple[str, ...]):
    for lang in langs:
        hit = table.get(lang, {}).get(word)
        if hit is not None:
            return hit
    return None


def number_word(word: str, langs: tuple[str, ...]):
    return _lookup(lx.NUMBER_WORDS, word, langs)


def in_class(table: dict[str, set[str]], word: str, langs: tuple[str, ...]) -> bool:
    return any(word in table.get(lang, ()) for lang in langs)


def unit_word(word: str, langs: tuple[str, ...]):
    hit = _lookup(lx.UNIT_WORDS, word, langs)
    if hit is not None:
        return hit
    for lang in langs:
        if lang in lx.PREFIX_MATCH_LANGS:
            for w, unit in lx.UNIT_WORDS[lang].items():
                if word.startswith(w):
                    return unit
    return None


def currency_word(word: str, langs: tuple[str, ...]) -> bool:
    if word == "₹" or in_class(lx.CURRENCY_WORDS, word, langs):
        return True
    return any(word.startswith(w) for lang in langs if lang in lx.PREFIX_MATCH_LANGS
               for w in lx.CURRENCY_WORDS[lang] if len(w) > 2)


class _Group:
    """Reads one number said in words: 'two hundred and fifty', 'साढ़े तीन सौ', 'இருநூற்று ஐம்பது'."""

    def __init__(self) -> None:
        self.total = Decimal(0)
        self.current = Decimal(0)
        self.last: Decimal | None = None  # place value of the last part, so "three thirty" stays two numbers
        self.mark = Decimal(0)
        self.started = False

    def accepts(self, value: Decimal, kind: str) -> bool:
        if kind == "mark":  # saadhe / sawa / paune only start a number
            return not self.started
        if not self.started:
            return True
        if kind == "mult":
            return self.current < value or (self.current == 0 and self.last is not None and self.last > value)
        return self.last is None or value < self.last

    def add(self, value: Decimal, kind: str) -> None:
        self.started = True
        if kind == "mark":
            self.mark = value
            return
        if kind == "mult":
            base = self.current if self.current else Decimal(1)
            base += self.mark
            self.mark = Decimal(0)
            if value >= 1000:
                self.total += base * value
                self.current = Decimal(0)
            else:
                self.current = base * value
            self.last = value
            return
        self.current += value + self.mark
        self.mark = Decimal(0)
        self.last = value if value >= 1 else Decimal("0.5")

    @property
    def value(self) -> Decimal:
        return self.total + self.current + self.mark


def read_numbers(tokens: list[Token], langs: tuple[str, ...]) -> list[NumberSpan]:
    """Every number in the token list, in order. Adjacent rupees + paise ('12 rupees 50 paise') merge into 12.50."""
    spans: list[NumberSpan] = []
    i, n = 0, len(tokens)
    while i < n:
        tok = tokens[i]
        if tok.kind == "num":
            value = Decimal(tok.text.replace(",", ""))
            last = i
            nxt = number_word(tokens[i + 1].text, langs) if i + 1 < n and tokens[i + 1].kind == "word" else None
            if nxt and nxt[1] == "mult":  # "2 hundred", "5 ஆயிரம்"
                value *= nxt[0]
                last = i + 1
            spans.append(NumberSpan(value, tok.start, tokens[last].end, False, i, last))
            i = last + 1
            continue
        hit = number_word(tok.text, langs) if tok.kind == "word" else None
        if hit and i > 0 and (tokens[i - 1].text, tok.text) in lx.GIVE_PAIRS:
            hit = None
        if not hit:
            i += 1
            continue
        group, first, last = _Group(), i, i
        j = i
        while j < n:
            t = tokens[j]
            w = number_word(t.text, langs) if t.kind == "word" else None
            if w and group.accepts(*w):
                group.add(*w)
                last = j
                j += 1
                continue
            if t.kind == "word" and group.started and in_class(lx.NUMBER_CONNECTORS, t.text, langs):
                # a connector counts only if a number word follows it ("and a half", "hundred and fifty")
                k = j + 1
                while k < n and in_class(lx.NUMBER_CONNECTORS, tokens[k].text, langs):
                    k += 1
                w2 = number_word(tokens[k].text, langs) if k < n and tokens[k].kind == "word" else None
                if w2 and group.accepts(*w2):
                    j = k
                    continue
            break
        if group.value or tokens[first].text in ("zero",):
            spans.append(NumberSpan(group.value, tokens[first].start, tokens[last].end, True, first, last))
        i = last + 1
    return _merge_paise(spans, tokens, langs)


def _merge_paise(spans: list[NumberSpan], tokens: list[Token], langs: tuple[str, ...]) -> list[NumberSpan]:
    out: list[NumberSpan] = []
    k = 0
    while k < len(spans):
        a = spans[k]
        if k + 1 < len(spans):
            b = spans[k + 1]
            between = tokens[a.last + 1:b.first]
            after = tokens[b.last + 1] if b.last + 1 < len(tokens) else None
            rupees_then_paise = (len(between) == 1 and currency_word(between[0].text, langs) and after is not None
                                 and in_class(lx.PAISE_WORDS, after.text, langs))
            if rupees_then_paise and b.value < 100 and b.value == b.value.to_integral():
                out.append(NumberSpan(a.value + b.value / 100, a.start, after.end, a.words or b.words,
                                      a.first, b.last + 1))
                k += 2
                continue
        out.append(a)
        k += 1
    return out


def values_in(text: str, langs: tuple[str, ...]) -> set[Decimal]:
    return {s.value for s in read_numbers(tokenize(text), langs)}
