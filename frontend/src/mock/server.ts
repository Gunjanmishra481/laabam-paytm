// In-browser preview server: the Laabam API simulated inside the page.
// Used only by the phone preview build (npm run build:preview), never by the real app.
// The rules mirror the Python backend; grow this alongside the real endpoints.

type Handler = (req: Request, match: RegExpMatchArray) => unknown | Promise<unknown>

const routes: Array<[string, RegExp, Handler]> = [
  ['GET', /^\/api\/health$/, () => ({ ok: true, version: 'preview', sandbox: true })],
]

export function installPreviewServer() {
  const realFetch = window.fetch.bind(window)
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const req = new Request(input, init)
    const url = new URL(req.url, window.location.href)
    for (const [method, pattern, handler] of routes) {
      const m = url.pathname.match(pattern)
      if (m && req.method === method) {
        const body = await handler(req, m)
        return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
    }
    if (/^\/(api|v1|sandbox)\//.test(url.pathname)) {
      return new Response(JSON.stringify({ detail: 'Not in preview' }), { status: 404 })
    }
    return realFetch(input, init)
  }
}
