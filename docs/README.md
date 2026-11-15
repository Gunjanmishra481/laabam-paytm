# Docs

- `../BUILD_PLAN.md` — product rules, architecture, phases.
- `partner-api.md` — adapters and partner events (Phase 6).

## Foundations (Phase 0)

Copied from Undo and adapted, not imported:

| Piece | Where |
|---|---|
| `TZDateTime` (store UTC, show IST with a fixed +05:30) | `backend/laabam/models.py`, `clock.py` |
| ids, money in integer paise | `ids.py`, `money.py` |
| Append-only audit log (DB triggers, Postgres + SQLite) | `db.py`, `audit.py`, first migration |
| Durable timers: atomic claim, backoff, `needs_attention` | `timers.py` |
| Idempotency keys, inbound event de-duplication, HMAC signatures | `idempotency.py`, `security.py` |
| `llm.fill` and the "digit in model output → template" guard | `llm.py` |
| Adapter registry with sandbox-only implementations | `adapters/__init__.py` |
| SQLite + Postgres test conftest | `backend/tests/conftest.py` |
| PWA (manifest, service worker in production only), single-file preview | `frontend/` |
