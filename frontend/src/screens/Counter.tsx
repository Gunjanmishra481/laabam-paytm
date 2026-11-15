// 2.1 Counter (home). Design: design/screens/Counter.dc.html.

import { useEffect, useRef, useState } from 'react'
import { ApiError, api, type Alert, type Bill, type ProfitReport, type Today, type VoiceBill } from '../api'
import { useFlash } from '../OwnerApp'
import { Link, useRouter } from '../router'
import { fmt } from '../strings'
import { istDay, istTime } from '../time'
import { Banner, BottomSheet, Button, HoldButton, IconButton, Money, Screen } from '../ui'
import { AskIcon, KeyboardIcon, SettingsIcon } from '../ui/icons'
import { useSession } from '../useSession'
import { fromSample, holdHandlers, markSampleBill, useVoice, type RecJob } from '../voice'
import { alertWords } from './alertText'
import { billSummary } from './bill/lines'
import { wholeRupees } from './Profit'

const EXAMPLES: Record<string, string[]> = {
  en: ['rice 200, sugar 100, oil 165', 'rice, oil', '2 kg rice at 60, 3 biscuit 30'],
  ta: ['அரிசி 200, சர்க்கரை 100, எண்ணெய் 165', 'அரிசி, எண்ணெய்'],
  hi: ['चावल 200, चीनी 100, तेल 165', 'चावल, तेल'],
}

/** After the shop's closing time (IST): the evening summary is ready. */
function afterClosing(closing: string | undefined): boolean {
  if (!closing) return false
  const ist = new Date(Date.now() + 330 * 60_000)
  const [h, m] = closing.split(':').map(Number)
  return ist.getUTCHours() * 60 + ist.getUTCMinutes() >= h * 60 + m
}

export default function Counter() {
  const { t, merchant, lang, voice: voiceMode } = useSession()
  const { navigate } = useRouter()
  const { flash, setFlash } = useFlash()
  const voice = useVoice()
  const [today, setToday] = useState<Today | null>(null)
  const [recent, setRecent] = useState<Bill[]>([])
  const [profit, setProfit] = useState<ProfitReport | null>(null)
  const [alert, setAlert] = useState<Alert | null>(null)
  // a message handed over from Listening ("hold longer", mic denied) is read once, on arrival
  const [typing, setTyping] = useState(() => !!flash?.openTyping)
  const [message, setMessage] = useState<string | null>(() => flash?.text ?? null)
  const [now] = useState(() => Date.now())

  useEffect(() => {
    api.today().then(setToday, () => undefined)
    api.profit('today').then(setProfit, () => undefined)
    api.alerts().then((a) => setAlert(a[0] ?? null), () => undefined) // the most important open one
    api.bills().then((b) => setRecent(b.filter((x) => x.status === 'paid').slice(0, 3)), () => undefined)
  }, [merchant?.id])

  useEffect(() => {
    if (flash) setFlash(null)
  }, [flash, setFlash])

  const job = (): RecJob => ({
    onAudio: async (audio, recordedMs) => {
      const sent = performance.now()
      try {
        const bill: VoiceBill = await api.billFromVoice(audio)
        if (fromSample(bill.speech)) markSampleBill(bill.id) // never shown as the owner's own words
        console.info('voice bill', {
          recorded_ms: Math.round(recordedMs),
          round_trip_ms: Math.round(performance.now() - sent),
          bytes: audio.size,
          ...bill.timings,
        })
        navigate(`/bill/${bill.id}`, { replace: true })
      } catch (e) {
        setFlash({ text: (e as ApiError).message })
        navigate('/', { replace: true })
      }
    },
    onShort: (why) => {
      setFlash({ text: why === 'mic_ready' ? t('micReady') : t('holdLonger') })
      navigate('/', { replace: true })
    },
    onError: (text) => {
      setFlash({ text, openTyping: true })
      navigate('/', { replace: true })
    },
  })

  const [sampling, setSampling] = useState(false)
  const playSample = async () => {
    setSampling(true)
    setMessage(null)
    try {
      const bill = await api.sampleOrder()
      markSampleBill(bill.id)
      navigate(`/bill/${bill.id}`)
    } catch (e) {
      setMessage((e as ApiError).message)
      setSampling(false)
    }
  }

  const hold = holdHandlers(voice, job, () => {
    setMessage(null)
    navigate('/bill/new')
  })

  return (
    <Screen tab="counter" className="counter">
      <div className="counter__top">
        <div className="screen-head">
          <div className="counter__shop">
            <span className="counter__date">
              {istDay(now, lang)}
              {merchant?.sample_data && (
                <span className="sample-tag" data-testid="sample-data">
                  {t('sampleData')}
                </span>
              )}
            </span>
            <h1 className="counter__name">{merchant?.name}</h1>
          </div>
          <div className="counter__icons">
            <IconButton label={t('askLaabam')} to="/ask" tone="green">
              <AskIcon />
            </IconButton>
            <IconButton label={t('settings')} to="/settings">
              <SettingsIcon />
            </IconButton>
          </div>
        </div>

        <Link to="/profit" className="today" aria-live="polite" data-testid="today-card">
          <span className="today__cell">
            <span className="today__label">{t('todaySales')}</span>
            <span className="today__fig" data-testid="today-sales">
              <Money paise={today?.sales_paise ?? 0} />
            </span>
          </span>
          <span className="today__cell">
            <span className="today__label">{profit?.estimated ? t('profitAbout') : t('profitToday')}</span>
            <span className="today__fig today__fig--profit" data-testid="today-profit">
              {profit && profit.known_sales_paise > 0 ? (
                wholeRupees(profit.profit_paise)
              ) : (
                <>
                  <span aria-hidden="true">—</span>
                  <span className="sr-only">{t('profitNotYet')}</span>
                </>
              )}
            </span>
          </span>
          <span className="today__cell">
            <span className="today__label">{t('billsCount')}</span>
            <span className="today__fig num" data-testid="today-bills">
              {today?.paid_bills ?? 0}
            </span>
          </span>
        </Link>
        {afterClosing(merchant?.closing_time) && (
          <Banner tone="info" to="/summary" action={t('play')}>
            <span data-testid="summary-ready">{t('summaryReady')}</span>
          </Banner>
        )}
        {alert && (
          <Banner tone="amber" to="/alerts" action={t('see')}>
            <span data-testid="counter-alert">{alertWords(alert, t, lang).title}</span>
          </Banner>
        )}
      </div>

      <div className="counter__hold">
        {voiceMode === 'live' ? (
          <>
            <HoldButton
              holding={false}
              label={t('holdToBill')}
              disabled={voice.state !== 'idle'}
              data-testid="push-to-talk"
              {...hold}
            />
            <p className="counter__hint">
              {t('sayHint')}
              <br />
              <span className="counter__example">{t('sayExample')}</span>
            </p>
          </>
        ) : (
          <>
            {/* no speech service: say so plainly; the big button types instead of pretending to listen */}
            <Banner tone="amber" role="status">
              <span data-testid="voice-off">{voiceMode === 'samples' ? t('speechOffSamples') : t('speechOff')}</span>
            </Banner>
            <HoldButton holding={false} label={t('typeOrder')} icon={<KeyboardIcon size={44} />} onClick={() => setTyping(true)} />
            {voiceMode === 'samples' && (
              <Button variant="secondary" size="md" block={false} disabled={sampling} onClick={playSample} data-testid="sample-voice">
                {sampling ? t('hearing') : t('playSample')}
              </Button>
            )}
          </>
        )}
        {message && (
          <p className="counter__msg" role="status">
            {message}
          </p>
        )}
        {voiceMode === 'live' && (
          <Button variant="text" block={false} onClick={() => setTyping(true)} className="counter__type">
            {t('typeInstead')}
          </Button>
        )}
      </div>

      <section className="recent2" aria-labelledby="recent-h">
        <h2 id="recent-h" className="label-caps">
          {t('recentBills')}
        </h2>
        {recent.length === 0 && <p className="note">{t('noBillsYet')}</p>}
        {recent.map((b) => (
          <Link key={b.id} to={`/bill/${b.id}/view`} className="recent2__row">
            <span className="recent2__main">
              <span className="recent2__items">{billSummary(b) || '—'}</span>
              <span className="recent2__sub">{fmt(t('paidAtTime'), { TIME: istTime(b.paid_at ?? b.created_at, lang) })}</span>
            </span>
            <Money className="recent2__amount" paise={b.total_paise} />
          </Link>
        ))}
      </section>

      <TypeSheet open={typing} onClose={() => setTyping(false)} examples={EXAMPLES[lang] ?? EXAMPLES.en} />
    </Screen>
  )
}

/** "Type instead": the text fallback for billing (also accessibility and the demo). */
function TypeSheet({ open, onClose, examples }: { open: boolean; onClose: () => void; examples: string[] }) {
  const { t } = useSession()
  const { navigate } = useRouter()
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const area = useRef<HTMLTextAreaElement>(null)

  const submit = async () => {
    if (!text.trim() || busy) return
    setBusy(true)
    setError(null)
    try {
      const bill = await api.billFromText(text)
      onClose()
      navigate(`/bill/${bill.id}`)
    } catch (e) {
      setError((e as ApiError).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <BottomSheet open={open} onClose={onClose} label={t('typeOrder')}>
      <form
        className="sheet-form"
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <label htmlFor="order" className="sheet__title">
          {t('typeOrder')}
        </label>
        <textarea
          id="order"
          ref={area}
          className="input2"
          rows={3}
          value={text}
          data-autofocus
          placeholder={t('orderPlaceholder')}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              void submit()
            }
          }}
        />
        <div className="chip-row" aria-label={t('tryThese')}>
          {examples.map((ex) => (
            <button
              key={ex}
              type="button"
              className="chip-btn"
              onClick={() => {
                setText(ex)
                area.current?.focus()
              }}
            >
              {ex}
            </button>
          ))}
        </div>
        {error && <Banner tone="error" role="alert">{error}</Banner>}
        <Button type="submit" disabled={busy || !text.trim()}>
          {t('makeBill')}
        </Button>
      </form>
    </BottomSheet>
  )
}
