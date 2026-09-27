const OFFLINE_HTML = "__OFFLINE_HTML__";

// Safe to activate immediately: this worker never serves an old app bundle or private data.
// Existing documents are not reloaded; the UI offers a refresh when a new build arrives.
self.addEventListener('install', (event) => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);
  if (request.method !== 'GET' || request.mode !== 'navigate' || url.origin !== self.location.origin
      || /^\/(api|mcp)(\/|$)/.test(url.pathname) || /\.[^/]+$/.test(url.pathname)) return;
  event.respondWith(fetch(request).catch(() => new Response(OFFLINE_HTML, {
    status: 503,
    headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' },
  })));
});
