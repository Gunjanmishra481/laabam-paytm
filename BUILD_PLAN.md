# Laabam — build plan

Laabam shows a kirana owner their **real profit**, not just sales. Owners bill by voice at the counter
("rice 200, sugar 100"), confirm a checklist, and show a Paytm QR for the exact amount. Distributor
bills are read from photos for cost. Laabam works out profit per item, warns when costs creep, plans
restocks, and offers a loan sized to the next big order. It speaks to the owner in their own language
on the Soundbox.

Built for the Paytm Build for India AI Hackathon (Chennai, Track 1: Merchant Growth AI), and as a real
product: a feature Paytm plugs into Paytm for Business and the Soundbox. One project; the demo runs
on the same code against sandbox adapters.

This plan is written for Claude Code. Work phase by phase, commit after each phase, and stop at
every **Checkpoint** to show the user what was built before going on.

---

## 1. Rules the code must keep

These are product promises. Tests enforce them.

1. **The owner approves everything that matters.** No QR is shown until the owner confirms the bill
   checklist. No order, message or loan goes out without a tap. The agent proposes; it never acts alone.
2. **No figure is invented by a model.**
   - Voice: the model only *transcribes and structures* what the owner said. Every price on a bill
     line must be traceable to a number in the transcript (checked in code, see §5.2). Totals are
     added in code.
   - Bill photos: the model extracts line items; code checks each line (qty × rate ≈ amount) and the
     bill total. Lines that fail are flagged for the owner, never silently accepted.
   - Summaries and answers: the model writes text with placeholders (`{PROFIT_TODAY}`); code fills
     every number. Any digit in raw model output → fall back to a template. (Same rule as Undo.)
3. **Every figure has a source.** Each profit number links to the sales bills and purchase bill lines
   it came from. The UI lets the owner tap through.
4. **Listens only when asked.** The mic opens on push-to-talk or after "Laabam, bill". Audio is not
   stored by default; only the transcript is kept, with the bill.
5. **Works with no setup.** No catalogue, no customer records, no item master. The price list builds
   itself from what the owner says.
6. **Money in paise, integers only.** Never floats. IST for everything shown to the owner (fixed
   +05:30 offset, no zoneinfo: Windows has no tz database).
7. **Regional languages.** The owner picks a language; speech in, speech out and UI copy follow it.
   English is always available. Don't hard-code one language anywhere.
8. **Append-only audit log** for bills, confirmations, payments, alerts and loan offers.

---

## 2. Scope

### In (v1, for the hackathon and the first real pilot)
- Voice billing: push-to-talk → transcript → bill checklist → confirm → Paytm QR → paid → SMS bill.
- Spoken prices, with or without quantity ("rice 200", "rice 2 kg 120", "3 biscuit 30").
- Learned price list: once a price has been said, the item name alone is enough next time.
- Distributor bill photos → cost lines, distributor, date. Owner confirms or fixes.
- Item matching across voice bills and distributor bills (aliases, fuzzy match, owner can merge).
- Profit per item and per day; price-creep alerts; slow movers; stockout warnings.
- Restock list and a festival stock plan.
- Loan offer sized to a restock order (sandbox lending partner; owner approves).
- Evening summary on the Soundbox (text + speech), and voice questions ("why is profit down?").
- Soundbox simulator in the web app: shows the QR, plays the summary.

### Out (later)
- Customer credit (udhaar). Dropped for v1 by decision; see §12 for how it could return.
- GST invoices, inventory counts, multi-store, staff accounts.
- Distributor ordering integrations (v1 drafts a message; the owner sends it).

---

## 3. Architecture

```
Owner's phone (PWA)                     Soundbox (simulated in demo)
  push-to-talk, checklist, photos         QR screen, speaker
        │                                        ▲
        ▼                                        │
FastAPI ──► Agent (LangGraph) ──► Model providers (pluggable)
  │           proposes only         speech-to-text, vision, text, speech
  │
  ├─► Ledger (Postgres): items, prices, sales bills, purchase bills, payments, alerts, audit
  ├─► Paytm adapter: Dynamic QR, payment status / webhook        (sandbox | paytm)
  ├─► Soundbox adapter: show QR, play audio                       (simulator | paytm)
  ├─► SMS adapter: send the bill                                  (log | provider)
  ├─► Lending adapter: loan offer, decision                       (sandbox | partner)
  └─► Worker: durable timers (evening summary, alert scans, QR expiry)
```

### Stack (same as Undo, so patterns and code can be reused)
- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Postgres (SQLite for dev and tests),
  LangGraph for the agent, httpx.
- **Frontend:** React 19 + TypeScript + Vite PWA (manifest, icons, service worker in production only).
  Single-file phone preview build with an in-page mock API, as in Undo.
- **Infra (later phase):** AWS CDK in TypeScript, ECS Fargate (API + worker), RDS Postgres, S3 for bill
  photos, Secrets Manager. App Runner is closed to new customers; don't use it.
- **Model providers, all behind interfaces with an `offline` implementation for tests and demos:**
  - Speech-to-text and text-to-speech for Indian languages: Sarvam (check current model names and
    API at build time).
  - Bill reading: a vision model (Gemini by default; configurable).
  - Text: Bedrock or Gemini, configurable. Offline = templates.

### Patterns to copy from Undo (`github.com/Gunjanmishra481/undo`)
- `TZDateTime` (store UTC, show IST), `new_id`, money helpers.
- Append-only audit triggers for Postgres and SQLite (`install_append_only`).
- Durable timers with atomic claim, backoff, `needs_attention`.
- Idempotency keys on every partner call; HMAC-signed webhooks in and out.
- `llm.fill()` placeholder filling and the "digit in model output → template" guard.
- Adapter registry with `sandbox` / real implementations; sandbox HTTP routes behind `LAABAM_SANDBOX=1`.
- Test conftest that runs every test on SQLite and on Postgres when reachable.
Copy the code in, don't import across repos. Keep the two projects independent.

---

## 4. Data model

All money in paise (`int`). All times `TZDateTime`. Every table has `merchant_id` where it applies.

| Table | Key columns | Notes |
|---|---|---|
| `merchants` | id, name, language, upi_vpa, paytm_mid, soundbox_id, created_at | One shop. |
| `items` | id, merchant_id, name, unit (`kg`/`L`/`pc`/`pack`/null), aliases (JSON) | Created on first mention. Owner can rename or merge. |
| `price_points` | id, item_id, price_paise, per_qty, per_unit, source (`voice`/`edit`), seen_at, sales_line_id | The learned price list. Current price = latest point. |
| `sales_bills` | id, merchant_id, status (`draft`/`confirmed`/`paid`/`cancelled`/`expired`), transcript, language, total_paise, qr_order_id, qr_payload, paid_at, payment_ref, created_at | Draft until the owner confirms. |
| `sales_lines` | id, bill_id, item_id, qty (decimal as string or milli-units int), unit, line_total_paise, unit_price_paise (nullable), heard_text, price_source (`spoken`/`learned`) | `heard_text` = the transcript span the line came from. |
| `distributors` | id, merchant_id, name, phone | From bill headers. |
| `purchase_bills` | id, merchant_id, distributor_id, bill_date, photo_key, status (`read`/`confirmed`), total_paise, read_issues (JSON) | Photo in S3 (local disk in dev). |
| `purchase_lines` | id, purchase_bill_id, item_id, qty_milli, unit, unit_cost_paise, line_total_paise, raw_text, check_ok | `check_ok` = qty × rate matches amount. |
| `payments` | id, merchant_id, order_id, amount_paise, status, paytm_txn_id, received_at | From the Paytm adapter (status poll or webhook). |
| `alerts` | id, merchant_id, kind (`price_creep`/`slow_mover`/`stockout`/`low_margin`), item_id, detail (JSON), status (`open`/`seen`/`done`), created_at | |
| `restock_plans` | id, merchant_id, kind (`weekly`/`festival`), festival, lines (JSON), total_cost_paise, status | Drafts the owner can edit. |
| `loan_offers` | id, merchant_id, restock_plan_id, amount_paise, cash_on_hand_paise, status (`offered`/`approved`/`declined`/`disbursed`), partner_ref | Lending partner decides; Laabam only sizes and relays. |
| `summaries` | id, merchant_id, date, text, audio_key, figures (JSON) | `figures` = the numbers code filled in. |
| `audit_events` | id, merchant_id, actor (`owner`/`agent`/`system`/`partner`), kind, subject, detail, ts | Append-only. |
| `timers`, `inbound_events`, `idempotency_keys` | as in Undo | |

---

## 5. Core flows

### 5.1 Voice billing (the headline feature)

1. Owner taps **Bill** (push-to-talk) or says "Laabam, bill" (wake word is a stretch goal; push-to-talk
   is the v1 default).
2. Audio streams or uploads to `POST /v1/bills/voice` → speech-to-text in the merchant's language →
   transcript. Text input (`POST /v1/bills/text`) does the same without audio; used by tests, the
   demo fallback, and accessibility.
3. **Parse** (agent node `parse_bill`): the model returns structured lines:
   `{item_text, qty?, unit?, spoken_price?}` plus the transcript span for each. Prompt asks for
   structure only.
4. **Resolve** (code, not the model), for each line:
   - Match `item_text` to an existing item (exact alias → normalised name → fuzzy above threshold);
     otherwise create a new item.
   - Price:
     - spoken price present → `line_total = spoken_price` (if qty given with a per-unit phrase like
       "2 kg at 60", `line_total = qty × 60`; the parser marks which);
     - no price → use the learned price for that item × qty; mark `price_source = learned`;
     - no price and nothing learned → line flagged "price needed".
5. **Verify** (code): every spoken price on a line must appear in the transcript as digits or as a
   number word in the merchant's language (keep a small number-word table per supported language;
   start with English plus two regional languages, add more as you go). A mismatch flags the line.
   Total = sum of line totals, in code.
6. **Checklist** shown to the owner: each line (item, qty, amount, a "learned price" badge where it
   applies), flagged lines in amber, the total, and two actions: **Confirm, show QR** / **Fix a line**.
   Fixing is by tap (edit amount, qty, item) or by voice ("sugar 120 not 100").
   Confirm is disabled while any line is flagged.
7. **Confirm** → status `confirmed`, audit event, write `price_points` for spoken prices, call the
   Paytm adapter `create_qr(order_id=bill.id, amount)`, show the QR on the phone and push it to the
   Soundbox screen.
8. **Paid** → Paytm webhook or status poll → `payments` row → bill `paid` → Soundbox announces the
   amount → SMS bill to the customer if the owner entered a number (optional field on the checklist;
   no customer records are kept).
9. QR expires after N minutes (timer) → bill `expired`; the owner can re-show or cancel.

Edge cases to handle and test: repeated item in one order; "and" / filler words; numbers said in a
regional language; prices with paise ("12.50"); the owner correcting mid-sentence ("rice 200, no,
250"); empty transcript; partial payment or overpayment from a static QR (reconcile and flag);
two bills open at once (allowed; each has its own QR).

### 5.2 Distributor bill photo → costs

1. `POST /v1/purchases/photo` with the image → stored → vision model extracts header (distributor,
   date, bill no.) and lines `{raw_text, qty, unit, rate, amount}`.
2. Code checks each line (`qty × rate ≈ amount` within ₹1) and the sum against the bill total. Failed
   lines get `check_ok = false` and show amber.
3. Item matching as in 5.1, plus a learned alias table (distributor names differ from what the owner
   says: "SUNFLWR OIL 1LTR POUCH" ↔ "oil"). Unmatched lines ask the owner once: "Is this oil?"
4. Owner confirms → `confirmed`, unit costs available to profit maths.

### 5.3 Profit maths (code only)

- **Selling price per unit** for an item: from voice bills with a quantity, or from repeated
  no-quantity lines where the owner's usual unit is known. Until known, per-item profit uses the
  revenue-weighted margin: `margin_rate = 1 − unit_cost / unit_price` from the latest confirmed
  purchase line and the latest learned price for the same unit.
- **Profit for a sales line** = `line_total × margin_rate(item)` when the unit price is unknown, or
  `(unit_price − unit_cost) × qty` when it is known. Record which method was used.
- **Daily profit** = sum over paid bills that day; also show revenue from UPI payments that weren't
  billed by voice ("unbilled sales"), so the owner sees coverage honestly.
- Items with no cost yet are listed as "cost unknown", never guessed.

### 5.4 Alerts (worker job, after each confirmed purchase bill and nightly)
- **Price creep:** unit cost up by ≥ X% or ≥ ₹Y vs the previous bill for the same item → alert with
  both bill lines and the profit impact at current sales volume.
- **Low margin:** margin below a threshold for an item with real sales.
- **Slow mover:** stock bought, little or no sales in N days.
- **Stockout risk:** sales velocity × days to next usual restock > last purchase quantity.
  Thresholds in config; tests cover each.

### 5.5 Restock and festival plans
- Weekly restock list from sales velocity and purchase cycles.
- Festival plan: a festival calendar (config file) × last year's uplift for that festival if history
  exists, else a conservative default, clearly labelled as an assumption.
- Options: cheaper distributor for the same item (from purchase history); a drafted message to
  negotiate (owner sends it; Laabam never sends to distributors in v1).

### 5.6 Loan offer
- For a restock plan: `gap = plan_total − cash_on_hand` (cash on hand is entered or confirmed by the
  owner, never assumed). If `gap > 0`, ask the lending adapter for an offer of that amount.
- Show the offer with its basis (plan lines, cash on hand, the uplift assumption) and Approve / Not now.
  The lending partner makes the credit decision. Sandbox adapter returns a deterministic offer.

### 5.7 Evening summary and voice questions
- Timer at the merchant's closing time (default 21:00 IST): build `figures` in code (revenue, profit,
  top items, alerts), fill a template or a placeholder-only model draft, speak it in the merchant's
  language, send to the Soundbox, save in `summaries`.
- Questions ("why is profit down?", "what should I reorder?"): agent picks a tool (profit breakdown,
  alerts, restock plan), code computes, the model phrases the answer with placeholders, and the answer
  lists its sources (bill lines, dates).

---

## 6. API (first cut)

Owner app (JWT, aud `laabam`):
- `POST /v1/bills/voice` (audio) · `POST /v1/bills/text` → draft bill with checklist
- `PATCH /v1/bills/{id}/lines/{line_id}` · `POST /v1/bills/{id}/lines` · `DELETE …/lines/{line_id}`
- `POST /v1/bills/{id}/confirm` → QR · `POST /v1/bills/{id}/cancel` · `GET /v1/bills/{id}`
- `POST /v1/purchases/photo` · `GET/PATCH /v1/purchases/{id}` · `POST /v1/purchases/{id}/confirm`
- `GET /v1/items` · `PATCH /v1/items/{id}` · `POST /v1/items/{id}/merge`
- `GET /v1/profit?from&to` · `GET /v1/profit/items/{id}` (with sources)
- `GET /v1/alerts` · `POST /v1/alerts/{id}/seen`
- `GET/POST /v1/restock-plans` · `POST /v1/restock-plans/{id}/loan-offer` · `POST /v1/loan-offers/{id}/approve|decline`
- `POST /v1/ask` (voice or text question) · `GET /v1/summaries/latest`
- `GET/PATCH /v1/me` (language, closing time, cash on hand)

Partner (signed, idempotent):
- `POST /v1/events` — `payment.succeeded`, `payment.failed`, `loan.decided`, `soundbox.ack`

Sandbox (`LAABAM_SANDBOX=1` only):
- `POST /sandbox/session` (demo owner token) · `POST /sandbox/reset`
- `POST /sandbox/qr/{order_id}/pay` (play the customer paying)
- `GET /sandbox/soundbox/{merchant}` (simulator feed) · `POST /sandbox/timers/{id}/fire`

---

## 7. Adapters

Each adapter is a protocol with a sandbox implementation and a real one, picked per merchant.

- **PaymentsAdapter**
  - `create_qr(order_id, amount_paise) -> {payload, expires_at}`
  - `payment_status(order_id)`
  - Sandbox: builds a standard UPI intent string
    (`upi://pay?pa=<vpa>&pn=<name>&am=<rupees>&cu=INR&tr=<order_id>&tn=<note>`), renders it as a QR,
    and "pays" through the sandbox route.
  - Paytm: Create QR Code API (`/paymentservices/qr/create`, needs `posId`, amount as a two-decimal
    string, checksum signature) and the transaction status API. Dynamic QR is offered to selected
    enterprise merchants today, so production assumes Paytm enables it for Laabam merchants. Record
    the Paytm transaction ID against the order ID for reconciliation.
- **SoundboxAdapter:** `show_qr(payload, amount)`, `announce(text, audio)`, `play(audio)`. Simulator = a
  panel in the web app fed by SSE or polling.
- **SmsAdapter:** `send_bill(phone, text)`. Sandbox logs it.
- **LendingAdapter:** `offer(merchant, amount, basis)`, `decision(ref)`.
- **Speech / Vision / Text providers:** `transcribe(audio, language)`, `speak(text, language)`,
  `read_bill(image)`, `draft(template_hint, figures)`. Offline implementations use fixtures:
  a transcript table keyed by sample audio file names, canned bill-photo JSON, templates.

---

## 8. Frontend (PWA)

Screens:
1. **Counter (home):** big push-to-talk button, live transcript, today's sales and profit at the top.
2. **Checklist:** lines with tick marks, amber for flagged, learned-price badges, edit by tap or voice,
   optional customer phone for the SMS bill, **Confirm, show QR** / **Fix a line**.
3. **QR:** full-screen QR with the amount; "Paid" state with the amount spoken aloud.
4. **Bills in:** camera capture for distributor bills, the read result with amber lines, confirm.
5. **Profit:** today and this week; per item with margin; tap any number → its sources.
6. **Alerts and plans:** price creep, slow movers, restock list, festival plan, loan offer card.
7. **Ask:** voice question → answer with sources.
8. **Settings:** language, closing time, cash on hand, UPI details.

Demo view (`/demo`): the phone on the left; a Soundbox simulator (QR screen + speaker) and a
"behind the scenes" panel on the right (transcript, parsed lines, checks, adapter calls).

Design: take direction from the user before building screens; a design pass happens at Checkpoint 2.
Large touch targets (≥ 48px), high contrast, works one-handed, usable on a ₹8,000 Android phone.
All copy through a strings table keyed by language.

---

## 9. Phases

Each phase ends with tests passing on SQLite and Postgres and a commit. Stop at each checkpoint.

### Phase 0 — Repo and skeleton
- New repo `laabam`: `backend/`, `frontend/`, `docs/`, `infra/` (empty for now), `CLAUDE.md`, this file.
- Copy the reusable pieces from Undo (§3). Settings, health check, test conftest (SQLite + Postgres).
- Docker Compose for local Postgres. GitHub Actions: lint + tests.
- **Done when:** `pytest` and `npm run build` pass in CI.

### Phase 1 — Ledger and voice billing (text input first)
- Models + first migration for merchants, items, price_points, sales_bills, sales_lines, audit.
- `POST /v1/bills/text` → parse (offline parser for tests: a rule-based parser that handles the
  sample phrases) → resolve → verify → checklist.
- Line edits, confirm, cancel. Price list learning.
- Tests: 30+ phrase cases (with/without qty, corrections, number words, repeats, paise), the
  "price must appear in transcript" rule, total in code, no confirm with flagged lines.
- **Done when:** a typed order produces the right checklist and confirming writes price points.

### Phase 2 — QR, payment, Soundbox simulator, PWA counter flow
- PaymentsAdapter sandbox (UPI intent QR) + Paytm implementation behind config (unit-tested against
  recorded request/response shapes; no live calls in tests).
- Payment events, expiry timer, SMS sandbox.
- PWA: Counter, Checklist, QR, Paid. Demo view with Soundbox simulator.
- **Checkpoint 1:** show the full text-input billing flow end to end in the browser.
- Then real speech: SpeechProvider (Sarvam) behind `LAABAM_SPEECH=sarvam`, push-to-talk recording in
  the PWA, offline fixtures for tests. Measure and log latency per step.
- **Checkpoint 2:** voice billing on a real phone; agree the visual design before more screens.

### Phase 3 — Distributor bills and costs
- Purchase models + migration, photo upload, VisionProvider (offline fixtures + real provider),
  line checks, item matching with learned aliases, confirm flow, Bills-in screen.
- Tests: fixture bills including a handwritten one, a misread line (check fails), unmatched items.
- **Done when:** a photo becomes confirmed cost lines linked to items.

### Phase 4 — Profit, alerts, sources
- Profit maths (§5.3) with method recorded; profit endpoints with sources; Profit screen with
  tap-through.
- Alert scans (§5.4) on the worker; Alerts screen.
- Seed a synthetic shop ("Murugan Stores", one month of voice bills, purchase bills and UPI payments)
  with the planted findings from the pitch deck: oil cost up ₹8/L, ₹6,200 of slow stock, biscuits
  stocking out, milk at 3.7% margin. A test asserts Laabam finds each one.
- **Checkpoint 3:** walk the deck's "one price rise, caught and fixed" story in the app.

### Phase 5 — Plans, loans, summary, questions
- Restock and festival plans; cheaper-distributor suggestion; negotiation message draft.
- Loan sizing + LendingAdapter sandbox; offer card with its basis.
- Evening summary timer, TTS, Soundbox playback; `/v1/ask` with tools and cited answers.
- Model-output guard tests (digits → template fallback) for summary and answers.
- **Done when:** the closing slide's loan card is reproduced from the seeded shop's data.

### Phase 6 — Hardening and the demo
- Phone preview build (single file, mock API) like Undo's, for sharing.
- Demo script (§10) rehearsed; fallback to text input if the venue network is bad.
- Accessibility pass, empty states, error states, offline banner.
- Docs: `docs/partner-api.md` (adapters and events), README with run steps for Windows Git Bash too.

### Phase 7 — AWS (only when the user asks)
- CDK stack like Undo's: ECS Fargate API + worker, RDS, S3 for photos, Secrets Manager, CloudFront,
  alarms, budget alert. Check costs with the user before deploying.

---

## 10. Demo script (3 minutes)

1. **Counter:** owner holds the button and says "rice 200, sugar 100, oil 165" → checklist appears →
   one tap on Confirm → QR on the Soundbox screen → "pay" from the sandbox → Soundbox says "₹465
   received".
2. **Next order:** "rice, oil" → prices fill in from what was said before (learned badge).
3. **Bill photo:** photograph a printed distributor bill → lines appear → oil is up ₹8 a litre →
   alert: profit per litre down from ₹17 to ₹9.
4. **Ask:** "why is profit down this week?" → spoken answer citing the oil bill line.
5. **Evening summary** plays on the Soundbox in a regional language.
6. **Deepavali:** festival plan → ₹40,000 order, ₹15,000 cash on hand → loan offer ₹25,000 →
   Approve.
Say on stage that the shop and its numbers are synthetic.

---

## 11. Testing

- Every rule in §1 has at least one test named after it.
- Phrase corpus for the bill parser in `backend/tests/data/phrases.yaml` (text, language, expected lines).
- Fixture bill photos + expected JSON in `backend/tests/data/bills/`.
- Adapters: sandbox tested end to end; real adapters tested against recorded shapes, never live.
- Seeded-shop test for the planted findings.
- Frontend: typecheck, lint, and a Playwright script for the counter flow (text input) in CI.

---

## 12. Risks and decisions

| Risk | Plan |
|---|---|
| Speech recognition errors at a noisy counter | Checklist confirmation is mandatory; edit by tap or voice; transcript-number check; text fallback. |
| Prices said without units make per-item profit fuzzy | Record the profit method per line; show "estimated" where margin-rate is used; prompt for the unit once per item. |
| Dynamic QR is enterprise-only at Paytm today | Sandbox uses a standard UPI intent QR; pitch assumes Paytm enables it for Laabam merchants. |
| Bill photos misread | Line arithmetic checks, amber lines, owner confirms. |
| Latency (speech → checklist) | Target under 3 s on 4G; stream audio; log per-step timings; cache item matching. |
| Privacy of counter audio | Push-to-talk only in v1; no audio stored by default; transcript kept with the bill. |
| Credit (udhaar) requested later | Dropped for v1. Could return without customer records: "put it on Ravi's account" keeps a running balance by spoken name. |

Decided with the user (2026-11-09):
- Languages: English, Tamil and Hindi.
- SMS bill: sandbox log only; the SMS text shows in the demo's behind-the-scenes panel. No real SMS provider yet.
- Closing time 21:00 IST. Alert thresholds: cost rise ≥ 5% or ≥ ₹2 per unit; low margin below 8%;
  slow mover after 14 days without sales.

Decided with the user (2026-11-10, Checkpoint 2):
- Speech: Sarvam (saaras:v4 speech-to-text, bulbul:v3 text-to-speech), tested live on the laptop.
- Visual design: keep the current look (deep green and cream, large buttons, plain text); later
  screens follow it.
- Bill photos: Gemini behind LAABAM_VISION=gemini; offline sample bills for tests and the demo.
