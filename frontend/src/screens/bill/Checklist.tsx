// 2.3 Checklist. Design: design/screens/Checklist.dc.html.
// Lines arrive ticked. Unticking removes the line (optimistic, then synced); ticking again puts it back.
// Confirm is the only way to a QR, and it stays disabled while any line needs fixing.

import { useState } from 'react'
import { ApiError, api, type Bill, type Line } from '../../api'
import { rupees } from '../../money'
import { useRouter } from '../../router'
import { fmt } from '../../strings'
import { Banner, Button, Money, Screen, cx } from '../../ui'
import { MicIcon } from '../../ui/icons'
import { useSession } from '../../useSession'
import { isSampleBill } from '../../voice'
import FixLine, { LinePicker } from './FixLine'
import { lineName, lineNote } from './lines'

type Props = { bill: Bill; onChange: (b: Bill) => void }

const without = <V,>(r: Record<string, V>, key: string) => Object.fromEntries(Object.entries(r).filter(([k]) => k !== key))
type Sheet = { kind: 'line'; id: string } | { kind: 'new' } | { kind: 'pick' } | null

export default function Checklist({ bill, onChange }: Props) {
  const { t } = useSession()
  const { navigate } = useRouter()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // unticked lines stay on screen, struck through, so the owner can tick them back
  const [removed, setRemoved] = useState<Record<string, Line>>({})
  const flagged = bill.lines.filter((l) => l.flags.length > 0)
  // "Fix a line" opens by itself for the first line that needs the owner
  const [sheet, setSheet] = useState<Sheet>(() => (flagged.length ? { kind: 'line', id: flagged[0].id } : null))

  const run = async (fn: () => Promise<Bill>): Promise<boolean> => {
    setBusy(true)
    setError(null)
    try {
      onChange(await fn())
      return true
    } catch (e) {
      setError((e as ApiError).message)
      return false
    } finally {
      setBusy(false)
    }
  }

  const untick = async (line: Line) => {
    setRemoved((r) => ({ ...r, [line.id]: line }))
    if (!(await run(() => api.deleteLine(bill.id, line.id))))
      setRemoved((r) => without(r, line.id))
  }

  const retick = async (line: Line) => {
    setRemoved((r) => without(r, line.id))
    const edit = {
      item_text: line.item_name,
      ...(line.qty_milli != null ? { qty_milli: line.qty_milli, unit: line.unit ?? undefined } : {}),
      // a learned price is looked up again; a said or typed one is put back as it was
      ...(line.price_source !== 'learned' && line.line_total_paise != null ? { line_total_paise: line.line_total_paise } : {}),
    }
    if (!(await run(() => api.addLine(bill.id, edit)))) setRemoved((r) => ({ ...r, [line.id]: line }))
  }

  const confirm = async () => {
    if (await run(() => api.confirm(bill.id))) navigator.vibrate?.(20)
  }

  const cancel = async () => {
    if (await run(() => api.cancel(bill.id))) navigate('/', { replace: true })
  }

  // server lines plus the struck ones, in the order they were said
  const shown = [...bill.lines.filter((l) => !removed[l.id]), ...Object.values(removed)].sort((a, b) => a.position - b.position)
  const total = bill.lines.filter((l) => !removed[l.id]).reduce((s, l) => s + (l.line_total_paise ?? 0), 0)
  const pending = Object.keys(removed).some((id) => bill.lines.some((l) => l.id === id))
  const confirmLabel = flagged.length
    ? flagged.length === 1
      ? t('fixOneLine')
      : fmt(t('fixManyLines'), { N: flagged.length })
    : fmt(t('confirmAmount'), { AMOUNT: rupees(total) })
  const sheetLine = sheet?.kind === 'line' ? bill.lines.find((l) => l.id === sheet.id) ?? null : null

  return (
    <Screen className="checklist">
      <div className="screen-head">
        <h1 className="screen-title">{t('checklist')}</h1>
        <Button variant="text" block={false} disabled={busy} onClick={cancel}>
          {t('cancel')}
        </Button>
      </div>

      {bill.transcript && (
        <div className="said">
          <MicIcon size={20} className="said__icon" />
          <p>
            <span className="said__label">{t('youSaid')}</span> “{bill.transcript}”
          </p>
        </div>
      )}

      {isSampleBill(bill.id) && (
        <Banner tone="amber" role="status">
          <span data-testid="sample-notice">{t('sampleBillNotice')}</span>
        </Banner>
      )}

      <div className="card2 bill-card">
        <ul className="bill-lines" data-testid="checklist">
          {shown.map((line) => {
            const off = !!removed[line.id]
            const note = lineNote(line, t)
            const name = lineName(line, t)
            return (
              <li key={line.id} className={cx('bline', off && 'bline--off', note.attention && !off && 'bline--attention')}>
                <label className="bline__tickwrap">
                  <input
                    type="checkbox"
                    className="bline__tick"
                    checked={!off}
                    disabled={busy}
                    aria-label={fmt(t('takeLine'), { ITEM: name })}
                    onChange={() => (off ? retick(line) : untick(line))}
                  />
                </label>
                <button
                  type="button"
                  className="bline__body"
                  disabled={off || busy}
                  onClick={() => setSheet({ kind: 'line', id: line.id })}
                >
                  <span className="bline__text">
                    <span className="bline__name">{name}</span>
                    <span className={cx('bline__note', note.attention && 'bline__note--attention')}>{note.text}</span>
                  </span>
                  {line.line_total_paise != null ? (
                    <Money className="bline__amount" paise={line.line_total_paise} />
                  ) : (
                    <span className="bline__amount bline__amount--unknown">₹ ?</span>
                  )}
                </button>
              </li>
            )
          })}
        </ul>
        <div className="bill-total">
          <span>{t('total')}</span>
          <span className="bill-total__fig" data-testid="bill-total">
            <Money paise={total} />
          </span>
        </div>
      </div>

      {bill.lines.length === 0 && Object.keys(removed).length === 0 && <Banner tone="amber">{t('nothingHeard')}</Banner>}
      <p className="note">{t('untickHint')}</p>
      {error && (
        <Banner tone="error" role="alert">
          {error}
        </Banner>
      )}

      <div className="actions">
        <Button disabled={busy || pending || !bill.can_confirm || flagged.length > 0} onClick={confirm} data-testid="confirm">
          {confirmLabel}
        </Button>
        <Button
          variant="secondary"
          size="md"
          disabled={busy}
          onClick={() => setSheet(flagged.length ? { kind: 'line', id: flagged[0].id } : { kind: 'pick' })}
        >
          {t('fixALine')}
        </Button>
      </div>

      <LinePicker
        open={sheet?.kind === 'pick'}
        lines={bill.lines}
        onPick={(id) => setSheet(id ? { kind: 'line', id } : { kind: 'new' })}
        onClose={() => setSheet(null)}
      />
      {(sheetLine || sheet?.kind === 'new') && (
        <FixLine
          key={sheetLine?.id ?? 'new'}
          bill={bill}
          line={sheetLine}
          onChange={onChange}
          onClose={() => setSheet(null)}
          onNext={(next) => {
            const nextFlagged = next.lines.find((l) => l.flags.length > 0)
            setSheet(nextFlagged ? { kind: 'line', id: nextFlagged.id } : null)
          }}
        />
      )}
    </Screen>
  )
}
