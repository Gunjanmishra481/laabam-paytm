"""Money helpers. Amounts are always integer paise; never floats."""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def rupees(paise: int) -> str:
    """Format paise as rupees with lakh/crore grouping: 500000 -> '₹5,000', 35050 -> '₹350.50'."""
    if not isinstance(paise, int):
        raise TypeError("paise must be an int")
    whole, frac = divmod(abs(paise), 100)
    digits = str(whole)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups + [tail])
    sign = "-" if paise < 0 else ""
    return f"{sign}₹{digits}" + (f".{frac:02d}" if frac else "")


def to_paise(value: str | int | Decimal) -> int:
    """'12.50' -> 1250, '1,200' -> 120000, 200 -> 20000. Rounds half up to the paisa."""
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError("pass a string, int or Decimal, never a float")
    try:
        d = Decimal(str(value).replace(",", "").replace("₹", "").strip())
    except InvalidOperation as e:
        raise ValueError(f"not an amount: {value!r}") from e
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def upi_amount(paise: int) -> str:
    """Two-decimal rupee string for UPI intents and Paytm APIs: 46500 -> '465.00'."""
    whole, frac = divmod(paise, 100)
    return f"{whole}.{frac:02d}"


def mul_paise(paise: int, qty_milli: int) -> int:
    """Unit price × quantity in thousandths (2.5 kg = 2500), rounded half up to the paisa."""
    return int((Decimal(paise) * qty_milli / 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


_AMOUNT = re.compile(r"(?:₹|rs\.?|inr|rupees?)\s*([\d,]+(?:\.\d{1,2})?)|([\d,]+(?:\.\d{1,2})?)\s*(?:rs\.?|rupees?|/-)",
                     re.IGNORECASE)


def parse_amount_paise(text: str) -> int | None:
    """Find a marked amount in free text: '₹5,000', 'Rs 5000', '5000 rupees'."""
    m = _AMOUNT.search(text)
    if not m:
        return None
    paise = to_paise(m.group(1) or m.group(2))
    return paise if paise > 0 else None
