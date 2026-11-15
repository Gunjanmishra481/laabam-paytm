# Laabam — notes for Claude Code

Laabam shows kirana owners their real profit. Voice billing at the counter ("rice 200, sugar 100")
→ a checklist the owner confirms → a Paytm QR for the exact amount. Distributor bill photos give costs.
Profit per item, price-creep alerts, restock plans, loan offers, and an evening summary on the
Soundbox in the owner's language. Paytm Build for India AI Hackathon, Track 1 (Merchant Growth AI),
and a real product Paytm plugs in. One project; the demo runs on sandbox adapters.

Repo: github.com/Gunjanmishra481/laabam-paytm (the hackathon submission; started 2026-11-15 from the tracked files of
Gunjanmishra481/laabam at 48aefca, which is kept as it was). Work only here, in small commits.

**Read `BUILD_PLAN.md` first.** Work phase by phase, commit after each, stop at every Checkpoint.

## Rules the code relies on
- No QR until the owner confirms the checklist. No order, message or loan without a tap.
- No figure invented by a model: prices must appear in the transcript; totals, margins and summaries'
  numbers come from code; any digit in raw model text → template fallback.
- Every figure links to its source bill lines and payments.
- Push-to-talk only; no audio stored by default.
- No catalogue or customer records needed; the price list learns from what the owner says.
- Money in integer paise. Show IST with a fixed +05:30 offset (no zoneinfo).
- Regional languages through a language setting and strings table; never hard-code one language.
- Audit log is append-only.
- Customer credit (udhaar) is out of scope for v1.

## Reuse from Undo
Copy (don't import) from github.com/Gunjanmishra481/undo: TZDateTime, ids, money helpers,
append-only audit triggers, durable timers, idempotency + HMAC webhooks, `llm.fill` guard, adapter
registry with sandbox routes, the SQLite + Postgres test conftest, the PWA and single-file preview setup.

## Commands
```bash
cd backend && python -m venv .venv && source .venv/Scripts/activate   # Windows Git Bash
pip install -r requirements-dev.txt && python -m pytest -q && ruff check .   # hash-pinned lock (uv pip compile)
LAABAM_SANDBOX=1 python -m uvicorn laabam.app:app --port 8000
LAABAM_DATABASE_URL=sqlite:///mig.db alembic revision --autogenerate -m "..."   # then fix types (below)
cd frontend && npm install && npm run dev
npm run lint && npm run build && npm run build:preview
```
Postgres tests: `docker compose up -d db`; conftest's default URL
(`postgresql+psycopg://laabam:laabam@localhost:55432/postgres`) then picks it up, and every DB test runs
on SQLite and Postgres. `LAABAM_REQUIRE_PG=1` fails the run if Postgres is missing (CI sets it).
On this Windows machine the docker CLI isn't on Git Bash's PATH:
`export PATH="/c/Users/manas/AppData/Local/Programs/DockerDesktop/resources/bin:$PATH"`.

Keys live in `backend/.env` (git-ignored, never committed). The app reads only environment variables, so load the file
into the shell before starting: `cd backend && set -a && . ./.env && set +a && python -m uvicorn laabam.app:app --port 8000`.

Speech: `LAABAM_SPEECH=sarvam` + `LAABAM_SARVAM_API_KEY` (models: `LAABAM_SARVAM_STT_MODEL`, default
saaras:v4; `LAABAM_SARVAM_TTS_MODEL`, default bulbul:v3). Offline speech answers only the sample clips in
`laabam/speech_samples.yaml` (tests send `X-Laabam-Clip`); the sandbox demo plays them in turn.

Vision (bill photos): `LAABAM_VISION=sarvam` (default) | `gemini` | `offline`. Sarvam uses Document AI Extract
(`/doc-ai/v1/job/extract`, then poll status, then fetch results) with the speech key `LAABAM_SARVAM_API_KEY`;
JPEG/PNG go as they are, WebP/HEIC are converted. A live read runs on the worker (timer `purchase_read`): the bill is
"reading", then "read", or "failed" with Try again. `LAABAM_VISION_FALLBACK=gemini` (default; used only if
`LAABAM_GEMINI_API_KEY` is set) reads when Sarvam fails. A chosen reader without its key drops to offline with a
warning. Offline vision answers only the sample bills in `laabam/bill_samples.yaml` (tests send `X-Laabam-Sample`);
their pictures are `frontend/public/samples/bills/*.svg`. Live comparison on photo renders of them:
`cd backend && python -m scripts.compare_vision` (needs keys, spends credit; truth in `tests/data/bills/truth.yaml`).

E2E (counter flow, push-to-talk with Chromium's fake mic, bills in): `npm run build`, start the API with `LAABAM_SANDBOX=1` (it serves `frontend/dist`),
then `cd frontend && npx playwright test`. Demo view: http://localhost:8000/demo

## Conventions
- Migrations: autogenerate, then replace custom column types with `sa.DateTime(timezone=True)`;
  new NOT NULL columns need a `server_default`.
- Every real adapter has a sandbox twin; tests never call live services.
- User-facing copy: short, plain sentences.
