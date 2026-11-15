"""Profit maths (BUILD_PLAN §5.3). Code only: no model touches a figure, and every figure keeps its sources.

The cost of a sale is the latest confirmed purchase line for that item dated on or before the day it was
paid. Each paid sales line gets exactly one method, recorded with it:

  unit      quantity said, and a cost for that unit: profit = amount - cost x qty. Exact.
  margin    no quantity said: profit = amount x (1 - usual cost / usual price). An estimate, shown as "about".
            Usual price: what the owner repeatedly says for one pack ("oil 165"), or the latest per-kg/L price
            ("rice 2 kg 120" -> 60 a kg) for loose goods. Usual cost: the bill's per-pack rate, or per kg/L.
  no_cost   no confirmed cost yet. Profit unknown, never guessed.
  no_price  a cost, but no usual price to compare it with. Unknown.

Money is integer paise; ratios are Fractions and round half up to the paisa only at the end.
"""
from __future__ import annotations

import bisect
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from fractions import Fraction

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .clock import IST, ist_day, ist_day_bounds
from .models import Item, Payment, PricePoint, PurchaseBill, PurchaseLine, SalesBill, utcnow

COUNT_UNITS = ("pc", "pack")
PERIOD_DAYS = {"today": 1, "week": 7, "month": 28}
CHEAPER_WITHIN_DAYS = 60


def round_half_up(x: Fraction) -> int:
    """Nearest integer, halves away from zero (no float involved)."""
    sign = -1 if x < 0 else 1
    return sign * int((abs(x) * 2 + 1) // 2)


# ---------------- costs ----------------


@dataclass(frozen=True)
class CostFact:
    """One confirmed purchase line: what an item cost, from whom, when."""

    item_id: str
    purchase_id: str
    line_id: str
    position: int  # 1-based, as on the bill
    bill_no: str
    day: date
    distributor: str
    distributor_id: str | None
    unit: str | None  # as billed: pc | pack | kg | L
    qty_milli: int | None
    rate_paise: int | None
    base_unit: str | None
    base_qty_milli: int | None
    unit_cost_paise: int | None  # per base unit

    @property
    def by_count(self) -> bool:
        return self.unit in COUNT_UNITS

    @property
    def usual_cost(self) -> int | None:
        """Per pack when bought by the pack (a 1 L pouch), else per kg / L."""
        return self.rate_paise if self.by_count else self.unit_cost_paise

    @property
    def pack_base_milli(self) -> int | None:
        """How much one pack holds in the base unit (1 L pouch -> 1000), when the bill says."""
        if self.by_count and self.base_unit in ("kg", "L") and self.qty_milli and self.base_qty_milli:
            return self.base_qty_milli * 1000 // self.qty_milli
        return None

    def cost_per(self, unit: str | None) -> int | None:
        """Cost of one of `unit` (as the owner said it), if this bill tells us."""
        if unit in ("kg", "L"):
            return self.unit_cost_paise if self.base_unit == unit else None
        if unit in COUNT_UNITS:
            if self.by_count:
                return self.rate_paise
            if self.base_unit == unit:
                return self.unit_cost_paise
        return None

    def ref(self) -> dict:
        return {"purchase_id": self.purchase_id, "line_id": self.line_id, "position": self.position,
                "bill_no": self.bill_no, "day": self.day.isoformat(), "distributor": self.distributor,
                "qty_milli": self.qty_milli, "unit": self.unit, "rate_paise": self.rate_paise,
                "base_unit": self.base_unit, "unit_cost_paise": self.unit_cost_paise,
                "usual_cost_paise": self.usual_cost}


class Costs:
    """Every confirmed cost per item, by bill date."""

    def __init__(self, facts: list[CostFact]) -> None:
        self.by_item: dict[str, list[CostFact]] = defaultdict(list)
        for f in sorted(facts, key=lambda f: (f.day, f.purchase_id, f.position)):
            self.by_item[f.item_id].append(f)
        self._days = {k: [f.day for f in v] for k, v in self.by_item.items()}

    def at(self, item_id: str | None, day: date) -> CostFact | None:
        """The latest cost dated on or before `day`."""
        if not item_id or item_id not in self.by_item:
            return None
        i = bisect.bisect_right(self._days[item_id], day)
        return self.by_item[item_id][i - 1] if i else None

    def latest(self, item_id: str) -> CostFact | None:
        facts = self.by_item.get(item_id)
        return facts[-1] if facts else None

    def before(self, fact: CostFact) -> CostFact | None:
        """The bill before this one for the same item (for "up ₹8 since last bill")."""
        facts = self.by_item.get(fact.item_id, [])
        i = facts.index(fact)
        return facts[i - 1] if i > 0 else None


def load_costs(s: Session, merchant_id: str) -> Costs:
    rows = s.execute(
        select(PurchaseLine, PurchaseBill).join(PurchaseBill)
        .where(PurchaseBill.merchant_id == merchant_id, PurchaseBill.status == "confirmed",
               PurchaseLine.item_id.is_not(None))).all()
    facts = []
    for ln, b in rows:
        day = b.bill_date or ist_day(b.confirmed_at or b.created_at)
        facts.append(CostFact(ln.item_id, b.id, ln.id, ln.position + 1, b.bill_no, day, b.distributor_name,
                              b.distributor_id, ln.unit, ln.qty_milli, ln.rate_paise, ln.base_unit,
                              ln.base_qty_milli, ln.unit_cost_paise))
    return Costs(facts)


# ---------------- prices ----------------


@dataclass(frozen=True)
class UsualPrice:
    paise: int
    basis: str  # pack | kg | L


def usual_price(points: list[PricePoint], fact: CostFact) -> UsualPrice | None:
    """The price to set against `fact`'s usual cost, from what the owner has said (newest first)."""
    if fact.by_count:
        no_qty = [p.price_paise for p in points if p.per_qty_milli is None]
        if no_qty:
            price, times = Counter(no_qty[:20]).most_common(1)[0]
            if times >= 2 or len(no_qty) == 1:  # said repeatedly: the price of one pack
                return UsualPrice(price, "pack")
        for p in points:  # "biscuit 3 30" -> 10 a pack
            if p.per_qty_milli and p.per_unit in COUNT_UNITS:
                return UsualPrice(round_half_up(Fraction(p.price_paise * 1000, p.per_qty_milli)), "pack")
        return None
    for p in points:  # loose goods: the latest price per kg / L
        if p.per_qty_milli and p.per_unit == fact.base_unit:
            return UsualPrice(round_half_up(Fraction(p.price_paise * 1000, p.per_qty_milli)), fact.base_unit or "")
    return None


def load_points(s: Session, merchant_id: str, upto: datetime) -> dict[str, list[PricePoint]]:
    rows = s.execute(select(PricePoint).where(PricePoint.merchant_id == merchant_id, PricePoint.seen_at < upto)
                     .order_by(PricePoint.seen_at.desc())).scalars()
    out: dict[str, list[PricePoint]] = defaultdict(list)
    for p in rows:
        out[p.item_id].append(p)
    return out


# ---------------- sales lines ----------------


@dataclass
class LineProfit:
    bill_id: str
    line_id: str
    item_id: str | None
    day: date
    paid_at: datetime
    amount_paise: int
    method: str  # unit | margin | no_cost | no_price
    profit_paise: int | None
    cost: CostFact | None
    qty_usual: Fraction | None  # how many usual units (packs, kg, L) were sold, exact or estimated
    heard_text: str
    qty_milli: int | None
    unit: str | None

    @property
    def estimated(self) -> bool:
        return self.method == "margin"


def line_profit(bill: SalesBill, ln, costs: Costs, points: dict[str, list[PricePoint]]) -> LineProfit:
    amount = ln.line_total_paise or 0
    day = ist_day(bill.paid_at)
    fact = costs.at(ln.item_id, day)
    out = LineProfit(bill.id, ln.id, ln.item_id, day, bill.paid_at, amount, "no_cost", None, fact, None,
                     ln.heard_text, ln.qty_milli, ln.unit)
    if fact is None:
        return out
    if ln.qty_milli and ln.unit:
        c = fact.cost_per(ln.unit)
        if c is not None:
            out.method = "unit"
            out.profit_paise = amount - round_half_up(Fraction(c * ln.qty_milli, 1000))
            qty = Fraction(ln.qty_milli, 1000)
            if fact.by_count and ln.unit in ("kg", "L") and fact.pack_base_milli:
                qty = qty * 1000 / fact.pack_base_milli
            out.qty_usual = qty
            return out
    price = usual_price(points.get(ln.item_id, []), fact)
    cost = fact.usual_cost
    if price is None or not cost or price.paise <= 0:
        out.method = "no_price"
        return out
    out.method = "margin"
    out.profit_paise = round_half_up(Fraction(amount) * (1 - Fraction(cost, price.paise)))
    out.qty_usual = Fraction(amount, price.paise)
    return out


def paid_bills(s: Session, merchant_id: str, start: datetime, end: datetime) -> list[SalesBill]:
    return list(s.execute(
        select(SalesBill).options(selectinload(SalesBill.lines))
        .where(SalesBill.merchant_id == merchant_id, SalesBill.status == "paid",
               SalesBill.paid_at >= start, SalesBill.paid_at < end)
        .order_by(SalesBill.paid_at)).scalars())


@dataclass
class Window:
    start_day: date
    end_day: date  # inclusive

    @property
    def bounds(self) -> tuple[datetime, datetime]:
        return ist_day_bounds(self.start_day)[0], ist_day_bounds(self.end_day)[1]

    def days(self) -> list[date]:
        return [self.start_day + timedelta(days=i) for i in range((self.end_day - self.start_day).days + 1)]


def window(period: str, today: date | None = None) -> Window:
    today = today or ist_day(utcnow())
    n = PERIOD_DAYS[period]
    return Window(today - timedelta(days=n - 1), today)


@dataclass
class Ledger:
    """Every paid line in a window, profited."""

    win: Window
    lines: list[LineProfit] = field(default_factory=list)
    costs: Costs | None = None
    items: dict[str, Item] = field(default_factory=dict)
    upi_paise: int = 0  # every UPI payment received, billed by voice or not

    @property
    def sales_paise(self) -> int:
        return sum(lp.amount_paise for lp in self.lines)


def ledger(s: Session, merchant_id: str, win: Window, costs: Costs | None = None) -> Ledger:
    start, end = win.bounds
    costs = costs or load_costs(s, merchant_id)
    points = load_points(s, merchant_id, end)
    led = Ledger(win, costs=costs)
    for b in paid_bills(s, merchant_id, start, end):
        for ln in b.lines:
            if ln.line_total_paise:
                led.lines.append(line_profit(b, ln, costs, points))
    led.items = {it.id: it for it in s.execute(select(Item).where(Item.merchant_id == merchant_id)).scalars()}
    led.upi_paise = sum(s.execute(select(Payment.amount_paise).where(
        Payment.merchant_id == merchant_id, Payment.status == "succeeded",
        Payment.received_at >= start, Payment.received_at < end)).scalars())
    return led


# ---------------- summaries the screens use ----------------


def bp(num: int, den: int) -> int | None:
    """A share in basis points (1060 = 10.6%), rounded half up; None when there's nothing to divide by."""
    return None if den <= 0 else round_half_up(Fraction(num * 10000, den))


def _sum(lines: list[LineProfit]) -> dict:
    known = [lp for lp in lines if lp.profit_paise is not None]
    sales = sum(lp.amount_paise for lp in lines)
    profit = sum(lp.profit_paise for lp in known)
    return {"sales_paise": sales, "profit_paise": profit,
            "estimated": any(lp.estimated for lp in known),
            "known_sales_paise": sum(lp.amount_paise for lp in known),
            "methods": dict(Counter(lp.method for lp in lines))}


def profit_report(s: Session, merchant_id: str, period: str, today: date | None = None) -> dict:
    win = window(period, today)
    led = ledger(s, merchant_id, win)
    totals = _sum(led.lines)
    billed = led.sales_paise
    out = {
        "period": period, "from": win.start_day.isoformat(), "to": win.end_day.isoformat(), **totals,
        "upi_paise": led.upi_paise, "unbilled_paise": max(0, led.upi_paise - billed),
        "coverage_bp": bp(billed, led.upi_paise) if led.upi_paise else None,
        "bars": [], "items": [], "no_cost_items": [],
    }
    if period == "week":
        for d in win.days():
            day = [lp for lp in led.lines if lp.day == d]
            out["bars"].append({"from": d.isoformat(), "to": d.isoformat(), "label": d.strftime("%a"),
                                "profit_paise": _sum(day)["profit_paise"]})
    elif period == "month":
        for w in range(4):
            a = win.start_day + timedelta(days=7 * w)
            b = a + timedelta(days=6)
            chunk = [lp for lp in led.lines if a <= lp.day <= b]
            out["bars"].append({"from": a.isoformat(), "to": b.isoformat(), "label": f"W{w + 1}",
                                "profit_paise": _sum(chunk)["profit_paise"]})
    by_item: dict[str, list[LineProfit]] = defaultdict(list)
    for lp in led.lines:
        if lp.item_id:
            by_item[lp.item_id].append(lp)
    for item_id, lines in by_item.items():
        name = led.items[item_id].name if item_id in led.items else "?"
        t = _sum(lines)
        if t["known_sales_paise"] == 0:
            out["no_cost_items"].append({"item_id": item_id, "name": name, "sales_paise": t["sales_paise"],
                                         "reason": "no_cost" if t["methods"].get("no_cost") else "no_price"})
            continue
        latest = led.costs.latest(item_id)
        prev = led.costs.before(latest) if latest else None
        down = bool(latest and prev and latest.usual_cost and prev.usual_cost and latest.usual_cost > prev.usual_cost
                    and latest.day >= win.start_day - timedelta(days=7))
        out["items"].append({"item_id": item_id, "name": name, "sales_paise": t["sales_paise"],
                             "profit_paise": t["profit_paise"], "estimated": t["estimated"],
                             "margin_bp": bp(t["profit_paise"], t["known_sales_paise"]),
                             "trend": "down" if down else None, "methods": t["methods"]})
    out["items"].sort(key=lambda i: -i["profit_paise"])
    out["no_cost_items"].sort(key=lambda i: -i["sales_paise"])
    return out


def unit_label(fact: CostFact | None) -> str:
    """What "a unit" means to the owner for this item: litre, kg, or pack."""
    if fact is None:
        return "pack"
    if fact.by_count:
        size = fact.pack_base_milli
        if size == 1000:
            return "litre" if fact.base_unit == "L" else "kg"
        return "pack"
    return "litre" if fact.base_unit == "L" else (fact.base_unit or "pack")


def cheaper_elsewhere(costs: Costs, current: CostFact, today: date) -> CostFact | None:
    """Another distributor who billed less for the same item recently (BUILD_PLAN §5.5)."""
    best = None
    for f in costs.by_item.get(current.item_id, []):
        if f.distributor_id == current.distributor_id and f.distributor == current.distributor:
            continue
        if (today - f.day).days > CHEAPER_WITHIN_DAYS or not f.usual_cost or not current.usual_cost:
            continue
        if f.by_count != current.by_count or f.usual_cost >= current.usual_cost:
            continue
        if best is None or f.usual_cost < best.usual_cost:
            best = f
    return best


def item_report(s: Session, merchant_id: str, item_id: str, period: str = "week", today: date | None = None) -> dict:
    today = today or ist_day(utcnow())
    win = window(period, today)
    led = ledger(s, merchant_id, win)
    item = led.items.get(item_id)
    if item is None:
        raise KeyError(item_id)
    lines = [lp for lp in led.lines if lp.item_id == item_id]
    costs = led.costs
    latest = costs.latest(item_id)
    prev = costs.before(latest) if latest else None
    points = load_points(s, merchant_id, win.bounds[1]).get(item_id, [])
    price = usual_price(points, latest) if latest else None
    t = _sum(lines)
    qty = sum((lp.qty_usual for lp in lines if lp.qty_usual is not None), Fraction(0))

    def per_unit(fact: CostFact | None) -> int | None:
        return price.paise - fact.usual_cost if (price and fact and fact.usual_cost) else None

    rise = (latest.usual_cost - prev.usual_cost) if (latest and prev and latest.usual_cost and prev.usual_cost) else 0
    # what the period's sales would have made at the bill before (only counts sales costed at the newer bill)
    at_new = [lp for lp in lines if lp.cost is latest and lp.qty_usual is not None]
    lost = round_half_up(sum((lp.qty_usual for lp in at_new), Fraction(0)) * rise) if rise > 0 else 0
    said = sorted({lp.amount_paise for lp in lines if lp.qty_milli is None})
    cheaper = cheaper_elsewhere(costs, latest, today) if latest else None
    return {
        "item": {"id": item.id, "name": item.name, "unit": item.unit},
        "period": period, "from": win.start_day.isoformat(), "to": win.end_day.isoformat(),
        "unit_label": unit_label(latest),
        "price": None if price is None else {"paise": price.paise, "basis": price.basis},
        "cost": None if latest is None else latest.ref(),
        "prev_cost": None if prev is None else prev.ref(),
        "profit_per_unit_paise": per_unit(latest),
        "prev_profit_per_unit_paise": per_unit(prev),
        "margin_bp": bp(per_unit(latest), price.paise) if (price and per_unit(latest) is not None) else None,
        "sold": {"qty_milli": round_half_up(qty * 1000), "estimated": any(lp.method == "margin" for lp in lines),
                 "bills": len({lp.bill_id for lp in lines}), "lines": len(lines), **t},
        "lost_to_rise_paise": lost,
        "said_prices_paise": said,
        "cheaper": None if cheaper is None else cheaper.ref(),
    }


def item_bills(s: Session, merchant_id: str, item_id: str, period: str = "week",
               today: date | None = None) -> list[dict]:
    """The voice bills behind an item's figures, newest first. KeyError if the item isn't this shop's."""
    led = ledger(s, merchant_id, window(period, today))
    if item_id not in led.items:
        raise KeyError(item_id)
    out = [{"bill_id": lp.bill_id, "line_id": lp.line_id, "paid_at": lp.paid_at.astimezone(IST).isoformat(),
            "heard_text": lp.heard_text, "amount_paise": lp.amount_paise, "qty_milli": lp.qty_milli, "unit": lp.unit,
            "method": lp.method, "profit_paise": lp.profit_paise}
           for lp in led.lines if lp.item_id == item_id]
    return sorted(out, key=lambda r: r["paid_at"], reverse=True)
