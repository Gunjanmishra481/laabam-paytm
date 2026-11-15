// The words for an alert, filled from its figures (computed in code by backend/laabam/alerts.py) through the
// strings table. No model writes these sentences.

import type { Alert } from '../api'
import { milliToText, rupees } from '../money'
import { SPEECH_LOCALE, fmt, type Lang, type StringKey } from '../strings'
import { amountOf, perUnit, shortDay } from './ItemDetail'
import { pct } from './Profit'

type T = (k: StringKey) => string

export type AlertWords = {
  label: string // PRICE RISE
  tone: 'rise' | 'out' | 'slow' | 'margin'
  title: string
  detail: string
  action: string
  href: string
}

/** 'today', 'tomorrow', or the weekday ('Thursday') for a day within the week. */
function dayWord(t: T, iso: string, lang: Lang): string {
  const today = new Date(Date.now() + 330 * 60_000).toISOString().slice(0, 10)
  const days = Math.round((Date.parse(`${iso}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`)) / 86_400_000)
  if (days <= 0) return t('today_word')
  if (days === 1) return t('tomorrow_word')
  return new Intl.DateTimeFormat(SPEECH_LOCALE[lang], { weekday: 'long', timeZone: 'UTC' }).format(Date.parse(`${iso}T00:00:00Z`))
}

/** 'Sri Balaji Agencies' -> 'Sri Balaji': the name owners use (as drawn; 'Kumar Traders' stays). */
export function shortName(distributor: string): string {
  return distributor.replace(/\s+(agencies|agency)$/i, '')
}

export function alertWords(a: Alert, t: T, lang: Lang): AlertWords {
  const f = a.figures
  const item = a.item_name ?? ''
  const per = perUnit(t, f.unit_label)
  const itemHref = a.item_id ? `/items/${a.item_id}` : '/profit'
  switch (a.kind) {
    case 'price_rise': {
      const fell =
        f.profit_per_unit_paise != null && f.prev_profit_per_unit_paise != null
          ? ' ' + fmt(t('alertRiseFell'), { PER: per, BEFORE: rupees(f.prev_profit_per_unit_paise), NOW: rupees(f.profit_per_unit_paise) })
          : ''
      return {
        label: t('alertKind_price_rise'),
        tone: 'rise',
        title: fmt(t('alertRiseTitle'), { ITEM: item, RISE: rupees(f.rise_paise ?? 0), PER: per }),
        detail:
          fmt(t('alertRiseDetail'), {
            WHO: shortName(f.new?.distributor ?? ''),
            DAY: f.new ? shortDay(f.new.day, lang) : '',
            NEW: rupees(f.new?.usual_cost_paise ?? 0),
            OLD: rupees(f.old?.usual_cost_paise ?? 0),
          }) + fell,
        action: t('seeDetails'),
        href: itemHref,
      }
    }
    case 'running_out':
      return {
        label: t('alertKind_running_out'),
        tone: 'out',
        title: fmt(t('alertOutTitle'), { ITEM: item, DAY: dayWord(t, f.out_day ?? '', lang) }),
        detail: fmt(t('alertOutDetail'), {
          PER_DAY: amountOf(t, f.unit_label, Math.round((f.per_day_milli ?? 0) / 1000) * 1000),
          LEFT: milliToText(Math.round((f.left_milli ?? 0) / 1000) * 1000),
        }),
        action: t('addToReorder'),
        href: '/plan/reorder',
      }
    case 'slow':
      return {
        label: t('alertKind_slow'),
        tone: 'slow',
        title: fmt(t(f.last_sale_day ? 'alertSlowTitle' : 'alertSlowNeverTitle'), { ITEM: item, N: f.days_quiet ?? 0 }),
        detail: f.value_paise ? fmt(t('alertSlowDetail'), { VALUE: rupees(f.value_paise) }) : t('alertSlowDetailNoValue'),
        action: t('seeItem'),
        href: itemHref,
      }
    case 'low_margin':
    default:
      return {
        label: t('alertKind_low_margin'),
        tone: 'margin',
        title: fmt(t('alertMarginTitle'), { ITEM: item, PCT: pct(f.margin_bp ?? 0) }),
        detail: fmt(t('alertMarginDetail'), { PRICE: rupees(f.price_paise ?? 0), COST: rupees(f.cost?.usual_cost_paise ?? 0) }),
        action: t('seeMargins'),
        href: '/profit',
      }
  }
}
