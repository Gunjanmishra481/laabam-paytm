// Laabam service worker: caches the app shell so it opens instantly and offline.
// User data (/v1, /api, /sandbox) is never cached.
const CACHE = 'laabam-shell-v1'
const SHELL = ['/', '/manifest.webmanifest', '/laabam-logo.svg', '/icon-192.png']

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()))
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url)
  if (event.request.method !== 'GET' || url.origin !== self.location.origin) return
  if (/^\/(v1|api|sandbox)\//.test(url.pathname)) return // always live

  if (url.pathname.startsWith('/assets/')) {
    // Hashed build files never change: cache first.
    event.respondWith(
      caches.match(event.request).then(
        (hit) =>
          hit ||
          fetch(event.request).then((res) => {
            const copy = res.clone()
            caches.open(CACHE).then((c) => c.put(event.request, copy))
            return res
          }),
      ),
    )
    return
  }

  if (event.request.mode === 'navigate') {
    // Pages: network first, cached shell when offline.
    event.respondWith(fetch(event.request).catch(() => caches.match('/')))
  }
})
