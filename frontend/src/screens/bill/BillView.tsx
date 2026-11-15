// A bill as it was: from Recent bills and from "View this bill" on the Paid screen. Read-only.

import { useEffect, useState } from 'react'
import { ApiError, api, type Bill, type BillStatus } from '../../api'
import { useRouter } from '../../router'
import { istTime } from '../../time'
import { Banner, IconButton, ListRow, Money, Screen } from '../../ui'
import { BackIcon, MicIcon } from '../../ui/icons'
import { useSession } from '../../useSession'
import { lineName, lineNote } from './lines'

export default function BillView({ id }: { id: string }) {
  const { t, lang } = useSession()
  const { navigate } = useRouter()
  const [bill, setBill] = useState<Bill | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.bill(id).then((b) => {
      // still open: the live flow is the right screen
      if (b.status === 'draft' || b.status === 'confirmed' || b.status === 'expired') navigate(`/bill/${id}`, { replace: true })
      else setBill(b)
    }, (e: ApiError) => setError(e.message))
  }, [id, navigate])

  return (
    <Screen className="bill-view">
      <div className="screen-head screen-head--start">
        <IconButton label={t('back')} onClick={() => navigate('/')}>
          <BackIcon />
        </IconButton>
        <h1 className="screen-title screen-title--detail">{t('billTitle')}</h1>
      </div>
      {error && (
        <Banner tone="error" role="alert">
          {error}
        </Banner>
      )}
      {bill && (
        <>
          <p className="note">
            {istTime(bill.paid_at ?? bill.created_at, lang)} · {t(`status_${bill.status as BillStatus}`)}
          </p>
          {bill.transcript && (
            <div className="said">
              <MicIcon size={20} className="said__icon" />
              <p>
                <span className="said__label">{t('youSaid')}</span> “{bill.transcript}”
              </p>
            </div>
          )}
          <div className="card2 bill-card">
            {bill.lines.map((l) => (
              <ListRow key={l.id} label={lineName(l, t)} sub={lineNote(l, t).text} value={<Money paise={l.line_total_paise} />} />
            ))}
            <div className="bill-total">
              <span>{t('total')}</span>
              <span className="bill-total__fig">
                <Money paise={bill.total_paise} />
              </span>
            </div>
          </div>
        </>
      )}
    </Screen>
  )
}
