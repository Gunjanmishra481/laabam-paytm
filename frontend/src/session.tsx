// The signed-in shop and its language. A returning device signs in with its refresh cookie. Paytm for Business
// opens Laabam with a signed assertion in the URL fragment (#assertion=...), exchanged once and removed from the
// address bar. In the demo (sandbox), a device with no session starts a demo shop of its own.

import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { api, refreshSession, setToken, type Merchant, type PublicConfig, type SignedIn, type VoiceMode } from './api'
import { t as translate, type Lang } from './strings'
import { SessionContext, type Session, type SessionStatus } from './useSession'

function assertionFromHash(): string | null {
  const m = window.location.hash.match(/assertion=([^&]+)/)
  if (!m) return null
  window.history.replaceState(null, '', window.location.pathname + window.location.search) // not kept in history
  return decodeURIComponent(m[1])
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<SessionStatus>('loading')
  const [sandbox, setSandbox] = useState(false)
  const [voice, setVoice] = useState<VoiceMode>('off')
  const [vision, setVision] = useState<VoiceMode>('off')
  const [billSamples, setBillSamples] = useState<string[]>([])
  const [merchant, setMerchant] = useState<Merchant | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [config, setConfig] = useState<PublicConfig | null>(null)

  useEffect(() => {
    let live = true
    ;(async () => {
      try {
        const [h, c] = await Promise.all([api.health(), api.config().catch(() => null)])
        if (!live) return
        setConfig(c)
        setSandbox(h.sandbox)
        setVoice(h.voice ?? 'off')
        setVision(h.vision ?? 'off')
        setBillSamples(h.bill_samples ?? [])
        const assertion = assertionFromHash()
        const url = new URL(window.location.href)
        const fresh = url.searchParams.get('fresh') === '1'
        const resumed = assertion || fresh ? null : await refreshSession()
        if (assertion) {
          const s = await api.partnerSignIn(assertion)
          setToken(s.access_token)
          if (live) setMerchant(s.merchant)
        } else if (resumed) {
          if (live) setMerchant(resumed.merchant)
        } else if (h.sandbox) {
          // ?fresh=1 (demo): start as a new, empty shop. The flag is taken off the URL so a reload keeps it.
          const s = await api.sandboxSession({ fresh })
          if (fresh) {
            url.searchParams.delete('fresh')
            window.history.replaceState(null, '', url.pathname + url.search + url.hash)
          }
          setToken(s.access_token)
          if (live) setMerchant(s.merchant)
        } else {
          if (live) setStatus('signed_out')
          return
        }
        if (live) setStatus('ready')
      } catch {
        if (live) setStatus('offline')
      }
    })()
    return () => {
      live = false
    }
  }, [attempt])

  const resetDemo = useCallback(async (opts: { fresh?: boolean; language?: Lang } = {}) => {
    const s = await api.sandboxSession({ reset: !opts.fresh, fresh: opts.fresh, language: opts.language })
    setToken(s.access_token)
    setMerchant(s.merchant)
  }, [])

  const signedIn = useCallback((s: SignedIn) => {
    setToken(s.access_token)
    setMerchant(s.merchant)
    setStatus('ready')
  }, [])

  const lang: Lang = merchant?.language ?? 'en'
  const value: Session = {
    status,
    sandbox,
    voice,
    vision,
    billSamples,
    merchant,
    lang,
    t: (key) => translate(lang, key),
    retry: () => {
      setStatus('loading')
      setAttempt((a) => a + 1)
    },
    resetDemo,
    setMerchant,
    config,
    beta: !!config?.beta,
    signedIn,
  }
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}
