"""Restock and festival plans, and the loan sized from them (BUILD_PLAN §5.5, §5.6). Code only.

Usual week: what each item sold over the last 21 whole days, divided by 3 (in its usual unit: packs, or kg / L for
loose goods; sales said without a quantity are counted from the usual price, so these are estimates).

Festival plan: for the festival week, the usual week x the owner's multiplier (1.5x, 2x by default, 2.5x: a guess
the owner changes), rounded up to whole units, at the cheapest cost a distributor billed in the last 60 days.
Shortfall = total - cash in hand (entered by the owner, never assumed), never below 0. A loan is offered only for
a shortfall, through the lending adapter; the partner decides.

Reorder list (weekly): items that run out before their usual next bill, enough to last one usual cycle.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from . import alerts, audit
from .clock import ist_day
from .ids import new_id
from .models import LoanOffer, Merchant, RestockPlan, utcnow
from .profit import CHEAPER_WITHIN_DAYS, CostFact, Costs, Window, ledger, unit_label

MULTIPLIERS_BP = (15000, 20000, 25000)  # 1.5x, 2x (default), 2.5x
DEFAULT_MULTIPLIER_BP = 20000
USUAL_DAYS = 21
SELLING_DAYS = 14  # sold within this many days, or it is slow stock
FESTIVALS_FILE = Path(__file__).with_name("festivals.yaml")


class PlanError(ValueError):
    pass


def festivals() -> dict:
    return yaml.safe_load(FESTIVALS_FILE.read_text(encoding="utf-8"))


def next_festival(key: str, today: date) -> tuple[str, dict, date] | None:
    f = festivals().get(key)
    if not f:
        return None
    days = sorted(d if isinstance(d, date) else date.fromisoformat(str(d)) for d in f["dates"])
    upcoming = [d for d in days if d >= today]
    return (key, f, upcoming[0]) if upcoming else None


def usual_week(s: Session, merchant_id: str, today: date, costs: Costs) -> dict[str, Fraction]:
    """Units sold per item in a usual week: the last 21 whole days / 3. Items with no sale in the last 14 days
    are slow stock, not something to stock up on, and are left out."""
    led = ledger(s, merchant_id, Window(today - timedelta(days=USUAL_DAYS), today - timedelta(days=1)), costs)
    out: dict[str, Fraction] = {}
    recent: set[str] = set()
    for lp in led.lines:
        if lp.item_id and lp.qty_usual is not None:
            out[lp.item_id] = out.get(lp.item_id, Fraction(0)) + lp.qty_usual
            if lp.day >= today - timedelta(days=SELLING_DAYS):
                recent.add(lp.item_id)
    return {k: v * 7 / USUAL_DAYS for k, v in out.items() if k in recent}


def best_cost(costs: Costs, item_id: str, today: date) -> CostFact | None:
    """Where to buy: each distributor at their latest price for the item (a price that rose doesn't count at the
    old rate), billed in the last 60 days; the cheapest of those, else the latest bill."""
    latest = costs.latest(item_id)
    if latest is None or not latest.usual_cost:
        return None
    current: dict[str, CostFact] = {}
    for f in costs.by_item.get(item_id, []):  # by date: a later bill replaces the same distributor's earlier one
        current[f.distributor_id or f.distributor] = f
    best = latest
    for f in current.values():
        if f.usual_cost and f.by_count == latest.by_count and (today - f.day).days <= CHEAPER_WITHIN_DAYS \
                and f.usual_cost < best.usual_cost:
            best = f
    return best


def line(item, fact: CostFact, qty: int, latest: CostFact | None) -> dict:
    return {"item_id": item.id, "name": item.name, "unit_label": unit_label(fact), "qty": qty,
            "cost_paise": fact.usual_cost, "amount_paise": qty * fact.usual_cost, "distributor": fact.distributor,
            "cheaper": latest is not None and fact.line_id != latest.line_id, "cost": fact.ref()}


def festival_plan(s: Session, m: Merchant, multiplier_bp: int = DEFAULT_MULTIPLIER_BP, festival: str = "deepavali",
                  today: date | None = None) -> dict:
    if multiplier_bp not in MULTIPLIERS_BP:
        raise PlanError("Choose 1.5x, 2x or 2.5x.")
    today = today or ist_day(utcnow())
    led = ledger(s, m.id, Window(today, today))
    costs = led.costs
    week = usual_week(s, m.id, today, costs)
    lines = []
    for item_id, units in week.items():
        fact = best_cost(costs, item_id, today)
        if fact is None or units <= 0 or item_id not in led.items:
            continue
        qty = math.ceil(units * multiplier_bp / 10000)
        lines.append(line(led.items[item_id], fact, qty, costs.latest(item_id)))
    lines.sort(key=lambda x: -x["amount_paise"])
    total = sum(x["amount_paise"] for x in lines)
    fest = next_festival(festival, today)
    if fest and m.festival_date and m.festival_date >= today:  # the owner's date for this year's festival
        fest = (fest[0], fest[1], m.festival_date)
    cash = m.cash_on_hand_paise
    return {
        "kind": "festival", "festival": festival if fest else None,
        "festival_names": fest[1]["names"] if fest else None,
        "festival_date": fest[2].isoformat() if fest else None,
        "days_to_go": (fest[2] - today).days if fest else None,
        "multiplier_bp": multiplier_bp, "multipliers_bp": list(MULTIPLIERS_BP), "guess": True,
        "usual_days": USUAL_DAYS, "lines": lines, "total_paise": total,
        "cash_on_hand_paise": cash, "shortfall_paise": None if cash is None else max(0, total - cash),
    }


def reorder_list(s: Session, m: Merchant, th: alerts.Thresholds, today: date | None = None) -> dict:
    """Items that run out before their usual next bill: enough for one usual cycle, at the cheapest recent cost."""
    today = today or ist_day(utcnow())
    states, costs, _ = alerts.item_states(s, m.id, today)
    lines = []
    for st in states:
        f = alerts.running_out(st, today)
        if f is None:
            continue
        per_day = Fraction(f["per_day_milli"], 1000)
        need = math.ceil(per_day * st.cycle_days() - Fraction(f["left_milli"], 1000))
        fact = best_cost(costs, st.item.id, today)
        if need > 0 and fact is not None:
            out = line(st.item, fact, need, costs.latest(st.item.id))
            out["out_day"] = f["out_day"]
            lines.append(out)
    lines.sort(key=lambda x: x["out_day"])
    return {"kind": "weekly", "lines": lines, "total_paise": sum(x["amount_paise"] for x in lines)}


def save_plan(s: Session, m: Merchant, plan: dict) -> RestockPlan:
    when = date.fromisoformat(plan["festival_date"]) if plan.get("festival_date") else None
    p = RestockPlan(id=new_id("plan"), merchant_id=m.id, kind=plan["kind"], festival=plan.get("festival"),
                    festival_date=when, multiplier_bp=plan.get("multiplier_bp", 10000), lines=plan["lines"],
                    total_paise=plan["total_paise"], cash_on_hand_paise=m.cash_on_hand_paise)
    s.add(p)
    return p


def offer_json(o: LoanOffer, p: RestockPlan) -> dict:
    return {"id": o.id, "status": o.status, "amount_paise": o.amount_paise, "lender": o.lender,
            "partner_ref": o.partner_ref, "terms": o.terms, "created_at": o.created_at.isoformat(),
            "decided_at": o.decided_at.isoformat() if o.decided_at else None,
            "basis": {"plan_id": p.id, "kind": p.kind, "festival": p.festival,
                      "festival_names": (festivals().get(p.festival) or {}).get("names") if p.festival else None,
                      "festival_date": p.festival_date.isoformat() if p.festival_date else None,
                      "multiplier_bp": p.multiplier_bp, "total_paise": p.total_paise,
                      "cash_on_hand_paise": o.cash_on_hand_paise, "shortfall_paise": o.amount_paise,
                      "lines": len(p.lines)}}


# ---------------- loan offers (the owner taps; the partner decides) ----------------


def create_offer(s: Session, m: Merchant, plan: RestockPlan, lender, shop) -> LoanOffer:
    """A loan for the plan's shortfall. Cash in hand must be the owner's figure; no shortfall, no loan."""
    cash = plan.cash_on_hand_paise
    if cash is None:
        raise PlanError("Enter your cash in hand first.")
    gap = plan.total_paise - cash
    if gap <= 0:
        raise PlanError("Your cash in hand covers this order. No loan needed.")
    existing = s.query(LoanOffer).filter(LoanOffer.restock_plan_id == plan.id, LoanOffer.status == "offered").first()
    if existing:
        return existing
    q = lender.offer(shop, gap, f"{plan.kind}:{plan.festival or ''}")
    terms = {k: v for k, v in (("partner", q.partner), ("rate_text", q.rate_text), ("days", q.days),
                               ("daily_paise", q.daily_paise)) if v is not None}
    o = LoanOffer(id=new_id("loan"), merchant_id=m.id, restock_plan_id=plan.id, amount_paise=q.amount_paise,
                  cash_on_hand_paise=cash, lender=lender.name, partner_ref=q.ref, terms=terms)
    s.add(o)
    s.flush()
    audit.record(s, m.id, "agent", "loan_offered", o.id, plan_id=plan.id, amount_paise=o.amount_paise,
                 total_paise=plan.total_paise, cash_on_hand_paise=cash, lender=lender.name, partner_ref=q.ref)
    return o


def decide(s: Session, m: Merchant, o: LoanOffer, approve: bool, lender, shop) -> LoanOffer:
    """The owner's tap: approve sends the application to the partner; Not now declines. Repeats are no-ops."""
    if o.status != "offered":
        return o
    o.status = lender.apply(shop, o.partner_ref) if approve else "declined"
    o.decided_at = utcnow()
    audit.record(s, m.id, "owner", "loan_approved" if approve else "loan_declined", o.id,
                 amount_paise=o.amount_paise, status=o.status, partner_ref=o.partner_ref)
    return o
