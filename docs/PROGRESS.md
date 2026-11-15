# Progress

Updated with every commit. The current plan is docs/SECURITY_PLAN.md (phases S0-S6); the product plan is BUILD_PLAN.md.

## Security hardening and AWS (docs/SECURITY_PLAN.md)

- [x] S0 threat model and baseline: `docs/security/THREAT_MODEL.md` (assets, trust boundaries, STRIDE per flow,
      10 conflicts C1-C10 with the code today), `docs/security/ASVS.md` (OWASP ASVS 4.0.3 L2, 259 items:
      76 met, 46 partial, 72 gaps, 65 N/A). Reviewed; C1-C10 decided (with changes to C1, C3, C4, C5, C6, C9)
- [x] S1 identity and tenancy: ES256 access tokens (KMS in AWS, local key in dev), rotating refresh in a
      __Host- cookie + X-Laabam-Refresh header, device sessions (list, revoke, sign out; new device announced on
      the Soundbox), partner assertion exchange (docs/partner-identity.md; sandbox partner signs the same format),
      LAABAM_ENV and fail-closed config, Postgres RLS on every tenant table (app role laabam_app; ids-only SECURITY
      DEFINER lookups owned by laabam_definer), cross-tenant tests generated from the route list, rate limits on
      sign-in/refresh/demo shops, C1 demo shops of their own. ASVS: 102 met, 46 partial, 46 gaps, 65 N/A
- [x] S2 inputs and outside services: strict request models (extra=forbid, limits), RequestGuard (JSON 64 KB,
      415 on other types, repeated query keys refused, no-store/nosniff), presigned photo uploads (S3 in AWS, signed
      local URL in dev) + worker re-encode (EXIF and appended data stripped, 40 MP pixel cap, original deleted), audio
      codec magic-byte check and WAV duration, rate limits on the costly routes, outbound allowlist + timeouts +
      circuit breaker for every partner call, prompt-injection tests, no docs/versions outside dev, lint bans raw
      HTML/eval. ASVS: 123 met, 31 partial, 40 gaps, 65 N/A
- [x] S3 privacy and data: no customer phone numbers (C2: SMS bill and `sales_bills.sms_phone` removed); the
      audit log holds HMAC-SHA256 digests under a per-shop key, never words (C3: keys wrapped by KMS with the shop id
      as encryption context, local AES-GCM in dev; `audit.record` refuses raw text; deleting a shop deletes its key);
      Postgres TLS verify-full with the RDS CA (config refuses less in demo/prod); daily retention sweep (transcripts,
      heard text, photos after 365 days); `GET /v1/me/export` (zip, audited, rate-limited); `DELETE /v1/me` (typed
      shop name + sign-in within 10 min, 7-day grace, cancellable, audited); JSON logs with request ids, redaction
      filter, access denials logged, last-resort 500 handler; "What Laabam keeps", Download my data and Delete my shop
      in Settings (en/ta/hi); a bill read by the Gemini fallback says so (C5). ASVS: 141 met, 23 partial, 30 gaps, 65 N/A
- [ ] S4 pipeline (checkpoint: CI passing with all scans)
      Started: hash-pinned lockfiles (requirements.in/-dev.in -> uv pip compile --universal --generate-hashes),
      CI installs with --require-hashes, unused langgraph dropped; pip-audit and npm audit clean (2026-11-15).
      Still to do: CI scans (pip-audit, npm audit, gitleaks, CodeQL, Trivy, SBOM, Dependabot), hardened Dockerfile
      (bases pinned by digest; RDS CA bundle by sha256), infra/ CDK OIDC deploy role (not deployed), GitHub settings list
- [ ] S5 AWS infrastructure (checkpoint: cost estimate and cdk-nag; no deploy without go-ahead)
- [ ] S6 prove it (checkpoint: docs/security/REPORT.md)

## Product (BUILD_PLAN.md)


## Where things stand (2026-11-14)

- [x] Phase 3: bill photos → checked cost lines → owner confirms (Bills in, Check a read bill)
- [x] Bill photos read by Sarvam Document AI on the worker, Gemini fallback, offline fixtures
- [x] Phase 4: profit maths, method per line ("about" for estimates); `/v1/profit`, `/v1/profit/items/{id}` with sources
- [x] Phase 4: alerts (price rise ≥5% or ≥₹2, margin <8%, slow after 14 days, running out) on the worker;
      `/v1/alerts`, `/seen`, `/acted`; scan after each confirmed bill and nightly at 02:00 IST
- [x] Phase 4: Demo Kirana month (`demo_seed.py`) with the planted findings + `test_demo_seed.py`; the
      oil bill is the "balaji" sample photo (dated six days ago); sandbox reset seeds it (`history: false` to skip)
- [x] Screen 4.1 Profit (`/profit`): matched to 12-Profit; adds "+ N more items" so every item is reachable
- [x] Screen 4.2 Item and its sources (`/items/:id`): matched to 13-ItemDetail; bill lines open Check a read bill,
      voice bills open in a sheet; "about" on the litres sold (an estimate); Draft a message -> `/ask/draft/:id`
- [x] Screen 4.3 Alerts (`/alerts`): matched to 14-Alerts; one action + Got it; empty state; Counter shows the
      top alert (amber banner) and today's "Profit, about"; low-margin text no longer claims "other dairy" (no categories)
- [x] Screen 4.4 Ask (`/ask`, `/ask/draft/:item`): `POST /v1/ask`, `/v1/ask/voice`, `/v1/speak`; rule-based intents
      (why down, reorder, best item, draft) in en/ta/hi, figures in code, templates (model text guarded: any digit ->
      template); source chips; Listen (Sarvam TTS or the phone's voice); drafts via Web Share, never sent
- [x] Screen 4.5 Evening summary (`/summary`): timer `evening_summary` at closing time (moves with it), figures in
      code, en/ta/hi templates (model text guarded), spoken on the Soundbox; `GET /v1/summaries/latest`,
      `POST /v1/summaries` (today so far), sandbox `POST /sandbox/summary/{merchant}`; Counter links to it after closing
- [x] Phase 5 backend: `GET /v1/plans/festival` (usual week = last 21 days / 3, x 1.5/2/2.5, 2x default, a guess;
      cheapest current distributor price; slow and no-cost items left out), `GET /v1/plans/reorder`, `POST /v1/restock-plans`,
      `.../loan-offer` (shortfall = total - cash in hand, never assumed), `/v1/loan-offers/{id}/approve|decline` via
      the sandbox lender (terms from the partner: none in the sandbox). Seed tuned: Deepavali 2x = ₹40,000, ₹15,000 in
      hand, ₹25,000 loan. Cheaper distributor (Item detail) and the negotiation draft (Ask) were done with Phase 4
- [x] Screen 5.1 Festival plan (`/plan`): matched to 17-Plan; 1.5/2/2.5x (2x default, "a guess"), top 5 lines +
      "N more items", order total, cash in hand (Edit sheet), short by, loan link only when short. `/plan/reorder`: the
      reorder list (not drawn in the design; uses the plan's list style), from alerts and the summary
- [x] Screen 5.2 Loan offer (`/plan/loan/:id`): matched to 18-Loan; partner terms as placeholders until the partner
      gives them; the basis; Approve and apply (sent status, buttons hidden) / Not now
- [ ] Checkpoint 3: the oil story end to end; Deepavali plan with the ₹25,000 loan offer. Shown 2026-11-14,
      waiting for the owner's review. Next after sign-off: Phase 6 (hardening and the demo)

## Decisions carried forward

- Open: the plan is for the next Deepavali (2027-10-29, "in 349 days"). `LAABAM_PLAN_FESTIVAL=pongal` plans for
  Pongal (14 Jan) instead; any festival can be added to `backend/laabam/festivals.yaml`.
- Open: on the item page and in Ask, oil sold this week reads "about" (sales said without a quantity are estimates);
  the design shows it without "about".

- Oil price rise: the demo's new oil bill is dated six days ago (as if photographed late), so the whole
  week's oil sales are costed at ₹156 and "₹368 less this week" is literally true.
- Deepavali 2026 was 8 November, so the plan shows the next Deepavali from a festival calendar in config.
  The ₹40,000 / ₹15,000 / ₹25,000 figures don't depend on the date.
