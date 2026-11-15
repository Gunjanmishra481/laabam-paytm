# Laabam

Laabam shows a kirana owner their **real profit**, not just sales. Bill by voice at the counter
("rice 200, sugar 100"), confirm the checklist, and show a Paytm QR for the exact amount.
Distributor bill photos give costs. Laabam works out profit per item, warns when costs creep,
plans restocks, and speaks an evening summary on the Soundbox in the owner's language.

Built for the Paytm Build for India AI Hackathon (Track 1: Merchant Growth AI). The demo runs on
sandbox adapters; the shop and its numbers are synthetic.

See [`BUILD_PLAN.md`](BUILD_PLAN.md) for the product rules, architecture and phases.

## Run it

Backend (Windows Git Bash; on macOS/Linux use `.venv/bin/activate`):

```bash
cd backend
python -m venv .venv && source .venv/Scripts/activate
pip install -r requirements-dev.txt
python -m pytest -q
LAABAM_SANDBOX=1 python -m uvicorn laabam.app:app --port 8000
```

Frontend:

```bash
cd frontend
npm install
npm run dev            # http://localhost:5173, proxies the API on :8000
npm run build:preview  # one self-contained HTML file in dist-preview/, API simulated in the page
```

The demo: `npm run build` in `frontend/`, start the API with `LAABAM_SANDBOX=1` (it serves the built
app), and open http://localhost:8000/demo. The owner's phone is on the left; the Soundbox simulator and
a "behind the scenes" panel (transcript, parsed lines, price checks, adapter calls, the SMS text) are on
the right. "Customer pays" plays the customer scanning the QR on the Soundbox.

Voice billing: hold the mic button and say the order. With no speech service set up, the demo shows
"Play a sample voice order" instead and plays the sample clips in `backend/laabam/speech_samples.yaml`.
For live speech (Sarvam: saaras speech-to-text, bulbul text-to-speech for the Soundbox):

```bash
LAABAM_SANDBOX=1 LAABAM_SPEECH=sarvam LAABAM_SARVAM_API_KEY=... python -m uvicorn laabam.app:app --port 8000
```

Audio goes to Sarvam once and is not stored; only the transcript is kept with the bill. Each voice
step's time is logged as JSON (`laabam.latency`) and shown in the demo's behind-the-scenes panel.
Phones only allow the microphone on HTTPS (or localhost), so to try it on a real phone serve the API
over HTTPS, e.g. a tunnel (`cloudflared tunnel --url http://localhost:8000`) or uvicorn's
`--ssl-keyfile/--ssl-certfile` with a local certificate.

Distributor bills ("Bills in" on the counter): photograph a bill, check the lines, confirm, and the
costs are saved per item (per kg / L / pc, from the pack size). With no vision service set up, the demo
offers three sample bills (printed, handwritten, and one with a misread amount the line check catches).
For live reading, set `LAABAM_SARVAM_API_KEY` in `backend/.env` (Sarvam Document AI reads the photo on the
worker; the same key as speech). Photos are kept on local disk under `backend/var/photos` in development.

End-to-end tests (counter flow, push-to-talk and bills in) against that running API: `cd frontend && npx playwright test`.

Postgres (optional locally; CI always runs it):

```bash
docker compose up -d db
export LAABAM_TEST_PG=postgresql+psycopg://laabam:laabam@localhost:55432/postgres
cd backend && python -m pytest -q          # every DB test now runs on SQLite and Postgres
LAABAM_DATABASE_URL=postgresql+psycopg://laabam:laabam@localhost:55432/laabam alembic upgrade head
```

## Layout

```
backend/   FastAPI app (laabam/), Alembic migrations, tests
frontend/  React + Vite PWA, single-file preview build
docs/      design notes and the partner API
infra/     AWS CDK (Phase 7)
```
