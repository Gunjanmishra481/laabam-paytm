"""Database engine, sessions, and the append-only guard on the audit log."""
from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

# The audit log must never change after it is written. These triggers enforce that in
# the database itself, so no code path (or person with app credentials) can rewrite it.
APPEND_ONLY_SQL = {
    "postgresql": [
        """
        CREATE OR REPLACE FUNCTION laabam_audit_append_only() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'audit_events is append-only'; END;
        $$ LANGUAGE plpgsql
        """,
        "DROP TRIGGER IF EXISTS audit_events_append_only ON audit_events",
        """
        CREATE TRIGGER audit_events_append_only BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION laabam_audit_append_only()
        """,
    ],
    "sqlite": [
        """CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_events
        BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END""",
        """CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_events
        BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END""",
    ],
}

DROP_APPEND_ONLY_SQL = {
    "postgresql": [
        "DROP TRIGGER IF EXISTS audit_events_append_only ON audit_events",
        "DROP FUNCTION IF EXISTS laabam_audit_append_only()",
    ],
    "sqlite": ["DROP TRIGGER IF EXISTS audit_no_update", "DROP TRIGGER IF EXISTS audit_no_delete"],
}


def install_append_only(conn) -> None:
    for stmt in APPEND_ONLY_SQL.get(conn.dialect.name, []):
        conn.execute(text(stmt))


def drop_append_only(conn) -> None:
    for stmt in DROP_APPEND_ONLY_SQL.get(conn.dialect.name, []):
        conn.execute(text(stmt))


def make_engine(url: str) -> Engine:
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update(pool_pre_ping=True, pool_size=5, max_overflow=10)
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    return engine


def create_schema(engine: Engine) -> None:
    """For development and tests. Production uses Alembic migrations (backend/migrations).

    On Postgres this runs as the table owner and also installs row-level security (tenancy.install_rls). Connected
    as the app role (which owns nothing and may not create), it does nothing: the owner set the schema up."""
    if engine.dialect.name == "postgresql":
        from .tenancy import APP_ROLE
        with engine.connect() as conn:
            if conn.execute(text("SELECT current_user")).scalar() == APP_ROLE:
                return
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
    with engine.begin() as conn:
        install_append_only(conn)
        if engine.dialect.name == "postgresql":
            from .tenancy import install_rls
            install_rls(conn)


log = logging.getLogger("laabam.db")


def _add_missing_columns(engine: Engine) -> None:
    """A dev database made by an older version lacks newer columns, and create_all won't add them. Add the ones
    that can be added safely (nullable, or with a server default). Anything else needs Alembic."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                if not col.nullable and col.server_default is None:
                    raise RuntimeError(f"{table.name}.{col.name} is missing from the database; run Alembic")
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}"
                if col.server_default is not None:
                    ddl += f" DEFAULT {col.server_default.arg.compile(dialect=engine.dialect)}"
                conn.execute(text(ddl))
                log.warning("added column %s.%s to an older dev database", table.name, col.name)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    sessions = sessionmaker(engine, expire_on_commit=False)

    @event.listens_for(sessions, "after_commit")
    def _run_after_commit(session: Session) -> None:
        for fn in session.info.pop("after_commit", []):
            try:
                fn()
            except Exception:  # noqa: BLE001 - side effects never undo a commit
                log.exception("after-commit action failed")

    @event.listens_for(sessions, "after_rollback")
    def _drop_after_commit(session: Session) -> None:
        session.info.pop("after_commit", None)

    return sessions


def after_commit(s: Session, fn: Callable[[], None]) -> None:
    """Run fn once the transaction commits (Soundbox, SMS: never for a change that rolled back)."""
    s.info.setdefault("after_commit", []).append(fn)
