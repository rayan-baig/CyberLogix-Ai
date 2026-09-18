/* The installed app's service worker.
 *
 * One rule matters more than every other line in this file: NOTHING
 * UNDER /api IS EVER SERVED FROM CACHE. This application exists to say
 * whether a freezer is cold. An offline-first cache that answered a
 * stale "everything is fine" would be worse than no app at all -- the
 * one failure the product is sold to prevent, delivered by the thing
 * sold to prevent it.
 *
 * So the cache holds the shell: the HTML, the stylesheet, the icons.
 * Data is network-only, and when the network is gone the page is told,
 * loudly, that what it is showing is not live.
 */

const VERSION = "clx-shell-v2";
const SHELL = [
  "/console",
  "/static/theme.css",
  "/static/logo.svg",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/offline",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(VERSION)
      // addAll is all-or-nothing: one 404 and the whole install fails,
      // leaving no worker at all. Each entry is allowed to miss.
      .then((cache) => Promise.allSettled(SHELL.map((url) => cache.add(url))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Never cached, never served stale. A temperature, an incident, a
  // licence state: all of it has to come off the wire or not at all.
  if (url.pathname.startsWith("/api/")) return;

  // Opening the app is a navigation, and a navigation goes to the
  // network first. Cache-first was the first version and it was wrong:
  // it serves the shell that was cached at install, so a deploy would
  // not be picked up until the second launch after it -- including a
  // deploy that fixed something. The cache is the fallback, and the
  // offline page is the fallback to that.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response && response.ok) {
            const copy = response.clone();
            caches.open(VERSION).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => caches.match(request).then(
          (hit) => hit || caches.match("/offline")
        ))
    );
    return;
  }

  // Everything else -- stylesheet, icons, scripts -- is cache first so
  // the app paints instantly, revalidated behind the render.
  event.respondWith(
    caches.open(VERSION).then((cache) =>
      cache.match(request).then((hit) => {
        const live = fetch(request)
          .then((response) => {
            if (response && response.ok) cache.put(request, response.clone());
            return response;
          })
          .catch(() => hit);
        return hit || live;
      })
    )
  );
});
