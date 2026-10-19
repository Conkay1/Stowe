const CACHE_NAME = "stowe-v4";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil((async () => {
  for (const name of await caches.keys()) {
    if (/^stowe-v\d+$/.test(name) && name !== CACHE_NAME) await caches.delete(name);
  }
  await self.clients.claim();
})()));

self.addEventListener("fetch", e => {
  if (e.request.method !== "GET") return;
  const url = new URL(e.request.url);
  if (url.origin !== self.location.origin || !url.pathname.startsWith("/static/")) return;
  e.respondWith(
    fetch(e.request)
      .then(async r => {
        if (r.ok) {
          const cache = await caches.open(CACHE_NAME);
          await cache.put(e.request, r.clone());
        }
        return r;
      })
      .catch(async () => (await caches.open(CACHE_NAME)).match(e.request))
  );
});
