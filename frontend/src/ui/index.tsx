// Shared components (design/DESIGN.md §2). Screens build from these; styles live in ui.css.

import {
  forwardRef,
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
} from 'react'
import QRCodeLib from 'qrcode'
import { rupees } from '../money'
import { Link, useRouter } from '../router'
import { useSession } from '../useSession'
import { CalendarIcon, CameraIcon, ChartIcon, MicIcon, WarnIcon } from './icons'

const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(' ')

// ---------------- layout ----------------

export type Tone = 'light' | 'white' | 'dark' | 'green'
export type TabKey = 'counter' | 'bills' | 'profit' | 'plan'

/** A full-height screen. `tab` shows the tab bar (Counter, Bills in, Profit, Plan only). */
export function Screen({
  tone = 'light',
  tab,
  className,
  children,
  label,
  testId,
}: {
  tone?: Tone
  tab?: TabKey
  className?: string
  children: ReactNode
  label?: string
  testId?: string
}) {
  return (
    <>
      <main className={cx('screen', `screen--${tone}`, tab && 'screen--tabs', className)} aria-label={label} data-testid={testId}>
        <OfflineBanner />
        {children}
      </main>
      {tab && <TabBar active={tab} />}
    </>
  )
}

function OfflineBanner() {
  const { t } = useSession()
  const [online, setOnline] = useState(() => navigator.onLine)
  useEffect(() => {
    const on = () => setOnline(true)
    const off = () => setOnline(false)
    window.addEventListener('online', on)
    window.addEventListener('offline', off)
    return () => {
      window.removeEventListener('online', on)
      window.removeEventListener('offline', off)
    }
  }, [])
  if (online) return null
  return (
    <Banner tone="amber" role="status">
      {t('offlineBanner')}
    </Banner>
  )
}

const TABS: { key: TabKey; to: string; icon: (p: { size?: number }) => ReactNode; label: 'tab_counter' | 'tab_bills' | 'tab_profit' | 'tab_plan' }[] = [
  { key: 'counter', to: '/', icon: MicIcon, label: 'tab_counter' },
  { key: 'bills', to: '/bills-in', icon: CameraIcon, label: 'tab_bills' },
  { key: 'profit', to: '/profit', icon: ChartIcon, label: 'tab_profit' },
  { key: 'plan', to: '/plan', icon: CalendarIcon, label: 'tab_plan' },
]

export function TabBar({ active }: { active: TabKey }) {
  const { t } = useSession()
  return (
    <nav className="tabbar" aria-label={t('mainNav')}>
      {TABS.map(({ key, to, icon: Icon, label }) => (
        <Link key={key} to={to} className={cx('tab', key === active && 'tab--active')} aria-current={key === active ? 'page' : undefined}>
          <Icon size={24} />
          {t(label)}
        </Link>
      ))}
    </nav>
  )
}

// ---------------- buttons ----------------

/** 44×44 icon-only control. Always labelled. A `to` makes it a link. */
export function IconButton({
  label,
  to,
  onClick,
  children,
  tone = 'ink',
}: {
  label: string
  to?: string
  onClick?: () => void
  children: ReactNode
  tone?: 'ink' | 'green' | 'dark'
}) {
  const cls = cx('ibtn', tone === 'green' && 'ibtn--green', tone === 'dark' && 'ibtn--dark')
  if (to)
    return (
      <Link to={to} className={cls} aria-label={label}>
        {children}
      </Link>
    )
  return (
    <button type="button" className={cls} aria-label={label} onClick={onClick}>
      {children}
    </button>
  )
}

type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'light' | 'dark-outline' | 'text' | 'text-light'

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant
  size?: 'lg' | 'md'
  block?: boolean
  to?: string
}

/** Primary (green), secondary (white, outlined), danger (outlined, red text). Disabled says why in its label. */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', size = 'lg', block = true, to, className, children, type = 'button', ...rest },
  ref,
) {
  const { navigate } = useRouter()
  const cls = cx('btn2', `btn2--${variant}`, `btn2--${size}`, block && 'btn2--block', className)
  return (
    <button
      ref={ref}
      type={type}
      className={cls}
      {...rest}
      onClick={(e) => {
        rest.onClick?.(e)
        if (to && !e.defaultPrevented) navigate(to)
      }}
    >
      {children}
    </button>
  )
})

/** The 172px push-to-talk circle. Behaviour lives in useRecorder; this is the look and the a11y. */
export const HoldButton = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & { holding: boolean; label: string; icon?: ReactNode }
>(function HoldButton({ holding, label, icon, className, ...rest }, ref) {
  return (
    <button
      ref={ref}
      type="button"
      className={cx('hold', holding && 'hold--on', className)}
      aria-pressed={holding}
      onContextMenu={(e) => e.preventDefault()}
      {...rest}
    >
      {icon ?? <MicIcon size={holding ? 40 : 44} filled={holding} />}
      <span className="hold__label">{label}</span>
    </button>
  )
})

// ---------------- surfaces ----------------

export function Card({ dark = false, className, children }: { dark?: boolean; className?: string; children: ReactNode }) {
  return <div className={cx('card2', dark && 'card2--dark', className)}>{children}</div>
}

export function ListRow({
  label,
  sub,
  value,
  onClick,
  to,
}: {
  label: ReactNode
  sub?: ReactNode
  value?: ReactNode
  onClick?: () => void
  to?: string
}) {
  const body = (
    <>
      <span className="row2__main">
        <span className="row2__label">{label}</span>
        {sub && <span className="row2__sub">{sub}</span>}
      </span>
      {value != null && <span className="row2__value">{value}</span>}
    </>
  )
  if (to)
    return (
      <Link to={to} className="row2 row2--tap">
        {body}
      </Link>
    )
  if (onClick)
    return (
      <button type="button" className="row2 row2--tap" onClick={onClick}>
        {body}
      </button>
    )
  return <div className="row2">{body}</div>
}

/** Amber = needs attention (and nothing else). Info = mint. A `to` makes the whole banner a link. */
export function Banner({
  tone = 'info',
  icon,
  action,
  to,
  role,
  testId,
  children,
}: {
  tone?: 'amber' | 'info' | 'error'
  testId?: string
  icon?: ReactNode
  action?: string
  to?: string
  role?: 'status' | 'alert'
  children: ReactNode
}) {
  const content = (
    <>
      {icon ?? (tone === 'amber' ? <WarnIcon size={22} className="banner2__icon" /> : null)}
      <span className="banner2__text">{children}</span>
      {action && <span className="banner2__action">{action}</span>}
    </>
  )
  const cls = cx('banner2', `banner2--${tone}`)
  if (to)
    return (
      <Link to={to} className={cls}>
        {content}
      </Link>
    )
  return (
    <div className={cls} role={role} data-testid={testId}>
      {content}
    </div>
  )
}

export function SegmentedControl<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string
  options: { value: T; label: string }[]
  value: T
  onChange: (v: T) => void
}) {
  return (
    <div className="seg" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={o.value === value}
          className={cx('seg__opt', o.value === value && 'seg__opt--on')}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function Chip({ children, tone = 'light' }: { children: ReactNode; tone?: 'light' | 'dark' }) {
  return <span className={cx('chip2', `chip2--${tone}`)}>{children}</span>
}

export function Switch({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      className={cx('switch', checked && 'switch--on')}
      onClick={() => onChange(!checked)}
    >
      <span className="switch__knob" />
    </button>
  )
}

// ---------------- bottom sheet ----------------

const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select,textarea,[tabindex]:not([tabindex="-1"])'

/** Scrim + sheet. role=dialog, focus trapped, Esc closes, focus goes back to whatever opened it. */
export function BottomSheet({
  open,
  onClose,
  label,
  children,
}: {
  open: boolean
  onClose: () => void
  label: string
  children: ReactNode
}) {
  const ref = useRef<HTMLDivElement>(null)
  const opener = useRef<Element | null>(null)
  const closeRef = useRef(onClose)
  useEffect(() => {
    closeRef.current = onClose
  })

  useEffect(() => {
    if (!open) return
    opener.current = document.activeElement
    const sheet = ref.current
    const first = sheet?.querySelector<HTMLElement>('[autofocus],[data-autofocus]') ?? sheet?.querySelector<HTMLElement>(FOCUSABLE)
    first?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        closeRef.current()
      } else if (e.key === 'Tab' && sheet) {
        const els = Array.from(sheet.querySelectorAll<HTMLElement>(FOCUSABLE))
        if (!els.length) return
        const [a, b] = [els[0], els[els.length - 1]]
        if (e.shiftKey && document.activeElement === a) {
          e.preventDefault()
          b.focus()
        } else if (!e.shiftKey && document.activeElement === b) {
          e.preventDefault()
          a.focus()
        }
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      const back = opener.current
      if (back instanceof HTMLElement && back.isConnected) back.focus()
    }
  }, [open])

  if (!open) return null
  return (
    <div className="sheet-layer">
      <div className="sheet-scrim" aria-hidden="true" onClick={onClose} />
      <div className="sheet" role="dialog" aria-modal="true" aria-label={label} ref={ref}>
        <span className="sheet__handle" aria-hidden="true" />
        {children}
      </div>
    </div>
  )
}

// ---------------- money and QR ----------------

/** Paise → ₹ en-IN. `about` marks an estimate. */
export function Money({ paise, about = false, className }: { paise: number | null | undefined; about?: boolean; className?: string }) {
  const { t } = useSession()
  if (paise == null) return <span className={className}>—</span>
  return (
    <span className={cx('money', className)}>
      {about && <span className="money__about">{t('about')} </span>}
      {rupees(paise)}
    </span>
  )
}

/** A UPI intent rendered as a QR, with a white quiet zone. `mark` adds the design's green centre tile; the
 * code then uses error correction level H (about 30% recoverable), so the tile (about 4%) never stops a scan. */
export function QRCode({ payload, size = 252, label, mark = false }: { payload: string; size?: number; label: string; mark?: boolean }) {
  const [src, setSrc] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    const css = getComputedStyle(document.documentElement)
    const color = { dark: css.getPropertyValue('--ink').trim() || '#000000', light: css.getPropertyValue('--surface').trim() || '#ffffff' }
    QRCodeLib.toDataURL(payload, { width: size * 2, margin: 0, errorCorrectionLevel: mark ? 'H' : 'M', color })
      .then((url) => live && setSrc(url))
      .catch(() => live && setSrc(null))
    return () => {
      live = false
    }
  }, [payload, size, mark])
  const img = src ? (
    <img className="qr2" src={src} width={size} height={size} alt={label} data-payload={payload} />
  ) : (
    <div className="qr2 qr2--loading" style={{ width: size, height: size }} role="img" aria-label={label} aria-busy="true" />
  )
  if (!mark) return img
  return (
    <span className="qr2-wrap" style={{ width: size, height: size }}>
      {img}
      <span className="qr2-mark" aria-hidden="true" />
    </span>
  )
}

/** A text field with its label above it. */
export function Field({
  label,
  children,
  hint,
}: {
  label: string
  children: (id: string) => ReactNode
  hint?: ReactNode
}) {
  const id = useId()
  return (
    <div className="field2">
      <label htmlFor={id} className="field2__label">
        {label}
      </label>
      {children(id)}
      {hint && <span className="field2__hint">{hint}</span>}
    </div>
  )
}

export { cx }

/** Fills {PLACEHOLDERS} in a translated string; the filled values are bold, as the design shows them. */
export function Rich({ text, vars }: { text: string; vars: Record<string, ReactNode> }) {
  const parts = text.split(/\{([A-Z_]+)\}/g)
  return (
    <>
      {parts.map((p, i) => (i % 2 === 1 ? <b key={i}>{p in vars ? vars[p] : `{${p}}`}</b> : p))}
    </>
  )
}
