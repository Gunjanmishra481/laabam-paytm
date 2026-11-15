// 5.3 Settings. Design: design/screens/Settings.dc.html. In the sandbox it also resets the demo.

import { useEffect, useState } from 'react'
import { ApiError, api, exportData, setToken, type DeviceSessionT } from '../api'
import { parsePaise, rupees } from '../money'
import { Link, useRouter } from '../router'
import { LANGUAGES, fmt } from '../strings'
import { Banner, IconButton, Screen, Switch } from '../ui'
import { ChevronBackIcon } from '../ui/icons'
import { useSession } from '../useSession'
import { closingLabel } from './Setup'
import { amountInput } from './bill/lines'

/** Signed-in devices: each can be signed out from here (it stops at its next request). */
function Devices() {
  const { t, lang, retry } = useSession()
  const [list, setList] = useState<DeviceSessionT[] | null>(null)
  const load = () => api.sessions().then(setList, () => setList([]))
  useEffect(() => {
    load()
  }, [])
  const signOut = async (s: DeviceSessionT) => {
    if (s.current) {
      await api.logout().catch(() => undefined)
      setToken(null)
      retry()
      return
    }
    await api.revokeSession(s.id).catch(() => undefined)
    load()
  }
  if (!list || list.length === 0) return null
  const when = (iso: string) => new Intl.DateTimeFormat(lang === 'en' ? 'en-IN' : lang, { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' }).format(new Date(iso))
  return (
    <>
      <h2 className="settings__section">{t('signedInDevices')}</h2>
      <div className="settings__card" data-testid="devices">
        {list.map((s) => (
          <div key={s.id} className="srow">
            <span className="srow__stack">
              <span>{s.current ? fmt(t('thisDevice'), { DEVICE: s.label }) : s.label}</span>
              <span className="srow__sub">{fmt(t('lastUsed'), { WHEN: when(s.last_seen_at) })}</span>
            </span>
            <button type="button" className="small-btn" onClick={() => signOut(s)}>
              {t('signOut')}
            </button>
          </div>
        ))}
      </div>
    </>
  )
}

const DELETE_GRACE_DAYS = 7 // LAABAM_DELETION_GRACE_DAYS

/** What Laabam keeps, a copy of it all, and deleting the shop (after a grace period, cancellable). */
function YourData() {
  const { t, lang, merchant, setMerchant } = useSession()
  const [asking, setAsking] = useState(false)
  const [typed, setTyped] = useState('')
  const [note, setNote] = useState<{ tone: 'error' | 'info'; text: string } | null>(null)
  const [busy, setBusy] = useState(false)
  if (!merchant) return null
  const when = (iso: string) => new Intl.DateTimeFormat(lang === 'en' ? 'en-IN' : lang, { day: 'numeric', month: 'long' }).format(new Date(iso))

  const download = async () => {
    setNote(null)
    setBusy(true)
    try {
      const blob = await exportData()
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = 'laabam-data.zip'
      a.click()
      setTimeout(() => URL.revokeObjectURL(a.href), 10_000)
      setNote({ tone: 'info', text: t('exportReady') })
    } catch (e) {
      setNote({ tone: 'error', text: (e as ApiError).message })
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    setNote(null)
    setBusy(true)
    try {
      const { deletion_due_at } = await api.deleteShop(typed)
      setMerchant({ ...merchant, deletion_due_at })
      setAsking(false)
      setTyped('')
    } catch (e) {
      const err = e as ApiError
      setNote({ tone: 'error', text: err.status === 403 ? t('deleteSignInAgain') : err.message })
    } finally {
      setBusy(false)
    }
  }

  const keep = async () => {
    setBusy(true)
    try {
      setMerchant(await api.keepShop())
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="settings__card settings__card--privacy" data-testid="privacy-notice">
        <span className="srow__sub settings__note">{t('privacyWhatWeKeep')}</span>
        <ul className="settings__list">
          <li>{t('privacyKeep1')}</li>
          <li>{t('privacyKeep2')}</li>
          <li>{t('privacyKeep3')}</li>
          <li>{t('privacyKeep4')}</li>
        </ul>
      </div>
      {merchant.deletion_due_at && (
        <Banner tone="amber" role="status" testId="deletion-due">
          {fmt(t('deletionScheduled'), { WHEN: when(merchant.deletion_due_at) })}{' '}
          <button type="button" className="small-btn" disabled={busy} onClick={keep} data-testid="keep-shop">
            {t('keepShop')}
          </button>
        </Banner>
      )}
      <div className="settings__card">
        <button type="button" className="srow" disabled={busy} onClick={download} data-testid="export-data">
          <span className="srow__stack">
            <span>{t('exportData')}</span>
            <span className="srow__sub">{t('exportDataSub')}</span>
          </span>
          <span className="srow__value">›</span>
        </button>
        {!merchant.deletion_due_at && !asking && (
          <button type="button" className="srow srow--danger" onClick={() => setAsking(true)} data-testid="delete-shop">
            <span className="srow__stack">
              <span>{t('deleteShop')}</span>
              <span className="srow__sub">{fmt(t('deleteShopSub'), { DAYS: DELETE_GRACE_DAYS })}</span>
            </span>
            <span className="srow__value">›</span>
          </button>
        )}
        {asking && (
          <div className="srow srow--stack" data-testid="delete-confirm">
            <label className="srow__stack">
              <span>{fmt(t('deleteTypeName'), { NAME: merchant.name })}</span>
              <input className="srow__input srow__input--wide" value={typed} autoComplete="off" onChange={(e) => setTyped(e.target.value)} aria-label={t('shopName')} />
            </label>
            <span className="srow__actions">
              <button type="button" className="small-btn" onClick={() => (setAsking(false), setTyped(''))}>
                {t('cancel')}
              </button>
              <button
                type="button"
                className="small-btn small-btn--danger"
                disabled={busy || typed.trim().toLowerCase() !== merchant.name.trim().toLowerCase()}
                onClick={remove}
              >
                {t('deleteConfirm')}
              </button>
            </span>
          </div>
        )}
      </div>
      {note && (
        <Banner tone={note.tone === 'error' ? 'error' : 'info'} role={note.tone === 'error' ? 'alert' : 'status'}>
          {note.text}
        </Banner>
      )}
    </>
  )
}

export default function Settings() {
  const { t, lang, merchant, setMerchant, sandbox, resetDemo } = useSession()
  const { navigate } = useRouter()
  const [learned, setLearned] = useState<number | null>(null)
  const [cash, setCash] = useState(amountInput(merchant?.cash_on_hand_paise ?? null))
  const [cashFocus, setCashFocus] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.items().then((items) => setLearned(items.filter((i) => i.price).length), () => undefined)
  }, [merchant?.id])

  const patch = async (p: Parameters<typeof api.updateMe>[0]) => {
    setError(null)
    try {
      setMerchant(await api.updateMe(p))
      return true
    } catch (e) {
      setError((e as ApiError).message)
      return false
    }
  }

  const saveCash = async () => {
    setCashFocus(false)
    const v = cash.trim()
    if (!v) return void patch({ clear_cash_on_hand: true })
    const p = parsePaise(v)
    if (p == null) return setError(t('amountNeeded'))
    if (await patch({ cash_on_hand_paise: p })) setCash(amountInput(p))
  }

  const reset = async (fresh: boolean) => {
    setBusy(true)
    try {
      await resetDemo({ fresh, language: fresh ? undefined : lang })
      navigate('/', { replace: true })
    } finally {
      setBusy(false)
    }
  }

  if (!merchant) return null
  const cashPaise = parsePaise(cash)
  return (
    <Screen className="settings" testId="settings">
      <div className="settings__head">
        <IconButton label={t('back')} onClick={() => navigate('/')}>
          <ChevronBackIcon />
        </IconButton>
        <h1 className="screen-title screen-title--detail">{t('settings')}</h1>
      </div>

      <h2 className="settings__section">{t('settingsShop')}</h2>
      <div className="settings__card">
        <Link to="/setup" className="srow">
          <span>{t('shopName')}</span>
          <span className="srow__value">{merchant.name} ›</span>
        </Link>
        <Link to="/setup" className="srow">
          <span>{t('upiId')}</span>
          <span className="srow__value">{merchant.upi_vpa} ›</span>
        </Link>
        <Link to="/setup" className="srow">
          <span>{t('closingTimeShort')}</span>
          <span className="srow__value">{closingLabel(merchant.closing_time, lang)} ›</span>
        </Link>
        <label className="srow">
          <span>{t('cashInHand')}</span>
          {cashFocus ? (
            <input
              className="srow__input num"
              inputMode="numeric"
              aria-label={t('cashInHand')}
              value={cash}
              autoFocus
              onChange={(e) => setCash(e.target.value)}
              onBlur={saveCash}
              onKeyDown={(e) => e.key === 'Enter' && (e.currentTarget as HTMLInputElement).blur()}
            />
          ) : (
            <input
              className="srow__input num"
              inputMode="numeric"
              aria-label={t('cashInHand')}
              readOnly
              value={cashPaise != null ? rupees(cashPaise) : ''}
              placeholder="₹0"
              onFocus={() => setCashFocus(true)}
            />
          )}
        </label>
      </div>

      <div className="settings__card">
      <label className="srow">
        <span>{t('nextFestival')}</span>
        <input
          className="srow__input"
          type="date"
          aria-label={t('nextFestival')}
          value={merchant.festival_date ?? ''}
          onChange={async (e) => {
            const v = e.currentTarget.value
            setMerchant(await api.updateMe(v ? { festival_date: v } : { clear_festival_date: true }))
          }}
        />
      </label>
      </div>

      <h2 className="settings__section">{t('settingsApp')}</h2>
      <div className="settings__card">
        <Link to="/welcome" className="srow">
          <span>{t('language')}</span>
          <span className="srow__value">{LANGUAGES[merchant.language]} ›</span>
        </Link>
        <div className="srow">
          <span>{t('soundbox')}</span>
          {merchant.soundbox_connected ? (
            <span className="srow__ok">
              <span className="srow__dot" aria-hidden="true" />
              {t('connected')}
            </span>
          ) : (
            <span className="srow__value">{t('notConnected')}</span>
          )}
        </div>
        <div className="srow">
          <span>{t('pricesLearned')}</span>
          <span className="srow__value">{learned == null ? '' : fmt(t('itemsCount'), { N: learned })} ›</span>
        </div>
      </div>

      <h2 className="settings__section">{t('settingsPrivacy')}</h2>
      <div className="settings__card settings__card--privacy">
        <div className="srow srow--switch">
          <span className="srow__stack">
            <span>{t('keepRecordings')}</span>
            <span className="srow__sub">{t('keepRecordingsOff')}</span>
          </span>
          <Switch checked={merchant.keep_recordings} label={t('keepRecordings')} onChange={(v) => void patch({ keep_recordings: v })} />
        </div>
        <span className="settings__note">{t('micOnlyWhileHolding')}</span>
      </div>
      <YourData />

      <Devices />

      {error && (
        <Banner tone="error" role="alert">
          {error}
        </Banner>
      )}

      {sandbox && (
        <>
          <h2 className="settings__section">{t('settingsDemo')}</h2>
          <div className="settings__card">
            <button type="button" className="srow" disabled={busy} onClick={() => reset(false)} data-testid="reset-sample">
              <span>{t('resetSampleShop')}</span>
              <span className="srow__value">›</span>
            </button>
            <button type="button" className="srow" disabled={busy} onClick={() => reset(true)} data-testid="reset-fresh">
              <span>{t('resetNewShop')}</span>
              <span className="srow__value">›</span>
            </button>
          </div>
        </>
      )}
    </Screen>
  )
}

