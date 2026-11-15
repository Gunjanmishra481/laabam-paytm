// 2.2 Listening. Design: design/screens/Listening.dc.html.
// Shown while the owner holds to talk (the press began on the Counter). Letting go anywhere stops it.

import { useEffect, useRef } from 'react'
import { useRouter } from '../router'
import { Button, HoldButton, Screen } from '../ui'
import { useSession } from '../useSession'
import { useVoice } from '../voice'

const BARS = [24, 40, 58, 36, 64, 48, 70, 44, 60, 32, 52, 38, 26]

export default function Listening() {
  const { t, voice: voiceMode } = useSession()
  const { navigate } = useRouter()
  const voice = useVoice()
  const sending = voice.state === 'sending'

  // landed here without a recording (a refresh, or the back button): go home
  useEffect(() => {
    if (voice.state === 'idle') navigate('/', { replace: true })
  }, [voice.state, navigate])

  return (
    <Screen tone="dark" className="listening" label={t('listeningTitle')}>
      <div className="screen-head">
        <span className="listening__state">
          <span className="listening__dot" aria-hidden="true" />
          {sending ? t('hearing') : t('listeningTitle')}
        </span>
        <Button
          variant="dark-outline"
          block={false}
          disabled={sending}
          onClick={() => {
            voice.cancel()
            navigate('/', { replace: true })
          }}
        >
          {t('cancel')}
        </Button>
      </div>

      <p className="listening__words" aria-live="polite">
        {sending ? (
          t('hearing')
        ) : (
          <>
            “{t('listenPrompt')}
            <span className="listening__more">…</span>”
          </>
        )}
      </p>

      <Bars live={voice.state === 'recording' && voiceMode === 'live'} level={voice.level} />

      <p className="listening__hint">{t('sayAgainHint')}</p>

      <HoldButton
        holding
        className="listening__btn"
        label={voice.mode === 'toggle' ? t('tapToCheck') : t('letGoToCheck')}
        autoFocus={voice.mode === 'toggle'}
        disabled={sending}
        onClick={() => voice.stop()}
      />
    </Screen>
  )
}

/** Sound bars that move with the incoming audio; still when nothing comes in (and under reduced motion). */
function Bars({ live, level }: { live: boolean; level: React.MutableRefObject<number> }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el || !live) return
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
    let raf = 0
    let t0 = performance.now()
    const bars = Array.from(el.children) as HTMLElement[]
    const tick = (now: number) => {
      const l = level.current
      const dt = (now - t0) / 1000
      bars.forEach((b, i) => {
        const wobble = 0.5 + 0.5 * Math.sin(dt * (5 + (i % 4) * 1.3) + i)
        b.style.transform = `scaleY(${(0.35 + l * 0.65 * (0.5 + 0.5 * wobble)).toFixed(3)})`
      })
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => {
      cancelAnimationFrame(raf)
      t0 = 0
      bars.forEach((b) => (b.style.transform = ''))
    }
  }, [live, level])
  return (
    <div className="bars" aria-hidden="true" ref={ref}>
      {BARS.map((h, i) => (
        <span key={i} className="bars__bar" style={{ height: h }} />
      ))}
    </div>
  )
}
