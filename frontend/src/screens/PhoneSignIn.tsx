// Beta sign-up and sign-in by phone. Firebase Phone Auth sends the code and proves the number (invisible
// reCAPTCHA); the API checks Firebase's ID token and gives us our own session. Firebase's session isn't kept.
// A new number needs the box ticked: the privacy notice says what we store, why, and how to delete it.

import { useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../api'
import { Link } from '../router'
import { LANGUAGES, t as translate, type Lang, type StringKey } from '../strings'
import { Banner, Button, Screen } from '../ui'
import { useSession } from '../useSession'

type Firebase = {
  auth: import('@firebase/auth').Auth
  mod: typeof import('@firebase/auth')
}

async function loadFirebase(config: NonNullable<import('../api').PublicConfig['firebase']>): Promise<Firebase> {
  const [{ initializeApp, getApps }, mod] = await Promise.all([import('@firebase/app'), import('@firebase/auth')])
  const app = getApps()[0] ?? initializeApp(config)
  const auth = mod.getAuth(app)
  auth.useDeviceLanguage()
  return { auth, mod }
}

/** "98765 43210" or "+91 98765 43210" -> "+919876543210"; null if it isn't an Indian mobile number. */
export function indianMobile(raw: string): string | null {
  const d = raw.replace(/\D/g, '')
  const ten = d.length === 12 && d.startsWith('91') ? d.slice(2) : d
  return /^[6-9]\d{9}$/.test(ten) ? `+91${ten}` : null
}

export default function PhoneSignIn() {
  const { config, signedIn } = useSession()
  const [lang, setLang] = useState<Lang>('en')
  const t = (k: StringKey) => translate(lang, k)
  const [phone, setPhone] = useState('')
  const [agree, setAgree] = useState(false)
  const [code, setCode] = useState('')
  const [step, setStep] = useState<'phone' | 'code'>('phone')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fb = useRef<Firebase | null>(null)
  const verifier = useRef<import('@firebase/auth').RecaptchaVerifier | null>(null)
  const confirmation = useRef<import('@firebase/auth').ConfirmationResult | null>(null)

  useEffect(() => () => verifier.current?.clear(), [])

  if (!config?.firebase) return null
  const e164 = indianMobile(phone)

  const sendCode = async () => {
    if (!e164 || !agree) return
    setBusy(true)
    setError(null)
    try {
      await api.phoneStart(e164) // our limits first: 5 codes an hour per number, 10 per device address
      fb.current ??= await loadFirebase(config.firebase!)
      const { auth, mod } = fb.current
      verifier.current ??= new mod.RecaptchaVerifier(auth, 'send-code', { size: 'invisible' })
      confirmation.current = await mod.signInWithPhoneNumber(auth, e164, verifier.current)
      setStep('code')
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t('codeNotSent'))
      verifier.current?.clear()
      verifier.current = null
    } finally {
      setBusy(false)
    }
  }

  const checkCode = async () => {
    if (!confirmation.current || !/^\d{6}$/.test(code)) return
    setBusy(true)
    setError(null)
    try {
      const cred = await confirmation.current.confirm(code)
      const idToken = await cred.user.getIdToken()
      const s = await api.phoneSignIn(idToken, agree, config.privacy_version)
      await fb.current?.auth.signOut() // Firebase only proved the number; our session takes over
      signedIn(s)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t('codeWrong'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Screen className="signin" testId="phone-signin">
      <div className="signin__langs" role="radiogroup" aria-label={t('language')}>
        {(Object.keys(LANGUAGES) as Lang[]).map((l) => (
          <button key={l} type="button" role="radio" aria-checked={l === lang} className="small-btn" onClick={() => setLang(l)}>
            {LANGUAGES[l]}
          </button>
        ))}
      </div>
      <h1 className="setup__title">{t('signInTitle')}</h1>
      <p className="setup__lede">{t('signInLead')}</p>

      {step === 'phone' ? (
        <>
          <label className="setup__field">
            <span className="setup__label">{t('phoneNumber')}</span>
            <span className="signin__phone">
              <span className="signin__cc">+91</span>
              <input
                className="setup__input num"
                inputMode="tel"
                autoComplete="tel-national"
                value={phone}
                placeholder="98765 43210"
                onChange={(e) => setPhone(e.target.value)}
                aria-label={t('phoneNumber')}
              />
            </span>
          </label>
          <label className="signin__consent">
            <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} data-testid="consent" />
            <span>
              {t('consentAgree')}{' '}
              <Link to="/privacy" className="inline-link">
                {t('privacyNotice')}
              </Link>
            </span>
          </label>
          <Button id="send-code" disabled={busy || !e164 || !agree} onClick={sendCode}>
            {t('sendCode')}
          </Button>
        </>
      ) : (
        <>
          <label className="setup__field">
            <span className="setup__label">{t('enterCode')}</span>
            <input
              className="setup__input num"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
              aria-label={t('enterCode')}
            />
          </label>
          <Button disabled={busy || code.length !== 6} onClick={checkCode}>
            {t('verifyCode')}
          </Button>
          <button type="button" className="link" onClick={() => (setStep('phone'), setCode(''))}>
            {t('changeNumber')}
          </button>
        </>
      )}
      {error && (
        <Banner tone="error" role="alert">
          {error}
        </Banner>
      )}
      <p className="settings__note">{t('signInNoPassword')}</p>
    </Screen>
  )
}
