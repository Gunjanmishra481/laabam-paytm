// Money is integer paise everywhere; these only format and parse. No floats touch an amount.

const INR = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 })

/** 21050000 -> '₹2,10,500'; 1250 -> '₹12.50'. Whole rupees go through Intl (an integer); paise are appended. */
export function rupees(paise: number): string {
  const neg = paise < 0
  const abs = Math.abs(Math.trunc(paise))
  const whole = Math.floor(abs / 100)
  const frac = abs % 100
  return `${neg ? '-' : ''}₹${INR.format(whole)}${frac ? '.' + String(frac).padStart(2, '0') : ''}`
}

/** '12.50' -> 1250, '200' -> 20000. Returns null when it isn't an amount. */
export function parsePaise(text: string): number | null {
  const m = text.replace(/[₹,\s]/g, '').match(/^(\d{1,7})(?:\.(\d{1,2}))?$/)
  if (!m) return null
  return Number(m[1]) * 100 + Number((m[2] ?? '').padEnd(2, '0'))
}

/** '1.5' -> 1500 thousandths. */
export function parseMilli(text: string): number | null {
  const m = text.trim().match(/^(\d{1,5})(?:\.(\d{1,3}))?$/)
  if (!m) return null
  const v = Number(m[1]) * 1000 + Number((m[2] ?? '').padEnd(3, '0'))
  return v > 0 ? v : null
}

function milliToText(q: number): string {
  const whole = Math.floor(q / 1000)
  const frac = q % 1000
  return frac ? `${whole}.${String(frac).padStart(3, '0').replace(/0+$/, '')}` : String(whole)
}

/** Quantity for display: 500 milli-kg -> '500 g', 2000 -> '2 kg', 3000 pc -> '3'. */
export function qtyParts(qtyMilli: number | null, unit: string | null): { value: string; unit: string | null } | null {
  if (qtyMilli == null) return null
  if (unit === 'kg' && qtyMilli < 1000) return { value: String(qtyMilli), unit: 'g' }
  if (unit === 'L' && qtyMilli < 1000) return { value: String(qtyMilli), unit: 'ml' }
  return { value: milliToText(qtyMilli), unit }
}

export { milliToText }

/** 46500 -> '465.00' for an input box (integer maths, no floats). */
export function paiseToInput(paise: number): string {
  return `${Math.floor(paise / 100)}.${String(paise % 100).padStart(2, '0')}`
}
