"""A shop's data as a whole: deleting it (demo shops after a day; the owner's "delete my shop" in S3).

Deletion removes every tenant row and the shop's photos. The audit log is append-only and stays; it holds
ids, amounts and keyed hashes, never the text (security plan C3).
"""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import audit, tenancy
from .models import (
    Alert,
    DeviceSession,
    Distributor,
    Item,
    LoanOffer,
    Merchant,
    Payment,
    PricePoint,
    PurchaseBill,
    PurchaseLine,
    RefreshToken,
    RestockPlan,
    SalesBill,
    SalesLine,
    ShopKey,
    Summary,
    Timer,
)

# children before parents
ORDER = (RefreshToken, DeviceSession, LoanOffer, RestockPlan, Summary, Alert, Payment, PricePoint, SalesLine,
         SalesBill, PurchaseLine, PurchaseBill, Distributor, Item, ShopKey)


def delete_shop(s: Session, merchant_id: str, photos, reason: str) -> dict:
    tenancy.set_shop(s, merchant_id)
    keys = [k for k in s.execute(select(PurchaseBill.photo_key).where(PurchaseBill.merchant_id == merchant_id,
                                                                      PurchaseBill.photo_key.is_not(None))).scalars()]
    counts = {}
    for model in ORDER:
        counts[model.__tablename__] = s.execute(delete(model).where(model.merchant_id == merchant_id)).rowcount
    s.execute(delete(Timer).where(Timer.merchant_id == merchant_id))
    s.execute(delete(Merchant).where(Merchant.id == merchant_id))
    if audit.shop_keys is not None:  # the key row is gone with the rest; drop the cached copy too
        audit.shop_keys.forget(merchant_id)
    audit.record(s, merchant_id, "system", "shop_deleted", merchant_id, reason=reason, rows=sum(counts.values()),
                 photos=len(keys))
    for k in keys:
        try:
            photos.delete(k)
        except Exception:  # noqa: BLE001 - the rows are gone; a photo left behind is swept by retention
            pass
    return counts
