"""After the owner confirms: the QR, the payment, the Soundbox and the SMS bill.

- Confirm -> create a QR for the exact total (order id = bill id + attempt) -> show it on the phone and
  the Soundbox -> an expiry timer (and, for real Paytm, a status poll).
- Payment event (Paytm webhook, status poll, or the sandbox) -> applied once -> a payments row ->
  the bill is paid only if the amount matches; otherwise the bill is flagged for the owner.
- Paid -> the Soundbox announces the amount -> an SMS bill if the owner typed a number (the number
  is cleared as soon as the SMS is handed over: no customer records).
Calls to Soundbox and SMS run after the database commit, so a rolled-back change never speaks.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from . import audit, tenancy, timers
from .adapters import AdapterRegistry
from .adapters.base import AdapterError, Shop
from .billing.service import NotAllowed, NotFound, bill_json
from .clock import ist_day, ist_day_bounds
from .config import Settings
from .db import after_commit
from .idempotency import first_time, outbound_key
from .ids import new_id
from .models import Merchant, Payment, SalesBill, Timer, utcnow
from .money import rupees
from .strings import say

log = logging.getLogger("laabam.payments")



class QRFailed(Exception):
    pass


def shop_of(m: Merchant) -> Shop:
    return Shop(m.id, m.name, m.language, m.upi_vpa, m.paytm_mid, m.soundbox_id)


class PaymentService:
    def __init__(self, sessions: sessionmaker[Session], adapters: AdapterRegistry, settings: Settings,
                 trace: Callable[[str, str, dict[str, Any]], None]) -> None:
        self.sessions = sessions
        self.adapters = adapters
        self.settings = settings
        self.trace = trace
        self.speech = None  # set by the app: voices announcements when the provider can speak

    # ---------------- QR ----------------

    def on_confirmed(self, s: Session, m: Merchant, bill: SalesBill) -> None:
        """Runs inside the confirm transaction: if the QR can't be made, the bill stays a draft."""
        self._issue_qr(s, m, bill)

    def _issue_qr(self, s: Session, m: Merchant, bill: SalesBill) -> None:
        payments = self.adapters.get("payments", m.payments_adapter)
        bill.qr_attempts = (bill.qr_attempts or 0) + 1
        order_id = f"{bill.id}-{bill.qr_attempts}"
        note = f"{m.name} bill"[:50]
        try:
            qr = payments.create_qr(shop_of(m), order_id, bill.total_paise, note, outbound_key("qr", order_id))
        except AdapterError as e:
            self.trace(m.id, "payments.create_qr", {"adapter": payments.name, "order_id": order_id, "error": str(e)})
            raise QRFailed(str(e)) from e
        now = utcnow()
        bill.qr_order_id, bill.qr_payload = qr.order_id, qr.payload
        bill.qr_expires_at = now + timedelta(seconds=self.settings.qr_ttl_seconds)
        bill.status = "confirmed"
        timers.cancel(s, "qr_expiry", bill.id)
        timers.cancel(s, "payment_check", bill.id)
        timers.schedule(s, "qr_expiry", bill.qr_expires_at, m.id, bill.id, order_id=order_id)
        if payments.name != "sandbox":  # the sandbox pushes its events; real Paytm is also polled
            timers.schedule(s, "payment_check", now + timedelta(seconds=self.settings.payment_poll_seconds), m.id,
                            bill.id, order_id=order_id)
        audit.record(s, m.id, "system", "qr_shown", bill.id, order_id=order_id, amount_paise=bill.total_paise,
                     adapter=payments.name)
        self.trace(m.id, "payments.create_qr", {"adapter": payments.name, "order_id": order_id,
                                                "amount_paise": bill.total_paise, "payload": qr.payload})
        shop, amount, text = shop_of(m), bill.total_paise, say(m.language, "qr_screen", AMOUNT=rupees(bill.total_paise))
        after_commit(s, lambda: self._soundbox(shop, "show_qr", qr.payload, amount, text))

    def on_cancelled(self, s: Session, bill: SalesBill, prior: str) -> None:
        """A cancelled bill's QR comes off the Soundbox and its timers stop."""
        timers.cancel(s, "qr_expiry", bill.id)
        timers.cancel(s, "payment_check", bill.id)
        if prior == "confirmed":
            shop = shop_of(s.get(Merchant, bill.merchant_id))
            after_commit(s, lambda: self._soundbox(shop, "clear"))

    def show_again(self, merchant_id: str, bill_id: str) -> dict:
        with self.sessions.begin() as s:
            m, bill = self._load(s, merchant_id, bill_id)
            if bill.status not in ("confirmed", "expired"):
                raise NotAllowed("Only an unpaid, confirmed bill can show its QR again.")
            audit.record(s, m.id, "owner", "qr_requested_again", bill.id)
            self._issue_qr(s, m, bill)
            return bill_json(bill)

    # ---------------- payments ----------------

    def handle_payment(self, source: str, event_id: str, order_id: str, amount_paise: int, txn_id: str | None,
                       status: str = "succeeded") -> str:
        """Apply a payment event once. Returns what happened (for logs, tests and the partner)."""
        if not first_time(self.sessions, source, event_id, f"payment.{status}"):
            return "already_applied"
        bill_id = order_id.rsplit("-", 1)[0]
        with self.sessions.begin() as s:  # an event names an order, not a shop: find the shop (id only) first
            mid = tenancy.shop_for_bill(s, bill_id)
        if mid is None:
            log.warning("payment for unknown order %s from %s", order_id, source)
            return "unknown_order"
        tenancy.use_shop(mid)
        with self.sessions.begin() as s:
            bill = s.execute(select(SalesBill).where(SalesBill.id == bill_id).with_for_update()).scalar_one_or_none()
            if bill is None:
                log.warning("payment for unknown order %s from %s", order_id, source)
                return "unknown_order"
            m = s.get(Merchant, bill.merchant_id)
            if status != "succeeded":
                s.add(Payment(id=new_id("pay"), merchant_id=m.id, order_id=order_id, bill_id=bill.id,
                              amount_paise=amount_paise, status="failed", paytm_txn_id=txn_id, source=source))
                audit.record(s, m.id, "partner", "payment_failed", bill.id, order_id=order_id)
                self.trace(m.id, "payment.failed", {"order_id": order_id})
                return "failed"
            if bill.status == "paid":
                result, issue = "duplicate", "paid_twice"
            elif bill.status not in ("confirmed", "expired"):
                result, issue = "unmatched", "paid_after_cancel"
            elif amount_paise != bill.total_paise:
                result = "mismatch"
                issue = "short_payment" if amount_paise < bill.total_paise else "over_payment"
            else:
                result, issue = "succeeded", None
            s.add(Payment(id=new_id("pay"), merchant_id=m.id, order_id=order_id, bill_id=bill.id,
                          amount_paise=amount_paise, status=result, paytm_txn_id=txn_id, source=source))
            if result == "succeeded":
                bill.status, bill.paid_at, bill.payment_ref, bill.payment_issue = "paid", utcnow(), txn_id, None
                timers.cancel(s, "qr_expiry", bill.id)
                timers.cancel(s, "payment_check", bill.id)
            else:
                bill.payment_issue = issue
            audit.record(s, m.id, "partner", "payment_received", bill.id, order_id=order_id, txn_id=txn_id,
                         amount_paise=amount_paise, bill_total_paise=bill.total_paise, result=result, source=source)
            self.trace(m.id, "payment.received", {"order_id": order_id, "amount_paise": amount_paise,
                                                  "txn_id": txn_id, "result": result, "issue": issue})
            shop = shop_of(m)
            text = say(m.language, "paid_announce", AMOUNT=rupees(amount_paise))
            lang = m.language
            after_commit(s, lambda: self._announce(shop, text, lang))
            return result

    # ---------------- side effects (after commit) ----------------

    def _soundbox(self, shop: Shop, action: str, *args) -> None:
        try:
            box = self.adapters.get("soundbox", self._adapter_name(shop.id, "soundbox_adapter"))
            getattr(box, action)(shop, *args)
            self.trace(shop.id, f"soundbox.{action}", {"adapter": box.name, "text": next(
                (a for a in args if isinstance(a, str) and not a.startswith("upi://")), "")})
        except Exception as e:  # noqa: BLE001 - the Soundbox is best effort; the phone shows the same
            log.warning("soundbox %s failed: %s", action, e)
            self.trace(shop.id, f"soundbox.{action}", {"error": str(e)})

    def _announce(self, shop: Shop, text: str, language: str) -> None:
        """Speaks the code-made template in the shop's language; the Soundbox's own voice if that fails."""
        audio = None
        if self.speech is not None:
            t0 = time.perf_counter()
            try:
                audio = self.speech.speak(text, language)
            except Exception as e:  # noqa: BLE001 - the text is still shown and spoken by the device
                log.warning("speak failed: %s", e)
            if audio:
                self.trace(shop.id, "speech.spoken", {"provider": self.speech.name, "audio_bytes": len(audio),
                                                      "speak_ms": round((time.perf_counter() - t0) * 1000, 1)})
        self._soundbox(shop, "announce", text, language, audio)

    def _adapter_name(self, merchant_id: str, column: str) -> str:
        with self.sessions() as s:
            return getattr(s.get(Merchant, merchant_id), column)

    # ---------------- timers ----------------

    def on_qr_expiry(self, s: Session, t: Timer) -> None:
        bill = s.get(SalesBill, t.subject)
        if bill is None or bill.status != "confirmed" or bill.qr_order_id != t.payload.get("order_id"):
            return
        bill.status = "expired"
        audit.record(s, bill.merchant_id, "system", "qr_expired", bill.id, order_id=bill.qr_order_id)
        shop = shop_of(s.get(Merchant, bill.merchant_id))
        after_commit(s, lambda: self._soundbox(shop, "clear"))
        self.trace(bill.merchant_id, "qr.expired", {"order_id": bill.qr_order_id})

    def on_payment_check(self, s: Session, t: Timer) -> None:
        bill = s.get(SalesBill, t.subject)
        order_id = t.payload.get("order_id")
        if bill is None or bill.status != "confirmed" or bill.qr_order_id != order_id:
            return
        m = s.get(Merchant, bill.merchant_id)
        st = self.adapters.get("payments", m.payments_adapter).payment_status(shop_of(m), order_id)
        if st.status in ("succeeded", "failed"):
            amount = st.amount_paise if st.amount_paise is not None else 0
            after_commit(s, lambda: self.handle_payment("poll", f"poll:{order_id}:{st.txn_id or st.status}",
                                                        order_id, amount, st.txn_id, st.status))
        elif bill.qr_expires_at and utcnow() < bill.qr_expires_at:
            timers.schedule(s, "payment_check", utcnow() + timedelta(seconds=self.settings.payment_poll_seconds),
                            m.id, bill.id, order_id=order_id)

    # ---------------- reading ----------------

    def _load(self, s: Session, merchant_id: str, bill_id: str) -> tuple[Merchant, SalesBill]:
        bill = s.execute(select(SalesBill).where(SalesBill.id == bill_id, SalesBill.merchant_id == merchant_id)
                         .with_for_update()).scalar_one_or_none()
        if bill is None:
            raise NotFound("bill")
        return s.get(Merchant, merchant_id), bill

    def today(self, merchant_id: str) -> dict:
        start, end = ist_day_bounds(ist_day(utcnow()))
        with self.sessions() as s:
            count, total = s.execute(
                select(func.count(SalesBill.id), func.coalesce(func.sum(SalesBill.total_paise), 0))
                .where(SalesBill.merchant_id == merchant_id, SalesBill.status == "paid",
                       SalesBill.paid_at >= start, SalesBill.paid_at < end)).one()
            issues = s.execute(select(func.count(SalesBill.id)).where(
                SalesBill.merchant_id == merchant_id, SalesBill.payment_issue.is_not(None),
                SalesBill.updated_at >= start)).scalar_one()
        return {"date": start.date().isoformat(), "paid_bills": count, "sales_paise": int(total),
                "payment_issues": issues}
