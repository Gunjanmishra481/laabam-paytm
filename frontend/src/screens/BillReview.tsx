// 3.2 Check a read bill. Design: design/screens/BillReview.dc.html.
// Lines that need the owner come first, amber, each with its two actions; Confirm stays off until none are
// left. Lines that check out are listed after, folded when there are many.

import { useEffect, useState } from 'react'
import { ApiError, api, whileReading, type ItemInfo, type Purchase, type PurchaseLine, type PurchaseLineEdit } from '../api'
import { milliToText, parseMilli, parsePaise, qtyParts, rupees } from '../money'
import { useRouter } from '../router'
import { fmt, isStringKey, type StringKey } from '../strings'
import { Banner, BottomSheet, Button, IconButton, Rich, Screen, cx } from '../ui'
import { ChevronBackIcon, WarnIcon } from '../ui/icons'
import { useSession } from '../useSession'
import { amountInput, capitalise } from './bill/lines'
import { billDay } from './BillsIn'

type T = (k: StringKey) => string
const SHOWN_OK = 2 // lines that check out shown before "+ N more"

function unitText(t: T, unit: string | null): string {
  if (!unit) return ''
  const key = `unit_${unit}`
  return isStringKey(key) ? t(key) : unit
}

/** '15', '25 kg', '500 ml' */
function qtyLabel(t: T, line: Pick<PurchaseLine, 'qty_milli' | 'unit'>): string {
  const q = qtyParts(line.qty_milli, line.unit)
  if (!q) return '?'
  return !q.unit || q.unit === 'pc' ? q.value : `${q.value} ${unitText(t, q.unit)}`
}

export default function BillReview({ id }: { id: string }) {
  const { t } = useSession()
  const { navigate } = useRouter()
  const [bill, setBill] = useState<Purchase | null>(null)
  const [items, setItems] = useState<ItemInfo[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sheet, setSheet] = useState<{ kind: 'line'; id: string } | { kind: 'new' } | { kind: 'details' } | null>(null)
  const [showAll, setShowAll] = useState(false)

  useEffect(() => {
    let alive = true
    api
      .purchase(id)
      .then(async (p) => {
        if (!alive) return
        setBill(p)
        if (p.status === 'reading') {
          const done = await whileReading(p, () => alive) // the worker is still reading the photo
          if (alive) setBill(done)
        }
      })
      .catch((e: ApiError) => alive && setError(e.message))
    api.items().then(setItems, () => undefined)
    return () => {
      alive = false
    }
  }, [id])

  const retry = async () => {
    const ok = await run(() => api.retryPurchase(id))
    if (ok) setBill(await whileReading(await api.purchase(id)))
  }

  const run = async (fn: () => Promise<Purchase>): Promise<boolean> => {
    setBusy(true)
    setError(null)
    try {
      setBill(await fn())
      return true
    } catch (e) {
      setError((e as ApiError).message)
      return false
    } finally {
      setBusy(false)
    }
  }
  const edit = async (lineId: string, e: PurchaseLineEdit) => {
    const ok = await run(() => api.editPurchaseLine(id, lineId, e))
    if (ok && (e.item_id || e.accept_suggestion || e.new_item_name)) api.items().then(setItems, () => undefined)
    return ok
  }

  if (!bill)
    return (
      <Screen className="review">
        {error ? (
          <Banner tone="error" role="alert">
            {error}
          </Banner>
        ) : (
          <p className="note">{t('reading')}</p>
        )}
      </Screen>
    )

  if (bill.status === 'reading' || bill.status === 'failed')
    return (
      <Screen className="review" testId="bill-review">
        <div className="review__head">
          <IconButton label={t('back')} onClick={() => navigate('/bills-in')}>
            <ChevronBackIcon />
          </IconButton>
          <span className="review__title">
            <span className="review__name">{t('billPhoto')}</span>
            <span className="review__meta">{billDay(bill, t)}</span>
          </span>
          <BillPhoto bill={bill} />
        </div>
        {bill.status === 'reading' ? (
          <div className="camera-card camera-card--reading" role="status" aria-label={t('reading')}>
            <span className="camera-card__title">{t('reading')}</span>
            <span className="skeleton" aria-hidden="true">
              {[88, 72, 80, 64].map((w, i) => (
                <span key={i} className="skeleton__line" style={{ width: `${w}%` }} />
              ))}
            </span>
          </div>
        ) : (
          <>
            <Banner tone="error" role="alert">
              <span data-testid="read-failed">{t(bill.read_issues.includes('no_lines') ? 'readNoLines' : 'readFailed')}</span>
            </Banner>
            {bill.can_retry && (
              <Button variant="primary" disabled={busy} onClick={retry} data-testid="read-retry">
                {t('tryAgain')}
              </Button>
            )}
            <Button
              variant="danger"
              disabled={busy}
              onClick={async () => (await run(() => api.discardPurchase(id))) && navigate('/bills-in')}
            >
              {t('discardThisBill')}
            </Button>
          </>
        )}
        {error && (
          <Banner tone="error" role="alert">
            {error}
          </Banner>
        )}
      </Screen>
    )

  const open = bill.status === 'read'
  const attention = bill.lines.filter((l) => l.flags.length > 0)
  const fine = bill.lines.filter((l) => l.flags.length === 0)
  const shownFine = showAll || fine.length <= SHOWN_OK + 1 ? fine : fine.slice(0, SHOWN_OK)
  const mismatch = bill.read_issues.includes('total_mismatch') && bill.printed_total_paise != null
  const costNow = (itemId: string | null) => items.find((i) => i.id === itemId)?.cost ?? null
  const sheetLine = sheet?.kind === 'line' ? bill.lines.find((l) => l.id === sheet.id) ?? null : null

  return (
    <Screen className="review" testId="bill-review">
      <div className="review__top">
        <div className="review__head">
          <IconButton label={t('back')} onClick={() => navigate('/bills-in')}>
            <ChevronBackIcon />
          </IconButton>
          <button
            type="button"
            className="review__title"
            disabled={!open}
            onClick={() => setSheet({ kind: 'details' })}
            aria-label={open ? `${t('billDetails')}: ${bill.distributor_name}` : undefined}
          >
            <span className="review__name" data-testid="distributor">
              {bill.distributor_name || '—'}
            </span>
            <span className="review__meta">
              {fmt(t('billMeta'), { NO: bill.bill_no || '—', DATE: billDay(bill, t), N: bill.lines.length })}
            </span>
          </button>
          <BillPhoto bill={bill} />
        </div>
        {mismatch && (
          <div className="review__warn" role="status">
            <WarnIcon size={18} />
            <span>
              <Rich text={t('mismatch')} vars={{ SUM: rupees(bill.total_paise), TOTAL: rupees(bill.printed_total_paise!) }} />
            </span>
          </div>
        )}
        {bill.read_issues.includes('maybe_duplicate') && <Banner tone="amber">{t('readIssue_maybe_duplicate')}</Banner>}
        {bill.read_issues.includes('read_by_fallback') && (
          <Banner tone="amber" testId="read-by-fallback">
            {t('readIssue_read_by_fallback')}
          </Banner>
        )}
        {bill.status === 'confirmed' && (
          <Banner tone="info" role="status">
            <span data-testid="costs-saved">{t('costsSaved')}</span>
          </Banner>
        )}
      </div>

      <div className="review__lines" data-testid="purchase-lines">
        {attention.map((line) => (
          <AttentionCard
            key={line.id}
            line={line}
            items={items}
            busy={busy || !open}
            onEdit={() => setSheet({ kind: 'line', id: line.id })}
            onAnswer={(e) => edit(line.id, e)}
          />
        ))}

        {(fine.length > 0 || open) && (
          <div className="okcard">
            {shownFine.map((line) => {
              const before = costNow(line.item_id)
              const up =
                open && before && line.unit_cost_paise != null && before.per_unit === line.base_unit
                  ? line.unit_cost_paise - before.unit_cost_paise
                  : 0
              const body = (
                <>
                  <span className="okrow__main">
                    <span className="okrow__name">{line.item_name ? capitalise(line.item_name) : line.raw_text}</span>
                    {up > 0 && <span className="okrow__up">{fmt(t('upSinceLast'), { AMOUNT: rupees(up) })}</span>}
                  </span>
                  <span className="okrow__nums num">
                    {qtyLabel(t, line)} × {line.rate_paise != null ? rupees(line.rate_paise) : '?'}
                  </span>
                </>
              )
              return open ? (
                <button
                  key={line.id}
                  type="button"
                  className={cx('okrow', up > 0 && 'okrow--tall')}
                  onClick={() => setSheet({ kind: 'line', id: line.id })}
                  data-testid="purchase-line"
                >
                  {body}
                </button>
              ) : (
                <div key={line.id} className={cx('okrow', up > 0 && 'okrow--tall')} data-testid="purchase-line">
                  {body}
                </div>
              )
            })}
            {shownFine.length < fine.length && (
              <button type="button" className="okrow okrow--more" onClick={() => setShowAll(true)}>
                <span className="okrow__muted">{fmt(t('moreLinesOk'), { N: fine.length - shownFine.length })}</span>
                <span className="okrow__link">{t('show')}</span>
              </button>
            )}
            {open && (
              <button type="button" className="okrow okrow--more" onClick={() => setSheet({ kind: 'new' })}>
                <span className="okrow__link">+ {t('addLine')}</span>
              </button>
            )}
          </div>
        )}
      </div>

      <div className="review__bottom">
        {error && (
          <Banner tone="error" role="alert">
            {error}
          </Banner>
        )}
        {open && (
          <>
            <Button
              className="review__confirm"
              disabled={busy || !bill.can_confirm}
              onClick={() => run(() => api.confirmPurchase(id))}
              data-testid="confirm-costs"
            >
              {attention.length === 1
                ? t('checkOneToConfirm')
                : attention.length > 1
                  ? fmt(t('checkNToConfirm'), { N: attention.length })
                  : t('confirmCosts')}
            </Button>
          </>
        )}
      </div>

      {(sheetLine || sheet?.kind === 'new') && (
        <LineSheet
          key={sheetLine?.id ?? 'new'}
          line={sheetLine}
          busy={busy}
          onClose={() => setSheet(null)}
          onSave={async (e) => {
            const ok = sheetLine ? await edit(sheetLine.id, e) : await run(() => api.addPurchaseLine(id, e))
            if (ok) setSheet(null)
          }}
          onRemove={
            sheetLine
              ? async () => {
                  if (await run(() => api.deletePurchaseLine(id, sheetLine.id))) setSheet(null)
                }
              : undefined
          }
        />
      )}
      {sheet?.kind === 'details' && (
        <DetailsSheet
          bill={bill}
          busy={busy}
          onClose={() => setSheet(null)}
          onSave={async (p) => {
            if (await run(() => api.patchPurchase(id, p))) setSheet(null)
          }}
          onDiscard={async () => {
            if (await run(() => api.discardPurchase(id))) navigate('/bills-in')
          }}
        />
      )}
    </Screen>
  )
}

/** A line that needs the owner: its question and two actions. */
function AttentionCard({
  line,
  items,
  busy,
  onEdit,
  onAnswer,
}: {
  line: PurchaseLine
  items: ItemInfo[]
  busy: boolean
  onEdit: () => void
  onAnswer: (e: PurchaseLineEdit) => Promise<boolean>
}) {
  const { t } = useSession()
  const askItem = line.flags.includes('item_ask')
  const newItem = line.flags.includes('item_new')
  const [naming, setNaming] = useState(newItem)
  const [name, setName] = useState(line.name_hint)
  const math = line.flags.includes('math')
  const numberFlags = line.flags.filter((f) => f !== 'item_ask' && f !== 'item_new' && f !== 'math')
  const itemQuestion = askItem || newItem

  const title = itemQuestion ? `“${line.raw_text}”` : line.item_name ? capitalise(line.item_name) : line.raw_text
  const sub = itemQuestion ? (askItem ? t('newNameFromDistributor') : t('newItemFromDistributor')) : fmt(t('readAs'), { RAW: line.raw_text })
  const qty = line.qty_milli != null ? milliToText(line.qty_milli) : '?'
  const rate = line.rate_paise != null ? rupees(line.rate_paise) : '?'

  return (
    <div className="askcard" data-testid="purchase-line">
      <div className="askcard__top">
        <span className="askcard__id">
          <span className="askcard__title">{title}</span>
          <span className={cx('askcard__sub', !itemQuestion && 'askcard__sub--mono')}>{sub}</span>
        </span>
        <span className="askcard__nums num">
          {qty} × {rate} = <b>{line.line_total_paise != null ? rupees(line.line_total_paise) : '?'}</b>
        </span>
      </div>

      {math && (
        <>
          <span className="askcard__q">
            {fmt(t('mathAsk'), { QTY: qty, RATE: rate, EXPECTED: line.expected_paise != null ? rupees(line.expected_paise) : '?' })}
          </span>
          <div className="askcard__acts">
            {line.expected_paise != null && (
              <button type="button" className="act act--yes" disabled={busy} onClick={() => onAnswer({ line_total_paise: line.expected_paise! })}>
                {fmt(t('itsAmount'), { AMOUNT: rupees(line.expected_paise) })}
              </button>
            )}
            <button type="button" className="act" disabled={busy} onClick={onEdit}>
              {t('editLine')}
            </button>
          </div>
        </>
      )}

      {numberFlags.map((f) => {
        const key = `pflag_${f}`
        return (
          <span key={f} className="askcard__q">
            {isStringKey(key) ? t(key) : f}
          </span>
        )
      })}
      {numberFlags.length > 0 && !math && (
        <div className="askcard__acts">
          <button type="button" className="act" disabled={busy} onClick={onEdit}>
            {t('editLine')}
          </button>
        </div>
      )}

      {askItem && !naming && (
        <>
          <span className="askcard__q">{fmt(t('isThisYouBill'), { ITEM: (line.suggested_item_name ?? '').toLocaleLowerCase() })}</span>
          <div className="askcard__acts">
            <button type="button" className="act act--yes" disabled={busy} onClick={() => onAnswer({ accept_suggestion: true })}>
              {fmt(t('yesItem'), { ITEM: (line.suggested_item_name ?? '').toLocaleLowerCase() })}
            </button>
            <button type="button" className="act" disabled={busy} onClick={() => setNaming(true)}>
              {t('noNewItem')}
            </button>
          </div>
        </>
      )}
      {itemQuestion && naming && (
        <form
          className="askcard__name"
          onSubmit={(e) => {
            e.preventDefault()
            if (name.trim()) void onAnswer({ new_item_name: name.trim() })
          }}
        >
          <label className="askcard__q" htmlFor={`name-${line.id}`}>
            {t('whatCallThis')}
          </label>
          <div className="askcard__acts">
            <input id={`name-${line.id}`} className="askcard__input" value={name} onChange={(e) => setName(e.target.value)} />
            <button type="submit" className="act act--yes act--fit" disabled={busy || !name.trim()}>
              {t('addAsNew')}
            </button>
          </div>
          {items.length > 0 && (
            <select
              className="askcard__pick"
              aria-label={t('pickItem')}
              value=""
              disabled={busy}
              onChange={(e) => e.target.value && void onAnswer({ item_id: e.target.value })}
            >
              <option value="">{t('pickItem')}</option>
              {items.map((it) => (
                <option key={it.id} value={it.id}>
                  {it.name}
                </option>
              ))}
            </select>
          )}
        </form>
      )}
    </div>
  )
}

const UNITS = ['pc', 'pack', 'kg', 'L'] as const

/** Edit a read line, or add one the reader missed. */
function LineSheet({
  line,
  busy,
  onSave,
  onRemove,
  onClose,
}: {
  line: PurchaseLine | null
  busy: boolean
  onSave: (e: PurchaseLineEdit) => void
  onRemove?: () => void
  onClose: () => void
}) {
  const { t } = useSession()
  const [text, setText] = useState(line?.raw_text ?? '')
  const [qty, setQty] = useState(line?.qty_milli != null ? milliToText(line.qty_milli) : '')
  const [unit, setUnit] = useState(line?.unit ?? 'pc')
  const [rate, setRate] = useState(amountInput(line?.rate_paise ?? null))
  const [amount, setAmount] = useState(amountInput(line?.line_total_paise ?? null))
  const [error, setError] = useState<string | null>(null)

  const save = () => {
    const e: PurchaseLineEdit = {}
    if (text.trim() && text.trim() !== line?.raw_text) e.raw_text = text.trim()
    const q = qty.trim() ? parseMilli(qty) : null
    const r = rate.trim() ? parsePaise(rate) : null
    const a = amount.trim() ? parsePaise(amount) : null
    if ((qty.trim() && q == null) || (rate.trim() && r == null) || (amount.trim() && a == null)) return setError(t('amountNeeded'))
    if (!line && !text.trim()) return setError(t('lineAsPrinted'))
    if (q != null && q !== line?.qty_milli) e.qty_milli = q
    if (unit !== line?.unit) e.unit = unit
    if (r != null && r !== line?.rate_paise) e.rate_paise = r
    if (!rate.trim() && line?.rate_paise != null) e.clear_rate = true
    if (a != null && a !== line?.line_total_paise) e.line_total_paise = a
    onSave(e)
  }

  return (
    <BottomSheet open onClose={onClose} label={line ? t('editLine') : t('addLine')}>
      <h2 className="sheet__title">{line ? t('editLine') : t('addLine')}</h2>
      <div className="field2">
        <label className="field2__label" htmlFor="pl-text">
          {t('lineAsPrinted')}
        </label>
        <input id="pl-text" className="input2 mono" value={text} data-autofocus={!line || undefined} onChange={(e) => setText(e.target.value)} />
      </div>
      <div className="fix-grid">
        <div className="field2">
          <label className="field2__label" htmlFor="pl-qty">
            {t('qty')}
          </label>
          <input id="pl-qty" className="input2 num" inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} />
        </div>
        <div className="field2">
          <label className="field2__label" htmlFor="pl-unit">
            {t('unit')}
          </label>
          <select id="pl-unit" className="input2" value={unit} onChange={(e) => setUnit(e.target.value)}>
            {UNITS.map((u) => (
              <option key={u} value={u}>
                {unitText(t, u)}
              </option>
            ))}
          </select>
        </div>
        <div className="field2">
          <label className="field2__label" htmlFor="pl-rate">
            {t('rate')}
          </label>
          <input id="pl-rate" className="input2 num" inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} />
        </div>
        <div className="field2">
          <label className="field2__label" htmlFor="pl-amount">
            {t('amount')}
          </label>
          <input
            id="pl-amount"
            className="input2 num"
            inputMode="numeric"
            value={amount}
            data-autofocus={line ? true : undefined}
            onChange={(e) => setAmount(e.target.value)}
          />
        </div>
      </div>
      {error && (
        <Banner tone="error" role="alert">
          {error}
        </Banner>
      )}
      <div className="btn-row fix-actions">
        {onRemove && (
          <Button variant="danger" size="md" className="btn-row__1" disabled={busy} onClick={onRemove}>
            {t('removeLine')}
          </Button>
        )}
        <Button size="md" className="btn-row__2" disabled={busy} onClick={save}>
          {line ? t('saveLine') : t('addLineBtn')}
        </Button>
      </div>
    </BottomSheet>
  )
}

function DetailsSheet({
  bill,
  busy,
  onSave,
  onDiscard,
  onClose,
}: {
  bill: Purchase
  busy: boolean
  onSave: (p: { distributor_name: string; bill_no: string; bill_date: string | null }) => void
  onDiscard: () => void
  onClose: () => void
}) {
  const { t } = useSession()
  const [name, setName] = useState(bill.distributor_name)
  const [no, setNo] = useState(bill.bill_no)
  const [day, setDay] = useState(bill.bill_date ?? '')
  return (
    <BottomSheet open onClose={onClose} label={t('billDetails')}>
      <h2 className="sheet__title">{t('billDetails')}</h2>
      <div className="field2">
        <label className="field2__label" htmlFor="bd-name">
          {t('distributor')}
        </label>
        <input id="bd-name" className="input2" value={name} data-autofocus onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="fix-grid">
        <div className="field2">
          <label className="field2__label" htmlFor="bd-no">
            {t('billNo')}
          </label>
          <input id="bd-no" className="input2" value={no} onChange={(e) => setNo(e.target.value)} />
        </div>
        <div className="field2">
          <label className="field2__label" htmlFor="bd-date">
            {t('billDate')}
          </label>
          <input id="bd-date" className="input2" type="date" value={day} onChange={(e) => setDay(e.target.value)} />
        </div>
      </div>
      <div className="btn-row fix-actions">
        <Button variant="danger" size="md" className="btn-row__1" disabled={busy} onClick={onDiscard} data-testid="discard-bill">
          {t('discardThisBill')}
        </Button>
        <Button size="md" className="btn-row__2" disabled={busy} onClick={() => onSave({ distributor_name: name, bill_no: no, bill_date: day || null })}>
          {t('save')}
        </Button>
      </div>
    </BottomSheet>
  )
}

/** The bill photo as a 48×60 thumbnail; tap to see it full size. */
function BillPhoto({ bill }: { bill: Purchase }) {
  const { t } = useSession()
  const [url, setUrl] = useState<string | null>(null)
  const [big, setBig] = useState(false)
  useEffect(() => {
    if (!bill.has_photo) return
    let live = true
    let made: string | null = null
    api.purchasePhoto(bill.id).then((blob) => {
      if (!live || !blob) return
      made = URL.createObjectURL(blob)
      setUrl(made)
    })
    return () => {
      live = false
      if (made) URL.revokeObjectURL(made)
    }
  }, [bill.id, bill.has_photo])
  const src = bill.has_photo ? url : bill.sample ? `/samples/bills/${bill.sample}.svg` : null
  return (
    <>
      <button type="button" className="thumb" aria-label={t('photoOf')} disabled={!src} onClick={() => setBig(true)}>
        {src && <img src={src} alt="" />}
      </button>
      {big && src && (
        <BottomSheet open onClose={() => setBig(false)} label={t('photoOf')}>
          <img className="thumb__big" src={src} alt={t('photoOf')} />
          <Button variant="secondary" size="md" onClick={() => setBig(false)}>
            {t('close')}
          </Button>
        </BottomSheet>
      )}
    </>
  )
}
