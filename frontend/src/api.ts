// Typed API client. In development Vite proxies /api, /v1 and /sandbox to the backend on :8000.

import type { Lang } from './strings'

/** live: a speech service; samples: the demo plays sample orders; off: type orders. */
export type VoiceMode = 'live' | 'samples' | 'off'

export type Health = {
  ok: boolean
  version: string
  sandbox: boolean
  voice: VoiceMode
  vision: VoiceMode // same three modes, for reading bill photos
  bill_samples: string[]
}

export type Timings = Record<string, number>
export type Heard = { provider: string; sample: string | null; heard: string }

export type Merchant = {
  id: string
  name: string
  language: Lang
  language_set: boolean
  closing_time: string
  upi_vpa: string
  languages: Lang[]
  /** What onboarding still has to ask: 'language' (Welcome), then 'shop' (Setup). */
  needs: ('language' | 'shop')[]
  /** The seeded demo shop: its figures are sample data. */
  sample_data: boolean
  festival_date: string | null
  cash_on_hand_paise: number | null
  keep_recordings: boolean
  soundbox_connected: boolean
  /** the owner asked to delete the shop: it goes at this time unless they keep it */
  deletion_due_at: string | null
}

export type MePatch = Partial<Pick<Merchant, 'name' | 'language' | 'closing_time' | 'upi_vpa' | 'keep_recordings' | 'festival_date'>> & {
  clear_festival_date?: boolean
  cash_on_hand_paise?: number
  clear_cash_on_hand?: boolean
}

export type Line = {
  id: string
  position: number
  item_id: string | null
  item_name: string
  qty_milli: number | null
  unit: string | null
  line_total_paise: number | null
  unit_price_paise: number | null
  price_kind: string | null
  price_source: 'spoken' | 'learned' | 'edit' | null
  learned: boolean
  heard_text: string
  flags: string[]
}

export type BillStatus = 'draft' | 'confirmed' | 'paid' | 'cancelled' | 'expired'

export type Bill = {
  id: string
  status: BillStatus
  language: Lang
  transcript: string
  total_paise: number
  lines: Line[]
  can_confirm: boolean
  created_at: string
  confirmed_at: string | null
  paid_at: string | null
  qr: { order_id: string; payload: string; expires_at: string } | null
  payment_ref: string | null
  payment_issue: string | null
}

export type VoiceBill = Bill & { speech: Heard; timings: Timings }

export type PurchaseLine = {
  id: string
  position: number
  raw_text: string
  item_id: string | null
  item_name: string | null
  match: 'alias' | 'fuzzy' | 'owner' | 'new' | null
  suggested_item_id: string | null
  suggested_item_name: string | null
  name_hint: string
  qty_milli: number | null
  unit: string | null
  rate_paise: number | null
  line_total_paise: number | null
  base_unit: string | null
  base_qty_milli: number | null
  unit_cost_paise: number | null
  check_ok: boolean
  flags: string[]
  expected_paise: number | null
}

export type Purchase = {
  id: string
  /** reading: the worker is reading the photo; failed: it couldn't (can_retry) */
  status: 'reading' | 'failed' | 'read' | 'confirmed' | 'discarded'
  distributor_name: string
  bill_no: string
  bill_date: string | null
  printed_total_paise: number | null
  total_paise: number
  read_issues: string[]
  reader: string
  sample: string | null
  has_photo: boolean
  lines: PurchaseLine[]
  can_confirm: boolean
  can_retry: boolean
  created_at: string
  confirmed_at: string | null
}

export type Period = 'today' | 'week' | 'month'

export type ProfitReport = {
  period: Period
  from: string
  to: string
  sales_paise: number
  profit_paise: number
  estimated: boolean
  known_sales_paise: number
  upi_paise: number
  unbilled_paise: number
  coverage_bp: number | null
  bars: { from: string; to: string; label: string; profit_paise: number }[]
  items: { item_id: string; name: string; sales_paise: number; profit_paise: number; estimated: boolean; margin_bp: number | null; trend: 'down' | null }[]
  no_cost_items: { item_id: string; name: string; sales_paise: number; reason: 'no_cost' | 'no_price' }[]
}

/** A confirmed distributor bill line: where a cost comes from. */
export type CostRef = {
  purchase_id: string
  line_id: string
  position: number
  bill_no: string
  day: string
  distributor: string
  qty_milli: number | null
  unit: string | null
  rate_paise: number | null
  base_unit: string | null
  unit_cost_paise: number | null
  usual_cost_paise: number | null
}

export type ItemReport = {
  item: { id: string; name: string; unit: string | null }
  period: Period
  from: string
  to: string
  unit_label: 'litre' | 'kg' | 'pack'
  price: { paise: number; basis: string } | null
  cost: CostRef | null
  prev_cost: CostRef | null
  profit_per_unit_paise: number | null
  prev_profit_per_unit_paise: number | null
  margin_bp: number | null
  sold: { qty_milli: number; estimated: boolean; bills: number; lines: number; sales_paise: number; profit_paise: number }
  lost_to_rise_paise: number
  said_prices_paise: number[]
  cheaper: CostRef | null
}

export type ItemBill = {
  bill_id: string
  line_id: string
  paid_at: string
  heard_text: string
  amount_paise: number
  qty_milli: number | null
  unit: string | null
  method: 'unit' | 'margin' | 'no_cost' | 'no_price'
  profit_paise: number | null
}

export type AlertKind = 'price_rise' | 'running_out' | 'slow' | 'low_margin'
export type Alert = {
  id: string
  kind: AlertKind
  status: string
  item_id: string | null
  item_name: string | null
  created_at: string
  // every figure computed in code (backend/laabam/alerts.py)
  figures: {
    unit_label: 'litre' | 'kg' | 'pack'
    rise_paise?: number
    rise_bp?: number
    new?: CostRef
    old?: CostRef
    price_paise?: number | null
    profit_per_unit_paise?: number | null
    prev_profit_per_unit_paise?: number | null
    sold_this_week_milli?: number
    lost_this_week_paise?: number
    per_day_milli?: number
    left_milli?: number
    days_left_milli?: number
    out_day?: string
    days_quiet?: number
    last_sale_day?: string | null
    value_paise?: number | null
    margin_bp?: number
    cost?: CostRef
  }
}

export type AnswerSource =
  | { kind: 'bill_line'; purchase_id: string; line_id: string; position: number; distributor: string; day: string; bill_no: string }
  | { kind: 'voice_bills'; item_id: string; count: number; period?: string }
  | { kind: 'item'; item_id: string; name: string }

/** An answer to a question: the words filled by code, the key figures marked bold, and where they come from. */
export type Answer = {
  question: string
  intent: 'why_down' | 'reorder' | 'best' | 'draft' | 'unknown'
  language: string
  text: string
  segments: { t: string; b: boolean }[]
  phrased_by: 'template' | 'model'
  sources: AnswerSource[]
  item_id: string | null
  draft: { to: string; item_id: string; text: string } | null
}

export type Summary = {
  id: string
  day: string
  language: string
  text: string
  phrased_by: 'template' | 'model'
  created_at: string
  played_at: string | null
  figures: {
    day: string
    sales_paise: number
    profit_paise: number
    profit_known: boolean
    estimated: boolean
    top: { item_id: string; name: string; profit_paise: number } | null
    watch: { alert_id: string; kind: AlertKind; item_id: string; item_name: string }[]
    coverage_bp: number | null
    segments: { t: string; b: boolean }[]
  }
}

export type PlanLine = {
  item_id: string
  name: string
  unit_label: 'litre' | 'kg' | 'pack'
  qty: number
  cost_paise: number
  amount_paise: number
  distributor: string
  cheaper: boolean
  cost: CostRef
  out_day?: string
}

export type FestivalPlan = {
  kind: 'festival'
  festival: string | null
  festival_names: Record<string, string> | null
  festival_date: string | null
  days_to_go: number | null
  multiplier_bp: number
  multipliers_bp: number[]
  guess: boolean
  lines: PlanLine[]
  total_paise: number
  cash_on_hand_paise: number | null
  shortfall_paise: number | null
}

export type LoanOfferT = {
  id: string
  status: 'offered' | 'applied' | 'declined' | 'failed'
  amount_paise: number
  lender: string
  partner_ref: string | null
  terms: { partner?: string; rate_text?: string; days?: number; daily_paise?: number }
  created_at: string
  decided_at: string | null
  basis: {
    plan_id: string
    kind: string
    festival: string | null
    festival_names: Record<string, string> | null
    festival_date: string | null
    multiplier_bp: number
    total_paise: number
    cash_on_hand_paise: number
    shortfall_paise: number
    lines: number
  }
}

export type DeviceSessionT = { id: string; label: string; via: string; created_at: string; last_seen_at: string; current: boolean }

export type PurchaseLineEdit = {
  item_id?: string
  accept_suggestion?: boolean
  new_item_name?: string
  raw_text?: string
  qty_milli?: number
  unit?: string
  rate_paise?: number
  clear_rate?: boolean
  line_total_paise?: number
}

export type ItemInfo = {
  id: string
  name: string
  unit: string | null
  aliases: string[]
  price: { price_paise: number; per_qty_milli: number | null; per_unit: string | null; seen_at: string } | null
  cost: { unit_cost_paise: number; per_unit: string | null; purchase_id: string; line_id: string; distributor: string; bill_date: string | null } | null
}

export type Today = { date: string; paid_bills: number; sales_paise: number; payment_issues: number }

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

// The access token lives in memory only (15 minutes). The refresh token is an HttpOnly cookie the page can't read;
// POST /v1/auth/refresh (with the X-Laabam-Refresh header) turns it into a new access token.
let token: string | null = null
export function setToken(t: string | null) {
  token = t
}

export type SignedIn = { access_token: string; expires_at: number; session_id: string; merchant: Merchant }

let refreshing: Promise<SignedIn | null> | null = null
/** A new access token from the refresh cookie, or null if this device must sign in again. One at a time. */
export function refreshSession(): Promise<SignedIn | null> {
  refreshing ??= (async () => {
    try {
      const res = await fetch('/v1/auth/refresh', { method: 'POST', headers: { 'X-Laabam-Refresh': '1' } })
      if (!res.ok) return null
      const s = (await res.json()) as SignedIn
      token = s.access_token
      return s
    } catch {
      return null
    } finally {
      refreshing = null
    }
  })()
  return refreshing
}

async function call<T>(
  method: string,
  path: string,
  body?: unknown,
  audio?: Blob,
  extra?: Record<string, string>,
): Promise<T> {
  const headers: Record<string, string> = { ...extra }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (audio) headers['Content-Type'] = audio.type || 'application/octet-stream'
  const signIn = path.startsWith('/sandbox/session') || path.startsWith('/sandbox/reset') || path.startsWith('/v1/auth/')
  const send = () => {
    if (token && !signIn) headers.Authorization = `Bearer ${token}`
    return fetch(path, { method, headers, body: audio ?? (body === undefined ? undefined : JSON.stringify(body)) })
  }
  let res: Response
  try {
    res = await send()
    if (res.status === 401 && !signIn && token && (await refreshSession())) res = await send() // the 15 minutes ran out
  } catch {
    throw new ApiError(0, 'offline')
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      const j = await res.json()
      if (typeof j.detail === 'string') detail = j.detail
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail)
  }
  if (res.status === 204) return null as T
  return (await res.json()) as T
}

/** GET /api/config: how this deployment signs owners in. Firebase's web settings are public identifiers. */
export type PublicConfig = {
  beta: boolean
  signin: 'sandbox' | 'phone' | 'partner'
  privacy_version: string
  firebase?: { apiKey: string; authDomain: string; projectId: string; appId: string }
}

export type LineEdit = {
  item_text?: string
  qty_milli?: number
  unit?: string
  line_total_paise?: number
  clear_qty?: boolean
}

export const api = {
  health: () => call<Health>('GET', '/api/health'),
  /** The demo owner. reset: a new seeded demo shop; fresh: a new empty shop that goes through onboarding. */
  /** Partner sign-in: Paytm for Business opens Laabam with a signed assertion (docs/partner-identity.md). */
  partnerSignIn: (assertion: string) => call<SignedIn>('POST', '/v1/auth/partner', { assertion }),
  logout: () => call<{ ok: boolean }>('POST', '/v1/auth/logout'),
  sessions: () => call<DeviceSessionT[]>('GET', '/v1/sessions'),
  revokeSession: (id: string) => call<{ ok: boolean }>('DELETE', '/v1/sessions/' + id),
  /** Demo: every sign-in is a new demo shop of its own. */
  sandboxSession: (opts: { reset?: boolean; fresh?: boolean; language?: Lang } = {}) =>
    call<SignedIn>('POST', opts.reset ? '/sandbox/reset' : '/sandbox/session', {
      reset: !!opts.reset,
      fresh: !!opts.fresh,
      ...(opts.language ? { language: opts.language } : {}),
    }),
  me: () => call<Merchant>('GET', '/v1/me'),
  updateMe: (patch: MePatch) => call<Merchant>('PATCH', '/v1/me', patch),
  config: () => call<PublicConfig>('GET', '/api/config'),
  /** Beta sign-up: asked before the phone requests a code from Firebase (rate limits per IP and per number). */
  phoneStart: (phone: string) => call<null>('POST', '/v1/auth/phone/start', { phone }),
  /** Beta sign-up: the Firebase ID token proves the number; we get our own session back. */
  phoneSignIn: (idToken: string, consent: boolean, consentVersion: string) =>
    call<SignedIn>('POST', '/v1/auth/firebase', { id_token: idToken, consent, consent_version: consent ? consentVersion : null }),
  /** Beta: payments are simulated; plays the customer paying this QR in full. */
  betaPay: (orderId: string) => call<{ result: string }>('POST', `/v1/beta/qr/${orderId}/pay`),
  deleteShop: (shopName: string) => call<{ deletion_due_at: string }>('DELETE', '/v1/me', { shop_name: shopName }),
  keepShop: () => call<Merchant>('DELETE', '/v1/me/deletion'),
  today: () => call<Today>('GET', '/v1/today'),
  bills: () => call<Bill[]>('GET', '/v1/bills'),
  bill: (id: string) => call<Bill>('GET', `/v1/bills/${id}`),
  billFromText: (text: string) => call<Bill>('POST', '/v1/bills/text', { text }),
  say: (id: string, text: string) => call<Bill>('POST', `/v1/bills/${id}/say`, { text }),
  // push-to-talk: the recording goes up as-is and is not stored; the reply is the checklist
  billFromVoice: (audio: Blob) => call<VoiceBill>('POST', '/v1/bills/voice', undefined, audio),
  /** Demo only (sandbox, no speech service): the next sample order, asked for by name. Never a recording. */
  sampleOrder: () =>
    call<VoiceBill>('POST', '/v1/bills/voice', undefined, new Blob([], { type: 'audio/webm' }), {
      'X-Laabam-Sample-Order': 'next',
    }),
  sayVoice: (id: string, audio: Blob) => call<VoiceBill>('POST', `/v1/bills/${id}/say/voice`, undefined, audio),
  addLine: (id: string, edit: LineEdit) => call<Bill>('POST', `/v1/bills/${id}/lines`, edit),
  editLine: (id: string, lineId: string, edit: LineEdit) => call<Bill>('PATCH', `/v1/bills/${id}/lines/${lineId}`, edit),
  deleteLine: (id: string, lineId: string) => call<Bill>('DELETE', `/v1/bills/${id}/lines/${lineId}`),
  confirm: (id: string) => call<Bill>('POST', `/v1/bills/${id}/confirm`),
  cancel: (id: string) => call<Bill>('POST', `/v1/bills/${id}/cancel`),
  showQr: (id: string) => call<Bill>('POST', `/v1/bills/${id}/show-qr`),
  /** Back to the checklist from the QR: the QR is withdrawn and the bill is a draft again. */
  reopen: (id: string) => call<Bill>('POST', `/v1/bills/${id}/edit`),

  // distributor bills
  purchases: () => call<Purchase[]>('GET', '/v1/purchases'),
  purchase: (id: string) => call<Purchase>('GET', `/v1/purchases/${id}`),
  /** A bill photo goes straight to storage: an upload link for this type and size (5 minutes), the PUT, then
   *  'uploaded' (the worker re-encodes and reads it). */
  purchaseFromPhoto: async (photo: Blob): Promise<Purchase> => {
    const type = photo.type || 'image/jpeg'
    const { purchase, upload } = await call<{ purchase: Purchase; upload: { url: string; method: string; headers: Record<string, string> } }>(
      'POST', '/v1/purchases/uploads', { content_type: type, size: photo.size })
    const put = await fetch(upload.url, { method: upload.method, headers: upload.headers, body: photo })
    if (!put.ok) throw new ApiError(put.status, 'The photo didn’t upload. Please try again.')
    return call<Purchase>('POST', '/v1/purchases/' + purchase.id + '/uploaded')
  },
  purchaseFromSample: (name: string) =>
    call<Purchase>('POST', '/v1/purchases/photo', undefined, new Blob([], { type: 'image/jpeg' }), {
      'X-Laabam-Sample': name,
    }),
  patchPurchase: (id: string, patch: { distributor_name?: string; bill_no?: string; bill_date?: string | null }) =>
    call<Purchase>('PATCH', `/v1/purchases/${id}`, patch),
  editPurchaseLine: (id: string, lineId: string, edit: PurchaseLineEdit) =>
    call<Purchase>('PATCH', `/v1/purchases/${id}/lines/${lineId}`, edit),
  addPurchaseLine: (id: string, edit: PurchaseLineEdit) => call<Purchase>('POST', `/v1/purchases/${id}/lines`, edit),
  deletePurchaseLine: (id: string, lineId: string) => call<Purchase>('DELETE', `/v1/purchases/${id}/lines/${lineId}`),
  retryPurchase: (id: string) => call<Purchase>('POST', `/v1/purchases/${id}/retry`),
  confirmPurchase: (id: string) => call<Purchase>('POST', `/v1/purchases/${id}/confirm`),
  discardPurchase: (id: string) => call<Purchase>('POST', `/v1/purchases/${id}/discard`),
  /** The photo needs the owner token, so it is fetched and shown from a blob URL. */
  purchasePhoto: async (id: string): Promise<Blob | null> => {
    const res = await fetch(`/v1/purchases/${id}/photo`, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
    return res.ok ? res.blob() : null
  },
  items: () => call<ItemInfo[]>('GET', '/v1/items'),
  profit: (period: Period) => call<ProfitReport>('GET', `/v1/profit?period=${period}`),
  itemProfit: (id: string, period: Period = 'week') => call<ItemReport>('GET', `/v1/profit/items/${id}?period=${period}`),
  itemBills: (id: string, period: Period = 'week') => call<ItemBill[]>('GET', `/v1/profit/items/${id}/bills?period=${period}`),
  alerts: () => call<Alert[]>('GET', '/v1/alerts'),
  /** The latest evening summary, or null before the first one. */
  latestSummary: async (): Promise<Summary | null> => {
    try {
      return await call<Summary>('GET', '/v1/summaries/latest')
    } catch (e) {
      if ((e as ApiError).status === 404) return null
      throw e
    }
  },
  summaryNow: () => call<Summary>('POST', '/v1/summaries'),
  festivalPlan: (multiplierBp: number) => call<FestivalPlan>('GET', '/v1/plans/festival?multiplier_bp=' + multiplierBp),
  reorderPlan: () => call<{ kind: 'weekly'; lines: PlanLine[]; total_paise: number }>('GET', '/v1/plans/reorder'),
  /** Keeps a copy of the plan as it is now: what a loan offer is sized from. */
  saveRestockPlan: (multiplierBp: number) => call<FestivalPlan & { id: string }>('POST', '/v1/restock-plans', { multiplier_bp: multiplierBp }),
  loanOffer: (planId: string) => call<LoanOfferT>('POST', '/v1/restock-plans/' + planId + '/loan-offer'),
  getLoanOffer: (id: string) => call<LoanOfferT>('GET', '/v1/loan-offers/' + id),
  approveLoan: (id: string) => call<LoanOfferT>('POST', '/v1/loan-offers/' + id + '/approve'),
  declineLoan: (id: string) => call<LoanOfferT>('POST', '/v1/loan-offers/' + id + '/decline'),
  ask: (q: { text: string; intent?: string; item_id?: string }) => call<Answer>('POST', '/v1/ask', q),
  askVoice: (audio: Blob) => call<Answer>('POST', '/v1/ask/voice', undefined, audio),
  /** The answer read aloud by the speech service; null when there is none (the phone's own voice reads it). */
  speak: async (text: string): Promise<Blob | null> => {
    const res = await fetch('/v1/speak', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify({ text }),
    })
    return res.status === 200 ? res.blob() : null
  },
  alertSeen: (id: string) => call<Alert>('POST', `/v1/alerts/${id}/seen`),
  alertActed: (id: string) => call<Alert>('POST', `/v1/alerts/${id}/acted`),

  // sandbox (demo view)
  soundbox: (merchantId: string, after = 0) =>
    call<{ screen: SoundboxScreen; events: SoundboxEvent[] }>('GET', `/sandbox/soundbox/${merchantId}?after=${after}`),
  trace: (merchantId: string, after = 0) => call<TraceEvent[]>('GET', `/sandbox/trace/${merchantId}?after=${after}`),
  pay: (orderId: string, amountPaise?: number) =>
    call<{ result: string }>('POST', `/sandbox/qr/${orderId}/pay`, amountPaise ? { amount_paise: amountPaise } : {}),
  expire: (billId: string) => call<{ status: string }>('POST', `/sandbox/bills/${billId}/expire`),
}

export type SoundboxScreen =
  | { kind: 'idle' }
  | { kind: 'qr'; payload: string; amount_paise: number; text: string }
  | { kind: 'message'; text: string }

export type SoundboxEvent = {
  seq: number
  ts: string
  kind: string
  text?: string
  language?: Lang
  amount_paise?: number
  audio?: boolean // a spoken announcement is waiting at soundboxAudioUrl(seq)
}

/** Everything the shop has, as a zip (GET /v1/me/export). */
export async function exportData(): Promise<Blob> {
  const get = () => fetch('/v1/me/export', { headers: token ? { Authorization: `Bearer ${token}` } : {} })
  let res = await get()
  if (res.status === 401 && token && (await refreshSession())) res = await get()
  if (!res.ok) {
    let detail = res.statusText
    try {
      const j = await res.json()
      if (typeof j.detail === 'string') detail = j.detail
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail)
  }
  return res.blob()
}

/** A spoken Soundbox announcement (demo), fetched with the shop's token. */
export async function soundboxAudio(seq: number): Promise<Blob | null> {
  const res = await fetch(`/sandbox/soundbox/audio/${seq}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
  return res.ok ? res.blob() : null
}

export type TraceEvent = { seq: number; ts: string; kind: string; data: Record<string, unknown> }

const READ_POLL_MS = 1500
const READ_WAIT_MS = 180_000

/** A live reader takes a while: poll the bill until it is no longer "reading" (or `alive()` says stop).
 *  Returns the bill as last seen; still "reading" after READ_WAIT_MS. */
export async function whileReading(p: Purchase, alive: () => boolean = () => true): Promise<Purchase> {
  const until = Date.now() + READ_WAIT_MS
  while (p.status === 'reading' && alive() && Date.now() < until) {
    await new Promise((r) => setTimeout(r, READ_POLL_MS))
    if (!alive()) break
    try {
      p = await api.purchase(p.id)
    } catch {
      // a dropped request on a weak connection: keep waiting
    }
  }
  return p
}
