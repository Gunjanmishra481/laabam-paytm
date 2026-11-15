// 4.4 Ask. Design: design/screens/Ask.dc.html.
// A spoken (hold to ask) or typed question; the answer's figures come from code (POST /v1/ask), with source chips
// that open the bill line or the voice bills, and Listen (Sarvam TTS, or the phone's own voice). A drafted
// message is shared with the Web Share API (or copied). Laabam never sends it.
// /ask/draft/:itemId opens straight to a draft (Item detail's "Draft a message").

import { useEffect, useRef, useState } from 'react'
import { ApiError, api, type Answer, type AnswerSource } from '../api'
import { Link, useRouter } from '../router'
import { SPEECH_LOCALE, fmt, type Lang, type StringKey } from '../strings'
import { Banner, IconButton, Screen } from '../ui'
import { ChevronBackIcon, KeyboardIcon, MicIcon } from '../ui/icons'
import { useSession } from '../useSession'
import { holdHandlers, useVoice, type RecJob } from '../voice'
import { shortName } from './alertText'
import { shortDay } from './ItemDetail'

type T = (k: StringKey) => string
type Turn = { id: number; question: string; answer: Answer | null; error?: string }

function sourceLabel(t: T, s: AnswerSource, lang: Lang): string {
  if (s.kind === 'bill_line') return fmt(t('chipBillLine'), { WHO: shortName(s.distributor), DAY: shortDay(s.day, lang), LINE: s.position })
  if (s.kind === 'voice_bills') return s.count === 1 ? t('chipOneVoiceBill') : fmt(t('chipVoiceBills'), { N: s.count })
  return s.name
}

function sourceHref(s: AnswerSource): string {
  return s.kind === 'bill_line' ? `/bills-in/${s.purchase_id}` : `/items/${s.item_id}`
}

/** Read text aloud: the speech service's voice if there is one, else the phone's own. */
async function listen(text: string, lang: Lang, audio: React.MutableRefObject<HTMLAudioElement | null>) {
  const clip = await api.speak(text)
  if (clip) {
    audio.current?.pause()
    audio.current = new Audio(URL.createObjectURL(clip))
    await audio.current.play()
    return
  }
  if ('speechSynthesis' in window) {
    window.speechSynthesis.cancel()
    const u = new SpeechSynthesisUtterance(text)
    u.lang = SPEECH_LOCALE[lang]
    window.speechSynthesis.speak(u)
  }
}

function DraftCard({ text, onShared }: { text: string; onShared: (msg: string) => void }) {
  const { t } = useSession()
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(text)
  const share = async () => {
    try {
      if (navigator.share) {
        await navigator.share({ text: value })
        return
      }
      await navigator.clipboard.writeText(value)
      onShared(t('draftCopied'))
    } catch (e) {
      if ((e as DOMException)?.name !== 'AbortError') onShared(t('draftShareFailed'))
    }
  }
  return (
    <div className="bubble bubble--answer" data-testid="draft">
      <span className="label-caps">{t('draftYouSend')}</span>
      {editing ? (
        <textarea className="draft__text draft__text--edit" value={value} onChange={(e) => setValue(e.target.value)} rows={5} aria-label={t('draftEditLabel')} autoFocus />
      ) : (
        <p className="draft__text" data-testid="draft-text">
          {value}
        </p>
      )}
      <div className="draft__actions">
        <button type="button" className="small-btn small-btn--primary" onClick={share}>
          {t('share')}
        </button>
        <button type="button" className="small-btn" onClick={() => setEditing((e) => !e)}>
          {editing ? t('done') : t('edit')}
        </button>
      </div>
    </div>
  )
}

export default function Ask({ draftItem }: { draftItem?: string }) {
  const { t, lang, voice: voiceMode } = useSession()
  const { back } = useRouter()
  const voice = useVoice()
  const [turns, setTurns] = useState<Turn[]>([])
  const [typing, setTyping] = useState(voiceMode !== 'live')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const audio = useRef<HTMLAudioElement | null>(null)
  const nextId = useRef(1)
  const asked = useRef(false)
  const end = useRef<HTMLDivElement>(null)

  const run = async (question: string, fn: () => Promise<Answer>) => {
    const id = nextId.current++
    setTurns((ts) => [...ts, { id, question, answer: null }])
    setBusy(true)
    setNote(null)
    try {
      const a = await fn()
      const q = question || a.question || (a.draft ? fmt(t('writeTo'), { WHO: shortName(a.draft.to) }) : '')
      setTurns((ts) => ts.map((x) => (x.id === id ? { ...x, question: q, answer: a } : x)))
    } catch (e) {
      setTurns((ts) => ts.map((x) => (x.id === id ? { ...x, error: (e as ApiError).message } : x)))
    } finally {
      setBusy(false)
    }
  }
  const ask = (question: string, extra: { intent?: string; item_id?: string } = {}) =>
    run(question, () => api.ask({ text: question, ...extra }))

  useEffect(() => {
    if (draftItem && !asked.current) {
      asked.current = true
      run('', () => api.ask({ text: '', intent: 'draft', item_id: draftItem }))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftItem])

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: 'end' })
  }, [turns])

  const last = [...turns].reverse().find((x) => x.answer)?.answer
  // a follow-up the last answer makes possible: write to the distributor who raised the price
  const rise = last && last.intent === 'why_down' ? last.sources.find((s) => s.kind === 'bill_line') : undefined
  const suggestions: { label: string; go: () => void }[] = [
    ...(rise && rise.kind === 'bill_line'
      ? [{ label: fmt(t('writeAboutIt'), { WHO: shortName(rise.distributor) }), go: () => ask(fmt(t('writeAboutIt'), { WHO: shortName(rise.distributor) }), { intent: 'draft', item_id: last?.item_id ?? undefined }) }]
      : []),
    ...(turns.length === 0 ? [{ label: t('qWhyDown'), go: () => ask(t('qWhyDown')) }] : []),
    { label: t('qReorder'), go: () => ask(t('qReorder')) },
    { label: t('qBest'), go: () => ask(t('qBest')) },
  ]

  const job = (): RecJob => ({
    onAudio: async (clip) => {
      await run('', () => api.askVoice(clip))
    },
    onShort: () => setNote(t('holdLonger')),
    onError: (m) => setNote(m),
  })
  const hold = holdHandlers(voice, job)

  return (
    <Screen className="ask" testId="ask">
      <div className="detail-head ask__head">
        <IconButton label={t('back')} onClick={back}>
          <ChevronBackIcon />
        </IconButton>
        <h1 className="screen-title screen-title--item">{t('askTitle')}</h1>
      </div>

      <div className="ask__talk" aria-live="polite">
        {turns.length === 0 && <p className="note ask__hint">{t('askHint')}</p>}
        {turns.map((turn) => (
          <div key={turn.id} className="ask__turn">
            {turn.question && <span className="bubble bubble--q">{turn.question}</span>}
            {turn.error && (
              <Banner tone="error" role="alert">
                {turn.error}
              </Banner>
            )}
            {!turn.answer && !turn.error && <span className="bubble bubble--answer bubble--wait">{t('thinking')}</span>}
            {turn.answer && (!turn.answer.draft || turn.answer.intent !== 'draft') && (
              <div className="bubble bubble--answer" data-testid="answer">
                <p className="answer__text">
                  {turn.answer.segments.map((s, i) => (s.b ? <b key={i}>{s.t}</b> : <span key={i}>{s.t}</span>))}
                </p>
                {turn.answer.sources.length > 0 && (
                  <div className="answer__chips">
                    {turn.answer.sources.map((s, i) => (
                      <Link key={i} to={sourceHref(s)} className="source-chip" data-testid="source-chip">
                        {sourceLabel(t, s, lang)}
                      </Link>
                    ))}
                  </div>
                )}
                <button type="button" className="small-btn answer__listen" onClick={() => listen(turn.answer!.text, lang, audio)}>
                  <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
                    <path d="M3 1.8v10.4L12 7z" fill="currentColor" />
                  </svg>
                  {t('listen')}
                </button>
              </div>
            )}
            {turn.answer?.draft && <DraftCard text={turn.answer.draft.text} onShared={setNote} />}
          </div>
        ))}
        <div ref={end} />
      </div>

      <div className="ask__bottom">
        {note && <p className="note" role="status">{note}</p>}
        <div className="ask__suggest">
          {suggestions.map((s) => (
            <button key={s.label} type="button" className="suggest-chip" onClick={s.go} disabled={busy}>
              {s.label}
            </button>
          ))}
        </div>
        {typing ? (
          <form
            className="ask__type"
            onSubmit={(e) => {
              e.preventDefault()
              const q = text.trim()
              if (!q) return
              setText('')
              ask(q)
            }}
          >
            <input className="input2 ask__input" value={text} onChange={(e) => setText(e.target.value)} placeholder={t('typeQuestion')} aria-label={t('typeQuestion')} maxLength={500} />
            <button type="submit" className="small-btn small-btn--primary ask__send" disabled={busy || !text.trim()}>
              {t('askButton')}
            </button>
            {voiceMode === 'live' && (
              <IconButton label={t('holdToAsk')} onClick={() => setTyping(false)}>
                <MicIcon size={20} />
              </IconButton>
            )}
          </form>
        ) : (
          <div className="ask__holdrow">
            <button type="button" className="hold-ask" disabled={busy || voice.state === 'sending'} data-testid="hold-to-ask" {...hold}>
              <MicIcon size={22} />
              {voice.state === 'recording' ? t('listeningNow') : t('holdToAsk')}
            </button>
            <IconButton label={t('typeInstead')} onClick={() => setTyping(true)}>
              <KeyboardIcon size={20} />
            </IconButton>
          </div>
        )}
      </div>
    </Screen>
  )
}
