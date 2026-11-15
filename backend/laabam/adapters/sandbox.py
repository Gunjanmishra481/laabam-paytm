"""Sandbox twins: a UPI QR that the demo can "pay", a Soundbox simulator feed, an SMS log, and the
behind-the-scenes trace. In memory, per API process; only with LAABAM_SANDBOX=1."""
from __future__ import annotations

import secrets
import threading
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlencode

from ..clock import IST
from ..models import utcnow
from ..money import upi_amount
from .base import AdapterError, PaymentStatus, QRCode, Shop


def upi_intent(vpa: str, name: str, amount_paise: int, order_id: str, note: str) -> str:
    """Standard UPI intent: any UPI app can pay it."""
    params = {"pa": vpa, "pn": name, "am": upi_amount(amount_paise), "cu": "INR", "tr": order_id, "tn": note}
    return "upi://pay?" + urlencode(params, quote_via=quote)


class Trace:
    """What happened, step by step, for the demo's behind-the-scenes panel."""

    def __init__(self, keep: int = 300) -> None:
        self._lock = threading.Lock()
        self._seq = 0
        self._feeds: dict[str, deque] = defaultdict(lambda: deque(maxlen=keep))

    def record(self, merchant_id: str, kind: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._seq += 1
            self._feeds[merchant_id].append({"seq": self._seq, "ts": utcnow().astimezone(IST).isoformat(),
                                             "kind": kind, "data": data})

    def feed(self, merchant_id: str, after: int = 0) -> list[dict]:
        with self._lock:
            return [e for e in self._feeds.get(merchant_id, ()) if e["seq"] > after]


class SandboxPayments:
    name = "sandbox"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.orders: dict[str, dict] = {}
        self.by_key: dict[str, QRCode] = {}
        # Set by the app: called as (event_id, order_id, amount_paise, txn_id, status) when the demo pays.
        self.on_event: Callable[[str, str, int, str, str], Any] | None = None

    def create_qr(self, shop: Shop, order_id: str, amount_paise: int, note: str, key: str) -> QRCode:
        with self._lock:
            if key in self.by_key:  # retried call: same QR, nothing new
                return self.by_key[key]
            if not shop.upi_vpa:
                raise AdapterError("The shop has no UPI ID")
            qr = QRCode(order_id, upi_intent(shop.upi_vpa, shop.name, amount_paise, order_id, note),
                        provider_ref="sbx_qr_" + secrets.token_hex(4))
            self.orders[order_id] = {"merchant_id": shop.id, "amount_paise": amount_paise, "status": "pending",
                                     "txn_id": None, "paid_at": None}
            self.by_key[key] = qr
            return qr

    def payment_status(self, shop: Shop, order_id: str) -> PaymentStatus:
        o = self.orders.get(order_id)
        if o is None:
            return PaymentStatus(order_id, "pending")
        return PaymentStatus(order_id, o["status"], o["amount_paise"] if o["status"] == "succeeded" else None,
                             o["txn_id"], o["paid_at"])

    def pay(self, order_id: str, amount_paise: int | None = None) -> dict:
        """The customer scans and pays (amount_paise lets a demo play a wrong amount)."""
        with self._lock:
            o = self.orders.get(order_id)
            if o is None:
                raise KeyError(order_id)
            paid = o["amount_paise"] if amount_paise is None else amount_paise
            txn = "SBXTXN" + secrets.token_hex(5).upper()
            o.update(status="succeeded", txn_id=txn, paid_at=utcnow(), amount_paise=paid)
        result = self.on_event("evt_" + secrets.token_hex(8), order_id, paid, txn, "succeeded") if self.on_event \
            else None
        return {"order_id": order_id, "amount_paise": paid, "txn_id": txn, "result": result}


class SandboxSoundbox:
    """A Soundbox simulator: the web demo polls the feed and speaks the announcements."""

    name = "sandbox"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.screens: dict[str, dict] = {}
        self.events: dict[str, deque] = defaultdict(lambda: deque(maxlen=50))
        self.audio: dict[int, bytes] = {}  # seq -> spoken announcement (recent ones only, in memory)
        self.audio_owner: dict[int, str] = {}  # seq -> the shop it was spoken for (only it may fetch it)
        self._seq = 0

    def _push(self, shop: Shop, event: dict, audio: bytes | None = None) -> None:
        with self._lock:
            self._seq += 1
            if audio:
                self.audio[self._seq] = audio
                self.audio_owner[self._seq] = shop.id
                event = {**event, "audio": True}
                for old in [k for k in self.audio if k <= self._seq - 50]:
                    del self.audio[old]
                    self.audio_owner.pop(old, None)
            self.events[shop.id].append({"seq": self._seq, "ts": utcnow().isoformat(), **event})

    def show_qr(self, shop: Shop, payload: str, amount_paise: int, text: str) -> None:
        self.screens[shop.id] = {"kind": "qr", "payload": payload, "amount_paise": amount_paise, "text": text}
        self._push(shop, {"kind": "show_qr", "amount_paise": amount_paise, "text": text})

    def announce(self, shop: Shop, text: str, language: str, audio: bytes | None = None) -> None:
        self.screens[shop.id] = {"kind": "message", "text": text}
        self._push(shop, {"kind": "announce", "text": text, "language": language}, audio)

    def clear(self, shop: Shop) -> None:
        self.screens[shop.id] = {"kind": "idle"}
        self._push(shop, {"kind": "clear"})

    def state(self, merchant_id: str, after: int = 0) -> dict:
        with self._lock:
            return {"screen": self.screens.get(merchant_id, {"kind": "idle"}),
                    "events": [e for e in self.events.get(merchant_id, ()) if e["seq"] > after]}


class SandboxSms:
    """Logs the SMS instead of sending it (no SMS provider in v1)."""

    name = "sandbox"

    def __init__(self) -> None:
        self.sent: dict[str, list[dict]] = defaultdict(list)
        self._keys: dict[str, str] = {}

    def send_bill(self, shop: Shop, phone: str, text: str, key: str) -> str:
        if key in self._keys:
            return self._keys[key]
        ref = "sbx_sms_" + secrets.token_hex(4)
        masked = phone[:2] + "x" * max(0, len(phone) - 6) + phone[-4:]  # the log never keeps the number
        self.sent[shop.id].append({"ref": ref, "phone": masked, "text": text, "ts": utcnow().isoformat()})
        self._keys[key] = ref
        return ref
