// const CACHE_NAME = "v1";
// const urlsToCache = [
//   "/",
//   "/index.html",
//   "/icons/icon-192.png",
//   "/icons/icon-512.png",
// ];

// self.addEventListener("install", (event) => {
//   event.waitUntil(
//     caches.open(CACHE_NAME).then((cache) => cache.addAll(urlsToCache)),
//   );
// });

// self.addEventListener("fetch", (event) => {
//   event.respondWith(
//     caches
//       .match(event.request)
//       .then((response) => response || fetch(event.request)),
//   );
// });
const CACHE_NAME = "Glow-Flow-v2";

const ASSETS_TO_CACHE = [
  "/",
  "/index.html",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
];
self.addEventListener("install", (event) => {
  console.log("Service Worker installing...");
  event.waitUntil(caches.open(CACHE_NAME).then(() => self.skipWaiting()));
});
self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  if (event.request.mode === "navigate") {
    event.respondWith(
      (async () => {
        const networkResponse = await fetch(event.request);
        return networkResponse;
      })(),
    );
    return;
  }
  event.respondWith(
    caches.match(event.request).then((response) => {
      return response || fetch(event.request);
    }),
  );
});
