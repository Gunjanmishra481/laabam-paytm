// 2.6 Paid. Design: design/screens/Paid.dc.html. The Soundbox announcement happens on the server.

import { useEffect, useState } from 'react'
import { api, type Bill, type Today } from '../../api'
import { rupees } from '../../money'
import { useRouter } from '../../router'
import { fmt, isStringKey } from '../../strings'
import { istTime } from '../../time'
import { Banner, Button, Money, Screen } from '../../ui'
import { TickIcon } from '../../ui/icons'
import { useSession } from '../../useSession'

export default function Paid({ bill }: { bill: Bill }) {
  const { t, lang } = useSession()
  const { navigate } = useRouter()
  const [today, setToday] = useState<Today | null>(null)
  useEffect(() => {
    api.today().then(setToday, () => undefined)
  }, [])

  const issueKey = bill.payment_issue ? `issue_${bill.payment_issue}` : null
  const issue = issueKey && isStringKey(issueKey) ? t(issueKey) : null

  return (
    <Screen tone="green" className="paid2" testId="paid">
      <div className="paid2__body" role="status">
        <span className="paid2__tick" aria-hidden="true">
          <TickIcon size={56} />
        </span>
        <h1 className="paid2__amount display">
          <Money paise={bill.total_paise} />
        </h1>
        <p className="paid2__received">{t('received')}</p>
        {bill.paid_at && <p className="paid2__via">{fmt(t('paidVia'), { TIME: istTime(bill.paid_at, lang) })}</p>}
        {today && (
          <div className="paid2__today">
            <span className="paid2__today-label">{t('todaySoFar')}</span>
            <span className="paid2__today-fig">
              {today.paid_bills === 1
                ? fmt(t('fromOneBill'), { AMOUNT: rupees(today.sales_paise) })
                : fmt(t('fromBills'), { AMOUNT: rupees(today.sales_paise), N: today.paid_bills })}
            </span>
          </div>
        )}
        {issue && <Banner tone="amber">{issue}</Banner>}
      </div>
      <Button variant="light" onClick={() => navigate('/')}>
        {t('nextBill')}
      </Button>
      <Button variant="text-light" block={false} className="paid2__view" onClick={() => navigate(`/bill/${bill.id}/view`)}>
        {t('viewBill')}
      </Button>
    </Screen>
  )
}
