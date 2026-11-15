"""The owner's data rights and how long things are kept (security plan S3).

- Retention: a daily sweep clears what the owner said (bill transcripts, the heard text on lines) and deletes bill
  photos once they are older than LAABAM_RETENTION_DAYS. Amounts, items and the audit log stay.
- Export: everything the shop has, as a zip of JSON files plus its bill photos. Audited.
- Delete: the owner types the shop's name and must have signed in within the last 10 minutes. Deletion runs after
  a grace period (LAABAM_DELETION_GRACE_DAYS) and can be cancelled until then. Audited at each step.
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import audit, tenancy, timers
from .models import (
    Alert,
    AuditEvent,
    DeviceSession,
    Distributor,
    Item,
    LoanOffer,
    Merchant,
    Payment,
    PricePoint,
    PurchaseBill,
    PurchaseLine,
    RestockPlan,
    SalesBill,
    SalesLine,
    Summary,
    Timer,
    utcnow,
)

STEP_UP_SECONDS = 600
# What an export holds. Not included: refresh-token hashes and the wrapped audit key (secrets, not the owner's data).
EXPORT = (("shop", Merchant, "id"), ("items", Item, "merchant_id"), ("prices", PricePoint, "merchant_id"),
          ("sales_bills", SalesBill, "merchant_id"), ("sales_lines", SalesLine, "merchant_id"),
          ("payments", Payment, "merchant_id"), ("distributors", Distributor, "merchant_id"),
          ("purchase_bills", PurchaseBill, "merchant_id"), ("purchase_lines", PurchaseLine, "merchant_id"),
          ("alerts", Alert, "merchant_id"), ("summaries", Summary, "merchant_id"),
          ("restock_plans", RestockPlan, "merchant_id"), ("loan_offers", LoanOffer, "merchant_id"),
          ("devices", DeviceSession, "merchant_id"), ("audit_log", AuditEvent, "merchant_id"))


class NeedsFreshSignIn(PermissionError):
    pass


class WrongName(ValueError):
    pass


# ---------------- retention ----------------


def sweep(s: Session, merchant_id: str, photos, days: int, now: datetime | None = None) -> dict:
    """Clear old transcripts and heard text, delete old photos. Returns the counts (also audited)."""
    tenancy.set_shop(s, merchant_id)
    cutoff = (now or utcnow()) - timedelta(days=days)
    old_bills = select(SalesBill.id).where(SalesBill.merchant_id == merchant_id, SalesBill.created_at < cutoff)
    said = s.execute(update(SalesBill).where(SalesBill.id.in_(old_bills), SalesBill.transcript != "")
                     .values(transcript="")).rowcount
    heard = s.execute(update(SalesLine).where(SalesLine.merchant_id == merchant_id, SalesLine.bill_id.in_(old_bills),
                                              SalesLine.heard_text != "").values(heard_text="")).rowcount
    old_photos = s.execute(select(PurchaseBill).where(PurchaseBill.merchant_id == merchant_id,
                                                      PurchaseBill.created_at < cutoff,
                                                      PurchaseBill.photo_key.is_not(None))).scalars().all()
    for b in old_photos:
        try:
            photos.delete(b.photo_key)
        except Exception:  # noqa: BLE001 - tried again tomorrow while the key is still recorded
            continue
        b.photo_key = None
    counts = {"bills_cleared": said, "lines_cleared": heard, "photos": len(old_photos)}
    if any(counts.values()):
        audit.record(s, merchant_id, "system", "retention_sweep", merchant_id, days=days, **counts)
    return counts


def on_retention(svc, s: Session, _timer) -> None:
    for mid in tenancy.shop_ids(s):
        sweep(s, mid, svc.purchases.photos, svc.settings.retention_days)
    ensure_retention(s, after=utcnow())


def ensure_retention(s: Session, after: datetime | None = None) -> None:
    pending = s.execute(select(Timer.id).where(Timer.kind == "retention", Timer.status == "pending")).first()
    if after is not None or not pending:
        timers.schedule(s, "retention", (after or utcnow()) + timedelta(days=1))


# ---------------- export ----------------


def _jsonable(v: Any) -> Any:
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, bytes):
        return None
    return v


def _rows(s: Session, model, col: str, merchant_id: str) -> list[dict]:
    rows = s.execute(select(model).where(getattr(model, col) == merchant_id)).scalars().all()
    return [{c.key: _jsonable(getattr(r, c.key)) for c in model.__mapper__.column_attrs} for r in rows]


def export(s: Session, merchant_id: str, photos) -> bytes:
    tenancy.set_shop(s, merchant_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", "Your shop's data from Laabam. Money is in paise (100 paise = 1 rupee); "
                                 "times are UTC. Audit log texts are keyed digests, never the words.\n")
        n_photos = 0
        for name, model, col in EXPORT:
            rows = _rows(s, model, col, merchant_id)
            z.writestr(f"{name}.json", json.dumps(rows, ensure_ascii=False, indent=1))
            if model is PurchaseBill:
                for r in rows:
                    data = photos.get(r["photo_key"]) if r.get("photo_key") and hasattr(photos, "get") else None
                    if data:
                        z.writestr(f"photos/{r['id']}.jpg", data)
                        n_photos += 1
        audit.record(s, merchant_id, "owner", "data_exported", merchant_id, photos=n_photos)
    return buf.getvalue()


# ---------------- delete ----------------


def request_delete(s: Session, merchant_id: str, session_id: str, typed_name: str, grace_days: int) -> datetime:
    tenancy.set_shop(s, merchant_id)
    m = s.get(Merchant, merchant_id)
    sess = s.get(DeviceSession, session_id)
    if sess is None or sess.created_at < utcnow() - timedelta(seconds=STEP_UP_SECONDS):
        raise NeedsFreshSignIn("sign in again first")
    if typed_name.strip().casefold() != (m.name or "").strip().casefold() or not typed_name.strip():
        raise WrongName("the shop name doesn't match")
    if m.deletion_due_at is None:
        m.deletion_due_at = utcnow() + timedelta(days=grace_days)
        timers.schedule(s, "shop_delete", m.deletion_due_at, merchant_id, subject=merchant_id)
        audit.record(s, merchant_id, "owner", "deletion_requested", merchant_id, session=session_id,
                     due=m.deletion_due_at.isoformat())
    return m.deletion_due_at


def cancel_delete(s: Session, merchant_id: str) -> bool:
    tenancy.set_shop(s, merchant_id)
    m = s.get(Merchant, merchant_id)
    if m.deletion_due_at is None:
        return False
    m.deletion_due_at = None
    timers.cancel(s, "shop_delete", merchant_id)
    audit.record(s, merchant_id, "owner", "deletion_cancelled", merchant_id)
    return True


def on_shop_delete(svc, s: Session, timer) -> None:
    from .shopdata import delete_shop
    m = s.get(Merchant, timer.merchant_id)
    if m is None or m.deletion_due_at is None or m.deletion_due_at > utcnow():
        return  # cancelled (or moved) since it was scheduled
    delete_shop(s, m.id, svc.purchases.photos, reason="owner_request")
