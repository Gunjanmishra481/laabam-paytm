// 5.1 Festival plan. Design: design/screens/Plan.dc.html.
// The usual week x a multiplier the owner picks (1.5x, 2x by default, 2.5x: labelled a guess), each line at the
// cheapest current distributor price; cash in hand (the owner's figure, edited here), the shortfall, and a loan
// offer for it. All figures from GET /v1/plans/festival (code). /plan/reorder: this week's reorder list.

import { useEffect, useState } from 'react'
import { ApiError, api, type FestivalPlan, type PlanLine } from '../api'
import { parsePaise, paiseToInput, rupees } from '../money'
import { Link, useRouter } from '../router'
import { fmt, type StringKey } from '../strings'
import { Banner, BottomSheet, Button, Field, IconButton, Screen, cx } from '../ui'
import { ChevronBackIcon } from '../ui/icons'
import { useSession } from '../useSession'
import { shortName } from './alertText'
import { perUnit, shortDay } from './ItemDetail'

type T = (k: StringKey) => string
const SHOWN = 5

function lineNote(t: T, l: PlanLine): string {
  return l.cheaper ? `${l.distributor}, ${rupees(l.cost_paise)} ${perUnit(t, l.unit_label)}` : shortName(l.distributor)
}

function mult(bp: number): string {
  return `${bp / 10000}×`
}

export function PlanLines({ lines, total, more }: { lines: PlanLine[]; total: number; more?: (l: PlanLine) => string }) {
  const { t } = useSession()
  const [all, setAll] = useState(false)
  const shown = all || lines.length <= SHOWN + 1 ? lines : lines.slice(0, SHOWN)
  const rest = lines.slice(shown.length)
  return (
    <div className="plan-lines" data-testid="plan-lines">
      {shown.map((l) => (
        <div key={l.item_id} className="plan-line" data-testid="plan-line">
          <span className="plan-line__main">
            <span className="plan-line__name">{l.name}</span>
            <span className="plan-line__note">{more ? more(l) : lineNote(t, l)}</span>
          </span>
          <span className="plan-line__amt">{rupees(l.amount_paise)}</span>
        </div>
      ))}
      {rest.length > 0 && (
        <button type="button" className="plan-line plan-line--more" onClick={() => setAll(true)}>
          <span className="plan-line__main">
            <span className="plan-line__name">{fmt(t('moreItemsPlain'), { N: rest.length })}</span>
            <span className="plan-line__note">{t('tapToSeeAll')}</span>
          </span>
          <span className="plan-line__amt">{rupees(rest.reduce((a, l) => a + l.amount_paise, 0))}</span>
        </button>
      )}
      <div className="plan-total">
        <span className="plan-total__label">{t('orderTotal')}</span>
        <span className="plan-total__amt" data-testid="plan-total">
          {rupees(total)}
        </span>
      </div>
    </div>
  )
}

export default function Plan() {
  const { t, lang, merchant, setMerchant } = useSession()
  const { navigate } = useRouter()
  const [bp, setBp] = useState(20000)
  const [plan, setPlan] = useState<FestivalPlan | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [editCash, setEditCash] = useState(false)
  const [cashText, setCashText] = useState('')

  const load = (m: number) =>
    api.festivalPlan(m).then(
      (p) => setPlan(p),
      (e: ApiError) => setError(e.message),
    )
  useEffect(() => {
    load(bp)
  }, [bp])

  const saveCash = async () => {
    const paise = parsePaise(cashText)
    if (paise == null) return setError(t('cashInvalid'))
    setBusy(true)
    try {
      setMerchant(await api.updateMe({ cash_on_hand_paise: paise }))
      setEditCash(false)
      await load(bp)
    } catch (e) {
      setError((e as ApiError).message)
    } finally {
      setBusy(false)
    }
  }

  const seeLoan = async () => {
    setBusy(true)
    setError(null)
    try {
      const saved = await api.saveRestockPlan(bp)
      const offer = await api.loanOffer(saved.id)
      navigate(`/plan/loan/${offer.id}`)
    } catch (e) {
      setError((e as ApiError).message)
      setBusy(false)
    }
  }

  const name = plan?.festival_names ? (plan.festival_names[lang] ?? plan.festival_names.en) : t('festival')
  const days = plan?.days_to_go
  const when = days == null ? '' : days === 0 ? t('today_word') : days === 1 ? t('tomorrow_word') : fmt(t('inDays'), { N: days })
  const short = plan?.shortfall_paise

  return (
    <Screen tab="plan" className="plan" testId="plan">
      <div className="plan__head">
        <h1 className="screen-title">{fmt(t('festivalStock'), { FESTIVAL: name })}</h1>
        {plan?.festival_date && (
          <span className="plan__when" title={shortDay(plan.festival_date, lang)}>
            {when}
          </span>
        )}
      </div>

      <section className="mult-card">
        <span className="mult-card__q">
          {t('howBusy')} <span className="mult-card__guess">{t('aGuess')}</span>
        </span>
        <div className="mult-card__opts" role="radiogroup" aria-label={t('multLabel')}>
          {(plan?.multipliers_bp ?? [15000, 20000, 25000]).map((m) => (
            <button key={m} type="button" role="radio" aria-checked={m === bp} className={cx('mult', m === bp && 'mult--on')} onClick={() => setBp(m)}>
              {fmt(t('xUsual'), { X: mult(m) })}
            </button>
          ))}
        </div>
      </section>

      {error && (
        <Banner tone="error" role="alert">
          {error}
        </Banner>
      )}

      {plan && plan.lines.length === 0 && <p className="note">{t('planEmpty')}</p>}
      {plan && plan.lines.length > 0 && <PlanLines key={bp} lines={plan.lines} total={plan.total_paise} />}

      {plan && plan.lines.length > 0 && (
        <section className="cash-card" data-testid="cash-card">
          <div className="cash-card__row">
            <span>
              {t('cashInHand')}{' '}
              <button
                type="button"
                className="cash-card__edit"
                onClick={() => {
                  setCashText(plan.cash_on_hand_paise != null ? paiseToInput(plan.cash_on_hand_paise) : '')
                  setEditCash(true)
                }}
              >
                {plan.cash_on_hand_paise == null ? t('add') : t('edit')}
              </button>
            </span>
            <span className="cash-card__cash">{plan.cash_on_hand_paise == null ? '—' : rupees(plan.cash_on_hand_paise)}</span>
          </div>
          {short == null ? (
            <p className="cash-card__note">{t('enterCashFirst')}</p>
          ) : short > 0 ? (
            <>
              <div className="cash-card__row cash-card__row--short">
                <span>{t('shortBy')}</span>
                <span className="cash-card__short" data-testid="shortfall">
                  {rupees(short)}
                </span>
              </div>
              <button type="button" className="cash-card__loan" onClick={seeLoan} disabled={busy} data-testid="see-loan">
                {fmt(t('seeLoanFor'), { AMOUNT: rupees(short) })}
              </button>
            </>
          ) : (
            <p className="cash-card__note">{t('cashCovers')}</p>
          )}
        </section>
      )}

      <BottomSheet open={editCash} onClose={() => setEditCash(false)} label={t('cashInHand')}>
        <h2 className="sheet__title">{t('cashInHand')}</h2>
        <p className="sheet__sub">{t('cashInHandHint')}</p>
        <Field label={t('amountRupees')}>
          {(id) => <input id={id} className="input2 input2--big" inputMode="numeric" value={cashText} onChange={(e) => setCashText(e.target.value)} autoFocus />}
        </Field>
        <Button onClick={saveCash} disabled={busy}>
          {t('save')}
        </Button>
      </BottomSheet>
      {merchant?.sample_data && <span className="sr-only">{t('sampleData')}</span>}
    </Screen>
  )
}

export function Reorder() {
  const { t, lang } = useSession()
  const { back } = useRouter()
  const [plan, setPlan] = useState<{ lines: PlanLine[]; total_paise: number } | null>(null)
  const [note, setNote] = useState<string | null>(null)
  useEffect(() => {
    api.reorderPlan().then(setPlan, (e: ApiError) => setNote(e.message))
  }, [])
  const share = async () => {
    if (!plan) return
    const text = plan.lines.map((l) => `${l.name} × ${l.qty}`).join('\n')
    try {
      if (navigator.share) await navigator.share({ text })
      else {
        await navigator.clipboard.writeText(text)
        setNote(t('draftCopied'))
      }
    } catch {
      /* the owner closed the share sheet */
    }
  }
  return (
    <Screen className="plan" testId="reorder">
      <div className="detail-head">
        <IconButton label={t('back')} onClick={back}>
          <ChevronBackIcon />
        </IconButton>
        <h1 className="screen-title screen-title--item">{t('reorderTitle')}</h1>
      </div>
      {note && <p className="note" role="status">{note}</p>}
      {plan && plan.lines.length === 0 && <p className="note">{t('reorderNone')}</p>}
      {plan && plan.lines.length > 0 && (
        <>
          <PlanLines
            lines={plan.lines}
            total={plan.total_paise}
            more={(l) => `${l.qty} · ${shortName(l.distributor)} · ${fmt(t('runsOutBy'), { DAY: shortDay(l.out_day ?? '', lang) })}`}
          />
          <p className="note">{t('reorderNote')}</p>
          <Button variant="secondary" onClick={share}>
            {t('shareList')}
          </Button>
        </>
      )}
      <Link to="/plan" className="note">
        {t('festivalPlanLink')}
      </Link>
    </Screen>
  )
}
