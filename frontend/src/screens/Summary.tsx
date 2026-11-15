// 4.5 Evening summary. Design: design/screens/Summary.dc.html.
// The closing-time summary the Soundbox spoke (GET /v1/summaries/latest), or today's so far. Play/pause reads it
// aloud (the speech service's voice, else the phone's own), the transcript below, and four figures. Every number
// in it was filled by code; a model's wording is used only when it has no digits (backend/laabam/summary.py).

import { useEffect, useRef, useState } from 'react'
import { ApiError, api, type Summary as SummaryT } from '../api'
import { Link, useRouter } from '../router'
import { SPEECH_LOCALE, fmt, type StringKey } from '../strings'
import { istTime } from '../time'
import { Banner, IconButton, Screen } from '../ui'
import { useSession } from '../useSession'
import { wholeRupees } from './Profit'

const today = () => new Date(Date.now() + 330 * 60_000).toISOString().slice(0, 10)

function clock(s: number): string {
  const n = Math.max(0, Math.round(s))
  return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`
}

export default function Summary() {
  const { t, lang } = useSession()
  const { navigate } = useRouter()
  const [sum, setSum] = useState<SummaryT | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [playing, setPlaying] = useState(false)
  const [progress, setProgress] = useState(1)
  const [length, setLength] = useState<number | null>(null)
  const audio = useRef<HTMLAudioElement | null>(null)

  useEffect(() => {
    let live = true
    api
      .latestSummary()
      .then((s) => (s && s.day === today() ? s : api.summaryNow())) // none yet today: today's so far
      .then((s) => live && setSum(s), (e: ApiError) => live && setError(e.message))
    return () => {
      live = false
      audio.current?.pause()
      if ('speechSynthesis' in window) window.speechSynthesis.cancel()
    }
  }, [])

  const toggle = async () => {
    if (!sum) return
    if (playing) {
      audio.current?.pause()
      if ('speechSynthesis' in window) window.speechSynthesis.pause()
      setPlaying(false)
      return
    }
    if (audio.current) {
      await audio.current.play()
      setPlaying(true)
      return
    }
    if ('speechSynthesis' in window && window.speechSynthesis.paused) {
      window.speechSynthesis.resume()
      setPlaying(true)
      return
    }
    setPlaying(true)
    setProgress(0)
    const clip = await api.speak(sum.text)
    if (clip) {
      const a = new Audio(URL.createObjectURL(clip))
      audio.current = a
      a.onloadedmetadata = () => setLength(a.duration)
      a.ontimeupdate = () => a.duration && setProgress(a.currentTime / a.duration)
      a.onended = () => {
        setPlaying(false)
        setProgress(1)
      }
      await a.play()
    } else if ('speechSynthesis' in window) {
      const u = new SpeechSynthesisUtterance(sum.text)
      u.lang = SPEECH_LOCALE[lang]
      u.onboundary = (e) => setProgress(e.charIndex / sum.text.length)
      u.onend = () => {
        setPlaying(false)
        setProgress(1)
      }
      window.speechSynthesis.cancel()
      window.speechSynthesis.speak(u)
    } else setPlaying(false)
  }

  const f = sum?.figures
  const watch = f?.watch[0]
  const at = sum ? (sum.played_at ?? sum.created_at) : null
  const weekday = sum ? new Intl.DateTimeFormat(SPEECH_LOCALE[lang], { weekday: 'long', timeZone: 'UTC' }).format(Date.parse(`${sum.day}T00:00:00Z`)) : ''

  return (
    <Screen tone="dark" className="summary" testId="summary">
      <div className="detail-head">
        <IconButton label={t('close')} tone="dark" onClick={() => navigate('/')}>
          <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
            <path d="M5 5l10 10M15 5L5 15" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        </IconButton>
        <span className="summary__head">
          <span className="summary__when">{at ? `${weekday} · ${istTime(at, lang)}` : ''}</span>
          <h1 className="summary__title">{t('todaysSummary')}</h1>
        </span>
      </div>

      {error && (
        <Banner tone="error" role="alert">
          {error}
        </Banner>
      )}

      {sum && f && (
        <>
          <section className="player">
            <div className="player__row">
              <button type="button" className="player__btn" onClick={toggle} aria-label={playing ? t('pauseSummary') : t('playSummary')} data-testid="play">
                {playing ? (
                  <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">
                    <rect x="5" y="4" width="4" height="14" rx="1" fill="currentColor" />
                    <rect x="13" y="4" width="4" height="14" rx="1" fill="currentColor" />
                  </svg>
                ) : (
                  <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">
                    <path d="M7 4.5v13l11-6.5z" fill="currentColor" />
                  </svg>
                )}
              </button>
              <div className="player__info">
                <span className="player__title">{sum.played_at ? t('playedOnSoundbox') : t('todaySoFar')}</span>
                <span className="player__track" aria-hidden="true">
                  <span className="player__fill" style={{ width: `${Math.round(progress * 100)}%` }} />
                </span>
                <span className="player__meta">{length ? `${clock(length)} · ${t('inYourLanguage')}` : t('inYourLanguage')}</span>
              </div>
            </div>
            <p className="player__text" data-testid="summary-text">
              “{sum.text}”
            </p>
          </section>

          <div className="sumtiles" data-testid="summary-figures">
            <span className="sumtile">
              <span className="sumtile__label">{t('salesLabel')}</span>
              <span className="sumtile__money">{wholeRupees(f.sales_paise)}</span>
            </span>
            <span className="sumtile">
              <span className="sumtile__label">{f.estimated ? t('profitAbout') : t('profitLabel')}</span>
              <span className="sumtile__money sumtile__money--profit">{f.profit_known ? wholeRupees(f.profit_paise) : '—'}</span>
            </span>
            <span className="sumtile">
              <span className="sumtile__label">{t('earnedMost')}</span>
              <span className="sumtile__word">{f.top?.name ?? '—'}</span>
            </span>
            <span className="sumtile">
              <span className="sumtile__label">{t('toWatch')}</span>
              <span className="sumtile__word">{watch ? fmt(t(`watch_${watch.kind}` as StringKey), { ITEM: watch.item_name }) : t('nothingToWatch')}</span>
            </span>
          </div>

          <Link to="/plan/reorder" className="btn2 btn2--lg summary__cta">
            {t('seeReorderList')}
          </Link>
        </>
      )}
    </Screen>
  )
}
