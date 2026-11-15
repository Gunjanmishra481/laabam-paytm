// The privacy notice (beta). Public: readable before signing up. Plain words; the version is the one sign-up
// records consent to (PRIVACY_VERSION in backend/laabam/routes_auth.py).

import { useState } from 'react'
import { useRouter } from '../router'
import { LANGUAGES, t as translate, type Lang, type StringKey } from '../strings'
import { IconButton, Screen } from '../ui'
import { ChevronBackIcon } from '../ui/icons'
import { useSession } from '../useSession'

const SECTIONS: [StringKey, StringKey[]][] = [
  ['privacyWhoTitle', ['privacyWho']],
  ['privacyWhatTitle', ['privacyWhat1', 'privacyWhat2', 'privacyWhat3', 'privacyWhat4']],
  ['privacyNotTitle', ['privacyNot1', 'privacyNot2', 'privacyNot3']],
  ['privacyWhyTitle', ['privacyWhy']],
  ['privacyWhereTitle', ['privacyWhere']],
  ['privacyKeepTitle', ['privacyKeep']],
  ['privacyRightsTitle', ['privacyRights1', 'privacyRights2', 'privacyRights3']],
  ['privacyBetaTitle', ['privacyBeta']],
]

export default function Privacy() {
  const { merchant, config } = useSession()
  const { navigate } = useRouter()
  const [lang, setLang] = useState<Lang>(merchant?.language ?? 'en')
  const t = (k: StringKey) => translate(lang, k)
  return (
    <Screen className="privacy" testId="privacy">
      <div className="settings__head">
        <IconButton label={t('back')} onClick={() => (window.history.length > 1 ? window.history.back() : navigate('/'))}>
          <ChevronBackIcon />
        </IconButton>
        <h1 className="screen-title screen-title--detail">{t('privacyNotice')}</h1>
      </div>
      <div className="signin__langs" role="radiogroup" aria-label={t('language')}>
        {(Object.keys(LANGUAGES) as Lang[]).map((l) => (
          <button key={l} type="button" role="radio" aria-checked={l === lang} className="small-btn" onClick={() => setLang(l)}>
            {LANGUAGES[l]}
          </button>
        ))}
      </div>
      {SECTIONS.map(([title, lines]) => (
        <section key={title} className="settings__card privacy__section">
          <h2 className="privacy__h">{t(title)}</h2>
          {lines.length > 1 ? (
            <ul className="settings__list">
              {lines.map((k) => (
                <li key={k}>{t(k)}</li>
              ))}
            </ul>
          ) : (
            <p className="privacy__p">{t(lines[0])}</p>
          )}
        </section>
      ))}
      <p className="settings__note">
        {t('privacyVersion')} {config?.privacy_version ?? ''}
      </p>
    </Screen>
  )
}
