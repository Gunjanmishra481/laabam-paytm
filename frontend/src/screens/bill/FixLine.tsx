// 2.4 Fix a line — a bottom sheet over the checklist. Design: design/screens/FixLine.dc.html.
// Fix by tap (quantity, amount, item) or by voice ("sugar 120"). With no line given, it adds an item.

import { useState } from 'react'
import { ApiError, api, type Bill, type Line, type LineEdit } from '../../api'
import { parsePaise, qtyParts } from '../../money'
import { fmt, isStringKey } from '../../strings'
import { Banner, BottomSheet, Button, ListRow, Money } from '../../ui'
import { MicIcon } from '../../ui/icons'
import { useSession } from '../../useSession'
import { fromSample, holdHandlers, markSampleBill, useVoice, type RecJob } from '../../voice'
import { amountInput, capitalise, lineName } from './lines'

type Props = {
  bill: Bill
  line: Line | null
  onChange: (b: Bill) => void
  onClose: () => void
  /** After a save: the updated bill, so the checklist can open the next line that needs fixing. */
  onNext: (b: Bill) => void
}

const FRACTIONAL = new Set(['kg', 'L'])

function stepQty(q: number, unit: string | null, dir: 1 | -1): number {
  const fine = unit != null && FRACTIONAL.has(unit)
  if (dir > 0) return q >= 1000 || !fine ? q + 1000 : q + 250
  if (q > 1000) return q - 1000
  return fine ? Math.max(250, q - 250) : q
}

export default function FixLine({ bill, line, onChange, onClose, onNext }: Props) {
  const { t, voice: voiceMode } = useSession()
  const voice = useVoice()
  const isNew = line == null
  const needsItem = isNew || line.flags.includes('item_needed')
  const needsPrice = !isNew && line.flags.includes('price_needed')
  const [item, setItem] = useState(isNew ? '' : line.item_name)
  const startQty = line?.qty_milli ?? 1000
  const [qty, setQty] = useState<number>(startQty)
  const unit = line?.unit ?? null
  const [amount, setAmount] = useState(amountInput(line?.line_total_paise ?? null))
  const [typed, setTyped] = useState('')
  const [typing, setTyping] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const name = isNew ? '' : capitalise(line.item_name || '?')
  const title = isNew
    ? t('newItemTitle')
    : needsItem
      ? t('fixTitleItem')
      : needsPrice
        ? fmt(t('fixTitlePrice'), { ITEM: (line.item_name || '?').toLocaleLowerCase() })
        : fmt(t('fixTitle'), { ITEM: name })
  const flagKey = line?.flags[0] ? `flag_${line.flags[0]}` : null
  const sub = needsPrice ? t('fixSubPrice') : flagKey && isStringKey(flagKey) ? t(flagKey) : null

  const apply = async (fn: () => Promise<Bill>) => {
    setBusy(true)
    setError(null)
    try {
      const next = await fn()
      onChange(next)
      onNext(next)
    } catch (e) {
      setError((e as ApiError).message)
    } finally {
      setBusy(false)
    }
  }

  const save = () => {
    const edit: LineEdit = {}
    if (needsItem || (line && item.trim() !== line.item_name)) {
      if (!item.trim()) return setError(t('fixTitleItem'))
      edit.item_text = item.trim()
    }
    if (qty !== startQty) {
      edit.qty_milli = qty
      if (unit) edit.unit = unit
    }
    const p = amount.trim() ? parsePaise(amount) : null
    if (amount.trim() && p == null) return setError(t('amountNeeded'))
    if ((needsPrice || isNew) && p == null) return setError(t('amountNeeded'))
    if (p != null && p !== line?.line_total_paise) edit.line_total_paise = p
    if (!Object.keys(edit).length) return onClose()
    void apply(() => (isNew ? api.addLine(bill.id, edit) : api.editLine(bill.id, line.id, edit)))
  }

  const sayJob = (): RecJob => ({
    onAudio: async (audio) =>
      apply(async () => {
        const b = await api.sayVoice(bill.id, audio)
        if (fromSample(b.speech)) markSampleBill(b.id)
        return b
      }),
    onShort: () => setError(t('holdLonger')),
    onError: (m) => {
      setError(m)
      setTyping(true)
    },
  })
  const hold = holdHandlers(voice, sayJob)
  const recording = voice.state === 'recording' || voice.state === 'starting'
  const q = qtyParts(qty, unit)
  const unitKey = q?.unit ? `unit_${q.unit}` : null
  const qtyLabel = q ? `${q.value}${q.unit && q.unit !== 'pc' && unitKey && isStringKey(unitKey) ? ' ' + t(unitKey) : ''}` : ''

  return (
    <BottomSheet
      open
      onClose={onClose}
      label={needsPrice ? fmt(t('sheetLabelPrice'), { ITEM: (line?.item_name ?? '').toLocaleLowerCase() }) : title}
    >
      <div>
        <h2 className="sheet__title">{title}</h2>
        {sub && <p className="sheet__sub">{sub}</p>}
      </div>

      {needsItem && (
        <div className="field2">
          <label className="field2__label" htmlFor="fix-item">
            {t('itemName')}
          </label>
          <input id="fix-item" className="input2" value={item} data-autofocus onChange={(e) => setItem(e.target.value)} />
        </div>
      )}

      <div className="fix-grid">
        <div className="field2">
          <span className="field2__label" id="fix-qty-l">
            {t('qty')}
          </span>
          <span className="stepper" role="group" aria-labelledby="fix-qty-l">
            <button
              type="button"
              className="stepper__btn"
              aria-label={t('less')}
              disabled={stepQty(qty, unit, -1) === qty}
              onClick={() => setQty(stepQty(qty, unit, -1))}
            >
              −
            </button>
            <span className="stepper__val num" aria-live="polite">
              {qtyLabel}
            </span>
            <button type="button" className="stepper__btn" aria-label={t('more')} onClick={() => setQty(stepQty(qty, unit, 1))}>
              +
            </button>
          </span>
        </div>
        <div className="field2">
          <label className="field2__label" htmlFor="fix-amount">
            {t('amount')}
          </label>
          <input
            id="fix-amount"
            className="input2 input2--big num"
            type="text"
            inputMode="numeric"
            autoComplete="off"
            value={amount}
            {...(!needsItem ? { 'data-autofocus': true } : {})}
            onChange={(e) => setAmount(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && save()}
          />
        </div>
      </div>

      {voiceMode === 'live' && !typing ? (
        <Button variant="secondary" size="md" className="hold-small" aria-pressed={recording} disabled={busy || voice.state === 'sending'} {...hold}>
          <MicIcon size={20} filled={recording} />
          {recording ? t('listening') : voice.state === 'sending' ? t('hearing') : t('orHoldSayIt')}
        </Button>
      ) : typing ? (
        <form
          className="fix-say"
          onSubmit={(e) => {
            e.preventDefault()
            if (typed.trim()) void apply(() => api.say(bill.id, typed))
          }}
        >
          <label className="sr-only" htmlFor="fix-say">
            {t('typeFix')}
          </label>
          <input
            id="fix-say"
            className="input2"
            placeholder={t('fixPlaceholder')}
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
          />
          <Button type="submit" size="md" block={false} disabled={busy || !typed.trim()}>
            {t('apply')}
          </Button>
        </form>
      ) : (
        <Button variant="secondary" size="md" onClick={() => setTyping(true)}>
          {t('orTypeIt')}
        </Button>
      )}

      {error && (
        <Banner tone="error" role="alert">
          {error}
        </Banner>
      )}

      <div className="btn-row fix-actions">
        {!isNew && (
          <Button
            variant="danger"
            size="md"
            className="btn-row__1"
            disabled={busy}
            onClick={() => apply(() => api.deleteLine(bill.id, line.id))}
          >
            {t('removeLine')}
          </Button>
        )}
        <Button className="btn-row__2" size="md" disabled={busy} onClick={save}>
          {isNew ? t('addLineBtn') : t('saveLine')}
        </Button>
      </div>
    </BottomSheet>
  )
}

/** "Fix a line" with nothing flagged: pick the line, or add one. */
export function LinePicker({
  open,
  lines,
  onPick,
  onClose,
}: {
  open: boolean
  lines: Line[]
  onPick: (id: string | null) => void
  onClose: () => void
}) {
  const { t } = useSession()
  return (
    <BottomSheet open={open} onClose={onClose} label={t('whichLine')}>
      <h2 className="sheet__title">{t('whichLine')}</h2>
      <div className="card2 card2--flat">
        {lines.map((l) => (
          <ListRow key={l.id} label={lineName(l, t)} value={<Money paise={l.line_total_paise} />} onClick={() => onPick(l.id)} />
        ))}
      </div>
      <Button variant="secondary" size="md" onClick={() => onPick(null)}>
        + {t('addItem')}
      </Button>
    </BottomSheet>
  )
}
