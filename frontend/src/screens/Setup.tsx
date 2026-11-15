// 1.2 Shop setup. Design: design/screens/Setup.dc.html.
// Onboarding step 2, and where Settings sends the owner to change the shop name, UPI ID or closing time.

import { useState } from 'react'
import { ApiError, api } from '../api'
import { useRouter } from '../router'
import { istTime } from '../time'
import { Banner, IconButton, Screen } from '../ui'
import { ChevronBackIcon, MicIcon } from '../ui/icons'
import { useSession } from '../useSession'

const CLOSING = ['21:00', '21:30', '22:00']

/** '21:30' -> '9:30 pm' (IST wall-clock, no dates involved). */
export function closingLabel(hhmm: string, lang: Parameters<typeof istTime>[1]): string {
  const [h, m] = hhmm.split(':').map(Number)
  return istTime(Date.UTC(2000, 0, 1, h, m) - (5 * 60 + 30) * 60 * 1000, lang)
}

export default function Setup() {
  const { t, lang, merchant, setMerchant } = useSession()
  const { navigate } = useRouter()
  const onboarding = !!merchant?.needs.includes('shop')
  const [name, setName] = useState(merchant?.name ?? '')
  const [upi, setUpi] = useState(merchant?.upi_vpa ?? '')
  const [closing, setClosing] = useState(merchant?.closing_time ?? '21:00')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const options = CLOSING.includes(closing) ? CLOSING : [...CLOSING, closing].sort()

  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      const me = await api.updateMe({ name: name.trim(), upi_vpa: upi.trim(), closing_time: closing })
      setMerchant(me)
      navigate(onboarding ? '/' : '/settings', { replace: onboarding })
    } catch (e) {
      setError((e as ApiError).message)
      setBusy(false)
    }
  }

  const [before, after] = t('micPrivacy').split('{BILL}')
  return (
    <Screen className="setup" testId="setup">
      <div className="screen-head">
        <IconButton label={t('back')} onClick={() => navigate(onboarding ? '/welcome' : '/settings')}>
          <ChevronBackIcon />
        </IconButton>
        {onboarding && <span className="setup__step">{t('stepOf')}</span>}
      </div>
      <div className="setup__intro">
        <h1 className="setup__title">{t('yourShop')}</h1>
        <p className="setup__lede">{t('setupIntro')}</p>
      </div>
      <form
        className="setup__fields"
        onSubmit={(e) => {
          e.preventDefault()
          void save()
        }}
      >
        <label className="setup__field">
          <span className="setup__label">{t('shopName')}</span>
          <input className="setup__input" value={name} autoComplete="organization" onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="setup__field">
          <span className="setup__label">{t('upiForPayments')}</span>
          <input
            className="setup__input"
            value={upi}
            placeholder="shopname@paytm"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            onChange={(e) => setUpi(e.target.value)}
          />
          <span className="setup__hint">{t('upiHint')}</span>
        </label>
        <label className="setup__field">
          <span className="setup__label">{t('whenClose')}</span>
          <select className="setup__input" value={closing} onChange={(e) => setClosing(e.target.value)}>
            {options.map((c) => (
              <option key={c} value={c}>
                {closingLabel(c, lang)}
              </option>
            ))}
          </select>
          <span className="setup__hint">{t('closeHint')}</span>
        </label>
        <button type="submit" hidden />
      </form>
      <div className="setup__privacy">
        <MicIcon size={28} className="setup__mic" />
        <p>
          {before}
          <b>{t('billWord')}</b>
          {after}
        </p>
      </div>
      {error && (
        <Banner tone="error" role="alert">
          {error}
        </Banner>
      )}
      <button type="button" className="setup__go" disabled={busy || !name.trim() || !upi.trim()} onClick={save}>
        {onboarding ? t('startBilling') : t('save')}
      </button>
    </Screen>
  )
}
