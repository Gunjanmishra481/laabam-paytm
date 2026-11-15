"""The Demo Kirana's month (BUILD_PLAN Phase 4): synthetic voice bills, UPI payments and distributor bills, with
the findings from the pitch deck planted in them. All sample data; the app labels the shop as such.

Planted (and asserted by tests/test_demo_seed.py):
  - oil:      Sri Balaji billed sunflower oil at ₹148 a litre (bill 4302); the next bill, read from the
              "balaji" sample photo during the demo, says ₹156 (bill 4471, line 4): up ₹8, profit a litre
              ₹17 -> ₹9. Kumar Traders billed ₹149 a month ago: the cheaper distributor.
  - slow:     health drink (no sale in 21 days, about ₹1,840 on the shelf) and basmati (never sold since it
              came, ₹4,360): ₹6,200 of slow stock.
  - biscuits: sell about 14 packs a day, about 40 left, next bill not due for days: running out.
  - Deepavali at 2x usual: a ₹40,000 order; ₹15,000 in hand; ₹25,000 short, the loan offer.
  - milk:     sold at ₹27, costs ₹26: 3.7% margin.
  - 7 items sold but never on a distributor bill: profit shown as unknown, never guessed.
  - Cash in hand ₹15,000 (as if the owner entered it).

Dates are relative to today (IST), so the demo works any day. Today only gets the bills before "now".
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from . import audit
from .billing.items import display_name, item_key
from .clock import IST, ist_day
from .ids import new_id
from .models import (
    Distributor,
    Item,
    Merchant,
    Payment,
    PricePoint,
    PurchaseBill,
    PurchaseLine,
    SalesBill,
    SalesLine,
    utcnow,
)
from .purchases.reading import base_quantity, check_line, match_key, milli_of, paise_of, unit_of

DAYS = 28
CASH_ON_HAND_PAISE = 15_000_00
# Lines a day over the last 21 days (the plan's usual week). Solved so the Deepavali plan at 2x usual comes to
# exactly ₹40,000 (oil from Kumar Traders at ₹149): with ₹15,000 in hand, the deck's ₹25,000 loan.
STEADY_DAYS = 21
FESTIVAL_IN_DAYS = 9
STEADY = {"Biscuits": 14, "Toor dal": 3, "Milk": 11, "Tea": 2, "Ghee": 2, "Soap": 2, "Sugar": 5, "Rice": 3}


# ---------------- building blocks (tests use these too) ----------------


def add_item(s: Session, merchant_id: str, name: str, unit: str | None = None) -> Item:
    it = Item(id=new_id("item"), merchant_id=merchant_id, name=display_name(name), key=item_key(name),
              aliases=[item_key(name)], unit=unit)
    s.add(it)
    return it


@dataclass
class Sold:
    """One spoken line: "oil 165" (no quantity) or "sugar 1 kg 48"."""

    item: Item
    words: str
    amount_paise: int
    qty_milli: int | None = None
    unit: str | None = None


def add_paid_bill(s: Session, m: Merchant, paid_at: datetime, lines: list[Sold], learn: bool = True,
                  payments: list[Payment] | None = None) -> SalesBill:
    """A voice bill the owner confirmed and the customer paid by UPI, as the counter flow writes it. Pass
    `payments` to collect the payment rows and add them after the bills are flushed (bulk seeding)."""
    bill_id = new_id("bill")
    total = sum(x.amount_paise for x in lines)
    order = f"LB{bill_id[-12:].upper()}"
    b = SalesBill(id=bill_id, merchant_id=m.id, status="paid", transcript=", ".join(x.words for x in lines),
                  language=m.language, total_paise=total, qr_order_id=order,
                  confirmed_at=paid_at - timedelta(seconds=40),
                  paid_at=paid_at, payment_ref=f"SBX{bill_id[-10:]}", created_at=paid_at - timedelta(seconds=55),
                  updated_at=paid_at)
    for pos, x in enumerate(lines):
        ln = SalesLine(id=new_id("ln"), bill_id=bill_id, position=pos, item_id=x.item.id, qty_milli=x.qty_milli,
                       unit=x.unit, line_total_paise=x.amount_paise, price_kind="total", price_source="spoken",
                       heard_text=x.words, flags=[])
        b.lines.append(ln)
        if learn:
            s.add(PricePoint(id=new_id("pp"), merchant_id=m.id, item_id=x.item.id, price_paise=x.amount_paise,
                             per_qty_milli=x.qty_milli, per_unit=x.unit, source="voice", seen_at=b.confirmed_at,
                             sales_line_id=ln.id))
    s.add(b)
    pay = Payment(id=new_id("pay"), merchant_id=m.id, order_id=order, bill_id=bill_id, amount_paise=total,
                  status="succeeded", paytm_txn_id=b.payment_ref, source="sandbox", received_at=paid_at)
    if payments is None:
        s.flush()
        s.add(pay)
    else:
        payments.append(pay)
    return b


def add_unbilled_payment(s: Session, m: Merchant, at: datetime, amount_paise: int) -> None:
    """A UPI payment with no voice bill behind it (paid to the shop's static QR)."""
    pid = new_id("pay")
    s.add(Payment(id=pid, merchant_id=m.id, order_id=f"STATIC{pid[-10:].upper()}", bill_id=None,
                  amount_paise=amount_paise, status="succeeded", paytm_txn_id=None, source="sandbox", received_at=at))


@dataclass
class Bought:
    """One printed line of a distributor bill, and the item it is."""

    item: Item
    raw_text: str
    qty: str
    unit: str
    rate: str


def distributor(s: Session, merchant_id: str, name: str) -> Distributor:
    d = Distributor(id=new_id("dist"), merchant_id=merchant_id, name=name, key=item_key(name))
    s.add(d)
    return d


def add_purchase(s: Session, merchant_id: str, dist: Distributor, bill_no: str, day: date,
                 lines: list[Bought]) -> PurchaseBill:
    """A distributor bill read, checked and confirmed by the owner, as the Bills in flow writes it."""
    at = datetime.combine(day, time(11, 0), IST)
    b = PurchaseBill(id=new_id("pur"), merchant_id=merchant_id, status="confirmed", distributor_id=dist.id,
                     distributor_name=dist.name, bill_no=bill_no, bill_date=day, reader="offline", read_issues=[],
                     created_at=at, confirmed_at=at + timedelta(minutes=2), updated_at=at)
    for pos, x in enumerate(lines):
        qty, rate = milli_of(x.qty), paise_of(x.rate)
        amount = (qty * rate + 500) // 1000
        unit = unit_of(x.unit) or "pc"
        base_unit, base_qty = base_quantity(x.raw_text, qty, unit)
        c = check_line(qty, rate, amount, base_qty)
        b.lines.append(PurchaseLine(id=new_id("pln"), position=pos, raw_text=x.raw_text, item_id=x.item.id,
                                    match="owner", qty_milli=qty, unit=unit, rate_paise=rate, line_total_paise=amount,
                                    base_unit=base_unit, base_qty_milli=base_qty, unit_cost_paise=c.unit_cost_paise,
                                    check_ok=c.check_ok, flags=[]))
        key = match_key(x.raw_text)
        if key and key not in x.item.aliases:
            x.item.aliases = [*x.item.aliases, key]
    b.total_paise = sum(ln.line_total_paise for ln in b.lines)
    b.printed_total_paise = b.total_paise
    s.add(b)
    return b


# ---------------- the Demo Kirana ----------------


@dataclass
class Selling:
    name: str
    unit: str | None  # the item's usual unit
    per_day: float  # how many lines a day
    words: str  # what the owner says
    amount: int  # paise
    qty_milli: int | None = None
    said_unit: str | None = None


SELLING = [
    Selling("Sunflower oil", None, 6.6, "oil 165", 165_00),
    Selling("Toor dal", None, 9.5, "toor dal 140", 140_00),
    Selling("Biscuits", None, 14, "biscuits 10", 10_00),
    Selling("Milk", None, 20, "milk 27", 27_00),
    Selling("Sugar", "kg", 7, "sugar 1 kg 48", 48_00, 1000, "kg"),
    Selling("Rice", "kg", 5, "rice 2 kg 120", 120_00, 2000, "kg"),
    Selling("Tea", None, 3, "tea 140", 140_00),
    Selling("Ghee", None, 2.2, "ghee 150", 150_00),
    Selling("Soap", None, 4, "soap 40", 40_00),
    # never on a distributor bill: profit unknown
    Selling("Bread", None, 3, "bread 40", 40_00),
    Selling("Curd", None, 4, "curd 30", 30_00),
    Selling("Eggs", None, 2.5, "eggs 42", 42_00),
    Selling("Chips", None, 4, "chips 20", 20_00),
    Selling("Maggi", None, 3, "maggi 14", 14_00),
    Selling("Salt", None, 1.5, "salt 28", 28_00),
    Selling("Paneer", None, 1, "paneer 90", 90_00),
]


def _when(day: date, rng: random.Random) -> datetime:
    """A moment in opening hours (7:30 am to 9:30 pm IST)."""
    minute = rng.randint(7 * 60 + 30, 21 * 60 + 30)
    return datetime.combine(day, time(minute // 60, minute % 60, rng.randint(0, 59)), IST)


def seed_demo_month(s: Session, m: Merchant, now: datetime | None = None) -> dict:
    """Writes the Demo Kirana's last 28 days. Returns what was planted, for the tests and the trace."""
    now = now or utcnow()
    today = ist_day(now)
    rng = random.Random(4471)
    mid = m.id
    items = {x.name: add_item(s, mid, x.name, x.unit) for x in SELLING}
    health = add_item(s, mid, "Health drink 500 g")
    basmati = add_item(s, mid, "Basmati rice 5 kg")
    s.flush()

    # ---- distributor bills (all confirmed by the owner) ----
    balaji = distributor(s, mid, "Sri Balaji Agencies")
    kumar = distributor(s, mid, "Kumar Traders")
    dairy = distributor(s, mid, "Lakshmi Milk Agency")
    ago = lambda n: today - timedelta(days=n)  # noqa: E731
    it = items
    add_purchase(s, mid, kumar, "K-0918", ago(29), [
        Bought(it["Sunflower oil"], "SUNFLOWER OIL 1L POUCH", "60", "PCS", "149.00"),
        Bought(it["Sugar"], "SUGAR LOOSE", "100", "KG", "42.00"),
    ])
    add_purchase(s, mid, balaji, "4188", ago(30), [
        Bought(basmati, "BASMATI RICE 5KG BAG", "8", "BAG", "545.00"),
        Bought(it["Rice"], "PONNI RICE LOOSE", "200", "KG", "52.00"),
        Bought(it["Toor dal"], "TOOR DAL 1KG PKT", "150", "PKT", "125.00"),
        Bought(it["Tea"], "TEA DUST 250G", "60", "PCS", "122.00"),
    ])
    add_purchase(s, mid, balaji, "4231", ago(25), [
        Bought(health, "HEALTH DRINK 500G JAR", "10", "PCS", "230.00"),
        Bought(it["Ghee"], "GHEE 200ML", "48", "PCS", "132.00"),
        Bought(it["Soap"], "HAMAM SOAP 100G", "96", "PCS", "34.00"),
    ])
    add_purchase(s, mid, balaji, "4302", ago(15), [
        Bought(it["Toor dal"], "TOOR DAL 1KG PKT", "150", "PKT", "125.00"),
        Bought(it["Tea"], "TEA DUST 250G", "60", "PCS", "122.00"),
        Bought(it["Ghee"], "GHEE 200ML", "36", "PCS", "132.00"),
        Bought(it["Soap"], "HAMAM SOAP 100G", "72", "PCS", "34.00"),
        Bought(it["Sugar"], "SUGAR LOOSE", "150", "KG", "42.00"),
        Bought(it["Sunflower oil"], "SUNFLWR OIL 1LTR POUCH", "120", "PCS", "148.00"),
    ])
    add_purchase(s, mid, kumar, "K-0977", ago(12), [
        Bought(it["Rice"], "PONNI RICE LOOSE", "200", "KG", "52.00"),
    ])
    add_purchase(s, mid, balaji, "4262", ago(24), [Bought(it["Biscuits"], "PARLE-G BISCUIT 70G", "196", "PCS", "8.50")])
    add_purchase(s, mid, balaji, "4398", ago(10), [Bought(it["Biscuits"], "PARLE-G BISCUIT 70G", "110", "PCS", "8.50")])
    add_purchase(s, mid, balaji, "4446", ago(3), [Bought(it["Biscuits"], "PARLE-G BISCUIT 70G", "84", "PCS", "8.50")])
    for n in range(DAYS, 0, -1):  # milk every morning
        add_purchase(s, mid, dairy, f"M-{1000 + DAYS - n}", ago(n), [
            Bought(it["Milk"], "MILK 500ML PKT", "13", "PCS", "26.00")])
    s.flush()

    # ---- voice bills, paid by UPI ----
    plan: dict[date, list[list[Sold]]] = {}
    for n in range(DAYS - 1, -1, -1):
        day = ago(n)
        lines: list[Sold] = []
        for x in SELLING:
            count = int(x.per_day) + (1 if rng.random() < x.per_day - int(x.per_day) else 0)
            if n <= STEADY_DAYS and x.name in STEADY:
                count = STEADY[x.name]  # the last three weeks are steady, so the usual week is exact
            if x.name == "Sunflower oil" and n <= STEADY_DAYS:
                count = (7, 6, 7, 6, 7, 6, 7)[n % 7]  # 46 a week
            lines += [Sold(it[x.name], x.words, x.amount, x.qty_milli, x.said_unit) for _ in range(count)]
        if n == 21:
            lines.append(Sold(health, "health drink 260", 260_00))
            lines.append(Sold(health, "health drink 260", 260_00))
        rng.shuffle(lines)
        bills, i = [], 0
        while i < len(lines):  # 1 to 4 lines a bill, never the same item twice in one bill
            k = rng.choice((1, 1, 2, 2, 3, 4))
            chunk: list[Sold] = []
            while i < len(lines) and len(chunk) < k and all(c.item is not lines[i].item for c in chunk):
                chunk.append(lines[i])
                i += 1
            bills.append(chunk)
        plan[day] = bills

    n_bills, pays = 0, []
    for day, bills in plan.items():
        for chunk in bills:
            at = _when(day, rng)
            if at.astimezone(IST) > now.astimezone(IST):
                continue  # today: only what has happened so far
            add_paid_bill(s, m, at, chunk, payments=pays)
            n_bills += 1
        for _ in range(3 if day != today else 1):  # UPI payments with no voice bill
            at = _when(day, rng)
            if at <= now.astimezone(IST):
                add_unbilled_payment(s, m, at, rng.choice((60_00, 85_00, 120_00, 150_00, 210_00, 340_00)))

    s.flush()
    s.add_all(pays)
    m.cash_on_hand_paise = CASH_ON_HAND_PAISE
    m.festival_date = today + timedelta(days=FESTIVAL_IN_DAYS)  # the demo's Deepavali is always 9 days away
    s.flush()
    planted = {"bills": n_bills, "items": len(items) + 2, "cash_on_hand_paise": CASH_ON_HAND_PAISE,
               "oil_item_id": it["Sunflower oil"].id}
    audit.record(s, mid, "system", "demo_seeded", mid, **planted)
    return planted
