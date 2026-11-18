// Offline cache for the app shell. Study data lives in IndexedDB, not here.
// Bump VERSION whenever app files change so clients pick up the new shell.
const VERSION = "v7";
const SHELL = [
  "./", "index.html", "styles.css", "manifest.webmanifest", "icon.svg", "icon-192.png", "icon-512.png",
  "js/app.js", "js/db.js", "js/packs.js", "js/srs.js", "js/ai.js", "vendor/fflate.js",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

// Network-first for same-origin files (so updates show up when online), cache fallback offline.
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  // packs are big and land in IndexedDB; caching them here would store them twice.
  // (The published packs site is same-origin on github.io, so match it too.)
  if (/\/packs\//i.test(url.pathname) || /\.zip$|\/index\.json$/.test(url.pathname)) return;
  if (url.pathname.includes("/api/")) return; // live job status from the laptop server
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        const copy = res.clone();
        caches.open(VERSION).then((c) => c.put(e.request, copy));
        return res;
      })
      .catch(() => caches.match(e.request, { ignoreSearch: true })),
  );
});
