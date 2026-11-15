"""Tenancy: every query is for one shop (security plan §2).

Two layers. The code scopes every query by merchant_id. Postgres row-level security enforces it as well: each
transaction runs `set_config('app.merchant_id', <shop>, true)` (SET LOCAL) from the shop in context, and every
tenant table has a policy that hides other shops' rows. The app connects as `laabam_app`, which owns no table
and has no BYPASSRLS; migrations run as the owner role.

A few lookups have to find a shop before its context is known (a refresh token, a Paytm merchant id, a QR order
id from a payment event, the shop list for the nightly scan). They go through SECURITY DEFINER functions owned by
`laabam_definer` (no login, fixed search_path) that return shop ids only; `laabam_app` may only EXECUTE them.
SQLite (development and half the tests) has no RLS: the same lookups run as plain queries there.
"""
from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event, select, text
from sqlalchemy.orm import Session, sessionmaker

APP_ROLE = "laabam_app"
DEFINER_ROLE = "laabam_definer"
_shop: contextvars.ContextVar[str | None] = contextvars.ContextVar("laabam_shop", default=None)

# every table holding a shop's data (merchants by id; the rest by merchant_id). Queues are not tenant tables:
# timers (ids only in payloads), inbound_events, idempotency_keys, used_assertions, rate_buckets.
TENANT_TABLES = ("audit_events", "distributors", "items", "price_points", "sales_bills", "sales_lines", "payments",
                 "purchase_bills", "purchase_lines", "alerts", "summaries", "restock_plans", "loan_offers",
                 "device_sessions", "refresh_tokens", "shop_keys")
QUEUE_TABLES = ("timers", "inbound_events", "idempotency_keys", "used_assertions", "rate_buckets")


def current_shop() -> str | None:
    return _shop.get()


def use_shop(merchant_id: str | None) -> contextvars.Token:
    """Make `merchant_id` the shop for transactions that start from now on (in this context)."""
    return _shop.set(merchant_id)


@contextmanager
def as_shop(merchant_id: str | None) -> Iterator[None]:
    tok = _shop.set(merchant_id)
    try:
        yield
    finally:
        _shop.reset(tok)


def set_shop(s: Session, merchant_id: str) -> None:
    """Switch shop inside a running transaction (the nightly scan goes shop by shop)."""
    _shop.set(merchant_id)
    if s.get_bind().dialect.name == "postgresql":
        s.execute(text("SELECT set_config('app.merchant_id', :m, true)"), {"m": merchant_id})


def install_session_hook(sessions: sessionmaker[Session]) -> None:
    """Each Postgres transaction starts by setting the shop in context ('' when none: every policy denies)."""

    @event.listens_for(sessions, "after_begin")
    def _set_local(session: Session, _tx, connection) -> None:
        if connection.dialect.name == "postgresql":
            connection.execute(text("SELECT set_config('app.merchant_id', :m, true)"), {"m": _shop.get() or ""})


# ---------------- lookups across shops (ids only) ----------------


def _pg(s: Session) -> bool:
    return s.get_bind().dialect.name == "postgresql"


def shop_ids(s: Session) -> list[str]:
    from .models import Merchant
    if _pg(s):
        return list(s.execute(text("SELECT laabam_shop_ids()")).scalars())
    return list(s.execute(select(Merchant.id)).scalars())


def shop_for_paytm_mid(s: Session, mid: str) -> str | None:
    from .models import Merchant
    if _pg(s):
        return s.execute(text("SELECT laabam_shop_for_paytm_mid(:m)"), {"m": mid}).scalar()
    return s.execute(select(Merchant.id).where(Merchant.paytm_mid == mid)).scalars().first()


def shop_for_phone(s: Session, phone_hash: str) -> str | None:
    from .models import Merchant
    if _pg(s):
        return s.execute(text("SELECT laabam_shop_for_phone(:h)"), {"h": phone_hash}).scalar()
    return s.execute(select(Merchant.id).where(Merchant.phone_hash == phone_hash)).scalars().first()


def shop_for_refresh(s: Session, token_hash: str) -> str | None:
    from .models import RefreshToken
    if _pg(s):
        return s.execute(text("SELECT laabam_shop_for_refresh(:h)"), {"h": token_hash}).scalar()
    return s.execute(select(RefreshToken.merchant_id).where(RefreshToken.token_hash == token_hash)).scalar()


def shop_for_order(s: Session, order_id: str) -> str | None:
    from .models import SalesBill
    if _pg(s):
        return s.execute(text("SELECT laabam_shop_for_order(:o)"), {"o": order_id}).scalar()
    return s.execute(select(SalesBill.merchant_id).where(SalesBill.qr_order_id == order_id)).scalars().first()


def shop_for_bill(s: Session, bill_id: str) -> str | None:
    """A QR order id is '<bill id>-<attempt>'; payment events name the order."""
    from .models import SalesBill
    if _pg(s):
        return s.execute(text("SELECT laabam_shop_for_bill(:b)"), {"b": bill_id}).scalar()
    return s.execute(select(SalesBill.merchant_id).where(SalesBill.id == bill_id)).scalar()


# ---------------- Postgres: roles, policies, functions ----------------

_SETTING = "current_setting('app.merchant_id', true)"


def rls_sql(tables: tuple[str, ...] = TENANT_TABLES, phone: bool = False) -> list[str]:
    """Idempotent DDL, run by the owner role (Alembic, or the test set-up). Role passwords are set by operations
    (Secrets Manager rotation), never here. Migrations pass the tables that exist at their point in history."""
    out = [
        f"""DO $$ BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
              END IF;
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{DEFINER_ROLE}') THEN
                CREATE ROLE {DEFINER_ROLE} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
              END IF;
              -- A managed database (RDS) migrates as a non-superuser owner, which may hand functions to the
              -- definer only as a member of that role (Postgres 16). The definer itself can't log in.
              IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)
                 AND NOT pg_has_role(current_user, '{DEFINER_ROLE}', 'MEMBER') THEN
                EXECUTE format('GRANT %I TO %I', '{DEFINER_ROLE}', current_user);
              END IF;
            END $$""",
        f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}, {DEFINER_ROLE}",
        f"REVOKE CREATE ON SCHEMA public FROM {APP_ROLE}",
    ]
    for t in tables + ("merchants",):
        col = "id" if t == "merchants" else "merchant_id"
        out += [
            f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY",
            f"DROP POLICY IF EXISTS tenant ON {t}",
            # the definer role reads (ids only, through its functions); the app sees one shop
            f"CREATE POLICY tenant ON {t} USING ({col} = {_SETTING} OR current_user = '{DEFINER_ROLE}') "
            f"WITH CHECK ({col} = {_SETTING}"
            + (" OR merchant_id IS NULL" if t == "audit_events" else "") + ")",
        ]
        if t == "audit_events":  # append-only for the app as well as by trigger
            out.append(f"GRANT SELECT, INSERT ON {t} TO {APP_ROLE}")
            out.append(f"REVOKE UPDATE, DELETE, TRUNCATE ON {t} FROM {APP_ROLE}")
        else:
            out.append(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO {APP_ROLE}")
    for t in QUEUE_TABLES:
        out.append(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO {APP_ROLE}")
    out += [
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}",
        f"GRANT SELECT (id, paytm_mid{', phone_hash' if phone else ''}) ON merchants TO {DEFINER_ROLE}",
        f"GRANT SELECT (merchant_id, token_hash) ON refresh_tokens TO {DEFINER_ROLE}",
        f"GRANT SELECT (id, merchant_id, qr_order_id) ON sales_bills TO {DEFINER_ROLE}",
    ]
    fns = {
        "laabam_shop_ids()": ("SETOF text", "SELECT id FROM public.merchants"),
        "laabam_shop_for_paytm_mid(p_mid text)": ("text", "SELECT id FROM public.merchants WHERE paytm_mid = p_mid "
                                                          "LIMIT 1"),
        "laabam_shop_for_refresh(p_hash text)": ("text", "SELECT merchant_id FROM public.refresh_tokens "
                                                         "WHERE token_hash = p_hash"),
        "laabam_shop_for_order(p_order text)": ("text", "SELECT merchant_id FROM public.sales_bills "
                                                        "WHERE qr_order_id = p_order LIMIT 1"),
        "laabam_shop_for_bill(p_bill text)": ("text", "SELECT merchant_id FROM public.sales_bills WHERE id = p_bill"),
    }
    if phone:  # from migration 27c69390b45b (beta sign-up by phone) on
        fns["laabam_shop_for_phone(p_hash text)"] = (
            "text", "SELECT id FROM public.merchants WHERE phone_hash = p_hash LIMIT 1")
    for sig, (ret, body) in fns.items():
        name = sig.split("(")[0]
        args = sig[sig.index("(") + 1:-1]
        types = ", ".join(a.split()[1] for a in args.split(", ")) if args else ""
        out += [
            f"CREATE OR REPLACE FUNCTION public.{sig} RETURNS {ret} LANGUAGE sql STABLE SECURITY DEFINER "
            f"SET search_path = pg_catalog, pg_temp AS $fn$ {body} $fn$",
            f"ALTER FUNCTION public.{name}({types}) OWNER TO {DEFINER_ROLE}",
            f"REVOKE ALL ON FUNCTION public.{name}({types}) FROM PUBLIC",
            f"GRANT EXECUTE ON FUNCTION public.{name}({types}) TO {APP_ROLE}",
        ]
    return out


def install_rls(conn) -> None:
    for stmt in rls_sql(phone=True):
        conn.execute(text(stmt))
