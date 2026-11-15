// Times shown to the owner are IST with a fixed +05:30 offset (no time-zone database needed).

import { SPEECH_LOCALE, type Lang } from './strings'

const IST_MS = (5 * 60 + 30) * 60 * 1000

/** The IST wall clock as a Date whose UTC fields read as IST. */
function ist(at: string | number | Date): Date {
  return new Date(new Date(at).getTime() + IST_MS)
}

/** '6:42 pm' */
export function istTime(at: string | number | Date, lang: Lang = 'en'): string {
  return new Intl.DateTimeFormat(SPEECH_LOCALE[lang], { hour: 'numeric', minute: '2-digit', hour12: true, timeZone: 'UTC' })
    .format(ist(at))
    .replace(/\s?AM$/i, ' am')
    .replace(/\s?PM$/i, ' pm')
}

/** 'Thursday, 13 Nov' */
export function istDay(at: string | number | Date, lang: Lang = 'en'): string {
  return new Intl.DateTimeFormat(SPEECH_LOCALE[lang], { weekday: 'long', day: 'numeric', month: 'short', timeZone: 'UTC' }).format(ist(at))
}
