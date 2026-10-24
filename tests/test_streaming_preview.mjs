"";
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../web/app.js', import.meta.url), 'utf8');
const A = 'a'.repeat(32);
const B = 'b'.repeat(32);

async function createApp({ authToken = '', slowBlob = false, jobStatus = 'done',
                           jobProgress = null, wakeRequested = false, wakeActive = false,
                           savedWake = null } = {}) {
  const elements = new Map();
  const calls = [];
  const blobCalls = [];
  const renderRequests = [];
  const storageWrites = [];
  const objectUrls = [];
  const revoked = [];
  const links = [];
  let resolveSlowBlob;
  const history = { jobs: [
    { id: A, topic: 'Video A', status: jobStatus },
    { id: B, topic: 'Video B', status: jobStatus },
  ] };
  class Element {
    constructor(id = '') {
      this.id = id;
      this.listeners = new Map();
      this.children = [];
      this.classes = new Set();
      this.classList = {
        add: name => this.classes.add(name),
        remove: name => this.classes.delete(name),
        contains: name => this.classes.has(name),
        toggle: (name, forced) => {
          if (forced === undefined ? !this.classes.has(name) : forced)
            this.classes.add(name);
          else this.classes.delete(name);
        },
      };
      this.dataset = {};
      this.value = '';
      this.textContent = '';
      this.files = [];
      this.hidden = false;
      this.src = '';
      this.pauses = 0;
      this.loads = 0;
    }
    addEventListener(name, fn) { this.listeners.set(name, fn); }
    append(...items) { this.children.push(...items); }
    replaceChildren() { this.children = []; }
    querySelector() { return { disabled: false }; }
    pause() { this.pauses += 1; }
    load() { this.loads += 1; }
    removeAttribute(key) { if (key === 'src') this.src = ''; }
    click() { links.push({ href: this.href, download: this.download }); }
    remove() {}
  }
  const element = id => {
    if (!elements.has(id)) elements.set(id, new Element(id));
    return elements.get(id);
  };
  element('script').value = 'Scène vidéo.';
  const download = new Element('download');
  download.dataset.file = 'video.mp4';
  const fetch = async (url, options = {}) => {
    calls.push({ url, auth: options.headers?.Authorization || '' });
    let payload = {};
    if (url === '/api/health') payload = { ok: true };
    else if (url === '/api/jobs' && options.method === 'POST') {
      renderRequests.push(JSON.parse(options.body));
      payload = {id: A, status: 'queued'};
    }
    else if (url === '/api/config') payload = { has_ollama: false,
      has_piper: false, has_local_media: false };
    else if (url === '/api/jobs') payload = history;
    else if (url === '/api/media/storage') payload = { bytes: 0, limit_bytes: 1000,
      reclaimable_bytes: 0, reclaimable_count: 0, abandoned_bytes: 0,
      batches: [], batch_count: 0 };
    else if (url === `/api/jobs/${A}` || url === `/api/jobs/${B}`) payload = {
      id: url.endsWith(A) ? A : B, status: jobStatus, duration_seconds: 12,
      visual_mode: 'local', has_credits: false, warnings: [],
      progress: jobProgress,
      keep_awake_requested: wakeRequested, keep_awake_active: wakeActive,
    };
    else if (url === `/api/jobs/${A}/video.mp4` || url === `/api/jobs/${B}/video.mp4`) {
      return { ok: true, status: 200, json: async () => {}, blob: async () => {
        blobCalls.push(url);
        if (slowBlob && url === `/api/jobs/${A}/video.mp4`)
          return new Promise(resolve => { resolveSlowBlob = resolve; });
        return new Blob(['synthetic-mp4']);
      }};
    } else throw new Error(`Unexpected request ${url}`);
    return { ok: true, status: 200, json: async () => payload,
      blob: async () => { throw new Error('No Blob expected'); } };
  };
  const URLMock = {
    createObjectURL() { const uri = `blob:test-${objectUrls.length}`; objectUrls.push(uri); return uri; },
    revokeObjectURL(uri) { revoked.push(uri); },
  };
  const listeners = new Map();
  const context = {
    document: { getElementById: element, createElement: tag => new Element(tag),
      querySelectorAll: selector => selector === '.dl' ? [download] : [],
      body: { append() {} } },
    window: { addEventListener(name, fn) { listeners.set(name, fn); },
      confirm: () => false },
    sessionStorage: { getItem: () => authToken, setItem() {} },
    localStorage: {
      getItem: key => key === 'mpt-termux-wake-render-v1' ? savedWake : null,
      setItem: (key, value) => storageWrites.push({key, value}),
      removeItem() {},
    },
    fetch, URL: URLMock, Blob, Intl, setTimeout() {}, clearTimeout() {},
  };
  vm.runInNewContext(source, context, { timeout: 2000 });
  // Advance initial health -> configuration -> history -> storage promises.
  for (let i = 0; i < 12; i++) await Promise.resolve();
  await new Promise(resolve => setImmediate(resolve));
  async function select(id) {
    const item = element('history').children.find(button =>
      button.listeners.get('click') && button.children[0].textContent === `Video ${id === A ? 'A' : 'B'}`);
    assert.ok(item, `Missing history entry ${id}: calls=${JSON.stringify(calls)}, error=${element('error').textContent}, history=${JSON.stringify(element('history').children.map(x => ({id:x.id,children:x.children?.length,text:x.children?.[0]?.textContent})))}`);
    item.listeners.get('click')();
    for (let i = 0; i < 12; i++) await Promise.resolve();
    await new Promise(resolve => setImmediate(resolve));
  }
  return {
    element, download, calls, blobCalls, renderRequests, storageWrites,
    objectUrls, revoked, links, listeners,
    select, resolveSlowBlob: () => { if (resolveSlowBlob) resolveSlowBlob(new Blob(['late-a'])); },
    async settle() {
      for (let i = 0; i < 12; i++) await Promise.resolve();
      await new Promise(resolve => setImmediate(resolve));
    },
  };
}

test('local preview uses URL streaming without fetching a whole MP4 Blob', async () => {
  const app = await createApp();
  await app.select(A);
  assert.equal(app.element('video').src, `/api/jobs/${A}/video.mp4`);
  assert.equal(app.blobCalls.length, 0);
  assert.equal(app.objectUrls.length, 0);
  assert.ok(app.element('video').loads >= 1);
  await app.select(B);
  assert.equal(app.element('video').src, `/api/jobs/${B}/video.mp4`);
  assert.ok(app.element('video').pauses >= 2);
  assert.ok(app.element('video').loads >= 2);
});

test('local MP4 download uses native same-origin streaming instead of Blob', async () => {
  const app = await createApp();
  await app.select(A);
  await app.download.listeners.get('click')();
  assert.deepEqual(app.links, [{href: `/api/jobs/${A}/video.mp4`,
                                download: `${A.slice(0, 8)}-video.mp4`}]);
  assert.deepEqual(app.blobCalls, []);
});

test('token-protected preview keeps Authorization and Blob compatibility', async () => {
  const app = await createApp({authToken: 'remote-token'});
  await app.select(A);
  assert.equal(app.blobCalls.length, 1);
  assert.equal(app.calls.find(x => x.url === `/api/jobs/${A}/video.mp4`).auth,
               'Bearer remote-token');
  assert.equal(app.element('video').src, 'blob:test-0');
  await app.select(B);
  assert.deepEqual(app.revoked, ['blob:test-0']);
  assert.equal(app.element('video').src, 'blob:test-1');
});

test('obsolete authenticated responses cannot replace newer video selections', async () => {
  const app = await createApp({authToken: 'remote-token', slowBlob: true});
  await app.select(A);
  await app.select(B);
  const expected = app.element('video').src;
  app.resolveSlowBlob();
  await app.settle();
  assert.equal(app.element('video').src, expected);
  assert.equal(app.objectUrls.length, 1);
});

test('native video errors hide the broken preview and free decoder resources', async () => {
  const app = await createApp();
  await app.select(A);
  app.element('video').onerror();
  assert.equal(app.element('video').src, '');
  assert.equal(app.element('video').onerror, null);
  assert.match(app.element('job-notice').textContent, /Aperçu indisponible/);
});

test('pagehide releases native player and object URLs', async () => {
  const app = await createApp({authToken: 'remote-token'});
  await app.select(A);
  app.listeners.get('pagehide')();
  assert.equal(app.element('video').src, '');
  assert.deepEqual(app.revoked, ['blob:test-0']);
});


test('running render reports actual stage, elapsed time and completed stages', async () => {
  const app = await createApp({ jobStatus: 'running', jobProgress: {
    stage: 'encode', step: 4, steps: 6, elapsed_seconds: 167,
  }});
  await app.select(A);
  assert.equal(app.element('render-stage-panel').classList.contains('hidden'), false);
  assert.equal(app.element('render-stage-bar').value, 3);
  assert.equal(app.element('render-stage-time').textContent, '2:47 écoulées');
  assert.match(app.element('render-stage-label').textContent, /Étape 4\/6.*Encodage/);
  assert.match(app.element('job-notice').textContent, /Étape 4\/6/);
  assert.equal(app.blobCalls.length, 0);
});

test('first stage does not imply 1/6 of elapsed encoding is complete', async () => {
  const app = await createApp({ jobStatus: 'running', jobProgress: {
    stage: 'voice', step: 1, steps: 6, elapsed_seconds: 2,
  }});
  await app.select(A);
  assert.equal(app.element('render-stage-bar').value, 0);
  assert.match(app.element('render-stage-label').textContent, /Étape 1\/6/);
});

test('unsafe or missing stage data never enters the render UI', async () => {
  for (const progress of [null, {stage: '<img onerror=alert(1)>', step: 1, steps: 6,
                                   elapsed_seconds: 2},
                          {stage: 'publish', step: 7, steps: 6, elapsed_seconds: 2}]) {
    const app = await createApp({ jobStatus: 'running', jobProgress: progress });
    await app.select(A);
    assert.equal(app.element('render-stage-panel').classList.contains('hidden'), true);
    assert.match(app.element('job-notice').textContent, /Montage vidéo en cours/);
  }
});

test('completed project hides stale stage UI and shows normal MP4 preview', async () => {
  const app = await createApp({ jobStatus: 'done', jobProgress: {
    stage: 'encode', step: 4, steps: 6, elapsed_seconds: 600,
  }});
  await app.select(A);
  assert.equal(app.element('render-stage-panel').classList.contains('hidden'), true);
  assert.equal(app.element('video').src, `/api/jobs/${A}/video.mp4`);
  assert.match(app.element('job-notice').textContent, /Vidéo terminée/);
});


test('anti-sleep control is opt-in and a checked choice reaches the render API', async () => {
  const app = await createApp();
  assert.equal(app.element('keep-awake').checked, false);
  app.element('keep-awake').checked = true;
  app.element('keep-awake').listeners.get('change')();
  assert.deepEqual(app.storageWrites.at(-1), {
    key: 'mpt-termux-wake-render-v1', value: '1',
  });
  await app.element('create-form').listeners.get('submit')({preventDefault() {}});
  assert.equal(app.renderRequests.at(-1)?.keep_awake, true);
});

test('anti-sleep preference persists but is never enabled by default', async () => {
  const restored = await createApp({savedWake: '1'});
  assert.equal(restored.element('keep-awake').checked, true);
  const fresh = await createApp({savedWake: null});
  assert.equal(fresh.element('keep-awake').checked, false);
});

test('anti-sleep status is truthful when the optional Termux commands are unavailable', async () => {
  const unavailable = await createApp({jobStatus: 'running',
    wakeRequested: true, wakeActive: false});
  await unavailable.select(A);
  assert.match(unavailable.element('job-notice').textContent, /Anti-veille Termux indisponible/);
  const active = await createApp({jobStatus: 'running',
    wakeRequested: true, wakeActive: true});
  await active.select(A);
  assert.match(active.element('job-notice').textContent, /Anti-veille Termux demandé et actif/);
});
