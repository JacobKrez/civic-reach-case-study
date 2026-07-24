/* CivicReach's network-first service worker. Deliberately no cache: the demo
 * needs its live API/LLM connection and should never show stale assessments. */
self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

// A fetch handler makes the worker installable while preserving live network
// behavior. Failed requests are intentionally not replaced with cached data.
self.addEventListener("fetch", (event) => {
  if (event.request.method === "GET") {
    event.respondWith(fetch(event.request));
  }
});
