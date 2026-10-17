/** Offline static shell only: excludes GitHub API, MP4s, tokens and user data. */
import { SHELL_CACHE, SHELL_FILES, cacheableStaticRequest } from './offline_cache.mjs';

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(SHELL_CACHE)
      .then(cache => cache.addAll(SHELL_FILES.map(file => new URL(file, self.registration.scope).href)))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys
        .filter(name => name.startsWith('mpt-android-shell-') && name !== SHELL_CACHE)
        .map(name => caches.delete(name))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  if (!cacheableStaticRequest(event.request, self.registration.scope)) return;
  event.respondWith((async () => {
    try {
      const response = await fetch(event.request);
      if (response.ok && response.type !== 'opaque') {
        // Use the network first for immediate updates. Cache write failure
        // must never turn a successful online page load into an error.
        event.waitUntil(caches.open(SHELL_CACHE)
          .then(cache => cache.put(event.request, response.clone()))
          .catch(() => {}));
      }
      return response;
    } catch {
      return (await caches.match(event.request)) || Response.error();
    }
  })());
});
