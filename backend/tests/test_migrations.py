"""Alembic migrations build the same schema as the models, append-only triggers included."""
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from laabam.models import Base


def _config(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "migrations")
    cfg.attributes["url"] = url
    return cfg


def test_migrations_match_models(migration_db_url):
    db_url = migration_db_url
    cfg = _config(db_url)
    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": False}), Base.metadata)
        assert diff == []
        conn.execute(text("INSERT INTO audit_events (actor, kind, subject, detail, ts) "
                          "VALUES ('system', 'x', '', '{}', CURRENT_TIMESTAMP)"))
        conn.commit()
        with pytest.raises((IntegrityError, DBAPIError)):
            conn.execute(text("UPDATE audit_events SET kind = 'y'"))
        conn.rollback()
        if conn.dialect.name == "postgresql":  # row-level security on every tenant table
            from laabam.tenancy import TENANT_TABLES
            have = set(conn.execute(text("SELECT tablename FROM pg_policies WHERE policyname = 'tenant'")).scalars())
            assert have == set(TENANT_TABLES) | {"merchants"}
            rls = dict(conn.execute(text("SELECT relname, relrowsecurity FROM pg_class WHERE relname = ANY(:t)"),
                                    {"t": list(TENANT_TABLES)}).all())
            assert all(rls.values()) and len(rls) == len(TENANT_TABLES)
    command.downgrade(cfg, "base")
    engine.dispose()
