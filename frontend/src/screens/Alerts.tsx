// 4.3 Alerts ("Things to look at"). Design: design/screens/Alerts.dc.html.
// One primary action per alert plus "Got it" (marks it seen). The words are filled from figures computed in
// code (alertText.ts). Empty state: all clear.

import { useEffect, useState } from 'react'
import { ApiError, api, type Alert } from '../api'
import { useRouter } from '../router'
import { Banner, IconButton, Screen } from '../ui'
import { ChevronBackIcon } from '../ui/icons'
import { useSession } from '../useSession'
import { alertWords } from './alertText'

export default function Alerts() {
  const { t, lang } = useSession()
  const { back, navigate } = useRouter()
  const [alerts, setAlerts] = useState<Alert[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.alerts().then(setAlerts, (e: ApiError) => setError(e.message))
  }, [])

  const gotIt = async (a: Alert) => {
    setAlerts((xs) => (xs ?? []).filter((x) => x.id !== a.id))
    try {
      await api.alertSeen(a.id)
    } catch (e) {
      setError((e as ApiError).message)
    }
  }
  const act = async (a: Alert, href: string) => {
    if (a.kind === 'running_out') await api.alertActed(a.id).catch(() => undefined) // on the reorder list now
    navigate(href)
  }

  return (
    <Screen className="alerts" testId="alerts">
      <div className="detail-head">
        <IconButton label={t('back')} onClick={back}>
          <ChevronBackIcon />
        </IconButton>
        <h1 className="screen-title screen-title--detail">{t('thingsToLookAt')}</h1>
      </div>
      {error && (
        <Banner tone="error" role="alert">
          {error}
        </Banner>
      )}
      {(alerts ?? []).map((a) => {
        const w = alertWords(a, t, lang)
        return (
          <article key={a.id} className="alert-card" data-testid="alert" data-kind={a.kind}>
            <span className={`alert-card__kind alert-card__kind--${w.tone}`}>
              <span className="alert-card__dot" aria-hidden="true" />
              {w.label}
            </span>
            <h2 className="alert-card__title">{w.title}</h2>
            <p className="alert-card__detail">{w.detail}</p>
            <div className="alert-card__actions">
              <button type="button" className="btn2 btn2--primary alert-card__btn" onClick={() => act(a, w.href)}>
                {w.action}
              </button>
              <button type="button" className="btn2 btn2--secondary alert-card__btn" onClick={() => gotIt(a)}>
                {t('gotIt')}
              </button>
            </div>
          </article>
        )
      })}
      {alerts && alerts.length === 0 && (
        <p className="alerts__empty" data-testid="alerts-empty">
          {t('allClear')}
        </p>
      )}
    </Screen>
  )
}
