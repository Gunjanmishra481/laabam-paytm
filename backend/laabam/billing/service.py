"""Voice billing: transcript -> draft bill (checklist) -> owner edits -> confirm.

parse (structure only) -> resolve (items, prices: code) -> verify (spoken prices are in the
transcript: code) -> total (code). Nothing is charged until the owner confirms a bill with no flags.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .. import audit
from ..ids import new_id
from ..models import Item, Merchant, PricePoint, SalesBill, SalesLine, utcnow
from ..money import to_paise
from .items import find_item, merchant_items, resolve_item
from .parser import ParsedLine, parse_order
from .pricing import learned_price, scale, to_milli, unit_price
from .verify import PRICE_NOT_HEARD, price_was_heard

UNITS = ("kg", "L", "pc", "pack")
PRICE_FLAGS = {"price_needed", PRICE_NOT_HEARD, "two_prices"}
MAX_LINES = 60


class NotFound(Exception):
    pass


class NotAllowed(Exception):
    """The action doesn't fit the bill's state (e.g. confirming with a flagged line)."""


class BillParser(Protocol):
    name: str

    def parse_bill(self, transcript: str, language: str) -> list[ParsedLine]: ...


class OfflineParser:
    """Rule-based parser for tests, demos and as the fallback when a model is unavailable."""

    name = "offline"

    def parse_bill(self, transcript: str, language: str) -> list[ParsedLine]:
        return parse_order(transcript, language)


Trace = Callable[[str, str, dict[str, Any]], None]  # (merchant_id, kind, data) for the demo's behind-the-scenes panel


def _no_trace(_m: str, _k: str, _d: dict[str, Any]) -> None:
    pass


@dataclass
class LineEdit:
    item_text: str | None = None
    qty_milli: int | None = None
    unit: str | None = None
    line_total_paise: int | None = None
    clear_qty: bool = False


class BillService:
    def __init__(self, sessions: sessionmaker[Session], parser: BillParser, trace: Trace = _no_trace) -> None:
        self.sessions = sessions
        self.parser = parser
        self.trace = trace
        self.on_confirmed: list[Callable[[Session, Merchant, SalesBill], None]] = []
        self.on_cancelled: list[Callable[[Session, SalesBill, str], None]] = []  # (session, bill, prior status)

    # ---------------- reading ----------------

    def _merchant(self, s: Session, merchant_id: str) -> Merchant:
        m = s.get(Merchant, merchant_id)
        if m is None:
            raise NotFound("merchant")
        return m

    def _bill(self, s: Session, merchant_id: str, bill_id: str, lock: bool = False) -> SalesBill:
        q = select(SalesBill).where(SalesBill.id == bill_id, SalesBill.merchant_id == merchant_id)
        if lock:
            q = q.with_for_update()
        bill = s.execute(q).scalar_one_or_none()
        if bill is None:
            raise NotFound("bill")
        return bill

    def get(self, merchant_id: str, bill_id: str) -> dict:
        with self.sessions() as s:
            return bill_json(self._bill(s, merchant_id, bill_id))

    def list(self, merchant_id: str, limit: int = 20) -> list[dict]:
        with self.sessions() as s:
            bills = s.execute(select(SalesBill).where(SalesBill.merchant_id == merchant_id)
                              .order_by(SalesBill.created_at.desc()).limit(limit)).scalars()
            return [bill_json(b) for b in bills]

    # ---------------- drafting ----------------

    def create_from_text(self, merchant_id: str, transcript: str) -> dict:
        transcript = transcript.strip()
        if not transcript:
            raise NotAllowed("Nothing was heard. Please say the order again.")
        with self.sessions.begin() as s:
            m = self._merchant(s, merchant_id)
            t0 = time.perf_counter()
            parsed = self.parser.parse_bill(transcript, m.language)[:MAX_LINES]
            t1 = time.perf_counter()
            bill = SalesBill(id=new_id("bill"), merchant_id=m.id, transcript=transcript, language=m.language)
            s.add(bill)
            items = merchant_items(s, m.id)
            for pos, p in enumerate(parsed):
                line = SalesLine(id=new_id("line"), position=pos)
                self._fill_from_parse(s, m, line, p, transcript, items)
                bill.lines.append(line)
            t2 = time.perf_counter()
            _retotal(bill)
            s.flush()
            audit.record(s, m.id, "agent", "bill_drafted", bill.id, transcript=audit.digest(s, m.id, transcript),
                         parser=self.parser.name,
                         lines=[_line_snapshot(ln) for ln in bill.lines], total_paise=bill.total_paise)
            out = bill_json(bill)
        timings = {"parse_ms": round((t1 - t0) * 1000, 1), "resolve_verify_ms": round((t2 - t1) * 1000, 1)}
        self.trace(merchant_id, "bill_drafted", {
            "transcript": transcript, "parser": self.parser.name, "timings": timings,
            "parsed": [_parsed_json(p, transcript) for p in parsed],
            "checks": [{"item": ln["item_name"], "heard": ln["heard_text"], "price_source": ln["price_source"],
                        "flags": ln["flags"]} for ln in out["lines"]],
            "total_paise": out["total_paise"],
        })
        out["timings"] = timings
        return out

    def _fill_from_parse(self, s: Session, m: Merchant, line: SalesLine, p: ParsedLine, transcript: str,
                         items: list[Item]) -> None:
        heard = p.heard(transcript)
        item = resolve_item(s, m.id, p.item_text, items) if p.item_text.strip() else None
        line.item_id, line.item = (item.id, item) if item else (None, None)
        line.heard_text = heard
        line.qty_milli, line.unit = to_milli(p.qty), p.unit
        flags = [f for f in p.flags if f != "item_needed" or item is None]
        if item is None and "item_needed" not in flags:
            flags.append("item_needed")
        line.price_point_id = None
        if p.price is not None:
            price = to_paise(str(p.price))
            if not price_was_heard(price, heard, transcript, m.language):
                flags.append(PRICE_NOT_HEARD)
            line.price_source, line.price_kind = "spoken", p.price_kind or "total"
            if line.price_kind == "per_unit":
                if line.qty_milli is None:
                    line.qty_milli, line.unit = 1000, line.unit or "pc"  # "sugar 40 per kg": one unit
                line.unit_price_paise = price
                line.line_total_paise = scale(price, line.qty_milli, 1000)
            else:
                line.line_total_paise = price
                line.unit_price_paise = unit_price(price, line.qty_milli)
        else:
            line.price_kind = None
            self._apply_learned(s, line, flags)
        line.flags = flags

    def _apply_learned(self, s: Session, line: SalesLine, flags: list[str]) -> None:
        learned = learned_price(s, line.item_id, line.qty_milli, line.unit) if line.item_id else None
        if learned is None:
            line.price_source = line.line_total_paise = line.unit_price_paise = line.price_point_id = None
            if line.item_id and "price_needed" not in flags:
                flags.append("price_needed")
            return
        line.price_source = "learned"
        line.line_total_paise = learned.line_total_paise
        line.qty_milli, line.unit = learned.qty_milli, learned.unit
        line.unit_price_paise = unit_price(learned.line_total_paise, learned.qty_milli)
        line.price_point_id = learned.point.id

    # ---------------- owner edits (tap) ----------------

    def add_line(self, merchant_id: str, bill_id: str, edit: LineEdit) -> dict:
        with self.sessions.begin() as s:
            m = self._merchant(s, merchant_id)
            bill = self._draft(s, merchant_id, bill_id)
            if len(bill.lines) >= MAX_LINES:
                raise NotAllowed("This bill has too many lines.")
            line = SalesLine(id=new_id("line"), position=max((ln.position for ln in bill.lines), default=-1) + 1,
                             flags=[], heard_text="")
            bill.lines.append(line)
            self._apply_edit(s, m, line, edit)
            _retotal(bill)
            audit.record(s, m.id, "owner", "line_added", bill.id, line=_line_snapshot(line))
            return bill_json(bill)

    def edit_line(self, merchant_id: str, bill_id: str, line_id: str, edit: LineEdit) -> dict:
        with self.sessions.begin() as s:
            m = self._merchant(s, merchant_id)
            bill = self._draft(s, merchant_id, bill_id)
            line = _find_line(bill, line_id)
            before = _line_snapshot(line)
            self._apply_edit(s, m, line, edit)
            _retotal(bill)
            audit.record(s, m.id, "owner", "line_edited", bill.id, before=before, after=_line_snapshot(line))
            return bill_json(bill)

    def delete_line(self, merchant_id: str, bill_id: str, line_id: str) -> dict:
        with self.sessions.begin() as s:
            bill = self._draft(s, merchant_id, bill_id)
            line = _find_line(bill, line_id)
            snap = _line_snapshot(line)
            bill.lines.remove(line)
            _retotal(bill)
            audit.record(s, merchant_id, "owner", "line_removed", bill.id, line=snap)
            return bill_json(bill)

    def _apply_edit(self, s: Session, m: Merchant, line: SalesLine, e: LineEdit) -> None:
        flags = list(line.flags or [])
        if e.unit is not None and e.unit not in UNITS:
            raise NotAllowed("Unknown unit.")
        if e.qty_milli is not None and e.qty_milli <= 0:
            raise NotAllowed("Quantity must be more than zero.")
        if e.line_total_paise is not None and e.line_total_paise <= 0:
            raise NotAllowed("Amount must be more than zero.")
        if e.item_text is not None:
            item = resolve_item(s, m.id, e.item_text) if e.item_text.strip() else None
            if item is None:
                raise NotAllowed("Please name the item.")
            line.item_id, line.item = item.id, item
            flags = [f for f in flags if f != "item_needed"]
        qty_changed = e.clear_qty or e.qty_milli is not None or e.unit is not None
        if e.clear_qty:
            line.qty_milli, line.unit = None, None
        if e.qty_milli is not None:
            line.qty_milli = e.qty_milli
            line.unit = e.unit or line.unit or "pc"
        elif e.unit is not None:
            line.unit = e.unit
        if qty_changed:
            flags = [f for f in flags if f != "two_quantities"]
        if e.line_total_paise is not None:
            line.line_total_paise = e.line_total_paise
            line.price_source, line.price_kind, line.price_point_id = "edit", "total", None
            line.unit_price_paise = unit_price(e.line_total_paise, line.qty_milli)
            flags = [f for f in flags if f not in PRICE_FLAGS]
        elif line.price_source in (None, "learned") and (qty_changed or e.item_text is not None):
            flags = [f for f in flags if f != "price_needed"]
            self._apply_learned(s, line, flags)
        elif qty_changed and line.price_kind == "per_unit" and line.unit_price_paise and line.qty_milli:
            line.line_total_paise = scale(line.unit_price_paise, line.qty_milli, 1000)
        elif qty_changed and line.line_total_paise is not None:
            line.unit_price_paise = unit_price(line.line_total_paise, line.qty_milli)
        line.flags = flags

    # ---------------- owner edits (voice: "sugar 120 not 100") ----------------

    def say(self, merchant_id: str, bill_id: str, text: str) -> dict:
        """A spoken fix or addition. Lines for items already on the bill are replaced; others are added."""
        text = text.strip()
        if not text:
            raise NotAllowed("Nothing was heard.")
        with self.sessions.begin() as s:
            m = self._merchant(s, merchant_id)
            bill = self._draft(s, merchant_id, bill_id)
            items = merchant_items(s, m.id)
            parsed = self.parser.parse_bill(text, m.language)
            changed = []
            for p in parsed:
                existing = None
                if p.item_text.strip():
                    item, _ = find_item(items, p.item_text)
                    existing = next((ln for ln in bill.lines if item and ln.item_id == item.id), None)
                if existing is None:
                    if len(bill.lines) >= MAX_LINES:
                        raise NotAllowed("This bill has too many lines.")
                    existing = SalesLine(id=new_id("line"), flags=[],
                                         position=max((ln.position for ln in bill.lines), default=-1) + 1)
                    bill.lines.append(existing)
                elif p.qty is None and p.price is not None:
                    # "sugar 120": a new amount for the line; keep the quantity already on it
                    p.qty, p.unit = (None if existing.qty_milli is None else _from_milli(existing.qty_milli),
                                     existing.unit)
                self._fill_from_parse(s, m, existing, p, text, items)
                changed.append(existing.id)
            bill.transcript = f"{bill.transcript}\n{text}" if bill.transcript else text
            _retotal(bill)
            audit.record(s, m.id, "owner", "bill_said", bill.id, text=audit.digest(s, m.id, text),
                         lines_changed=changed)
            out = bill_json(bill)
        self.trace(merchant_id, "bill_said", {"text": text, "parsed": [_parsed_json(p, text) for p in parsed]})
        return out

    # ---------------- confirm / cancel ----------------

    def _draft(self, s: Session, merchant_id: str, bill_id: str) -> SalesBill:
        bill = self._bill(s, merchant_id, bill_id, lock=True)
        if bill.status != "draft":
            raise NotAllowed("This bill is already confirmed or closed.")
        return bill

    def confirm(self, merchant_id: str, bill_id: str) -> dict:
        with self.sessions.begin() as s:
            m = self._merchant(s, merchant_id)
            bill = self._draft(s, merchant_id, bill_id)
            problems = confirm_problems(bill)
            if problems:
                raise NotAllowed(problems[0])
            now = utcnow()
            bill.status, bill.confirmed_at = "confirmed", now
            for ln in bill.lines:
                if ln.price_source in ("spoken", "edit") and ln.item_id:
                    s.add(PricePoint(id=new_id("pp"), merchant_id=m.id, item_id=ln.item_id,
                                     price_paise=ln.line_total_paise, per_qty_milli=ln.qty_milli, per_unit=ln.unit,
                                     source="voice" if ln.price_source == "spoken" else "edit", seen_at=now,
                                     sales_line_id=ln.id))
                if ln.item is not None and ln.item.unit is None and ln.unit:
                    ln.item.unit = ln.unit
            audit.record(s, m.id, "owner", "bill_confirmed", bill.id, total_paise=bill.total_paise,
                         lines=[_line_snapshot(ln) for ln in bill.lines])
            for hook in self.on_confirmed:
                hook(s, m, bill)
            return bill_json(bill)

    def reopen(self, merchant_id: str, bill_id: str) -> dict:
        """"Edit bill" from the QR: the QR is withdrawn and the bill is a draft again. The prices this bill
        taught on confirm are taken back; confirming again writes them afresh."""
        with self.sessions.begin() as s:
            bill = self._bill(s, merchant_id, bill_id, lock=True)
            if bill.status not in ("confirmed", "expired"):
                raise NotAllowed("Only an unpaid, confirmed bill can be edited.")
            prior = bill.status
            line_ids = [ln.id for ln in bill.lines]
            if line_ids:
                for pp in s.execute(select(PricePoint).where(PricePoint.sales_line_id.in_(line_ids))).scalars():
                    s.delete(pp)
            bill.status, bill.confirmed_at = "draft", None
            bill.qr_payload, bill.qr_expires_at = None, None
            audit.record(s, merchant_id, "owner", "bill_reopened", bill.id, from_status=prior,
                         order_id=bill.qr_order_id)
            for hook in self.on_cancelled:  # the QR comes off the Soundbox and its timers stop
                hook(s, bill, prior)
            return bill_json(bill)

    def cancel(self, merchant_id: str, bill_id: str) -> dict:
        with self.sessions.begin() as s:
            bill = self._bill(s, merchant_id, bill_id, lock=True)
            if bill.status not in ("draft", "confirmed", "expired"):
                raise NotAllowed("A paid bill can't be cancelled.")
            prior = bill.status
            bill.status = "cancelled"
            audit.record(s, merchant_id, "owner", "bill_cancelled", bill.id, from_status=prior)
            for hook in self.on_cancelled:
                hook(s, bill, prior)
            return bill_json(bill)


# ---------------- helpers ----------------


def _from_milli(q: int) -> Decimal:
    return Decimal(q) / 1000


def _find_line(bill: SalesBill, line_id: str) -> SalesLine:
    for ln in bill.lines:
        if ln.id == line_id:
            return ln
    raise NotFound("line")


def _retotal(bill: SalesBill) -> None:
    """The total is always added up here, in code."""
    bill.total_paise = sum(ln.line_total_paise or 0 for ln in bill.lines)


def confirm_problems(bill: SalesBill) -> list[str]:
    if bill.status != "draft":
        return ["This bill is already confirmed or closed."]
    if not bill.lines:
        return ["Add at least one item."]
    if any(ln.flags for ln in bill.lines):
        return ["Fix the lines marked in amber first."]
    if any(ln.line_total_paise is None or ln.line_total_paise <= 0 for ln in bill.lines):
        return ["Every line needs an amount."]
    return []


def _line_snapshot(ln: SalesLine) -> dict:
    return {"id": ln.id, "item_id": ln.item_id, "qty_milli": ln.qty_milli, "unit": ln.unit,
            "line_total_paise": ln.line_total_paise, "price_source": ln.price_source, "flags": list(ln.flags or [])}


def _parsed_json(p: ParsedLine, transcript: str) -> dict:
    return {"item_text": p.item_text, "qty": None if p.qty is None else str(p.qty), "unit": p.unit,
            "price": None if p.price is None else str(p.price), "price_kind": p.price_kind,
            "heard": p.heard(transcript), "flags": p.flags}


def line_json(ln: SalesLine) -> dict:
    return {
        "id": ln.id, "position": ln.position, "item_id": ln.item_id,
        "item_name": ln.item.name if ln.item else "",
        "qty_milli": ln.qty_milli, "unit": ln.unit, "line_total_paise": ln.line_total_paise,
        "unit_price_paise": ln.unit_price_paise, "price_kind": ln.price_kind, "price_source": ln.price_source,
        "learned": ln.price_source == "learned", "price_point_id": ln.price_point_id,
        "heard_text": ln.heard_text, "flags": list(ln.flags or []),
    }


def bill_json(bill: SalesBill) -> dict:
    return {
        "id": bill.id, "status": bill.status, "language": bill.language, "transcript": bill.transcript,
        "total_paise": bill.total_paise, "lines": [line_json(ln) for ln in bill.lines],
        "can_confirm": not confirm_problems(bill), "created_at": bill.created_at.isoformat(),
        "confirmed_at": bill.confirmed_at.isoformat() if bill.confirmed_at else None,
        "paid_at": bill.paid_at.isoformat() if bill.paid_at else None,
        "qr": ({"order_id": bill.qr_order_id, "payload": bill.qr_payload,
                "expires_at": bill.qr_expires_at.isoformat() if bill.qr_expires_at else None}
               if bill.qr_payload else None),
        "payment_ref": bill.payment_ref, "payment_issue": bill.payment_issue,
    }
