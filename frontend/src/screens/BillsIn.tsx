// 3.1 Bills in. Design: design/screens/BillsIn.dc.html.
// Photograph a distributor bill -> Laabam reads it -> Check a read bill (/bills-in/:id).
// With no photo reader set up, the screen says so; the demo's sample bills are a separate, labelled choice.
// A live reader (Sarvam Document AI) reads on the worker: the card shows "Reading the bill…" while this screen
// polls; a read that fails offers Try again (same photo) here and in Recent bills.

import { useEffect, useRef, useState } from 'react'
import { ApiError, api, whileReading, type Purchase } from '../api'
import { shrinkPhoto } from '../photo'
import { Link, useRouter } from '../router'
import { fmt, isStringKey } from '../strings'
import { Banner, Button, Money, Rich, Screen, cx } from '../ui'
import { CameraIcon } from '../ui/icons'
import { useSession } from '../useSession'

/** 'Today' or '9 Nov' (IST). */
export function billDay(p: Pick<Purchase, 'bill_date' | 'created_at'>, t: (k: 'today') => string): string {
  const today = new Date(Date.now() + 330 * 60_000).toISOString().slice(0, 10)
  const day = p.bill_date ?? new Date(Date.parse(p.created_at) + 330 * 60_000).toISOString().slice(0, 10)
  if (day === today) return t('today')
  const [y, m, d] = day.split('-').map(Number)
  return new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(Date.UTC(y, m - 1, d))
}

export default function BillsIn() {
  const { t, vision, billSamples } = useSession()
  const { navigate } = useRouter()
  const [list, setList] = useState<Purchase[]>([])
  const [noCost, setNoCost] = useState(0)
  const [reading, setReading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [failed, setFailed] = useState<Purchase | null>(null)
  const alive = useRef(true)
  const camera = useRef<HTMLInputElement>(null)
  const gallery = useRef<HTMLInputElement>(null)

  const refresh = () =>
    api.purchases().then((ps) => alive.current && setList(ps.filter((p) => p.status !== 'discarded')), () => undefined)

  useEffect(() => {
    alive.current = true
    refresh()
    // items the owner has sold (a learned price) but has no cost for yet
    api.items().then((items) => setNoCost(items.filter((i) => i.price && !i.cost).length), () => undefined)
    return () => {
      alive.current = false
    }
  }, [])

  const read = async (fn: () => Promise<Purchase>) => {
    setReading(true)
    setError(null)
    setFailed(null)
    try {
      const p = await whileReading(await fn(), () => alive.current)
      if (!alive.current) return
      if (p.status === 'read') {
        navigate(`/bills-in/${p.id}`)
        return
      }
      if (p.status === 'failed') setFailed(p)
      else setError(t('stillReading'))
      setReading(false)
      refresh()
    } catch (e) {
      setError((e as ApiError).message)
      setReading(false)
    }
  }

  const onFile = async (input: HTMLInputElement | null) => {
    const file = input?.files?.[0]
    if (!file) return
    await read(async () => api.purchaseFromPhoto(await shrinkPhoto(file)))
    if (input) input.value = ''
  }

  return (
    <Screen tab="bills" className="billsin">
      <div className="billsin__head">
        <h1 className="screen-title">{t('billsIn')}</h1>
        <p className="billsin__sub">{t('billsInSub')}</p>
      </div>

      {reading ? (
        <div className="camera-card camera-card--reading" role="status" aria-label={t('reading')}>
          <span className="camera-card__title">{t('reading')}</span>
          <span className="skeleton" aria-hidden="true">
            {[88, 72, 80, 64].map((w, i) => (
              <span key={i} className="skeleton__line" style={{ width: `${w}%` }} />
            ))}
          </span>
        </div>
      ) : vision === 'live' ? (
        <>
          <input
            ref={camera}
            type="file"
            accept="image/*"
            capture="environment"
            hidden
            onChange={(e) => onFile(e.currentTarget)}
            data-testid="bill-photo-input"
          />
          <input ref={gallery} type="file" accept="image/*" hidden onChange={(e) => onFile(e.currentTarget)} data-testid="bill-gallery-input" />
          <button type="button" className="camera-card" onClick={() => camera.current?.click()}>
            <span className="camera-card__icon" aria-hidden="true">
              <CameraIcon size={36} />
            </span>
            <span className="camera-card__title">{t('takePhotoBill')}</span>
            <span className="camera-card__tip">{t('photoTip')}</span>
          </button>
          <button type="button" className="billsin__gallery" onClick={() => gallery.current?.click()}>
            {t('chooseGallery')}
          </button>
        </>
      ) : (
        <>
          <Banner tone="amber" role="status">
            <span data-testid="vision-off">{vision === 'samples' ? t('visionOffSamples') : t('visionOff')}</span>
          </Banner>
          {vision === 'samples' && billSamples.length > 0 && (
            <section className="samples" aria-labelledby="samples-h">
              <h2 id="samples-h" className="label-caps">
                {t('sampleBillsTitle')}
              </h2>
              <div className="samples__grid">
                {billSamples.map((name) => {
                  const key = `sample_${name}`
                  return (
                    <button
                      key={name}
                      type="button"
                      className="samples__bill"
                      onClick={() => read(() => api.purchaseFromSample(name))}
                      data-testid={`sample-bill-${name}`}
                    >
                      <img src={`/samples/bills/${name}.svg`} alt="" />
                      <span>{isStringKey(key) ? t(key) : name}</span>
                    </button>
                  )
                })}
              </div>
            </section>
          )}
        </>
      )}

      {error && (
        <Banner tone="error" role="alert">
          {error}
        </Banner>
      )}

      {failed && (
        <>
          <Banner tone="error" role="alert">
            <span data-testid="read-failed">{t(failed.read_issues.includes('no_lines') ? 'readNoLines' : 'readFailed')}</span>
          </Banner>
          {failed.can_retry && (
            <Button variant="secondary" size="md" onClick={() => read(() => api.retryPurchase(failed.id))} data-testid="read-retry">
              {t('tryAgain')}
            </Button>
          )}
        </>
      )}

      {noCost > 0 && (
        <Link to="/profit" className="nocost">
          <span className="nocost__text">
            <Rich text={t('noCostItems')} vars={{ COUNT: fmt(t('itemsCount'), { N: noCost }) }} />
          </span>
          <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true" className="nocost__chev">
            <path d="M7.5 4.5L13 10l-5.5 5.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </Link>
      )}

      <section className="recent2 recent2--flush" aria-labelledby="pur-h">
        <h2 id="pur-h" className="label-caps">
          {t('recentBills')}
        </h2>
        {list.length === 0 && <p className="note">{t('noPurchases')}</p>}
        {list.map((p) => {
          if (p.status === 'failed')
            return (
              <button
                key={p.id}
                type="button"
                className="recent2__row recent2__row--attention"
                onClick={() => read(() => api.retryPurchase(p.id))}
                disabled={reading || !p.can_retry}
                data-testid="purchase-row"
              >
                <span className="recent2__main">
                  <span className="recent2__title">{t('billPhoto')}</span>
                  <span className="recent2__sub recent2__sub--attention">
                    {billDay(p, t)} · {t('failedRow')}
                  </span>
                </span>
              </button>
            )
          if (p.status === 'reading')
            return (
              <Link key={p.id} to={`/bills-in/${p.id}`} className="recent2__row" data-testid="purchase-row">
                <span className="recent2__main">
                  <span className="recent2__title">{t('billPhoto')}</span>
                  <span className="recent2__sub">
                    {billDay(p, t)} · {t('readingRow')}
                  </span>
                </span>
              </Link>
            )
          const toCheck = p.lines.filter((l) => l.flags.length > 0).length
          const open = p.status === 'read'
          const status = !open
            ? fmt(t('linesDone'), { N: p.lines.length })
            : toCheck === 1
              ? t('oneLineToCheck')
              : toCheck > 1
                ? fmt(t('linesToCheck'), { N: toCheck })
                : fmt(t('readyToConfirm'), { N: p.lines.length })
          return (
            <Link key={p.id} to={`/bills-in/${p.id}`} className={cx('recent2__row', open && 'recent2__row--attention')} data-testid="purchase-row">
              <span className="recent2__main">
                <span className="recent2__title">{p.distributor_name || '—'}</span>
                <span className={cx('recent2__sub', open && 'recent2__sub--attention')}>
                  {billDay(p, t)} · {status}
                </span>
              </span>
              <Money className="recent2__amount" paise={p.printed_total_paise ?? p.total_paise} />
            </Link>
          )
        })}
      </section>
    </Screen>
  )
}

