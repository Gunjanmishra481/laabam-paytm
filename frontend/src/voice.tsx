// Push-to-talk recording, shared by the Counter → Listening screens and the "Or hold and say it" fix.
// Pointer: press to start, release (anywhere) to stop. Keyboard: Space/Enter starts, again stops.
// The mic opens on the first hold, never at load; it is closed as soon as recording stops.
// The audio goes to the server once and is not kept anywhere.

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useSession } from './useSession'

export const SHORT_MS = 400
const MAX_MS = 30_000
const TYPES = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4', 'audio/webm']

export type RecState = 'idle' | 'starting' | 'recording' | 'sending'
export type RecMode = 'hold' | 'toggle'

/** What to do with a finished recording. */
export type RecJob = {
  onAudio: (audio: Blob, recordedMs: number) => Promise<void>
  /** Released too soon, or before the mic opened. */
  onShort?: (reason: 'short' | 'mic_ready') => void
  onError?: (message: string) => void
}

type Voice = {
  state: RecState
  mode: RecMode
  /** 0..1 loudness of the incoming audio, updated every frame while recording. */
  level: React.MutableRefObject<number>
  start: (job: RecJob, mode: RecMode) => void
  stop: () => void
  cancel: () => void
}

const VoiceContext = createContext<Voice | null>(null)

export function canRecord(): boolean {
  return typeof window !== 'undefined' && !!navigator.mediaDevices?.getUserMedia && 'MediaRecorder' in window
}

export function VoiceProvider({ children }: { children: ReactNode }) {
  const { t, voice } = useSession()
  const [state, setState] = useState<RecState>('idle')
  const [mode, setMode] = useState<RecMode>('hold')
  const stateRef = useRef<RecState>('idle')
  const level = useRef(0)
  const job = useRef<RecJob | null>(null)
  const rec = useRef<MediaRecorder | null>(null)
  const stream = useRef<MediaStream | null>(null)
  const audioCtx = useRef<AudioContext | null>(null)
  const raf = useRef(0)
  const startedAt = useRef(0)
  const releasedEarly = useRef(false)
  const cancelled = useRef(false)
  const timer = useRef<number | undefined>(undefined)

  const set = (s: RecState) => {
    stateRef.current = s
    setState(s)
  }

  const teardown = useCallback(() => {
    window.clearTimeout(timer.current)
    cancelAnimationFrame(raf.current)
    stream.current?.getTracks().forEach((tr) => tr.stop())
    stream.current = null
    audioCtx.current?.close().catch(() => undefined)
    audioCtx.current = null
    level.current = 0
  }, [])

  useEffect(
    () => () => {
      const r = rec.current
      if (r && r.state !== 'inactive') {
        r.onstop = null
        r.stop()
      }
      teardown()
    },
    [teardown],
  )

  const finish = useCallback(
    async (audio: Blob, ms: number) => {
      const j = job.current
      if (!j) return set('idle')
      if (cancelled.current) {
        job.current = null
        return set('idle')
      }
      if (ms < SHORT_MS) {
        job.current = null
        set('idle')
        return j.onShort?.('short')
      }
      set('sending')
      try {
        await j.onAudio(audio, ms)
      } finally {
        job.current = null
        set('idle')
      }
    },
    [],
  )

  const stop = useCallback(() => {
    const s = stateRef.current
    if (s === 'starting') {
      releasedEarly.current = true
      return
    }
    if (s !== 'recording') return
    const r = rec.current
    if (r && r.state === 'recording') r.stop()
  }, [])

  const cancel = useCallback(() => {
    cancelled.current = true
    const r = rec.current
    if (r && r.state === 'recording') r.stop()
    else {
      teardown()
      job.current = null
      set('idle')
    }
  }, [teardown])

  const meter = (s: MediaStream) => {
    try {
      const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
      const ctx = new Ctx()
      const an = ctx.createAnalyser()
      an.fftSize = 512
      ctx.createMediaStreamSource(s).connect(an)
      audioCtx.current = ctx
      const buf = new Uint8Array(an.fftSize)
      const tick = () => {
        an.getByteTimeDomainData(buf)
        let sum = 0
        for (const v of buf) sum += (v - 128) * (v - 128)
        const rms = Math.sqrt(sum / buf.length) / 128
        level.current = Math.min(1, rms * 4)
        raf.current = requestAnimationFrame(tick)
      }
      tick()
    } catch {
      /* no meter: the bars stay still */
    }
  }

  const start = useCallback(
    (j: RecJob, m: RecMode) => {
      if (stateRef.current !== 'idle') return
      job.current = j
      cancelled.current = false
      releasedEarly.current = false
      setMode(m)
      if (voice !== 'live') {
        // no speech service: never pretend to record (a sample order must not stand in for the owner's words)
        job.current = null
        return j.onError?.(t('voiceOffShort'))
      }
      if (!canRecord()) {
        job.current = null
        return j.onError?.(t('micMissing'))
      }
      set('starting')
      navigator.mediaDevices
        .getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } })
        .then((s) => {
          if (cancelled.current || (releasedEarly.current && m === 'hold')) {
            // let go while the permission prompt was up: the mic is ready for the next hold
            s.getTracks().forEach((tr) => tr.stop())
            job.current = null
            set('idle')
            if (!cancelled.current) j.onShort?.('mic_ready')
            return
          }
          stream.current = s
          const mimeType = TYPES.find((x) => MediaRecorder.isTypeSupported(x))
          const r = new MediaRecorder(s, mimeType ? { mimeType, audioBitsPerSecond: 32_000 } : undefined)
          const chunks: Blob[] = []
          r.ondataavailable = (e) => e.data.size && chunks.push(e.data)
          r.onstop = () => {
            const ms = performance.now() - startedAt.current
            teardown()
            void finish(new Blob(chunks, { type: r.mimeType || mimeType || 'audio/webm' }), ms)
          }
          rec.current = r
          startedAt.current = performance.now()
          r.start()
          meter(s)
          timer.current = window.setTimeout(() => r.state === 'recording' && r.stop(), MAX_MS)
          set('recording')
        })
        .catch(() => {
          job.current = null
          set('idle')
          j.onError?.(t('micDenied'))
        })
    },
    [finish, t, teardown, voice],
  )

  // hold mode: letting go anywhere on the page stops (the press began on another screen's button)
  useEffect(() => {
    if (mode !== 'hold' || (state !== 'recording' && state !== 'starting')) return
    const up = () => stop()
    window.addEventListener('pointerup', up)
    window.addEventListener('pointercancel', up)
    return () => {
      window.removeEventListener('pointerup', up)
      window.removeEventListener('pointercancel', up)
    }
  }, [mode, state, stop])

  const value = useMemo(() => ({ state, mode, level, start, stop, cancel }), [state, mode, start, stop, cancel])
  return <VoiceContext.Provider value={value}>{children}</VoiceContext.Provider>
}

export function useVoice(): Voice {
  const v = useContext(VoiceContext)
  if (!v) throw new Error('useVoice outside VoiceProvider')
  return v
}

/** Pointer and keyboard handlers for a push-to-talk button. */
export function holdHandlers(v: Voice, job: () => RecJob, onStart?: () => void) {
  return {
    onPointerDown: (e: React.PointerEvent<HTMLButtonElement>) => {
      if (e.button !== 0 || v.state !== 'idle') return
      e.preventDefault()
      v.start(job(), 'hold')
      onStart?.()
    },
    onClick: (e: React.MouseEvent<HTMLButtonElement>) => {
      if (e.detail !== 0) return // pointer presses are handled on down/up
      if (v.state === 'idle') {
        v.start(job(), 'toggle')
        onStart?.()
      } else if (v.state === 'recording' || v.state === 'starting') v.stop()
    },
  }
}

// Bills made from a demo sample order, not from the owner's voice. Kept for the tab (sessionStorage) so the
// checklist can always say so, even after a refresh.
const SAMPLE_KEY = 'laabam.sampleBills'
function readSamples(): string[] {
  try {
    return JSON.parse(sessionStorage.getItem(SAMPLE_KEY) ?? '[]') as string[]
  } catch {
    return []
  }
}
const sampleBills = new Set<string>(readSamples())

export function markSampleBill(id: string) {
  sampleBills.add(id)
  try {
    sessionStorage.setItem(SAMPLE_KEY, JSON.stringify([...sampleBills].slice(-50)))
  } catch {
    /* the in-memory set still works */
  }
}

export const isSampleBill = (id: string) => sampleBills.has(id)

/** A voice reply that came from offline/sample speech rather than a live speech service. */
export const fromSample = (speech: { provider: string; sample: string | null } | undefined) =>
  !!speech && (speech.provider === 'offline' || !!speech.sample)
