"""Database tables (SQLAlchemy 2.0). Postgres in production; SQLite for development and tests.

Money is always integer paise. Times are timezone-aware UTC (TZDateTime) and shown in IST.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy import false as sa_false
from sqlalchemy import true as sa_true
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class TZDateTime(TypeDecorator):
    """Timezone-aware UTC timestamps on every backend (SQLite would drop tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            if value.tzinfo is None:
                raise ValueError("naive datetime; use timezone-aware UTC")
            value = value.astimezone(UTC)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value


_BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")


class AuditEvent(Base):
    """Append-only: database triggers reject UPDATE and DELETE (see db.py and the migrations)."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    merchant_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    ts: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    actor: Mapped[str] = mapped_column(String(16))  # owner | agent | system | partner
    kind: Mapped[str] = mapped_column(String(48))
    subject: Mapped[str] = mapped_column(String(64), default="")  # e.g. a bill or alert id
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Timer(Base):
    """Durable timers: a worker fires due rows, so nothing is lost on restart."""

    __tablename__ = "timers"
    __table_args__ = (Index("ix_timers_due", "status", "due_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    merchant_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    kind: Mapped[str] = mapped_column(String(48))  # e.g. qr_expiry | evening_summary | alert_scan
    subject: Mapped[str] = mapped_column(String(64), default="")
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    due_at: Mapped[datetime] = mapped_column(TZDateTime())
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | running | fired | cancelled | failed
    claimed_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set when a timer kept failing and gave up; someone should look.
    needs_attention: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class InboundEvent(Base):
    """Partner -> Laabam events already processed, so a retried event is applied once."""

    __tablename__ = "inbound_events"
    __table_args__ = (UniqueConstraint("source", "event_id"),)

    id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32))  # paytm | lending | soundbox | sandbox
    event_id: Mapped[str] = mapped_column(String(128))
    event_type: Mapped[str] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class IdempotencyKey(Base):
    """Requests carrying an Idempotency-Key return the same response when retried."""

    __tablename__ = "idempotency_keys"

    scope: Mapped[str] = mapped_column(String(64), primary_key=True)  # merchant id or partner name
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict[str, Any]] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


# ======================= ledger =======================


class Merchant(Base):
    """One shop."""

    __tablename__ = "merchants"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    language: Mapped[str] = mapped_column(String(8), default="en")  # en | ta | hi
    upi_vpa: Mapped[str] = mapped_column(String(128), default="")
    paytm_mid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    soundbox_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    closing_time: Mapped[str] = mapped_column(String(5), default="21:00")  # IST, HH:MM
    # False until the owner picks a language on Welcome (a new shop starts there).
    language_set: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa_true())
    # The seeded demo shop: its figures are sample data, and the app labels it so.
    sample_data: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_false())
    cash_on_hand_paise: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # entered by the owner
    # The festival the plan is for: the owner's date (a setting), else the next date in festivals.yaml.
    festival_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    keep_recordings: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_false())
    # The owner asked to delete the shop: it is deleted at this time unless they cancel first.
    deletion_due_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    # Which adapter serves this shop: "sandbox" for demos, "paytm" (payments, soundbox) in production.
    payments_adapter: Mapped[str] = mapped_column(String(16), default="sandbox")
    soundbox_adapter: Mapped[str] = mapped_column(String(16), default="sandbox")
    sms_adapter: Mapped[str] = mapped_column(String(16), default="sandbox")
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class Item(Base):
    """Created the first time the owner names it. No catalogue needed."""

    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("merchant_id", "key"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    key: Mapped[str] = mapped_column(String(120))  # normalised name
    unit: Mapped[str | None] = mapped_column(String(8), nullable=True)  # usual unit: kg | L | pc | pack
    aliases: Mapped[list[Any]] = mapped_column(default=list)  # normalised names that mean this item
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class PricePoint(Base):
    """The learned price list. The current price of an item is its latest point."""

    __tablename__ = "price_points"
    __table_args__ = (Index("ix_price_points_item_seen", "item_id", "seen_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"))
    price_paise: Mapped[int] = mapped_column(BigInteger)
    per_qty_milli: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # None: "rice 200", no quantity
    per_unit: Mapped[str | None] = mapped_column(String(8), nullable=True)
    source: Mapped[str] = mapped_column(String(8))  # voice | edit
    seen_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    sales_line_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class SalesBill(Base):
    __tablename__ = "sales_bills"
    __table_args__ = (Index("ix_sales_bills_merchant_created", "merchant_id", "created_at"),
                      UniqueConstraint("id", "merchant_id", name="uq_sales_bills_id_merchant"))

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | confirmed | paid | cancelled | expired
    transcript: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(8), default="en")
    total_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    qr_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    qr_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    qr_expires_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    qr_attempts: Mapped[int] = mapped_column(Integer, default=0)
    confirmed_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    payment_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # A payment that didn't match the bill (wrong amount, paid twice, paid after cancel). Shown to the owner.
    payment_issue: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, onupdate=utcnow)

    lines: Mapped[list[SalesLine]] = relationship(order_by="SalesLine.position", cascade="all, delete-orphan",
                                                  back_populates="bill")


class SalesLine(Base):
    __tablename__ = "sales_lines"
    # the shop is the bill's shop: the composite key makes any other value impossible
    __table_args__ = (ForeignKeyConstraint(["bill_id", "merchant_id"], ["sales_bills.id", "sales_bills.merchant_id"],
                                           name="fk_sales_lines_bill_merchant"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    bill_id: Mapped[str] = mapped_column(String(32), index=True)  # the composite key below points at the bill
    merchant_id: Mapped[str] = mapped_column(String(32), index=True)  # for row-level security; from the bill
    position: Mapped[int] = mapped_column(Integer)
    item_id: Mapped[str | None] = mapped_column(ForeignKey("items.id"), nullable=True, index=True)
    qty_milli: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # thousandths of `unit`; None = not said
    unit: Mapped[str | None] = mapped_column(String(8), nullable=True)
    line_total_paise: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # None until priced
    unit_price_paise: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    price_kind: Mapped[str | None] = mapped_column(String(8), nullable=True)  # total | per_unit (as spoken)
    price_source: Mapped[str | None] = mapped_column(String(8), nullable=True)  # spoken | learned | edit
    price_point_id: Mapped[str | None] = mapped_column(String(32), nullable=True)  # the learned price used
    heard_text: Mapped[str] = mapped_column(Text, default="")  # the transcript span this line came from
    flags: Mapped[list[Any]] = mapped_column(default=list)  # blocking problems; confirm needs none

    item: Mapped[Item | None] = relationship()
    bill: Mapped[SalesBill] = relationship(back_populates="lines")


class Payment(Base):
    """A payment Paytm (or the sandbox) reported for one of our QR orders."""

    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    order_id: Mapped[str] = mapped_column(String(64), index=True)
    bill_id: Mapped[str | None] = mapped_column(ForeignKey("sales_bills.id"), nullable=True, index=True)
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    # succeeded: matched its bill | failed | mismatch: wrong amount | duplicate: bill already paid |
    # unmatched: no open bill (cancelled, unknown order)
    status: Mapped[str] = mapped_column(String(16))
    paytm_txn_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(16))  # sandbox | paytm | poll
    received_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class Distributor(Base):
    """Who the shop buys from, as read from bill headers."""

    __tablename__ = "distributors"
    __table_args__ = (UniqueConstraint("merchant_id", "key"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    key: Mapped[str] = mapped_column(String(120))  # normalised name
    phone: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class PurchaseBill(Base):
    """A distributor bill, read from a photo. Costs count only once the owner confirms it."""

    __tablename__ = "purchase_bills"
    __table_args__ = (Index("ix_purchase_bills_merchant_created", "merchant_id", "created_at"),
                      UniqueConstraint("id", "merchant_id", name="uq_purchase_bills_id_merchant"))

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="read")  # reading | failed | read | confirmed | discarded
    distributor_id: Mapped[str | None] = mapped_column(ForeignKey("distributors.id"), nullable=True, index=True)
    distributor_name: Mapped[str] = mapped_column(String(120), default="")  # as read or as fixed by the owner
    bill_no: Mapped[str] = mapped_column(String(64), default="")
    bill_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    photo_key: Mapped[str | None] = mapped_column(String(200), nullable=True)  # in the photo store
    photo_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sample: Mapped[str | None] = mapped_column(String(64), nullable=True)  # offline: the sample bill used
    reader: Mapped[str] = mapped_column(String(32), default="")  # the vision provider that read it
    printed_total_paise: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # as printed on the bill
    total_paise: Mapped[int] = mapped_column(BigInteger, default=0)  # sum of the lines, in code
    read_issues: Mapped[list[Any]] = mapped_column(default=list)  # bill-level notes, e.g. total_mismatch
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, onupdate=utcnow)

    lines: Mapped[list[PurchaseLine]] = relationship(order_by="PurchaseLine.position", cascade="all, delete-orphan",
                                                     back_populates="bill")


class PurchaseLine(Base):
    __tablename__ = "purchase_lines"
    __table_args__ = (ForeignKeyConstraint(["purchase_bill_id", "merchant_id"],
                                           ["purchase_bills.id", "purchase_bills.merchant_id"],
                                           name="fk_purchase_lines_bill_merchant"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    purchase_bill_id: Mapped[str] = mapped_column(String(32), index=True)  # the composite key points at the bill
    merchant_id: Mapped[str] = mapped_column(String(32), index=True)  # for row-level security; from the bill
    position: Mapped[int] = mapped_column(Integer)
    raw_text: Mapped[str] = mapped_column(String(200), default="")  # the line as printed
    item_id: Mapped[str | None] = mapped_column(ForeignKey("items.id"), nullable=True, index=True)
    suggested_item_id: Mapped[str | None] = mapped_column(ForeignKey("items.id"), nullable=True)  # "Is this oil?"
    match: Mapped[str | None] = mapped_column(String(8), nullable=True)  # alias | fuzzy | owner | new
    qty_milli: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # thousandths of `unit`
    unit: Mapped[str | None] = mapped_column(String(8), nullable=True)  # as billed: pc | kg | L | pack ...
    rate_paise: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # per `unit`, as printed
    line_total_paise: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # amount, as printed
    # The pack size read from the text ("1LTR POUCH" -> 1 L), so costs compare per kg / L / pc.
    base_unit: Mapped[str | None] = mapped_column(String(8), nullable=True)
    base_qty_milli: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # total, in base_unit
    unit_cost_paise: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # per base_unit (or per unit)
    check_ok: Mapped[bool] = mapped_column(Boolean, default=False)  # qty x rate matches the amount
    flags: Mapped[list[Any]] = mapped_column(default=list)  # blocking problems; confirm needs none

    item: Mapped[Item | None] = relationship(foreign_keys=[item_id])
    suggested_item: Mapped[Item | None] = relationship(foreign_keys=[suggested_item_id])
    bill: Mapped[PurchaseBill] = relationship(back_populates="lines")


class Alert(Base):
    """Something for the owner to look at (BUILD_PLAN §5.4). Found by the worker's scans; every figure in
    `figures` is computed in code, with the bill lines and payments it came from."""

    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("merchant_id", "key"),
                      Index("ix_alerts_merchant_status", "merchant_id", "status"))

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"))
    kind: Mapped[str] = mapped_column(String(16))  # price_rise | running_out | slow | low_margin
    key: Mapped[str] = mapped_column(String(120))  # one alert per finding, e.g. price_rise:<purchase line>
    item_id: Mapped[str | None] = mapped_column(ForeignKey("items.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="open")  # open | seen | acted | resolved
    figures: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow, onupdate=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)


class Summary(Base):
    """The evening summary (BUILD_PLAN §5.7): figures from code, the words a template filled by code, spoken on
    the Soundbox at closing time. One per shop per day; asking again rebuilds it."""

    __tablename__ = "summaries"
    __table_args__ = (UniqueConstraint("merchant_id", "day"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    day: Mapped[date] = mapped_column(Date)
    language: Mapped[str] = mapped_column(String(8), default="en")
    figures: Mapped[dict[str, Any]] = mapped_column(default=dict)
    text: Mapped[str] = mapped_column(Text, default="")
    phrased_by: Mapped[str] = mapped_column(String(8), default="template")  # template | model
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    played_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)  # sent to the Soundbox


class RestockPlan(Base):
    """A plan the owner asked a loan for (BUILD_PLAN §5.5): a snapshot of its lines, total and cash in hand, so the
    loan offer always shows the basis it was sized from."""

    __tablename__ = "restock_plans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # festival | weekly
    festival: Mapped[str | None] = mapped_column(String(32), nullable=True)
    festival_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    multiplier_bp: Mapped[int] = mapped_column(Integer, default=10000)  # 2x usual = 20000
    lines: Mapped[list[Any]] = mapped_column(default=list)
    total_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    cash_on_hand_paise: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class LoanOffer(Base):
    """A loan offer for a plan's shortfall. The lending partner decides; Laabam sizes it and relays the owner's tap."""

    __tablename__ = "loan_offers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    restock_plan_id: Mapped[str] = mapped_column(ForeignKey("restock_plans.id"), index=True)
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    cash_on_hand_paise: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default="offered")  # offered | applied | declined | failed
    lender: Mapped[str] = mapped_column(String(16), default="sandbox")  # the adapter
    partner_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    terms: Mapped[dict[str, Any]] = mapped_column(default=dict)  # as the partner gives them; empty until then
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)



@event.listens_for(Session, "before_flush")
def _lines_take_their_bills_shop(session: Session, _ctx, _instances) -> None:
    """A line's merchant_id is always its bill's (row-level security reads it)."""
    for obj in session.new:
        if isinstance(obj, (SalesLine, PurchaseLine)) and obj.merchant_id is None and obj.bill is not None:
            obj.merchant_id = obj.bill.merchant_id


# ---------------- identity, sessions, limits (security plan S1) ----------------


class DeviceSession(Base):
    """One signed-in device. Revoking it ends its refresh tokens at once and its access token at the next request."""

    __tablename__ = "device_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    label: Mapped[str] = mapped_column(String(80), default="")  # "Chrome on Android", from the user agent
    via: Mapped[str] = mapped_column(String(16), default="partner")  # partner | sandbox
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime())  # absolute limit; sign in again after it
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)  # owner | reuse | expired | shop


class RefreshToken(Base):
    """Only the SHA-256 of a refresh token is stored. Each refresh uses one up and issues the next; presenting a
    used one again means it was copied, and the whole session is revoked."""

    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("device_sessions.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime())
    used_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)


class ShopKey(Base):
    """A shop's audit key (security plan C3), only ever stored wrapped: by AWS KMS in the deployed stacks, by a
    local key in development. Deleting the shop deletes this row, and its audit digests can't be checked again."""

    __tablename__ = "shop_keys"

    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), primary_key=True)
    wrapped: Mapped[bytes] = mapped_column(LargeBinary)
    keyring: Mapped[str] = mapped_column(String(16))  # kms | local
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), default=utcnow)


class UsedAssertion(Base):
    """Partner assertions seen (by jti), so each signs in once. Kept until the assertion would have expired."""

    __tablename__ = "used_assertions"

    jti: Mapped[str] = mapped_column(String(128), primary_key=True)
    issuer: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(TZDateTime(), index=True)


class RateBucket(Base):
    """A token bucket for rate limits (per shop, device or IP and action). Postgres row locks keep it exact."""

    __tablename__ = "rate_buckets"

    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    tokens: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime())
