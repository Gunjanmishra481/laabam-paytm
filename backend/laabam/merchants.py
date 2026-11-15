"""The shop's own settings (GET/PATCH /v1/me)."""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from .ids import new_id
from .lang import LANGUAGES
from .models import Merchant

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_VPA = re.compile(r"^[\w.\-]{2,64}@[A-Za-z]{2,64}$")


class InvalidSetting(ValueError):
    pass


def create_merchant(s: Session, name: str, language: str = "en", upi_vpa: str = "", *, id: str | None = None,
                    adapters: str = "sandbox", **kw) -> Merchant:
    """A shop. A new shop from onboarding may start with no name, UPI ID or language chosen yet. `adapters` names
    its payments, Soundbox and SMS adapters: "paytm" in production, "sandbox" in the demo (config decides)."""
    kw.setdefault("closing_time", "21:00")
    for adapter in ("payments_adapter", "soundbox_adapter", "sms_adapter"):
        kw.setdefault(adapter, adapters)
    m = Merchant(id=id or new_id("mer"), name=name, language=language, upi_vpa=upi_vpa, **kw)
    validate(m, require_name=bool(kw.get("language_set", True)))  # a fresh onboarding shop has no name yet
    s.add(m)
    return m


def validate(m: Merchant, require_name: bool = True) -> None:
    if m.language not in LANGUAGES:
        raise InvalidSetting("Unsupported language.")
    if not _HHMM.match(m.closing_time or ""):
        raise InvalidSetting("Closing time must look like 21:00.")
    if m.upi_vpa and not _VPA.match(m.upi_vpa):
        raise InvalidSetting("That doesn't look like a UPI ID.")
    if require_name and not (m.name or "").strip():
        raise InvalidSetting("Shop name is needed.")
    if m.cash_on_hand_paise is not None and not 0 <= m.cash_on_hand_paise <= 10**12:
        raise InvalidSetting("Cash in hand must be zero or more.")


def update(m: Merchant, **fields) -> Merchant:
    """PATCH /v1/me. Only the fields sent are checked, so Welcome can set the language before Setup."""
    for k in ("name", "language", "closing_time", "upi_vpa"):
        if fields.get(k) is not None:
            setattr(m, k, fields[k].strip() if isinstance(fields[k], str) else fields[k])
    if fields.get("language") is not None:
        m.language_set = True
    if "cash_on_hand_paise" in fields and fields["cash_on_hand_paise"] is not None:
        m.cash_on_hand_paise = fields["cash_on_hand_paise"]
    if fields.get("clear_cash_on_hand"):
        m.cash_on_hand_paise = None
    if fields.get("festival_date") is not None:
        m.festival_date = fields["festival_date"]
    if fields.get("clear_festival_date"):
        m.festival_date = None
    if fields.get("keep_recordings") is not None:
        if fields["keep_recordings"]:
            # Laabam doesn't store audio at all yet; don't pretend the setting does something.
            raise InvalidSetting("Keeping recordings isn't available yet. Only the words are saved.")
        m.keep_recordings = False
    validate(m, require_name=fields.get("name") is not None or bool((m.name or "").strip()))
    return m


def needs(m: Merchant) -> list[str]:
    """What onboarding still has to ask: the language first, then the shop (name and UPI ID)."""
    out = []
    if not m.language_set:
        out.append("language")
    if not (m.name or "").strip() or not m.upi_vpa:
        out.append("shop")
    return out


def me_json(m: Merchant) -> dict:
    return {"id": m.id, "name": m.name, "language": m.language, "language_set": m.language_set,
            "closing_time": m.closing_time, "upi_vpa": m.upi_vpa, "languages": list(LANGUAGES),
            "needs": needs(m), "sample_data": m.sample_data, "cash_on_hand_paise": m.cash_on_hand_paise,
            "keep_recordings": m.keep_recordings, "soundbox_connected": bool(m.soundbox_id),
            "festival_date": m.festival_date.isoformat() if m.festival_date else None,
            "deletion_due_at": m.deletion_due_at.isoformat() if m.deletion_due_at else None}
