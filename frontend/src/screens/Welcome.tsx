// 1.1 Welcome and language. Design: design/screens/Main.dc.html.
// First run starts here when the shop has no language; Settings > Language comes back here too.

import { useState } from 'react'
import { ApiError, api } from '../api'
import { useRouter } from '../router'
import { LANGUAGES, t as translate, type Lang } from '../strings'
import { Banner, Screen, cx } from '../ui'
import { LogoMark } from '../ui/icons'
import { useSession } from '../useSession'

const ENGLISH_NAME: Record<Lang, string> = { en: 'English', ta: 'Tamil', hi: 'Hindi' }

export default function Welcome() {
  const { merchant, setMerchant } = useSession()
  const { navigate } = useRouter()
  const [lang, setLang] = useState<Lang>(merchant?.language ?? 'en')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // the screen reads in the language being picked, so the owner sees what they're choosing
  const t = (k: Parameters<typeof translate>[1]) => translate(lang, k)
  const onboarding = !!merchant?.needs.includes('language')

  const go = async () => {
    setBusy(true)
    setError(null)
    try {
      const me = await api.updateMe({ language: lang })
      setMerchant(me)
      navigate(me.needs.includes('shop') ? '/setup' : onboarding ? '/' : '/settings', { replace: onboarding })
    } catch (e) {
      setError((e as ApiError).message)
      setBusy(false)
    }
  }

  return (
    <Screen tone="dark" className="welcome" testId="welcome">
      <div className="welcome__brand">
        <LogoMark />
        <span className="welcome__name">Laabam</span>
      </div>
      <div className="welcome__intro">
        <h1 className="welcome__title" lang={lang}>
          {t('welcomeTitle')}
        </h1>
        <p className="welcome__body">{t('welcomeBody')}</p>
      </div>
      <fieldset className="langs">
        <legend className="langs__legend">{t('chooseLanguage')}</legend>
        {(Object.keys(LANGUAGES) as Lang[]).map((l) => (
          <label key={l} className={cx('langs__opt', l === lang && 'langs__opt--on')}>
            <span className="langs__names">
              <span className="langs__native" lang={l}>
                {LANGUAGES[l]}
              </span>
              <span className="langs__english">{ENGLISH_NAME[l]}</span>
            </span>
            <input type="radio" name="lang" value={l} checked={l === lang} onChange={() => setLang(l)} />
          </label>
        ))}
        <p className="langs__note">{t('changeLater')}</p>
      </fieldset>
      {error && (
        <Banner tone="error" role="alert">
          {error}
        </Banner>
      )}
      <button type="button" className="welcome__go" disabled={busy} onClick={go}>
        {t('continueBtn')}
      </button>
    </Screen>
  )
}
