# Laabam — design spec for the frontend

This is the agreed visual design (Build plan, Checkpoint 2). Build the PWA screens to match it.

**What's in this folder**
- `DESIGN.md` — this spec: tokens, components, every screen, behaviour, and how screens map to the API.
- `screens/*.dc.html` — the design source for each screen. These are **reference markup, not runnable
  code**: they use a design-tool template syntax (`<x-dc>`, `{{hole}}`, `<sc-for>`, `<sc-if>`,
  `<dc-import>`, a `DCLogic` class). Read them for exact layout, spacing, sizes, colours and copy, and
  rebuild them as React components. Don't copy the template syntax or `support.js`.
- `screenshots/*.png` — every screen rendered at 2× (numbered in flow order), and `laabam-designs.pdf`
  with all of them, one per page. Open these to see the design; use the `.dc.html` files for exact values.
  The screenshots show the top of each screen; where content is taller than the screen (Plan, for
  example), the real screen scrolls above the tab bar.
- All figures, names and dates in the screens are sample data. In the app they come from the API
  (sandbox seed in the demo). Bracketed text like `[Your shop]` is a placeholder.

It's a **web app (PWA)**, mobile-first. Design width 390px. See "Larger screens" for tablet and desktop.

---

## 1. Tokens

Put these in one CSS file as custom properties and use them everywhere. Never hard-code a colour in a component.

```css
:root {
  /* colour */
  --green-900: #0F3D2A;   /* deep green: dark screens, summary cards */
  --green-800: #164D35;   /* cards on deep green */
  --green-700: #157347;   /* primary buttons, Paid screen, active tab */
  --green-line: #2E5A45;  /* borders on deep green */
  --saffron: #E9B866;     /* profit figures, accents and buttons on dark screens */
  --bg: #F4F6F1;          /* light screen background */
  --surface: #FFFFFF;     /* cards, inputs */
  --mint: #E3ECE0;        /* info panels, segmented control track */
  --mint-2: #DCEBDD;      /* soft tint, Hold-button halo */
  --ink: #12261B;         /* main text */
  --muted: #5B6E60;       /* labels, secondary text (passes 4.5:1 on --bg) */
  --on-dark-muted: #BFD3C5;
  --line: #DCE6D9;        /* card and input borders */
  --line-soft: #EEF2EC;   /* row dividers */
  --input-border: #C9D7C6;
  --amber-bg: #FDF0DB;    /* needs attention */
  --amber-line: #F2C894;
  --amber-text: #9A4A10;
  --danger: #9A2F1C;      /* price rises, remove, cancel */
  --disabled-bg: #C9D7C6; --disabled-text: #4A5B50;

  /* type */
  --font-display: 'Fraunces', Georgia, serif;   /* titles and money */
  --font-body: 'DM Sans', system-ui, sans-serif;

  /* radius */
  --r-sm: 10px; --r-md: 14px; --r-lg: 16px; --r-xl: 20px; --r-2xl: 22px; --r-sheet: 28px;

  /* spacing scale (px) */
  --s-1: 4px; --s-2: 8px; --s-3: 12px; --s-4: 16px; --s-5: 20px; --s-6: 24px;
}
```

- **Fonts:** self-host with `@fontsource-variable/fraunces` and `@fontsource-variable/dm-sans` so they
  work offline in the PWA. Tamil and Hindi text falls back to the system font; that's intended.
- **Type scale:** screen title 30px Fraunces 600 (26–28px on detail screens); big money 42–64px
  Fraunces 700; card figure 22–26px Fraunces 600; body 15–17px DM Sans; labels 13px DM Sans 700
  with 1px letter-spacing, uppercase; small notes 13–14px.
- **Numbers:** `font-variant-numeric: tabular-nums` on amounts in lists. Format money with
  `Intl.NumberFormat('en-IN')` so it reads ₹2,10,500. Money comes from the API in paise.
- **Colour use:** amber means "needs attention" and nothing else. Red-brown (`--danger`) is for cost
  increases and destructive actions. Things that must be told apart also differ in lightness.

---

## 2. Components

Build these once in `src/ui/` and reuse them.

| Component | Spec (see screens for exact values) |
|---|---|
| `Screen` | Full-height column, `--bg` (or `--green-900` for dark screens), padding 52–56px top, 20px sides. Respects `env(safe-area-inset-*)`. No fake status bar. |
| `TabBar` | Fixed bottom, 88px incl. safe area, white, top border `--line`. Four tabs: Counter, Bills in, Profit, Plan, each with a 24px stroke icon and a 12px label. Active = `--green-700`, weight 700. Source: `TabBar.dc.html`. Shown on those four screens only. |
| `IconButton` | 44×44, radius 14, white, 1px `--line` border. Always has an `aria-label`. Back, Settings, Ask. |
| `Button` | Primary: 56–58px tall, radius 16, `--green-700`, white 17–18px bold. Secondary: white with 1.5px `--input-border`. Danger text variant: `--danger`. Disabled: `--disabled-bg` with `--disabled-text` and a reason in the label ("Fix 1 line to continue"). |
| `HoldButton` | 172px circle. Idle: `--green-700`, mic icon, "Hold to bill", 12px `--mint-2` halo. Holding: `--saffron`, "Let go to check". See §4. |
| `Card` | White, radius 16–22. Dark variant `--green-900`/`--green-800`. |
| `ListRow` | Min height 48–64px, 1px `--line-soft` divider, label left, value right. Tappable rows are links/buttons. |
| `Banner` | Amber (`--amber-bg`, warning icon, `--amber-text` action) or info (`--mint`). |
| `SegmentedControl` | `--mint` track, white selected segment with a small shadow, `role="tablist"` or `radiogroup`. |
| `BottomSheet` | Scrim `rgba(15,61,42,0.45)`, white sheet with 28px top radius, drag handle, `role="dialog"`, focus trapped, Esc closes. |
| `Chip` | Pill. Source chips in Ask, transcript chips in Listening. |
| `Switch` | 52×32, `role="switch"`. Settings privacy toggle. |
| `QRCode` | Rendered from the payload with a QR library (e.g. `qrcode`). White quiet zone, 252px on the QR screen. |
| `Money` | Formats paise → ₹ en-IN; prop for "about" (estimates). |

---

## 3. Screens and routes

Billing is one flow with internal states (`listening → checklist → fix → qr → paid`), not separate
routes, so a refresh doesn't lose the bill: keep the draft bill id in the URL (`/bill/:id`).

| # | Screen | Route | Design source | Data / API |
|---|---|---|---|---|
| 1.1 | Welcome and language | `/welcome` | `Main.dc.html` | `PATCH /v1/me {language}` |
| 1.2 | Shop setup | `/setup` | `Setup.dc.html` | `PATCH /v1/me {name, upi_vpa, closing_time}` |
| 2.1 | Counter (home) | `/` | `Counter.dc.html` | `GET /v1/profit?from=today`, recent bills, open alerts count |
| 2.2 | Listening | `/bill/new` | `Listening.dc.html` | `POST /v1/bills/voice` (stream or upload); text fallback `POST /v1/bills/text` |
| 2.3 | Checklist | `/bill/:id` | `Checklist.dc.html` | `GET /v1/bills/:id`; untick = `DELETE …/lines/:line`; confirm = `POST /v1/bills/:id/confirm` |
| 2.4 | Fix a line | sheet on `/bill/:id` | `FixLine.dc.html` | `PATCH /v1/bills/:id/lines/:line`, or re-speak |
| 2.5 | QR | `/bill/:id` (state `confirmed`) | `QR.dc.html` | payload from confirm; poll `GET /v1/bills/:id` or SSE until `paid`/`expired` |
| 2.6 | Paid | `/bill/:id` (state `paid`) | `Paid.dc.html` | bill + today's running total |
| 2.7 | Soundbox screen | `/demo` panel | `Soundbox.dc.html` | sandbox `GET /sandbox/soundbox/:merchant` |
| 3.1 | Bills in | `/bills-in` | `BillsIn.dc.html` | purchase list; items with no cost count |
| 3.2 | Check a read bill | `/bills-in/:id` | `BillReview.dc.html` | `GET/PATCH /v1/purchases/:id`, `POST …/confirm` |
| 4.1 | Profit | `/profit` | `Profit.dc.html` | `GET /v1/profit?from&to` (today / week / month) |
| 4.2 | Item and its sources | `/items/:id` | `ItemDetail.dc.html` | `GET /v1/profit/items/:id` (with sources) |
| 4.3 | Alerts | `/alerts` | `Alerts.dc.html` | `GET /v1/alerts`, `POST /v1/alerts/:id/seen` |
| 4.4 | Ask | `/ask` | `Ask.dc.html` | `POST /v1/ask` |
| 4.5 | Evening summary | `/summary` | `Summary.dc.html` | `GET /v1/summaries/latest` (+ audio) |
| 5.1 | Festival plan | `/plan` | `Plan.dc.html` | `GET/POST /v1/restock-plans`; multiplier updates the plan |
| 5.2 | Loan offer | `/plan/loan/:id` | `Loan.dc.html` | `POST /v1/restock-plans/:id/loan-offer`, `POST /v1/loan-offers/:id/approve|decline` |
| 5.3 | Settings | `/settings` | `Settings.dc.html` | `GET/PATCH /v1/me` |
| — | Demo view | `/demo` | not drawn; see §6 | sandbox routes |

First run: if `/v1/me` has no language → `/welcome`; no shop name/UPI → `/setup`; else `/`.

### Per-screen notes (what the design source doesn't make obvious)

- **Counter:** the today card links to Profit; the amber banner shows only when there's an open alert
  (most important one) and links to Alerts. "Recent bills" shows the last 2–3 paid bills.
- **Listening:** transcript updates live; parsed items appear as chips as they're recognised. Cancel
  discards the draft. Sound bars animate only while audio comes in.
- **Checklist:** lines arrive ticked. Unticking removes the line and the total updates instantly
  (optimistic, then synced). A line with `price_source = learned` shows "Price from last time".
  Confirm is disabled while any line is flagged; its label says how many lines need fixing.
  No customer phone field (SMS is out of scope).
- **Fix a line:** opens automatically for the first flagged line after release. Amount input uses
  `inputmode="numeric"`. "Or hold and say it" re-records just that line.
- **QR:** shows only after confirm. 5-minute expiry from the API; at expiry show "This QR expired"
  with Show again / Cancel. "Edit bill" returns to the checklist and cancels the QR.
- **Paid:** "Next bill" returns to Counter; the Soundbox announcement is server-side.
- **Bills in:** the camera card is `<input type="file" accept="image/*" capture="environment">`.
  Show an upload/reading state (skeleton lines) while the vision step runs.
- **Check a read bill:** lines with `check_ok = false` and unmatched items are listed first, amber,
  each with its two actions. Confirm is disabled until none are left. The bill-total mismatch banner
  shows when line sum ≠ bill total.
- **Profit:** "about" before profit whenever any line used the margin-rate estimate. Always show the
  coverage line (share of UPI payments that were voice-billed). Bar chart only for week and month;
  the latest bar is saffron. Items with no cost link to Bills in.
- **Item detail:** every number is tappable to its source (bill line or voice bills list).
- **Alerts:** one primary action per alert + "Got it" (marks seen). Empty state text is in the source.
- **Ask:** answers show source chips and a Listen button (TTS). Drafted messages use the Web Share
  API (`navigator.share`), falling back to copy. Laabam never sends them.
- **Evening summary:** opened from the closing-time notification and from the Counter after
  closing time. Play/pause the TTS audio; the transcript is shown below.
- **Festival plan:** multiplier choices 1.5×, 2× (default), 2.5×; labelled as a guess the owner can
  change. Cash in hand comes from Settings. Shortfall = total − cash, never below 0; the loan link
  shows only when shortfall > 0.
- **Loan offer:** terms (rate, days, daily amount) come from the lending adapter; show placeholders
  until they arrive. After approve, show the "sent" status and hide the buttons.
- **Settings:** "Keep voice recordings" is off by default and maps to a merchant setting.

---

## 4. Interaction details

- **Hold to talk:** `pointerdown` starts recording (MediaRecorder), `pointerup`/`pointercancel`
  stops and goes to the checklist. Call `preventDefault` on `contextmenu` and set
  `touch-action: none` so a long press doesn't open the phone's menu. Very short presses (< 400ms)
  show "Hold the button while you speak".
  **Keyboard and accessibility:** Space/Enter toggles recording on and off (press once to start,
  again to stop), with a visible state and `aria-pressed`. Always offer a "Type instead" link to the
  text input.
- **Mic permission:** ask on the first hold, not at load. If denied, show a short how-to and the text
  input.
- **Motion:** the pulse, sound bars, spinner and Paid pop are CSS animations that turn off under
  `prefers-reduced-motion`.
- **Feedback:** use `navigator.vibrate(20)` on confirm and on payment received where supported.
- **Offline:** if the network drops, keep the draft bill locally and show an offline banner; QR needs
  the network.
- **Touch targets:** 44px minimum everywhere.

---

## 5. Copy and languages

- All visible text goes through the strings table (en, ta, hi). The English copy in the design
  sources is the source text; keep it short and plain.
- The language picker shows each language in its own script with the English name under it.
- Money and numbers stay in Western digits with ₹ in every language.

---

## 6. Larger screens and the demo view (not drawn; follow these rules)

- **Tablet/desktop (≥ 600px):** keep the app in a centred column, max-width 480px, on `--mint`
  around it. The tab bar stays inside the column. Don't stretch the screens.
- **`/demo` (sandbox only):** three columns on a laptop screen:
  1. the phone column (the real app, 390px wide in a simple rounded frame);
  2. the Soundbox panel (`Soundbox.dc.html`), showing the current QR and the last announcement;
  3. a "behind the scenes" panel: transcript, parsed lines and their checks, the adapter calls, and
     the logged SMS text. Plain list, monospace for payloads, newest at the top.
  Below 1100px, stack the panels under the phone.

---

## 7. Accessibility checklist

- Real `<button>`, `<a>`, `<input>` + `<label>` everywhere; icon-only buttons have `aria-label`.
- Text contrast ≥ 4.5:1 (the tokens above pass on their intended backgrounds).
- Bottom sheet: focus trap, Esc, returns focus to the trigger.
- Segmented controls and the multiplier use `role="tablist"`/`radiogroup` with `aria-selected`/`aria-checked`.
- Live regions: transcript (`aria-live="polite"`), payment status and "Paid" (`role="status"`).
- The QR has an `aria-label` with the amount.

---

## 8. Done when

- Every screen in §3 matches its design source at 390px (layout, spacing, type, colour, copy).
- The counter → checklist → QR → paid flow works end to end with the sandbox, by voice and by text.
- Lighthouse PWA installable; accessibility score ≥ 95 on Counter, Checklist and Profit.
- A Playwright test walks the billing flow by text input and checks the confirm-before-QR rule.
