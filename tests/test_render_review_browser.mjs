/** Real browser controller loaded with DOM + API stubs: post-export review never
 * marks an unfinished job as ready and never parses metadata as markup.
 */
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../web/app.js', import.meta.url), 'utf8');
const JOB_ID = 'd'.repeat(32);
const injected = '<img src=x onerror="alert(1)">';

async function setup({reportOverride = 'use-report'} = {}) {
  const ids = new Map();
  const requests = [];
  let jobStatus = 'done';
  const report = {
    status: 'exported-not-artistically-approved',
    summary: 'Rapport technique, pas une note esthétique.',
    checks: [
      {key: 'media', title: injected, detail: '1/2 média réel.', attention: true},
      {key: 'voice', title: 'Narration', detail: 'eSpeak synthétique', attention: true},
    ],
    warnings: [injected],
    warnings_total: 1,
    manual_review_required: true,
  };
  class ClassList {
    constructor() { this.items = new Set(); }
    add(x) { this.items.add(x); }
    remove(x) { this.items.delete(x); }
    toggle(x, force) {
      if (force === undefined) force = !this.items.has(x);
      if (force) this.items.add(x); else this.items.delete(x);
      return force;
    }
    contains(x) { return this.items.has(x); }
  }
  class Element {
    constructor(id='') {
      this.id = id;
      this.textContent = '';
      this.value = '';
      this.disabled = false;
      this.hidden = false;
      this.checked = false;
      this.listeners = new Map();
      this.children = [];
      this.classList = new ClassList();
      this.files = [];
      this.style = {};
    }
    set innerHTML(value) { throw new Error('Unsafe HTML injection: ' + value); }
    addEventListener(event, callback) { this.listeners.set(event, callback); }
    querySelector() { return {disabled: false}; }
    replaceChildren(...children) { this.children = [...children]; }
    append(...children) { this.children.push(...children); }
    pause() {}
    load() {}
    removeAttribute(name) { delete this[name]; }
  }
  const get = id => {
    if (!ids.has(id)) ids.set(id, new Element(id));
    return ids.get(id);
  };
  get('script').value = 'Test à deux scènes.';
  const fetch = async (url, options={}) => {
    const method = options.method || 'GET';
    requests.push(method + ' ' + url);
    let data;
    if (url === '/api/health') data = {ok: true};
    else if (url === '/api/config') data = {
      has_ollama: false, has_piper: false, has_local_media: false};
    else if (url === '/api/jobs') data = {
      jobs: [{id: JOB_ID, topic: 'Vidéo test', status: jobStatus}]};
    else if (url === '/api/media/storage') data = {
      bytes: 0, limit_bytes: 1e9, batch_count: 0,
      reclaimable_count: 0, reclaimable_bytes: 0,
      abandoned_bytes: 0, batches: []};
    else if (url === '/api/encoder/calibration') data = {status: 'idle'};
    else if (url === '/api/jobs/' + JOB_ID) data = {
      id: JOB_ID, status: jobStatus,
      render_review: reportOverride === 'use-report' ? report : reportOverride,
      has_credits: false, has_publication: false,
      duration_seconds: 5, visual_mode: 'mixed-media'};
    else throw Error('Unexpected ' + url);
    return {ok: true, status: 200, json: async () => data};
  };
  const ctx = {
    document: {
      getElementById: get, createElement: tag => new Element(tag),
      querySelectorAll: () => [], body: {append() {}},
    },
    localStorage: {getItem() { return null; }, setItem() {}, removeItem() {}},
    sessionStorage: {getItem() { return null; }, setItem() {}},
    window: {addEventListener() {}, confirm() { return false; }},
    fetch, Intl, URL, setTimeout() {}, clearTimeout() {},
  };
  vm.runInNewContext(source, ctx, {timeout: 1800});
  const settle = async () => {
    for (let i=0; i<40; i++) await Promise.resolve();
  };
  await settle();
  const choose = () => {
    assert.equal(get('history').children.length, 1);
    const callback = get('history').children[0].listeners.get('click');
    assert.equal(typeof callback, 'function');
    callback();
  };
  return {get, choose, settle, requests, setStatus: x => { jobStatus = x; }};
}

test('done: displays evidence and warnings only as inert text nodes', async () => {
  const mock = await setup();
  mock.choose();
  await mock.settle();
  assert.equal(mock.get('render-review').classList.contains('hidden'), false);
  assert.equal(mock.get('render-review-checks').children.length, 2);
  const first = mock.get('render-review-checks').children[0];
  assert.equal(first.children[0].textContent, injected);
  assert.equal(first.children[1].textContent, '1/2 média réel.');
  assert.equal(first.className, 'render-review-attention');
  assert.equal(mock.get('render-review-warnings-list').children[0].textContent, injected);
  assert.equal(mock.get('render-review-warnings').classList.contains('hidden'), false);
  assert.match(mock.get('render-review-summary').textContent, /pas une note esthétique/);
  assert.ok(mock.requests.includes('GET /api/jobs/' + JOB_ID));
});

test('error and running jobs must never show stale finished checks', async () => {
  const mock = await setup();
  mock.choose();
  await mock.settle();
  assert.equal(mock.get('render-review').classList.contains('hidden'), false);
  for (const status of ['error', 'running', 'cancelled']) {
    mock.setStatus(status);
    mock.choose();
    await mock.settle();
    assert.equal(mock.get('render-review').classList.contains('hidden'), true, status);
    assert.equal(mock.get('render-review-checks').children.length, 0, status);
    assert.equal(mock.get('render-review-warnings-list').children.length, 0, status);
  }
});

test('missing or malformed reports stay hidden on completed videos', async () => {
  for (const corrupt of [null, {}, {status: 'approved', checks: []},
                         {status: 'exported-not-artistically-approved',
                          checks: [{title: '<script>', detail: 'x', attention: 'yes'}]}]) {
    const mock = await setup({reportOverride: corrupt});
    mock.choose();
    await mock.settle();
    assert.equal(mock.get('render-review').classList.contains('hidden'), true);
    assert.equal(mock.get('render-review-checks').children.length, 0);
    assert.ok(!mock.get('status-badge').textContent.includes('APPROUVÉ'));
  }
});
