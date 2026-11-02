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

const VERSION = "clx-shell-v7";
const SHELL = [
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
  // network, every time. With no network it gets the offline page --
  // never a copy of a page from earlier. The earlier version kept a copy
  // of every page opened and fell back to it: offline, the console came
  // back as a sign-in form with a raw fetch error under it, a proof link
  // came back with its token, and none of it said "you are offline".
  // Pages are not cached at all, so a page's address (a reset link, a
  // proof token) is never kept on the device either.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(() => caches.match("/offline").then(
        (hit) => hit || new Response(
          "No connection. Nothing on this device is current.",
          { status: 503, headers: { "Content-Type": "text/plain" } })
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
          // Offline and never cached: a plain network error, rather
          // than handing respondWith nothing and throwing in the worker.
          .catch(() => hit || Response.error());
        return hit || live;
      })
    )
  );
});
