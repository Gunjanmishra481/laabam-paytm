// 4.1 Profit. Design: design/screens/Profit.dc.html.
// Today / This week / Month. Every figure comes from GET /v1/profit (code only); "about" when any line was
// estimated from a margin rate; the coverage line says how much of the period's UPI money had a voice bill.
// Bars for the week and month only. Each item opens its sources (/items/:id).

import { useEffect, useState } from 'react'
import { ApiError, api, type Period, type ProfitReport } from '../api'
import { rupees } from '../money'
import { Link } from '../router'
import { SPEECH_LOCALE, fmt, type StringKey } from '../strings'
import { Banner, Screen, SegmentedControl, cx } from '../ui'
import { MicIcon } from '../ui/icons'
import { useSession } from '../useSession'

export function pct(bp: number): string {
  return `${(bp / 100).toFixed(1)}%`
}

/** Profit and sales to the nearest rupee: they are sums of estimates, and the design shows whole rupees. */
export function wholeRupees(paise: number): string {
  return rupees(Math.round(paise / 100) * 100)
}

const SHOWN_ITEMS = 3 // items shown before "+ N more items"

const COVERAGE: Record<Period, StringKey> = { today: 'coverage_today', week: 'coverage_week', month: 'coverage_month' }

export default function Profit() {
  const { t, lang, merchant } = useSession()
  const [period, setPeriod] = useState<Period>('week')
  const [rep, setRep] = useState<ProfitReport | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [allItems, setAllItems] = useState(false)

  useEffect(() => {
    let live = true
    setError(null)
    api.profit(period).then(
      (r) => live && setRep(r),
      (e: ApiError) => live && setError(e.message),
    )
    return () => {
      live = false
    }
  }, [period])

  const shown = rep?.period === period ? rep : null
  const bars = shown?.bars ?? []
  const max = Math.max(1, ...bars.map((b) => b.profit_paise))
  const dayInitial = new Intl.DateTimeFormat(SPEECH_LOCALE[lang], { weekday: 'narrow', timeZone: 'UTC' })
  const barLabel = (b: ProfitReport['bars'][number], i: number) =>
    period === 'week' ? dayInitial.format(Date.parse(`${b.from}T00:00:00Z`)) : fmt(t('weekShort'), { N: i + 1 })
  const coverage =
    shown && shown.coverage_bp != null
      ? shown.coverage_bp >= 10000
        ? t(`${COVERAGE[period]}_all` as StringKey)
        : fmt(t(COVERAGE[period]), { PCT: Math.floor(shown.coverage_bp / 100) })
      : null
  const noCost = shown?.no_cost_items.length ?? 0
  // the top earners, and always any item whose margin just fell (the price-rise story)
  const items = shown?.items ?? []
  const top = items.slice(0, SHOWN_ITEMS)
  for (const it of items.slice(SHOWN_ITEMS)) if (it.trend === 'down') top.splice(top.length - 1, 1, it)
  const shownItems = allItems || items.length <= SHOWN_ITEMS + 1 ? items : items.filter((it) => top.includes(it))
  const hidden = items.length - shownItems.length

  return (
    <Screen tab="profit" className="profit">
      <div className="screen-head">
        <h1 className="screen-title">{t('tab_profit')}</h1>
        <Link to="/ask" className="pill-link">
          <MicIcon size={18} />
          {t('askWhy')}
        </Link>
      </div>

      <SegmentedControl<Period>
        label={t('periodLabel')}
        value={period}
        onChange={setPeriod}
        options={[
          { value: 'today', label: t('period_today') },
          { value: 'week', label: t('period_week') },
          { value: 'month', label: t('period_month') },
        ]}
      />

      {error && (
        <Banner tone="error" role="alert">
          {error}
        </Banner>
      )}

      <section className="profit-card" aria-busy={!shown} data-testid="profit-card">
        <div className="profit-card__top">
          <span className="profit-card__fig">
            <span className="profit-card__label">{shown?.estimated ? t('profitAbout') : t('profitLabel')}</span>
            <span className="profit-card__profit" data-testid="profit-total">
              {shown ? wholeRupees(shown.profit_paise) : '—'}
            </span>
          </span>
          <span className="profit-card__fig profit-card__fig--end">
            <span className="profit-card__label">{t('fromSalesOf')}</span>
            <span className="profit-card__sales">{shown ? wholeRupees(shown.sales_paise) : '—'}</span>
          </span>
        </div>
        {bars.length > 0 && (
          <div
            className="pbars"
            role="img"
            aria-label={fmt(t(period === 'week' ? 'chartByDay' : 'chartByWeek'), { MAX: wholeRupees(Math.max(0, ...bars.map((b) => b.profit_paise))) })}
          >
            {bars.map((b, i) => (
              <span key={b.from} className="pbars__col">
                <span
                  className={cx('pbars__bar', i === bars.length - 1 && 'pbars__bar--now')}
                  style={{ height: `${Math.max(2, Math.round((60 * Math.max(0, b.profit_paise)) / max))}px` }}
                />
                <span className="pbars__label">{barLabel(b, i)}</span>
              </span>
            ))}
          </div>
        )}
        {shown && (
          <span className="profit-card__note">
            {shown.sales_paise === 0 ? t('noSalesYet') : coverage}
            {merchant?.sample_data && shown.sales_paise > 0 ? ` ${t('figuresIllustrative')}` : ''}
          </span>
        )}
      </section>

      {shown && (shown.items.length > 0 || noCost > 0) && (
        <section className="byitem" aria-labelledby="byitem-h">
          <h2 id="byitem-h" className="label-caps">
            {t('byItem')}
          </h2>
          <div className="byitem__card">
            {shownItems.map((it) => (
              <Link key={it.item_id} to={`/items/${it.item_id}`} className="byitem__row" data-testid="profit-item">
                <span className="byitem__name">{it.name}</span>
                <span className={cx('byitem__margin', it.trend === 'down' && 'byitem__margin--down')}>
                  {it.margin_bp == null ? '' : pct(it.margin_bp)}
                  {it.trend === 'down' && <span aria-label={t('marginDown')}> ↓</span>}
                </span>
                <span className="byitem__profit">
                  {it.estimated && <span className="money__about">{t('about')} </span>}
                  {wholeRupees(it.profit_paise)}
                </span>
              </Link>
            ))}
            {hidden > 0 && (
              <button type="button" className="byitem__row byitem__more" onClick={() => setAllItems(true)}>
                {fmt(t('moreItems'), { N: hidden })}
              </button>
            )}
            {noCost > 0 && (
              <Link to="/bills-in" className="byitem__row byitem__row--nocost" data-testid="no-cost-link">
                <span className="byitem__name byitem__name--muted">{noCost === 1 ? t('oneItemNoCost') : fmt(t('itemsNoCost'), { N: noCost })}</span>
                <span className="byitem__add">{t('addABill')}</span>
              </Link>
            )}
          </div>
        </section>
      )}
    </Screen>
  )
}
