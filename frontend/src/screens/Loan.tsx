// 5.2 Loan offer. Design: design/screens/Loan.dc.html.
// The amount sized from the plan (shortfall = order - cash in hand), its basis, and the partner's terms (shown as
// placeholders until the lending partner gives them). Approve sends the application; the partner decides. After
// approve the buttons go and the "sent" status shows. Nothing is sent without the owner's tap.

import { useEffect, useState } from 'react'
import { ApiError, api, type LoanOfferT } from '../api'
import { rupees } from '../money'
import { useRouter } from '../router'
import { fmt } from '../strings'
import { Banner, IconButton, Screen } from '../ui'
import { ChevronBackIcon } from '../ui/icons'
import { useSession } from '../useSession'

export default function Loan({ id }: { id: string }) {
  const { t, lang } = useSession()
  const { back, navigate } = useRouter()
  const [offer, setOffer] = useState<LoanOfferT | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.getLoanOffer(id).then(setOffer, (e: ApiError) => setError(e.message))
  }, [id])

  const decide = async (approve: boolean) => {
    setBusy(true)
    setError(null)
    try {
      const o = approve ? await api.approveLoan(id) : await api.declineLoan(id)
      setOffer(o)
      if (!approve) navigate('/plan')
    } catch (e) {
      setError((e as ApiError).message)
    } finally {
      setBusy(false)
    }
  }

  const b = offer?.basis
  const terms = offer?.terms ?? {}
  const festival = b?.festival ? (b.festival_names?.[lang] ?? b.festival_names?.en ?? b.festival) : null

  return (
    <Screen className="loan" testId="loan">
      <div className="detail-head">
        <IconButton label={t('back')} onClick={back}>
          <ChevronBackIcon />
        </IconButton>
        <h1 className="screen-title screen-title--detail">{t('loanOffer')}</h1>
      </div>
      {error && (
        <Banner tone="error" role="alert">
          {error}
        </Banner>
      )}
      {offer && b && (
        <>
          <section className="loan-card">
            <span className="label-caps">{fmt(t('loanFrom'), { PARTNER: terms.partner ?? t('lendingPartnerPh') })}</span>
            <span className="loan-card__amt" data-testid="loan-amount">
              {rupees(offer.amount_paise)}
            </span>
            <p className="loan-card__for">{festival ? fmt(t('loanForFestival'), { FESTIVAL: festival }) : t('loanForStock')}</p>
            <div className="loan-terms">
              <div className="loan-terms__row">
                <span>{t('interest')}</span>
                <b>{terms.rate_text ?? t('partnerRatePh')}</b>
              </div>
              <div className="loan-terms__row">
                <span>{t('repayOver')}</span>
                <b>{terms.days != null ? fmt(t('nDays'), { N: terms.days }) : t('daysPh')}</b>
              </div>
              <div className="loan-terms__row">
                <span>{t('takenEachDay')}</span>
                <b>{terms.daily_paise != null ? fmt(t('aDay'), { AMOUNT: rupees(terms.daily_paise) }) : t('dailyPh')}</b>
              </div>
            </div>
          </section>

          <section className="sources" aria-labelledby="why-h">
            <h2 id="why-h" className="label-caps">
              {t('whyThisAmount')}
            </h2>
            <div className="sources__card" data-testid="loan-basis">
              <div className="basis-row">
                <span>{festival ? fmt(t('festivalOrder'), { FESTIVAL: festival, X: `${b.multiplier_bp / 10000}×` }) : t('stockOrder')}</span>
                <span>{rupees(b.total_paise)}</span>
              </div>
              <div className="basis-row">
                <span>{t('yourCashInHand')}</span>
                <span>- {rupees(b.cash_on_hand_paise)}</span>
              </div>
              <div className="basis-row basis-row--total">
                <span>{t('shortfall')}</span>
                <span>{rupees(b.shortfall_paise)}</span>
              </div>
            </div>
          </section>

          <div className="loan__foot">
            {offer.status === 'offered' ? (
              <>
                <p className="loan__note">{t('partnerDecides')}</p>
                <div className="loan__actions">
                  <button type="button" className="btn2 btn2--secondary loan__btn" onClick={() => decide(false)} disabled={busy}>
                    {t('notNow')}
                  </button>
                  <button type="button" className="btn2 btn2--primary loan__btn loan__btn--go" onClick={() => decide(true)} disabled={busy} data-testid="approve-loan">
                    {t('approveApply')}
                  </button>
                </div>
              </>
            ) : (
              <Banner tone="info" role="status">
                <span data-testid="loan-status">{offer.status === 'declined' ? t('loanDeclined') : t('loanSent')}</span>
              </Banner>
            )}
          </div>
        </>
      )}
    </Screen>
  )
}
