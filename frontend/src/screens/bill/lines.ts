// How a bill line reads on screen. Copy comes from the strings table.

import type { Bill, Line } from '../../api'
import { qtyParts, rupees } from '../../money'
import { fmt, isStringKey, type StringKey } from '../../strings'

type T = (k: StringKey) => string

export const capitalise = (s: string) => (s ? s[0].toLocaleUpperCase() + s.slice(1) : s)

/** '2 kg', '500 g', '× 3', or '' when no quantity was said. */
export function qtyText(line: Pick<Line, 'qty_milli' | 'unit'>, t: T): string {
  const q = qtyParts(line.qty_milli, line.unit)
  if (!q) return ''
  if (!q.unit || q.unit === 'pc') return `× ${q.value}`
  const key = `unit_${q.unit}`
  return `${q.value} ${isStringKey(key) ? t(key) : q.unit}`
}

/** 'Rice 2 kg', 'Biscuit × 3', 'Sugar'. */
export function lineName(line: Line, t: T): string {
  const name = capitalise(line.item_name || '?')
  const q = qtyText(line, t)
  return q ? `${name} ${q}` : name
}

/** The small note under a line: where its price came from, or what needs fixing. */
export function lineNote(line: Line, t: T): { text: string; attention: boolean } {
  if (line.flags.length) {
    const key = `flag_${line.flags[0]}`
    const short = line.flags[0] === 'price_needed' ? t('priceNeededShort') : isStringKey(key) ? t(key) : line.flags[0]
    return { text: short, attention: true }
  }
  const amount = line.line_total_paise != null ? rupees(line.line_total_paise) : ''
  if (line.price_source === 'learned') return { text: t('noteLearned'), attention: false }
  if (line.price_source === 'edit') return { text: t('noteEdited'), attention: false }
  const heardName = line.heard_text.replace(/[\d.,₹\s]+$/u, '').trim()
  if (heardName && heardName.toLocaleLowerCase() !== (line.item_name || '').toLocaleLowerCase())
    return { text: fmt(t('noteHeardAs'), { HEARD: heardName, AMOUNT: amount }), attention: false }
  return { text: fmt(t('noteSpoken'), { AMOUNT: amount }), attention: false }
}

/** 'Rice, sugar, sunflower oil' for the QR screen and recent bills. */
export function billSummary(bill: Pick<Bill, 'lines'>): string {
  const names = bill.lines.map((l) => {
    const name = (l.item_name || '?').toLocaleLowerCase()
    const count = (!l.unit || l.unit === 'pc') && l.qty_milli != null && l.qty_milli > 1000 && l.qty_milli % 1000 === 0
    return count ? `${name} × ${l.qty_milli! / 1000}` : name
  })
  return capitalise(names.join(', '))
}

/** '465' for a whole-rupee amount, '12.50' otherwise; for an input box. */
export function amountInput(paise: number | null): string {
  if (paise == null) return ''
  return paise % 100 ? `${Math.floor(paise / 100)}.${String(paise % 100).padStart(2, '0')}` : String(paise / 100)
}
