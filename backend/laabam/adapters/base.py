"""Adapter contracts. Each has a sandbox implementation (sandbox.py) and, where one exists, a real one."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class AdapterError(Exception):
    """A partner refused or failed a call. Safe to retry with the same idempotency key."""


@dataclass(frozen=True)
class Shop:
    """What an adapter may know about the shop."""

    id: str
    name: str
    language: str
    upi_vpa: str
    paytm_mid: str | None
    soundbox_id: str | None


@dataclass(frozen=True)
class QRCode:
    order_id: str
    payload: str  # the string inside the QR (a UPI intent)
    provider_ref: str | None = None


@dataclass(frozen=True)
class PaymentStatus:
    order_id: str
    status: str  # pending | succeeded | failed
    amount_paise: int | None = None
    txn_id: str | None = None
    paid_at: datetime | None = None


class PaymentsAdapter(Protocol):
    name: str

    def create_qr(self, shop: Shop, order_id: str, amount_paise: int, note: str, key: str) -> QRCode: ...

    def payment_status(self, shop: Shop, order_id: str) -> PaymentStatus: ...


class SoundboxAdapter(Protocol):
    name: str

    def show_qr(self, shop: Shop, payload: str, amount_paise: int, text: str) -> None: ...

    def announce(self, shop: Shop, text: str, language: str, audio: bytes | None = None) -> None: ...

    def clear(self, shop: Shop) -> None: ...


class SmsAdapter(Protocol):
    name: str

    def send_bill(self, shop: Shop, phone: str, text: str, key: str) -> str: ...


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str  # our language code (en, ta, hi)
    provider: str
    sample: str | None = None  # offline: which sample clip this came from
    request_id: str | None = None


class SpeechProvider(Protocol):
    """Speech-to-text and text-to-speech in the shop's language. Audio is never stored."""

    name: str

    def transcribe(self, audio: bytes, content_type: str, language: str, keyterms: tuple[str, ...] = (),
                   clip_name: str | None = None) -> Transcript: ...

    def speak(self, text: str, language: str) -> bytes | None: ...
