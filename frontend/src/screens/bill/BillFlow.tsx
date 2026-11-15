// /bill/:id — one flow whose screen follows the bill's status: draft → Checklist (+ Fix a line),
// confirmed/expired → QR, paid → Paid. The id in the URL means a refresh doesn't lose the bill.

import { useEffect, useState } from 'react'
import { ApiError, api, type Bill } from '../../api'
import { useRouter } from '../../router'
import { Banner, Button, Screen } from '../../ui'
import { useSession } from '../../useSession'
import Checklist from './Checklist'
import Paid from './Paid'
import QRScreen from './QRScreen'

export default function BillFlow({ id }: { id: string }) {
  const { t } = useSession()
  const { navigate } = useRouter()
  const [bill, setBill] = useState<Bill | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.bill(id).then(setBill, (e: ApiError) => setError(e.message))
  }, [id])

  if (error)
    return (
      <Screen>
        <Banner tone="error" role="alert">
          {error}
        </Banner>
        <Button variant="secondary" onClick={() => navigate('/')}>
          {t('tab_counter')}
        </Button>
      </Screen>
    )
  if (!bill) return <Screen className="screen--center">{t('connecting')}</Screen>
  if (bill.status === 'draft') return <Checklist bill={bill} onChange={setBill} />
  if (bill.status === 'paid') return <Paid bill={bill} />
  if (bill.status === 'cancelled')
    return (
      <Screen className="screen--center">
        <p>{t('billCancelled')}</p>
        <Button onClick={() => navigate('/')}>{t('nextBill')}</Button>
      </Screen>
    )
  return <QRScreen bill={bill} onChange={setBill} />
}
