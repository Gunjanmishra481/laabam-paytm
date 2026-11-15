// /demo: the owner's phone on the left; the Soundbox simulator and "behind the scenes" on the right.
// Sandbox only. The shop and its numbers are synthetic; no real money moves.

import { useEffect, useRef, useState, type ReactNode } from 'react'
import { api, soundboxAudio, type SoundboxScreen, type TraceEvent } from './api'
import { rupees } from './money'
import OwnerApp from './OwnerApp'
import { QRCode } from './ui'
import { RouterProvider } from './router'
import { LANGUAGES, SPEECH_LOCALE, type Lang } from './strings'
import { useSession } from './useSession'

/** Calls fn now and every `ms` until the component unmounts or `key` changes. */
function usePoll(fn: () => Promise<unknown>, ms: number, key: string) {
  const fnRef = useRef(fn)
  useEffect(() => {
    fnRef.current = fn
  })
  useEffect(() => {
    let live = true
    const tick = () => {
      if (live) fnRef.current().catch(() => undefined)
    }
    tick()
    const id = window.setInterval(tick, ms)
    return () => {
      live = false
      window.clearInterval(id)
    }
  }, [ms, key])
}

export default function Demo() {
  const { merchant, resetDemo, lang } = useSession()
  const mid = merchant?.id
  // The QR on the Soundbox is what a customer would scan, so the demo's "Customer pays" pays that one.
  const [screen, setScreen] = useState<SoundboxScreen>({ kind: 'idle' })
  const order = screen.kind === 'qr' ? orderFromPayload(screen.payload) : null
  return (
    <div className="demo">
      <header className="demo-bar">
        <strong>Laabam demo</strong>
        <span className="muted small">
          {merchant?.sample_data ? 'Sample data' : 'New shop'} · sandbox payments · no real money
        </span>
        <span className="spacer" />
        <label className="small">
          Language{' '}
          <select value={lang} onChange={(e) => resetDemo({ language: e.target.value as Lang })} aria-label="Demo language">
            {(Object.keys(LANGUAGES) as Lang[]).map((l) => (
              <option key={l} value={l}>
                {LANGUAGES[l]}
              </option>
            ))}
          </select>
        </label>
        <button className="btn ghost small-btn" onClick={() => resetDemo({ language: lang })}>
          Reset demo (sample shop)
        </button>
        <button className="btn ghost small-btn" onClick={() => resetDemo({ fresh: true })} data-testid="demo-fresh">
          Start as a new shop
        </button>
      </header>
      <div className="demo-body">
        <div className="phone" aria-label="Owner's phone">
          <div className="phone-screen">
            <RouterProvider memory key={mid ?? 'none'}>
              <OwnerApp root={false} />
            </RouterProvider>
          </div>
        </div>
        <div className="demo-side">
          {mid && <Soundbox key={`sb-${mid}`} merchantId={mid} shopName={merchant?.name ?? ''} screen={screen} onScreen={setScreen} />}
          {mid && <BehindTheScenes key={`bts-${mid}`} merchantId={mid} order={order} />}
        </div>
      </div>
    </div>
  )
}

function speak(text: string, language: Lang | undefined) {
  if (!('speechSynthesis' in window)) return
  const u = new SpeechSynthesisUtterance(text)
  u.lang = SPEECH_LOCALE[language ?? 'en']
  const voice = window.speechSynthesis.getVoices().find((v) => v.lang === u.lang)
  if (voice) u.voice = voice
  window.speechSynthesis.speak(u)
}

function orderFromPayload(payload: string): string | null {
  const q = payload.indexOf('?')
  return q < 0 ? null : new URLSearchParams(payload.slice(q + 1)).get('tr')
}

type SoundboxProps = { merchantId: string; shopName: string; screen: SoundboxScreen; onScreen: (s: SoundboxScreen) => void }

function Soundbox({ merchantId, shopName, screen, onScreen }: SoundboxProps) {
  const [sound, setSound] = useState(false)
  const [lastSaid, setLastSaid] = useState<string | null>(null)
  const after = useRef(0)
  const soundRef = useRef(sound)
  useEffect(() => {
    soundRef.current = sound
  }, [sound])

  usePoll(
    async () => {
      const s = await api.soundbox(merchantId, after.current)
      onScreen(s.screen)
      for (const e of s.events) {
        after.current = Math.max(after.current, e.seq)
        if (e.kind === 'announce' && e.text) setLastSaid(e.text)
        if (e.kind !== 'announce' || !e.text || !soundRef.current) continue
        if (e.audio) {
          // the speech service's voice (Sarvam); the browser's voice if it can't play
          const text = e.text
          soundboxAudio(e.seq)
            .then((clip) => (clip ? new Audio(URL.createObjectURL(clip)).play() : Promise.reject(new Error('no audio'))))
            .catch(() => speak(text, e.language))
        } else speak(e.text, e.language)
      }
    },
    1000,
    merchantId,
  )

  return (
    <section className="card soundbox" aria-label="Soundbox simulator">
      <div className="card-head">
        <h2>Soundbox</h2>
        <button className={`btn small-btn ${sound ? 'primary' : 'ghost'}`} onClick={() => setSound((s) => !s)}>
          {sound ? '🔊 Sound on' : '🔈 Turn sound on'}
        </button>
      </div>
      <div className="sb-device">
        <div className="sb-screen" data-testid="soundbox-screen">
          <span className="sb-shop">{shopName}</span>
          {screen.kind === 'qr' && (
            <>
              <span className="sb-amount">{rupees(screen.amount_paise)}</span>
              <QRCode payload={screen.payload} size={126} label={screen.text} />
              <span className="sb-scan">Scan with any UPI app</span>
            </>
          )}
          {screen.kind === 'message' && <span className="sb-message">{screen.text}</span>}
          {screen.kind === 'idle' && <span className="sb-idle">Ready</span>}
        </div>
        <div className="sb-grille" aria-hidden="true">
          {Array.from({ length: 27 }, (_, i) => (
            <span key={i} />
          ))}
        </div>
        <div className="sb-said" aria-live="polite">
          <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M4 9v6h4l5 4V5L8 9z" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
            <path d="M16.5 8.5a5 5 0 0 1 0 7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
          <span>
            {screen.kind === 'qr'
              ? `After payment: “${rupees(screen.amount_paise)} received”`
              : lastSaid
                ? `“${lastSaid}”`
                : 'Nothing said yet'}
          </span>
        </div>
      </div>
    </section>
  )
}

function BehindTheScenes({ merchantId, order }: { merchantId: string; order: string | null }) {
  const [events, setEvents] = useState<TraceEvent[]>([])
  const [note, setNote] = useState<string | null>(null)
  const after = useRef(0)

  usePoll(
    async () => {
      const fresh = await api.trace(merchantId, after.current)
      if (fresh.length) {
        after.current = fresh[fresh.length - 1].seq
        setEvents((prev) => [...prev, ...fresh].slice(-80))
      }
    },
    1000,
    merchantId,
  )

  const open = order

  const act = async (fn: () => Promise<unknown>, label: string) => {
    try {
      await fn()
      setNote(label)
    } catch (e) {
      setNote((e as Error).message)
    }
  }

  return (
    <section className="card bts" aria-label="Behind the scenes">
      <div className="card-head">
        <h2>Behind the scenes</h2>
      </div>
      <div className="row wrap">
        <button className="btn primary small-btn" disabled={!open} onClick={() => open && act(() => api.pay(open), 'Customer paid')}>
          Customer pays
        </button>
        <button
          className="btn ghost small-btn"
          disabled={!open}
          onClick={() => open && act(() => api.pay(open, 100), 'Customer paid ₹1')}
        >
          Pays ₹1 (wrong amount)
        </button>
        <button
          className="btn ghost small-btn"
          disabled={!open}
          onClick={() => open && act(() => api.expire(open.slice(0, open.lastIndexOf('-'))), 'QR expired')}
        >
          Let QR expire
        </button>
        {note && <span className="muted small">{note}</span>}
      </div>
      <ol className="trace" data-testid="trace">
        {[...events].reverse().map((e) => (
          <TraceItem key={e.seq} e={e} />
        ))}
        {events.length === 0 && <li className="muted">Make a bill on the phone to see each step here.</li>}
      </ol>
    </section>
  )
}

function str(v: unknown): string {
  return typeof v === 'string' ? v : JSON.stringify(v)
}

function TraceItem({ e }: { e: TraceEvent }) {
  const d = e.data
  const time = e.ts.slice(11, 19)
  let body: ReactNode = <pre>{JSON.stringify(d, null, 1)}</pre>
  let title = e.kind
  if (e.kind === 'bill_drafted') {
    title = 'Transcript → checklist'
    const parsed = (d.parsed as Array<Record<string, unknown>>) ?? []
    const checks = (d.checks as Array<Record<string, unknown>>) ?? []
    const timings = d.timings as Record<string, number>
    body = (
      <>
        <p>
          “{str(d.transcript)}” <span className="muted small">parser: {str(d.parser)} · parse {timings?.parse_ms} ms · resolve +
          verify {timings?.resolve_verify_ms} ms</span>
        </p>
        <table>
          <thead>
            <tr>
              <th>heard</th>
              <th>item</th>
              <th>qty</th>
              <th>price</th>
              <th>check</th>
            </tr>
          </thead>
          <tbody>
            {parsed.map((p, i) => {
              const c = checks[i] ?? {}
              const flags = (c.flags as string[]) ?? []
              return (
                <tr key={i}>
                  <td>“{str(p.heard)}”</td>
                  <td>{str(p.item_text)}</td>
                  <td>{p.qty ? `${str(p.qty)} ${str(p.unit ?? '')}` : '—'}</td>
                  <td>{p.price ? `₹${str(p.price)}${p.price_kind === 'per_unit' ? '/unit' : ''}` : c.price_source === 'learned' ? 'learned' : '—'}</td>
                  <td className={flags.length ? 'amber-text' : 'ok-text'}>{flags.length ? flags.join(', ') : '✓ price heard'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
        <p className="small">Total added in code: {rupees(Number(d.total_paise ?? 0))}</p>
      </>
    )
  } else if (e.kind === 'payments.create_qr') {
    title = `Payments adapter (${str(d.adapter)}): create QR`
    body = d.error ? (
      <p className="amber-text">{str(d.error)}</p>
    ) : (
      <p>
        order {str(d.order_id)} · {rupees(Number(d.amount_paise))}
        <br />
        <code className="wrap">{str(d.payload)}</code>
      </p>
    )
  } else if (e.kind.startsWith('soundbox.')) {
    title = `Soundbox: ${e.kind.slice(9)}`
    body = d.text ? <p>“{str(d.text)}”</p> : null
  } else if (e.kind === 'payment.received') {
    title = 'Payment event'
    body = (
      <p>
        {rupees(Number(d.amount_paise))} · txn {str(d.txn_id)} · <strong>{str(d.result)}</strong>
        {d.issue ? <span className="amber-text"> · {str(d.issue)}</span> : null}
      </p>
    )
  } else if (e.kind === 'sms.send_bill') {
    title = 'SMS bill (sandbox log, not sent)'
    body = (
      <p>
        to {str(d.phone)}: “{str(d.text)}”
      </p>
    )
  } else if (e.kind === 'qr.expired') {
    title = 'QR expired'
    body = <p>order {str(d.order_id)}</p>
  } else if (e.kind === 'speech.transcribed') {
    title = d.sample ? 'Speech to text (offline sample)' : `Speech to text (${str(d.provider)})`
    body = (
      <p>
        “{str(d.transcript)}”{' '}
        <span className="muted small">
          {d.sample ? `clip ${str(d.sample)}` : `${Math.round(Number(d.audio_bytes) / 1024)} KB audio, not stored`} ·{' '}
          {str(d.transcribe_ms)} ms{!d.sample && Number(d.keyterms) ? ` · ${str(d.keyterms)} item names as hints` : ''}
        </span>
      </p>
    )
  } else if (e.kind === 'purchase.read') {
    title = d.sample ? 'Bill photo → lines (offline sample)' : `Bill photo → lines (${str(d.provider)})`
    const lines = (d.lines as Array<Record<string, unknown>>) ?? []
    const money = (v: unknown) => (v == null ? '—' : rupees(Number(v)))
    body = (
      <>
        <p>
          {str(d.distributor)} <span className="muted small">· read {str(d.read_ms)} ms · numbers copied as printed, checked in code</span>
        </p>
        <table>
          <thead>
            <tr>
              <th>printed</th>
              <th>qty × rate = amount</th>
              <th>item</th>
              <th>check</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((l, i) => {
              const flags = (l.flags as string[]) ?? []
              return (
                <tr key={i}>
                  <td>{str(l.raw_text)}</td>
                  <td>
                    {l.qty_milli == null ? '?' : Number(l.qty_milli) / 1000} × {money(l.rate_paise)} = {money(l.line_total_paise)}
                  </td>
                  <td>{l.item ? str(l.item) : l.suggested ? `${str(l.suggested)}?` : 'new'}</td>
                  <td className={l.check_ok ? 'ok-text' : 'amber-text'}>
                    {l.check_ok ? `✓ ${money(l.unit_cost_paise)}/${str(l.base_unit)}` : flags.join(', ')}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        <p className="small">
          Lines add to {money(d.total_paise)} · bill says {money(d.printed_total_paise)}
          {(d.read_issues as string[])?.length ? <span className="amber-text"> · {(d.read_issues as string[]).join(', ')}</span> : null}
        </p>
      </>
    )
  } else if (e.kind === 'purchase.confirmed') {
    title = 'Costs saved (owner confirmed)'
    const learned = (d.aliases_learned as Array<Record<string, string>>) ?? []
    body = (
      <p>
        {str(d.distributor)} · {rupees(Number(d.total_paise))}
        {learned.length > 0 && (
          <span className="muted small"> · learned: {learned.map((a) => `“${a.alias}” = ${a.item}`).join(', ')}</span>
        )}
      </p>
    )
  } else if (e.kind === 'vision.failed') {
    title = `Bill photo (${str(d.provider)}) failed`
    body = <p className="amber-text">The owner was asked to take the photo again.</p>
  } else if (e.kind === 'speech.spoken') {
    title = `Text to speech (${str(d.provider)})`
    body = <p className="muted small">{Math.round(Number(d.audio_bytes) / 1024)} KB · {str(d.speak_ms)} ms</p>
  } else if (e.kind === 'speech.failed') {
    title = `Speech to text (${str(d.provider)}) failed`
    body = <p className="amber-text">The owner was asked to try again or type.</p>
  } else if (e.kind === 'bill_said') {
    title = 'Spoken fix'
    body = <p>“{str(d.text)}”</p>
  }
  return (
    <li className="trace-item">
      <div className="trace-head">
        <span>{title}</span>
        <span className="muted small">{time}</span>
      </div>
      {body}
    </li>
  )
}
