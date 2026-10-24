import test from 'node:test';
import assert from 'node:assert/strict';
import { SHELL_CACHE, SHELL_FILES, cacheableStaticRequest } from '../remote/offline_cache.mjs';

const SCOPE = 'https://example-studio.shipstatic.com/';

test('static Android shell is complete and includes the module graph', () => {
  assert.match(SHELL_CACHE, /^mpt-android-shell-v\d+$/);
  for (const file of ['./', './index.html', './style.css', './main.mjs',
                      './core.mjs', './downloads.mjs', './draft_backup.mjs', './production_modes.mjs', './offline_cache.mjs',
                      './workflow_progress.mjs', './manifest.webmanifest', './icon.svg']) {
    assert.ok(SHELL_FILES.includes(file), file);
  }
  assert.equal(new Set(SHELL_FILES).size, SHELL_FILES.length);
});

test('only same-origin static GET requests without queries can be cached', () => {
  for (const file of SHELL_FILES) {
    const url = new URL(file, SCOPE).href;
    assert.equal(cacheableStaticRequest({url, method:'GET'}, SCOPE), true, file);
  }
  const rejects = [
    ['https://api.github.com/repos/dbrckk/Moneyprinterturbo_perso/actions/runs','GET'],
    ['https://github.com/dbrckk/Moneyprinterturbo_perso/actions/runs/123','GET'],
    ['https://example-studio.shipstatic.com/video.mp4','GET'],
    ['https://example-studio.shipstatic.com/main.mjs?token=secret','GET'],
    ['https://example-studio.shipstatic.com/index.html#secret','GET'],
    ['https://example-studio.shipstatic.com/main.mjs','POST'],
    ['https://other-site.shipstatic.com/','GET'],
    ['file:///etc/passwd','GET'],
  ];
  for (const [url, method] of rejects) {
    assert.equal(cacheableStaticRequest({url, method}, SCOPE), false, url);
  }
  assert.equal(cacheableStaticRequest({}, SCOPE), false);
});

test('service worker caches no private user responses or GitHub credentials', async () => {
  const listeners = new Map();
  const previous = { self: globalThis.self, caches: globalThis.caches };
  globalThis.self = {
    registration: { scope: SCOPE },
    location: { origin: new URL(SCOPE).origin },
    addEventListener: (type, listener) => listeners.set(type, listener),
    skipWaiting: () => Promise.resolve(),
    clients: { claim: () => Promise.resolve() },
  };
  globalThis.caches = {
    open: async () => ({ addAll: async () => {}, put: async () => {} }),
    keys: async () => [],
    delete: async () => true,
    match: async () => null,
  };
  try {
    await import('../remote/sw.mjs');
    assert.deepEqual([...listeners.keys()], ['install', 'activate', 'fetch']);
    for (const url of ['https://api.github.com/repos/foo/actions',
                       'https://example-studio.shipstatic.com/video.mp4']) {
      let called = false;
      listeners.get('fetch')({
        request:{url,method:'GET'},
        respondWith:()=>{called=true;},
      });
      assert.equal(called,false,url);
    }
  } finally {
    globalThis.self = previous.self;
    globalThis.caches = previous.caches;
  }
});
