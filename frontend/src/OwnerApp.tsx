// The owner's phone. Routes follow design/DESIGN.md §3; billing is one flow under /bill/:id.

import { createContext, useContext, useEffect, useState } from 'react'
import { match, useRouter } from './router'
import { useSession } from './useSession'
import { VoiceProvider } from './voice'
import { Button, Screen } from './ui'
import Counter from './screens/Counter'
import Listening from './screens/Listening'
import BillFlow from './screens/bill/BillFlow'
import BillView from './screens/bill/BillView'
import NotYet from './screens/NotYet'
import Settings from './screens/Settings'
import Setup from './screens/Setup'
import Welcome from './screens/Welcome'
import BillsIn from './screens/BillsIn'
import BillReview from './screens/BillReview'
import Profit from './screens/Profit'
import ItemDetail from './screens/ItemDetail'
import Alerts from './screens/Alerts'
import Ask from './screens/Ask'
import Summary from './screens/Summary'
import Plan, { Reorder } from './screens/Plan'
import Loan from './screens/Loan'

/** A one-off message for the Counter (e.g. "Hold the button while you speak"), and whether to open typing. */
export type Flash = { text: string; openTyping?: boolean } | null
const FlashContext = createContext<{ flash: Flash; setFlash: (f: Flash) => void }>({ flash: null, setFlash: () => undefined })
export const useFlash = () => useContext(FlashContext)

export default function OwnerApp({ root = true }: { root?: boolean }) {
  const { status, t, retry, sandbox } = useSession()
  const [flash, setFlash] = useState<Flash>(null)

  let body
  if (status === 'loading') body = <Screen className="screen--center">{t('connecting')}</Screen>
  else if (status === 'offline' || status === 'signed_out')
    body = (
      <Screen className="screen--center">
        <p>{t('offline')}</p>
        <Button onClick={retry}>{t('retry')}</Button>
      </Screen>
    )
  else body = <Routes />

  return (
    <FlashContext.Provider value={{ flash, setFlash }}>
      <VoiceProvider>
        <div className={root ? 'app app--root' : 'app'}>
          {sandbox && (
            // Every sandbox shop (seeded or new) is a test shop: said on every screen, onboarding included
            <p className="test-notice" role="note" data-testid="test-shop-notice">
              {t('testShopNotice')}
            </p>
          )}
          {body}
        </div>
      </VoiceProvider>
    </FlashContext.Provider>
  )
}

/** Sends the app to `to` (replacing history) and shows nothing meanwhile. */
function Redirect({ to }: { to: string }) {
  const { navigate } = useRouter()
  useEffect(() => navigate(to, { replace: true }), [to, navigate])
  return null
}

function Routes() {
  const { path } = useRouter()
  const { merchant } = useSession()
  let m: Record<string, string> | null

  // First run (DESIGN.md §3): no language -> Welcome; no shop name or UPI ID -> Setup; then the Counter.
  const needs = merchant?.needs ?? []
  if (needs.includes('language') && path !== '/welcome') return <Redirect to="/welcome" />
  if (!needs.includes('language') && needs.includes('shop') && path !== '/setup' && path !== '/welcome')
    return <Redirect to="/setup" />

  if (path === '/welcome') return <Welcome />
  if (path === '/setup') return <Setup />
  if (path === '/' || path === '') return <Counter />
  if (path === '/bill/new') return <Listening />
  if ((m = match('/bill/:id/view', path))) return <BillView key={m.id} id={m.id} />
  if ((m = match('/bill/:id', path))) return <BillFlow key={m.id} id={m.id} />
  if (path === '/settings') return <Settings />
  if (path === '/bills-in') return <BillsIn />
  if ((m = match('/bills-in/:id', path))) return <BillReview key={m.id} id={m.id} />
  if (path === '/profit') return <Profit />
  if ((m = match('/items/:id', path))) return <ItemDetail key={m.id} id={m.id} />
  if (path === '/alerts') return <Alerts />
  if (path === '/ask') return <Ask />
  if (path === '/summary') return <Summary />
  if ((m = match('/ask/draft/:id', path))) return <Ask key={m.id} draftItem={m.id} />
  if (path === '/plan') return <Plan />
  if (path === '/plan/reorder') return <Reorder />
  if ((m = match('/plan/loan/:id', path))) return <Loan key={m.id} id={m.id} />
  return <NotYet />
}
