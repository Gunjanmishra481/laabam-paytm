import { createContext, useContext } from 'react'
import type { Merchant, VoiceMode } from './api'
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
}

export const SessionContext = createContext<Session | null>(null)

export function useSession(): Session {
  const s = useContext(SessionContext)
  if (!s) throw new Error('useSession outside SessionProvider')
  return s
}
