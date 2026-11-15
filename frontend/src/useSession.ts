import { createContext, useContext } from 'react'
import type { Merchant, PublicConfig, SignedIn, VoiceMode } from './api'
import type { Lang, StringKey } from './strings'

export type SessionStatus = 'loading' | 'ready' | 'offline' | 'signed_out'

export type Session = {
  status: SessionStatus
  sandbox: boolean
  voice: VoiceMode
  vision: VoiceMode
  billSamples: string[]
  merchant: Merchant | null
  lang: Lang
  t: (key: StringKey) => string
  retry: () => void
  /** Sandbox: a new seeded demo shop, or (fresh) a new empty shop that starts at Welcome. */
  resetDemo: (opts?: { fresh?: boolean; language?: Lang }) => Promise<void>
  setMerchant: (m: Merchant) => void
  /** How owners sign in here; beta (real sign-up by phone, simulated payments). */
  config: PublicConfig | null
  beta: boolean
  /** A sign-in finished (phone sign-up): keep the session and go on. */
  signedIn: (s: SignedIn) => void
}

export const SessionContext = createContext<Session | null>(null)

export function useSession(): Session {
  const s = useContext(SessionContext)
  if (!s) throw new Error('useSession outside SessionProvider')
  return s
}
