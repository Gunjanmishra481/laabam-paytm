"""Line prices, in code: spoken prices, learned prices, and the bill total."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PricePoint


def _round(d: Decimal) -> int:
    return int(d.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def scale(price_paise: int, qty_milli: int, per_qty_milli: int) -> int:
    """Price for qty_milli when price_paise buys per_qty_milli: (₹120 for 2 kg) at 500 g -> ₹30."""
    return _round(Decimal(price_paise) * qty_milli / per_qty_milli)


def unit_price(line_total_paise: int, qty_milli: int | None) -> int | None:
    """Price per one unit, when the quantity is known."""
    if not qty_milli:
        return None
    return _round(Decimal(line_total_paise) * 1000 / qty_milli)


@dataclass(frozen=True)
class Learned:
    line_total_paise: int
    qty_milli: int | None
    unit: str | None
    point: PricePoint


def latest_points(s: Session, item_id: str, limit: int = 20) -> list[PricePoint]:
    return list(s.execute(select(PricePoint).where(PricePoint.item_id == item_id)
                          .order_by(PricePoint.seen_at.desc()).limit(limit)).scalars())


def learned_price(s: Session, item_id: str, qty_milli: int | None, unit: str | None) -> Learned | None:
    """The owner's last price for this item, scaled to the quantity asked for.

    - no quantity said: repeat the last line as it was ("rice" -> last time's "rice 200").
    - quantity said: the latest price for the same unit ("rice 1 kg" after "rice 2 kg 120" -> ₹60);
      a count ("3 biscuit") can use a price said without quantity ("biscuit 10") as the price of one.
    """
    points = latest_points(s, item_id)
    if not points:
        return None
    if qty_milli is None:
        p = points[0]
        return Learned(p.price_paise, p.per_qty_milli, p.per_unit, p)
    for p in points:
        if p.per_qty_milli and p.per_unit == unit:
            return Learned(scale(p.price_paise, qty_milli, p.per_qty_milli), qty_milli, unit, p)
        if p.per_qty_milli is None and unit == "pc":
            return Learned(scale(p.price_paise, qty_milli, 1000), qty_milli, unit, p)
    return None


def to_milli(qty: Decimal | None) -> int | None:
    return None if qty is None else _round(qty * 1000)
