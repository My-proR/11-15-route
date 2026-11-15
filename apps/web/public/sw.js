/* RoutePilot service worker: makes the app installable and lets it open instantly / offline-shell.
   It NEVER caches API calls, auth, or realtime traffic: trips, codes and SOS always come live from the server. */
const VERSION = "rp-v1";
const SHELL = ["/", "/manifest.webmanifest", "/icons/icon-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;       // other origins (API, Supabase, map tiles) go straight to the network
  if (url.pathname.startsWith("/api/")) return;
  if (req.mode === "navigate") {                                                   // pages: network first, cached shell when offline
    e.respondWith(fetch(req).then((r) => { const copy = r.clone(); caches.open(VERSION).then((c) => c.put("/", copy)); return r; }).catch(() => caches.match("/")));
    return;
  }
  if (url.pathname.startsWith("/assets/") || url.pathname.startsWith("/icons/")) {  // hashed build files never change: cache first
    e.respondWith(caches.match(req).then((hit) => hit || fetch(req).then((r) => { const copy = r.clone(); caches.open(VERSION).then((c) => c.put(req, copy)); return r; })));
  }
});
