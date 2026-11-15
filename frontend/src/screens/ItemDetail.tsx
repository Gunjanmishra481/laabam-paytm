// 4.2 Item and its sources. Design: design/screens/ItemDetail.dc.html.
// Profit a unit now and before the last cost change, what you sell at, what it costs, the margin, how much sold,
// what the cost rise took this week. Every number links to where it comes from: the distributor bill lines (Open
// -> Check a read bill) and the voice bills (List). A cheaper distributor, if one billed less recently.

import { useEffect, useState } from 'react'
import { ApiError, api, type CostRef, type ItemBill, type ItemReport } from '../api'
import { milliToText, rupees } from '../money'
import { Link, useRouter } from '../router'
import { SPEECH_LOCALE, fmt, type Lang, type StringKey } from '../strings'
import { istTime } from '../time'
import { Banner, BottomSheet, IconButton, Rich, Screen, cx } from '../ui'
import { ChevronBackIcon } from '../ui/icons'
import { useSession } from '../useSession'
import { pct } from './Profit'

type T = (k: StringKey) => string

/** '13 Nov' from an ISO date, in the owner's language. */
export function shortDay(iso: string, lang: Lang): string {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number)
  return new Intl.DateTimeFormat(SPEECH_LOCALE[lang], { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(Date.UTC(y, m - 1, d))
}

/** 'a litre' / 'a kg' / 'a pack' */
export function perUnit(t: T, unit: ItemReport['unit_label']): string {
  return t(`per_${unit}` as StringKey)
}

/** '46 litres', '12.5 kg', '1 pack' */
export function amountOf(t: T, unit: ItemReport['unit_label'], qtyMilli: number): string {
  const n = milliToText(qtyMilli)
  return fmt(t(qtyMilli === 1000 ? (`one_${unit}` as StringKey) : (`many_${unit}` as StringKey)), { N: n })
}

/** 'Bill 4471, line 4: 12 × ₹156' */
export function lineText(t: T, c: CostRef): string {
  const qty = c.qty_milli == null ? '?' : milliToText(c.qty_milli)
  return fmt(t('billLineRef'), { NO: c.bill_no || '—', LINE: c.position, QTY: qty, RATE: c.rate_paise == null ? '?' : rupees(c.rate_paise) })
}

export default function ItemDetail({ id }: { id: string }) {
  const { t, lang } = useSession()
  const { back } = useRouter()
  const [rep, setRep] = useState<ItemReport | null>(null)
  const [bills, setBills] = useState<ItemBill[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.itemProfit(id, 'week').then(setRep, (e: ApiError) => setError(e.message))
  }, [id])

  const head = (
    <div className="detail-head">
      <IconButton label={t('back')} onClick={back}>
        <ChevronBackIcon />
      </IconButton>
      <h1 className="screen-title screen-title--item">{rep?.item.name ?? ''}</h1>
    </div>
  )
  if (!rep)
    return (
      <Screen className="itemdetail">
        {head}
        {error ? (
          <Banner tone="error" role="alert">
            {error}
          </Banner>
        ) : (
          <p className="note">{t('loading')}</p>
        )}
      </Screen>
    )

  const unit = rep.unit_label
  const now = rep.profit_per_unit_paise
  const before = rep.prev_profit_per_unit_paise
  const down = now != null && before != null && now < before
  const said = rep.said_prices_paise
  const sold = rep.sold

  const openBills = async () => {
    setBills(await api.itemBills(id, 'week'))
  }

  return (
    <Screen className="itemdetail" testId="item-detail">
      {head}

      <section className="item-card">
        {now == null ? (
          <p className="item-card__none">{rep.cost ? t('itemNoPrice') : t('itemNoCost')}</p>
        ) : (
          <div className="item-card__hero">
            <span className={cx('item-card__profit', down && 'item-card__profit--down')} data-testid="profit-per-unit">
              {rupees(now)}
            </span>
            <span className="item-card__per">
              {down ? (
                <Rich text={fmt(t('profitPerDownFrom'), { PER: perUnit(t, unit) })} vars={{ BEFORE: <b>{rupees(before)}</b> }} />
              ) : (
                fmt(t('profitPer'), { PER: perUnit(t, unit) })
              )}
            </span>
          </div>
        )}
        <div className="tiles">
          <span className="tile">
            <span className="tile__label">{t('youSellAt')}</span>
            <span className="tile__value">{rep.price ? rupees(rep.price.paise) : '—'}</span>
          </span>
          <span className="tile">
            <span className="tile__label">{t('itCosts')}</span>
            <span className="tile__value">{rep.cost?.usual_cost_paise != null ? rupees(rep.cost.usual_cost_paise) : '—'}</span>
          </span>
          <span className="tile">
            <span className="tile__label">{t('margin')}</span>
            <span className="tile__value">{rep.margin_bp == null ? '—' : pct(rep.margin_bp)}</span>
          </span>
        </div>
        {sold.qty_milli > 0 && (
          <p className="item-card__note" data-testid="sold-note">
            {fmt(t(sold.estimated ? 'soldAboutThisWeek' : 'soldThisWeek'), { AMOUNT: amountOf(t, unit, sold.qty_milli) })}
            {rep.lost_to_rise_paise > 0 && (
              <>
                {' '}
                <Rich text={t(sold.estimated ? 'oldCostMoreAbout' : 'oldCostMore')} vars={{ LOST: <b>{rupees(rep.lost_to_rise_paise)}</b> }} />
              </>
            )}
          </p>
        )}
      </section>

      <section className="sources" aria-labelledby="sources-h" data-testid="item-sources">
        <h2 id="sources-h" className="label-caps">
          {t('whereFrom')}
        </h2>
        <div className="sources__card">
          {[rep.cost, rep.prev_cost].map(
            (c, i) =>
              c && (
                <Link key={c.line_id} to={`/bills-in/${c.purchase_id}`} className="sources__row" data-testid="source-bill">
                  <span className="sources__main">
                    <span className="sources__title">
                      {c.distributor} · {shortDay(c.day, lang)}
                    </span>
                    <span className="sources__sub">{lineText(t, c)}</span>
                  </span>
                  <span className={cx('sources__go', i > 0 && 'sources__go--green')}>{t('open')}</span>
                </Link>
              ),
          )}
          {sold.bills > 0 && (
            <button type="button" className="sources__row" onClick={openBills} data-testid="source-voice">
              <span className="sources__main">
                <span className="sources__title">
                  {sold.bills === 1 ? t('oneVoiceBillThisWeek') : fmt(t('voiceBillsThisWeek'), { N: sold.bills })}
                </span>
                {said.length > 0 && (
                  <span className="sources__sub">
                    {said.length === 1
                      ? fmt(t('saidEachTime'), { PRICE: rupees(said[0]), PER: perUnit(t, unit) })
                      : fmt(t('saidBetween'), { LOW: rupees(said[0]), HIGH: rupees(said[said.length - 1]) })}
                  </span>
                )}
              </span>
              <span className="sources__go sources__go--green">{t('list')}</span>
            </button>
          )}
        </div>
      </section>

      {rep.cheaper && rep.cost && (
        <section className="cheaper" data-testid="cheaper">
          <span className="cheaper__text">
            <Rich
              text={fmt(t('cheaperBilled'), { PRICE: rupees(rep.cheaper.usual_cost_paise ?? 0), PER: perUnit(t, unit), DAY: shortDay(rep.cheaper.day, lang) })}
              vars={{ WHO: <b>{rep.cheaper.distributor}</b> }}
            />
          </span>
          <div className="cheaper__actions">
            <Link to={`/ask/draft/${rep.item.id}`} className="btn2 btn2--primary btn2--md">
              {t('draftMessage')}
            </Link>
            <Link to="/plan" className="btn2 btn2--secondary btn2--md">
              {fmt(t('buyFrom'), { WHO: rep.cheaper.distributor.split(' ')[0] })}
            </Link>
          </div>
          <span className="cheaper__note">{t('youDecideSend')}</span>
        </section>
      )}

      <BottomSheet open={bills != null} onClose={() => setBills(null)} label={fmt(t('voiceBillsThisWeek'), { N: bills?.length ?? 0 })}>
        <ul className="voicebills">
          {(bills ?? []).map((b) => (
            <li key={b.line_id}>
              <Link to={`/bill/${b.bill_id}/view`} className="voicebills__row">
                <span className="voicebills__when">{`${shortDay(b.paid_at, lang)} · ${istTime(b.paid_at, lang)}`}</span>
                <span className="voicebills__heard">“{b.heard_text}”</span>
                <span className="voicebills__amt">{rupees(b.amount_paise)}</span>
              </Link>
            </li>
          ))}
        </ul>
      </BottomSheet>
    </Screen>
  )
}
