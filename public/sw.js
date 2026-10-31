/* Qist service worker: offline app shell + cached pages. Bump VERSION to force an update. */
const VERSION = 'v2';
const STATIC = 'qist-static-' + VERSION, PAGES = 'qist-pages-' + VERSION;
const SHELL = ['/offline.html', '/css/style.css', '/js/app.js', '/js/pwa.js', '/manifest.json',
  '/favicon.svg', '/icons/icon-192.png', '/icons/icon-512.png', '/icons/apple-touch-icon.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(STATIC).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => ![STATIC, PAGES].includes(k)).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('message', e => {
  if (e.data === 'clear-pages') e.waitUntil(caches.delete(PAGES));   // sent on logout
});

self.addEventListener('fetch', e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== 'GET') return;                        // POSTs are handled by the page (offline queue)
  if (url.origin !== location.origin) {                    // Google Fonts: cache after first use
    if (/fonts\.(googleapis|gstatic)\.com$/.test(url.host)) e.respondWith(swr(req, STATIC));
    return;
  }
  if (url.pathname.startsWith('/api/')) return;            // never cache API calls
  if (req.mode === 'navigate' || req.headers.get('X-Warm') === '1') { e.respondWith(pageFirstNetwork(req)); return; }
  if (/\.(css|js|png|svg|json|ico|woff2?)$/.test(url.pathname)) e.respondWith(swr(req, STATIC));
});

function swr(req, cacheName) {                              // stale-while-revalidate
  return caches.open(cacheName).then(cache => cache.match(req).then(hit => {
    const net = fetch(req).then(res => { if (res && (res.ok || res.type === 'opaque')) cache.put(req, res.clone()); return res; }).catch(() => hit);
    return hit || net;
  }));
}

function pageFirstNetwork(req) {                            // network first, then last saved copy
  return fetchWithTimeout(req, 4000).then(res => {
    const ct = res.headers.get('content-type') || '';
    if (res.ok && !res.redirected && ct.includes('text/html')) {
      const copy = res.clone(); const key = new Request(req.url); caches.open(PAGES).then(c => c.put(key, copy));
    }
    return res;
  }).catch(() => caches.open(PAGES).then(c => c.match(new Request(req.url), { ignoreVary: true })).then(hit => hit || caches.match('/offline.html')));
}

function fetchWithTimeout(req, ms) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error('timeout')), ms);
    fetch(req).then(r => { clearTimeout(t); resolve(r); }, err => { clearTimeout(t); reject(err); });
  });
}
