// Keeps the pages you've read readable offline. Pages and data: the network
// first, this copy when it can't be reached. The page's own files: this copy.
const VERSION = "__VERSION__";
const ASSETS = `ecfr-assets-${VERSION}`;
const PAGES = "ecfr-pages";
const PRECACHE = [
  `/static/ecfr.css?v=${VERSION}`,
  `/static/ecfr.js?v=${VERSION}`,
  `/static/prefs.js?v=${VERSION}`,
  "/static/fonts/SourceSerif4-Roman.woff2",
  "/static/fonts/SourceSerif4-Italic.woff2",
  "/static/aliases.json",
  "/static/icon.svg",
];
const NEVER = ["/export/", "/settings", "/sw.js"];
const OFFLINE = `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Offline</title><link rel="stylesheet" href="/static/ecfr.css?v=${VERSION}">
<main><article class="text"><h1>Offline</h1><p>This page hasn’t been read on this computer yet, and the navigator can’t be reached. Pages you have read are still here.</p><p><a href="/">Titles</a></p></article></main>`;

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(ASSETS).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("ecfr-assets-") && k !== ASSETS).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== location.origin) return;
  if (NEVER.some((p) => url.pathname.startsWith(p))) return;
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(caches.match(request).then((hit) => hit || fetch(request).then((response) => keep(ASSETS, request, response))));
    return;
  }
  event.respondWith(
    fetch(request)
      .then((response) => keep(PAGES, request, response))
      .catch(async () => {
        const hit = await caches.match(request, { ignoreVary: true });
        if (hit) return hit;
        if (request.mode === "navigate") return new Response(OFFLINE, { headers: { "Content-Type": "text/html; charset=utf-8" } });
        return Response.error();
      })
  );
});

function keep(name, request, response) {
  if (response.ok && response.type === "basic") {
    const copy = response.clone();
    caches.open(name).then((cache) => cache.put(request, copy));
  }
  return response;
}
