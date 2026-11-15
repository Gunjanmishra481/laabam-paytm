// Stroke icons from the design sources. All decorative: the control around them carries the label.

type P = { size?: number; className?: string }
const svg = (size: number, className: string | undefined, children: React.ReactNode) => (
  <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false" className={className}>
    {children}
  </svg>
)
const S = { fill: 'none', stroke: 'currentColor', strokeWidth: 2 } as const

export const MicIcon = ({ size = 24, className, filled = false }: P & { filled?: boolean }) =>
  svg(size, className, <>
    <rect x="9" y="3" width="6" height="11" rx="3" {...S} fill={filled ? 'currentColor' : 'none'} />
    <path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" {...S} strokeLinecap="round" />
  </>)

export const CameraIcon = ({ size = 24, className }: P) =>
  svg(size, className, <>
    <path d="M3 8h4l2-3h6l2 3h4v11H3z" {...S} strokeLinejoin="round" />
    <circle cx="12" cy="13" r="3.5" {...S} />
  </>)

export const ChartIcon = ({ size = 24, className }: P) =>
  svg(size, className, <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" {...S} strokeLinecap="round" />)

export const CalendarIcon = ({ size = 24, className }: P) =>
  svg(size, className, <>
    <rect x="3" y="5" width="18" height="16" rx="2" {...S} />
    <path d="M3 10h18M8 3v4M16 3v4" {...S} strokeLinecap="round" />
  </>)

export const AskIcon = ({ size = 22, className }: P) =>
  svg(size, className, <>
    <path d="M4 5h16v11H9l-5 4z" {...S} strokeLinejoin="round" />
    <path d="M10 9.2a2 2 0 1 1 2.8 1.8c-.5.3-.8.7-.8 1.2M12 14h.01" {...S} strokeLinecap="round" />
  </>)

export const SettingsIcon = ({ size = 22, className }: P) =>
  svg(size, className, <>
    <circle cx="12" cy="12" r="3" {...S} />
    <path
      d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1"
      {...S}
      strokeLinecap="round"
    />
  </>)

export const WarnIcon = ({ size = 22, className }: P) =>
  svg(size, className, <>
    <path d="M12 3l9.5 17h-19z" {...S} strokeLinejoin="round" />
    <path d="M12 10v4M12 17h.01" {...S} strokeLinecap="round" />
  </>)

export const TickIcon = ({ size = 24, className }: P) =>
  svg(size, className, <path d="M5 12.5l4.5 4.5L19 7.5" {...S} strokeWidth={2.6} strokeLinecap="round" strokeLinejoin="round" />)

export const BackIcon = ({ size = 22, className }: P) =>
  svg(size, className, <path d="M15 5l-7 7 7 7" {...S} strokeLinecap="round" strokeLinejoin="round" />)

export const KeyboardIcon = ({ size = 24, className }: P) =>
  svg(size, className, <>
    <rect x="2.5" y="6" width="19" height="12" rx="2" {...S} />
    <path d="M6.5 10h.01M10 10h.01M13.5 10h.01M17 10h.01M8 14h8" {...S} strokeLinecap="round" />
  </>)

/** The design's back chevron (20px box). */
export const ChevronBackIcon = ({ size = 20, className }: P) => (
  <svg width={size} height={size} viewBox="0 0 20 20" aria-hidden="true" focusable="false" className={className}>
    <path d="M12.5 4.5L7 10l5.5 5.5" {...S} strokeLinecap="round" strokeLinejoin="round" />
  </svg>
)

/** The Laabam mark: a rising line on saffron. */
export const LogoMark = ({ size = 40 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 40 40" aria-hidden="true" focusable="false">
    <rect width="40" height="40" rx="12" fill="var(--saffron)" />
    <path d="M10 27l7-7 5 4 8-10" fill="none" stroke="var(--green-900)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
    <path d="M24 14h6v6" fill="none" stroke="var(--green-900)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
)
