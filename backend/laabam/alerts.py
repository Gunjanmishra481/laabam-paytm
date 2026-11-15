"""Alerts (BUILD_PLAN §5.4): scans run by the worker after each confirmed distributor bill and nightly.

Four findings, all computed in code from confirmed bill lines and paid voice bills:

  price_rise   an item's cost rose by >= 5% or >= ₹2 a unit since the bill before (both lines kept), with
               the profit it cost this week at the new cost
  running_out  stock left (bought minus sold) runs out before the item's usual restock day
  slow         stock on the shelf and no sale for 14 days or more
  low_margin   margin below 8% on an item with real sales (3+ bills in 14 days)

One alert per finding (`key`); a finding that no longer holds is resolved; "Got it" marks it seen. The screens
write the sentences from `figures` through the strings table, so no model ever touches these numbers.
Stock is an estimate (sales without a quantity are counted from the usual price) and is labelled "about".
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from fractions import Fraction
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit, tenancy, timers
from .clock import IST, ist_day, next_ist_time
from .config import Settings
from .ids import new_id
from .models import Alert, Item, Merchant, Timer, utcnow
from .profit import (
    CostFact,
    Costs,
    LineProfit,
    Window,
    bp,
    ledger,
    load_costs,
    load_points,
    round_half_up,
    unit_label,
    usual_price,
)

log = logging.getLogger("laabam.alerts")

LOOKBACK_DAYS = 60  # sales history a scan reads
RISE_RECENT_DAYS = 30  # a cost rise older than this is old news
DEFAULT_CYCLE_DAYS = 7  # restock cycle when an item has only one bill


@dataclass(frozen=True)
class Thresholds:
    rise_bp: int = 500
    rise_paise: int = 200
    low_margin_bp: int = 800
    slow_days: int = 14
    min_bills: int = 3

    @classmethod
    def of(cls, cfg: Settings) -> Thresholds:
        return cls(cfg.alert_rise_bp, cfg.alert_rise_paise, cfg.alert_low_margin_bp, cfg.alert_slow_days,
                   cfg.alert_min_bills)


def bought_usual(f: CostFact) -> Fraction | None:
    """How many usual units (packs, or kg / L for loose goods) a bill line bought."""
    if f.by_count:
        return Fraction(f.qty_milli, 1000) if f.qty_milli else None
    return Fraction(f.base_qty_milli, 1000) if f.base_qty_milli else None


def milli(x: Fraction) -> int:
    return round_half_up(x * 1000)


@dataclass
class ItemState:
    """What the scans need to know about one item."""

    item: Item
    facts: list[CostFact]
    lines: list[LineProfit]  # paid voice-bill lines in the lookback

    @property
    def latest(self) -> CostFact:
        return self.facts[-1]

    def sold_since(self, day: date) -> Fraction:
        return sum((lp.qty_usual for lp in self.lines if lp.day >= day and lp.qty_usual is not None), Fraction(0))

    def stock_left(self) -> Fraction:
        """Bought in every bill since the first one in the lookback, minus sold since then. Never below 0."""
        first = self.facts[0].day
        bought = sum((bought_usual(f) or Fraction(0) for f in self.facts), Fraction(0))
        return max(Fraction(0), bought - self.sold_since(first))

    def last_sale(self) -> date | None:
        return max((lp.day for lp in self.lines), default=None)

    def cycle_days(self) -> int:
        days = sorted({f.day for f in self.facts})
        gaps = [(b - a).days for a, b in zip(days, days[1:], strict=False) if (b - a).days > 0]
        return round(median(gaps)) if gaps else DEFAULT_CYCLE_DAYS


def item_states(s: Session, merchant_id: str, today: date) -> tuple[list[ItemState], Costs, Window]:
    win = Window(today - timedelta(days=LOOKBACK_DAYS - 1), today)
    costs = load_costs(s, merchant_id)
    led = ledger(s, merchant_id, win, costs)
    by_item: dict[str, list[LineProfit]] = defaultdict(list)
    for lp in led.lines:
        if lp.item_id:
            by_item[lp.item_id].append(lp)
    out = []
    for item_id, facts in costs.by_item.items():
        facts = [f for f in facts if f.day <= today]
        item = led.items.get(item_id)
        if facts and item is not None:
            recent = [f for f in facts if f.day >= win.start_day] or facts[-1:]
            out.append(ItemState(item, recent, by_item.get(item_id, [])))
    return out, costs, win


# ---------------- the four findings ----------------


def price_rises(st: ItemState, costs: Costs, price: int | None, today: date, th: Thresholds) -> dict | None:
    new = st.latest
    old = costs.before(new)
    if old is None or not new.usual_cost or not old.usual_cost or new.by_count != old.by_count:
        return None
    if (today - new.day).days > RISE_RECENT_DAYS:
        return None
    rise = new.usual_cost - old.usual_cost
    if rise <= 0 or (rise < th.rise_paise and rise * 10000 < th.rise_bp * old.usual_cost):
        return None
    week_start = today - timedelta(days=6)
    at_new = [lp for lp in st.lines if lp.cost is new and lp.day >= week_start and lp.qty_usual is not None]
    sold_at_new = sum((lp.qty_usual for lp in at_new), Fraction(0))
    return {
        "key": f"price_rise:{new.line_id}",
        "unit_label": unit_label(new), "rise_paise": rise, "rise_bp": bp(rise, old.usual_cost),
        "new": new.ref(), "old": old.ref(),
        "price_paise": price,
        "profit_per_unit_paise": None if price is None else price - new.usual_cost,
        "prev_profit_per_unit_paise": None if price is None else price - old.usual_cost,
        "sold_this_week_milli": milli(sold_at_new),
        "lost_this_week_paise": round_half_up(sold_at_new * rise),
        "estimated": any(lp.estimated for lp in at_new),
    }


def running_out(st: ItemState, today: date) -> dict | None:
    # the selling rate over the last 7 whole days (today isn't over, so it would understate it)
    week = sum((lp.qty_usual for lp in st.lines if today - timedelta(days=7) <= lp.day < today
                and lp.qty_usual is not None), Fraction(0))
    if week <= 0:
        return None
    per_day = week / 7
    left = st.stock_left()
    if left <= 0:
        return None
    days_left = left / per_day
    next_restock = st.latest.day + timedelta(days=st.cycle_days())
    out_day = today + timedelta(days=int(days_left))
    if days_left > 7 or out_day >= next_restock:
        return None
    return {
        "key": f"running_out:{st.item.id}:{st.latest.line_id}",
        "unit_label": unit_label(st.latest), "per_day_milli": milli(per_day), "left_milli": milli(left),
        "days_left_milli": milli(days_left), "out_day": out_day.isoformat(),
        "next_restock_day": next_restock.isoformat(), "last_bill": st.latest.ref(), "estimated": True,
    }


def slow(st: ItemState, today: date, th: Thresholds) -> dict | None:
    left = st.stock_left()
    last_sale = st.last_sale()
    since = last_sale or st.latest.day
    quiet = (today - since).days
    if left <= 0 or quiet < th.slow_days or (today - st.latest.day).days < th.slow_days:
        return None
    cost = st.latest.usual_cost
    return {
        "key": f"slow:{st.item.id}:{st.latest.line_id}",
        "unit_label": unit_label(st.latest), "days_quiet": quiet, "last_sale_day": last_sale.isoformat()
        if last_sale else None, "left_milli": milli(left),
        "value_paise": None if not cost else round_half_up(left * cost), "last_bill": st.latest.ref(),
        "estimated": True,
    }


def low_margin(st: ItemState, price: int | None, today: date, th: Thresholds) -> dict | None:
    cost = st.latest.usual_cost
    if price is None or not cost or price <= 0:
        return None
    recent = {lp.bill_id for lp in st.lines if lp.day >= today - timedelta(days=13)}
    margin = bp(price - cost, price)
    if len(recent) < th.min_bills or margin is None or margin >= th.low_margin_bp:
        return None
    return {
        "key": f"low_margin:{st.item.id}:{st.latest.line_id}:{price}",
        "unit_label": unit_label(st.latest), "margin_bp": margin, "price_paise": price, "cost": st.latest.ref(),
        "bills_14_days": len(recent),
    }


def findings(s: Session, merchant_id: str, today: date, th: Thresholds) -> list[tuple[str, str, dict]]:
    """(kind, item_id, figures) for everything worth an alert today."""
    states, costs, win = item_states(s, merchant_id, today)
    points = load_points(s, merchant_id, win.bounds[1])
    out = []
    for st in states:
        p = usual_price(points.get(st.item.id, []), st.latest)
        price = p.paise if p else None
        rise = price_rises(st, costs, price, today, th)
        # a cost rise already says the profit fell; a low-margin card for the same item would repeat it
        margin = None if rise else low_margin(st, price, today, th)
        for kind, f in (("price_rise", rise), ("running_out", running_out(st, today)), ("slow", slow(st, today, th)),
                        ("low_margin", margin)):
            if f is not None:
                out.append((kind, st.item.id, f))
    return out


# ---------------- storing them ----------------

ORDER = {"price_rise": 0, "running_out": 1, "slow": 2, "low_margin": 3}


def refresh(s: Session, merchant_id: str, th: Thresholds, today: date | None = None) -> dict:
    """Open an alert for each new finding; update the figures of open ones; resolve the ones that no longer
    hold. Seen alerts stay seen. Returns counts for the trace."""
    today = today or ist_day(utcnow())
    found = {f["key"]: (kind, item_id, f) for kind, item_id, f in findings(s, merchant_id, today, th)}
    existing = {a.key: a for a in s.execute(select(Alert).where(Alert.merchant_id == merchant_id)).scalars()}
    opened, resolved = [], []
    for key, (kind, item_id, f) in found.items():
        a = existing.get(key)
        if a is None:
            a = Alert(id=new_id("alt"), merchant_id=merchant_id, kind=kind, key=key, item_id=item_id, figures=f)
            s.add(a)
            opened.append(a)
        elif a.status in ("open", "resolved"):
            a.figures, a.status, a.closed_at = f, "open", None
    for key, a in existing.items():
        if key not in found and a.status == "open":
            a.status, a.closed_at = "resolved", utcnow()
            resolved.append(a)
    s.flush()
    for a in opened:
        audit.record(s, merchant_id, "agent", "alert_opened", a.id, alert_kind=a.kind, item_id=a.item_id,
                     figures=a.figures)
    for a in resolved:
        audit.record(s, merchant_id, "system", "alert_resolved", a.id, alert_kind=a.kind)
    return {"opened": len(opened), "resolved": len(resolved), "open": len(found)}


def alert_json(a: Alert, items: dict[str, Item]) -> dict:
    it = items.get(a.item_id) if a.item_id else None
    return {"id": a.id, "kind": a.kind, "status": a.status, "item_id": a.item_id,
            "item_name": it.name if it else None, "figures": a.figures, "created_at": a.created_at.isoformat()}


def open_alerts(s: Session, merchant_id: str) -> list[dict]:
    rows = list(s.execute(select(Alert).where(Alert.merchant_id == merchant_id, Alert.status == "open")).scalars())
    items = {it.id: it for it in s.execute(select(Item).where(Item.merchant_id == merchant_id)).scalars()}
    rows.sort(key=lambda a: (ORDER.get(a.kind, 9), -(a.figures.get("lost_this_week_paise") or 0), a.created_at))
    return [alert_json(a, items) for a in rows]


def mark(s: Session, merchant_id: str, alert_id: str, status: str) -> dict:
    a = s.get(Alert, alert_id, with_for_update=True)
    if a is None or a.merchant_id != merchant_id:
        raise KeyError(alert_id)
    if a.status == "open":
        a.status, a.closed_at = status, utcnow()
        audit.record(s, merchant_id, "owner", f"alert_{status}", a.id, alert_kind=a.kind)
    items = {a.item_id: s.get(Item, a.item_id)} if a.item_id else {}
    return alert_json(a, items)


# ---------------- the worker ----------------


class AlertScanner:
    """Timer kinds: alert_scan (one shop, now: after a confirmed bill) and alert_nightly (every shop, then
    schedules the next night)."""

    def __init__(self, cfg: Settings, trace) -> None:
        self.th = Thresholds.of(cfg)
        h, m = (int(x) for x in cfg.alert_scan_at.split(":"))
        self.at = time(h, m)
        self.trace = trace

    def on_purchase_confirmed(self, s: Session, m: Merchant, _bill) -> None:
        if not s.execute(select(Timer.id).where(Timer.kind == "alert_scan", Timer.subject == m.id,
                                                Timer.status == "pending")).first():
            timers.schedule(s, "alert_scan", utcnow(), m.id, subject=m.id)

    def on_scan(self, s: Session, t: Timer) -> None:
        counts = refresh(s, t.subject, self.th)
        self.trace(t.subject, "alerts.scan", counts)

    def on_nightly(self, s: Session, t: Timer) -> None:
        for mid in tenancy.shop_ids(s):  # ids only (a SECURITY DEFINER function); then each shop in turn
            tenancy.set_shop(s, mid)
            refresh(s, mid, self.th)
        self.ensure_nightly(s, after=utcnow())

    def ensure_nightly(self, s: Session, after: datetime | None = None) -> None:
        if not s.execute(select(Timer.id).where(Timer.kind == "alert_nightly", Timer.status.in_(
                ("pending", "running")))).first() or after is not None:
            timers.schedule(s, "alert_nightly", next_ist_time(after or utcnow(), self.at).astimezone(IST))
