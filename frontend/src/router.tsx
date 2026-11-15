// A small router: the path lives in the URL (so a refresh keeps the draft bill), or in memory inside
// the /demo phone frame so the demo page keeps its own URL.

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

type Router = { path: string; navigate: (to: string, opts?: { replace?: boolean }) => void; back: () => void }

const RouterContext = createContext<Router | null>(null)

export function RouterProvider({ memory = false, children }: { memory?: boolean; children: ReactNode }) {
  const [path, setPath] = useState(() => (memory ? '/' : window.location.pathname))
  const stack = useRef<string[]>([])

  useEffect(() => {
    if (memory) return
    const onPop = () => setPath(window.location.pathname)
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [memory])

  const navigate = useCallback(
    (to: string, opts?: { replace?: boolean }) => {
      if (memory) {
        if (!opts?.replace) stack.current.push(path)
      } else if (opts?.replace) window.history.replaceState(null, '', to)
      else window.history.pushState(null, '', to)
      setPath(to)
      window.scrollTo?.(0, 0)
    },
    [memory, path],
  )

  const back = useCallback(() => {
    if (!memory) return window.history.back()
    setPath(stack.current.pop() ?? '/')
  }, [memory])

  const value = useMemo(() => ({ path, navigate, back }), [path, navigate, back])
  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>
}

export function useRouter(): Router {
  const r = useContext(RouterContext)
  if (!r) throw new Error('useRouter outside RouterProvider')
  return r
}

/** '/bill/:id' against '/bill/abc' -> { id: 'abc' }; null when it doesn't match. */
export function match(pattern: string, path: string): Record<string, string> | null {
  const p = pattern.split('/').filter(Boolean)
  const a = path.replace(/\/+$/, '').split('/').filter(Boolean)
  if (p.length !== a.length) return null
  const out: Record<string, string> = {}
  for (let i = 0; i < p.length; i++) {
    if (p[i].startsWith(':')) out[p[i].slice(1)] = decodeURIComponent(a[i])
    else if (p[i] !== a[i]) return null
  }
  return out
}

/** An in-app link: a real <a href> that navigates without a reload. */
export function Link({ to, children, ...rest }: { to: string; children: ReactNode } & Omit<React.AnchorHTMLAttributes<HTMLAnchorElement>, 'href'>) {
  const { navigate } = useRouter()
  return (
    <a
      href={to}
      {...rest}
      onClick={(e) => {
        rest.onClick?.(e)
        if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return
        e.preventDefault()
        navigate(to)
      }}
    >
      {children}
    </a>
  )
}
