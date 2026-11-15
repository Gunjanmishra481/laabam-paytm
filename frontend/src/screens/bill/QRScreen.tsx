// 2.5 QR. Design: design/screens/QR.dc.html. Shown only after the owner confirmed the checklist.
// Polls the bill until it's paid or the QR expires.

import { useEffect, useState } from 'react'
import { ApiError, api, type Bill } from '../../api'
import { rupees } from '../../money'
import { useRouter } from '../../router'
import { fmt, isStringKey } from '../../strings'
import { Banner, Button, Money, QRCode, Screen } from '../../ui'
import { useSession } from '../../useSession'
import { billSummary } from './lines'

type Props = { bill: Bill; onChange: (b: Bill) => void }

function useSecondsLeft(expiresAt: string | undefined) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [])
  return expiresAt ? Math.max(0, Math.round((Date.parse(expiresAt) - now) / 1000)) : 0
}

export default function QRScreen({ bill, onChange }: Props) {
  const { t } = useSession()
  const { navigate } = useRouter()
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const left = useSecondsLeft(bill.qr?.expires_at)
  const waiting = bill.status === 'confirmed'
  const expired = bill.status === 'expired'

  useEffect(() => {
    if (!waiting) return
    const id = window.setInterval(() => {
      api.bill(bill.id).then((b) => {
        if (b.status === 'paid') navigator.vibrate?.(20)
        onChange(b)
      }, () => undefined)
    }, 1500)
    return () => window.clearInterval(id)
  }, [bill.id, waiting, onChange])

  const act = async (fn: () => Promise<Bill>, then?: (b: Bill) => void) => {
    setBusy(true)
    setError(null)
    try {
      const b = await fn()
      onChange(b)
      then?.(b)
    } catch (e) {
      setError((e as ApiError).message)
    } finally {
      setBusy(false)
    }
  }

  const issueKey = bill.payment_issue ? `issue_${bill.payment_issue}` : null
  const issue = issueKey && isStringKey(issueKey) ? t(issueKey) : null
  const amount = rupees(bill.total_paise)
  // the QR's whole life, as the API set it (5 minutes by default)
  const minutes =
    bill.qr?.expires_at && bill.confirmed_at
      ? Math.max(1, Math.round((Date.parse(bill.qr.expires_at) - Date.parse(bill.confirmed_at)) / 60000))
      : Math.max(1, Math.ceil(left / 60))

  return (
    <Screen tone="white" className="qr-screen">
      <span className="qr-screen__items">{billSummary(bill)}</span>
      <span className="qr-screen__amount display" data-testid="qr-amount">
        <Money paise={bill.total_paise} />
      </span>

      {expired ? (
        <div className="qr-screen__expired" role="status">
          <h1 className="sheet__title">{t('qrExpiredTitle')}</h1>
        </div>
      ) : (
        bill.qr && (
          <div className="qr-card">
            <QRCode payload={bill.qr.payload} label={fmt(t('qrLabel'), { AMOUNT: amount })} mark />
          </div>
        )
      )}

      {waiting && (
        <>
          <span className="qr-screen__scan">{t('scanAnyUpi')}</span>
          <div className="waiting" role="status">
            <span className="spinner" aria-hidden="true" />
            {t('waitingSoundbox')}
          </div>
          <span className="note qr-screen__timer">{fmt(t('qrWorksFor'), { N: minutes })}</span>
        </>
      )}

      {issue && <Banner tone="amber">{issue}</Banner>}
      {error && (
        <Banner tone="error" role="alert">
          {error}
        </Banner>
      )}

      <div className="actions">
        {expired && (
          <Button disabled={busy} onClick={() => act(() => api.showQr(bill.id))}>
            {t('showAgainShort')}
          </Button>
        )}
        <div className="btn-row">
          {!expired && (
            <Button variant="secondary" size="md" disabled={busy} onClick={() => act(() => api.reopen(bill.id))}>
              {t('editBill')}
            </Button>
          )}
          <Button
            variant="danger"
            size="md"
            disabled={busy}
            onClick={() => act(() => api.cancel(bill.id), () => navigate('/', { replace: true }))}
          >
            {t('cancelBill')}
          </Button>
        </div>
      </div>
    </Screen>
  )
}
