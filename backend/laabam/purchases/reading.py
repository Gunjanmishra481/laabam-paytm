"""What a bill photo says, and the checks code runs on it. No model here: numbers are copied as printed
by the vision provider, then parsed and checked in code (qty x rate = amount, lines add up to the total)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from ..billing.items import item_key
from ..lang.numbers import normalise

TOLERANCE_PAISE = 100  # ₹1: printed amounts are rounded


@dataclass(frozen=True)
class ReadLine:
    raw_text: str
    qty: str | None = None
    unit: str | None = None
    rate: str | None = None
    amount: str | None = None


@dataclass(frozen=True)
class BillReading:
    lines: list[ReadLine]
    distributor: str = ""
    bill_no: str = ""
    bill_date: str = ""
    total: str | None = None
    provider: str = ""
    sample: str | None = None
    request_id: str | None = None
    notes: list[str] = field(default_factory=list)


# ---------------- numbers as printed ----------------

_MONEY_NOISE = re.compile(r"(₹|rs\.?|inr|/-|\s)", re.I)


def decimal_of(text: str | int | float | None) -> Decimal | None:
    """'1,480.00' / '₹ 148' / '148/-' -> Decimal. Anything unclear -> None (the line then asks the owner)."""
    if text is None:
        return None
    s = normalise(str(text))
    s = _MONEY_NOISE.sub("", s).replace(",", "")
    if not s or not re.fullmatch(r"\d+(\.\d+)?", s):
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return d if d > 0 else None


def paise_of(text: str | None) -> int | None:
    d = decimal_of(text)
    return None if d is None else int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def milli_of(text: str | None) -> int | None:
    d = decimal_of(text)
    return None if d is None else int((d * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


_DATE_FORMATS = (
    re.compile(r"(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})"),
    re.compile(r"(?P<d>\d{1,2})[/.-](?P<m>\d{1,2})[/.-](?P<y>\d{2,4})"),
)


def date_of(text: str | None) -> date | None:
    """ISO or Indian day-first dates (08/11/2026, 8-11-26). Anything else -> None."""
    if not text:
        return None
    s = normalise(text)
    for rx in _DATE_FORMATS:
        m = rx.search(s)
        if m:
            y, mo, d = int(m["y"]), int(m["m"]), int(m["d"])
            if y < 100:
                y += 2000
            try:
                return date(y, mo, d)
            except ValueError:
                return None
    return None


# ---------------- units and pack sizes ----------------

UNIT_WORDS = {
    "kg": "kg", "kgs": "kg", "kilo": "kg",
    "l": "L", "lt": "L", "ltr": "L", "ltrs": "L", "litre": "L", "liter": "L",
    "pc": "pc", "pcs": "pc", "nos": "pc", "no": "pc", "piece": "pc", "pieces": "pc", "unit": "pc",
    "pkt": "pack", "pkts": "pack", "pack": "pack", "packet": "pack", "pouch": "pack", "box": "pack",
    "bag": "pack", "jar": "pack", "btl": "pack", "bottle": "pack", "case": "pack", "carton": "pack",
    # units written in Tamil and Hindi on regional bills
    "கிலோ": "kg", "கி.கி": "kg", "கிகி": "kg", "லிட்டர்": "L", "லி": "L", "பாக்கெட்": "pack", "பாக்": "pack",
    "மூட்டை": "pack", "பெட்டி": "pack", "எண்": "pc", "எண்ணம்": "pc",
    "किलो": "kg", "कि.ग्रा": "kg", "लीटर": "L", "ली": "L", "पैकेट": "pack", "पैक": "pack", "बोरी": "pack",
    "डिब्बा": "pack", "नग": "pc", "पीस": "pc",
}


def unit_of(text: str | None) -> str | None:
    if not text:
        return None
    return UNIT_WORDS.get(normalise(text).strip(" .").lower())


_PACK = re.compile(r"(\d+(?:\.\d+)?)\s*(kgs?|gms?|grams?|g|ltrs?|lt|litres?|l|ml)\b", re.I)
_SCALE = {"kg": ("kg", 1), "kgs": ("kg", 1), "g": ("kg", Decimal("0.001")), "gm": ("kg", Decimal("0.001")),
          "gms": ("kg", Decimal("0.001")), "gram": ("kg", Decimal("0.001")), "grams": ("kg", Decimal("0.001")),
          "l": ("L", 1), "lt": ("L", 1), "ltr": ("L", 1), "ltrs": ("L", 1), "litre": ("L", 1), "litres": ("L", 1),
          "ml": ("L", Decimal("0.001"))}


def pack_size(raw_text: str) -> tuple[str, int] | None:
    """'SUNFLWR OIL 1LTR POUCH' -> ('L', 1000); 'MILK 500ML' -> ('L', 500). In base-unit thousandths."""
    m = _PACK.search(normalise(raw_text))
    if not m:
        return None
    unit, factor = _SCALE[m.group(2).lower()]
    milli = int((Decimal(m.group(1)) * factor * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return (unit, milli) if milli > 0 else None


def base_quantity(raw_text: str, qty_milli: int | None, unit: str | None) -> tuple[str | None, int | None]:
    """The total bought in kg / L / pc, so costs compare across pack sizes.

    10 x '1LTR POUCH' -> 10 L; 25 kg loose -> 25 kg; 12 x 'SOAP 100G' -> 1.2 kg; 2 boxes, no size -> 2 pack.
    """
    if qty_milli is None:
        return None, None
    if unit in ("kg", "L"):
        return unit, qty_milli
    size = pack_size(raw_text)
    if size:
        base_unit, per_pack_milli = size
        return base_unit, qty_milli * per_pack_milli // 1000
    return unit or "pc", qty_milli


# ---------------- names ----------------

_NOISE = {"pouch", "pkt", "pkts", "pack", "packet", "box", "bag", "jar", "btl", "bottle", "loose", "refill",
          "nos", "pcs", "pc", "case", "carton", "kg", "kgs", "ltr", "ltrs", "lt", "gm", "gms", "ml", "x"}


def match_key(raw_text: str) -> str:
    """The name part of a printed line: 'SUNFLWR OIL 1LTR POUCH' -> 'sunflwr oil'."""
    text = _PACK.sub(" ", normalise(raw_text))
    text = re.sub(r"\d+(\.\d+)?", " ", text)
    words = [w for w in item_key(text).split() if w not in _NOISE and len(w) > 1]
    return " ".join(words)


def contains_name(key: str, alias: str) -> bool:
    """'ponni rice' contains 'rice'; 'rice bran oil' contains 'oil' (the owner is asked; never assumed)."""
    words, alias_words = key.split(), alias.split()
    return bool(alias_words) and all(w in words for w in alias_words)


# ---------------- checks ----------------


@dataclass
class LineCheck:
    check_ok: bool
    flags: list[str]
    unit_cost_paise: int | None


def check_line(qty_milli: int | None, rate_paise: int | None, amount_paise: int | None,
               base_qty_milli: int | None) -> LineCheck:
    """qty x rate must match the printed amount within ₹1. A missing rate is fine (many bills print only
    the amount); a missing quantity or amount is not, since the cost per unit needs both."""
    flags = []
    if amount_paise is None:
        flags.append("amount_missing")
    if qty_milli is None:
        flags.append("qty_missing")
    ok = not flags
    if ok and rate_paise is not None:
        expected = Decimal(qty_milli) * rate_paise / 1000
        if abs(expected - amount_paise) > TOLERANCE_PAISE:
            flags.append("math")
            ok = False
    cost = None
    if ok and base_qty_milli:
        cost = int((Decimal(amount_paise) * 1000 / base_qty_milli).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return LineCheck(ok, flags, cost)
