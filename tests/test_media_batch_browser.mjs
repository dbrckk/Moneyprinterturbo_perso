"";
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../web/app.js', import.meta.url), 'utf8');
const OLD = 'a'.repeat(32);
const NEW = 'b'.repeat(32);

async function simulate({ oldSealed = true, oldMissing = false,
                          rejectNewDetails = false, uploadFailure = '',
                          authToken = '', xhrAvailable = true,
                          failStorageAfterUpload = false,
                          failCleanup = false, calibrateClick = false } = {}) {
  const elements = new Map();
  const calls = [];
  let calibrationReady = false;
  const progressNotes = [];
  const progressValues = [];
  const uploadHeaders = [];
  const memory = new Map([['mpt-local-media-batch-v1', OLD]]);
  class Element {
    constructor(id) {
      this.id = id;
      this._value = '';
      this._textContent = '';
      this.checked = false;
      this.disabled = false;
      this.hidden = false;
      this.listeners = new Map();
      this.classList = { add() {}, remove() {}, toggle() {} };
      this.files = [];
    }
    get value() { return this._value; }
    set value(value) {
      this._value = value;
      if (this.id === 'upload-progress') progressValues.push(value);
    }
    get textContent() { return this._textContent; }
    set textContent(value) {
      this._textContent = value;
      if (this.id === 'media-note') progressNotes.push(value);
    }
    addEventListener(event, fn) { this.listeners.set(event, fn); }
    querySelector() { return { disabled: false }; }
    append() {}
    replaceChildren() {}
  }
  const get = id => {
    if (!elements.has(id)) elements.set(id, new Element(id));
    return elements.get(id);
  };
  get('script').value = 'Une seule scène.';
  get('upload-progress').hidden = true;
  get('media-files').files = [Object.assign(new Blob(['photo-data']), { name: 'photo.jpg' })];
  const fetch = async (url, options = {}) => {
    const method = options.method || 'GET';
    calls.push(`${method} ${url}`);
    let data = {};
    let status = 200;
    if (url === '/api/health') data = { ok: true };
    else if (url === '/api/config') data = { has_ollama: false, has_piper: false,
                                            has_local_media: false };
    else if (url === '/api/jobs') data = { jobs: [] };
    else if (method === 'GET' && url === '/api/encoder/calibration') {
      data = calibrationReady
        ? {available: true, state: 'ready', encoded_fps: {detail: 48.0}}
        : {available: false, state: 'idle'};
    }
    else if (method === 'POST' && url === '/api/encoder/calibration') {
      calibrationReady = true;
      data = {available: false, state: 'running'};
      status = 202;
    }
    else if (url === '/api/media/storage') {
      if (failStorageAfterUpload && calls.includes('POST /api/media/batches')) {
        status = 503;
        data = {error: 'Temporary storage stats failure'};
      } else data = { bytes: 0, limit_bytes: 1000000,
        batch_count: 2, reclaimable_count: 0, reclaimable_bytes: 0,
        abandoned_bytes: 0, batches: [] };
    }
    else if (method === 'POST' && url === '/api/media/batches') {
      data = { id: NEW }; status = 201;
    } else if (method === 'POST' && url === `/api/media/batches/${NEW}/files`) {
      data = { name: '001_photo.jpg' }; status = 201;
    } else if (method === 'GET' && url === `/api/media/batches/${OLD}`) {
      if (oldMissing) { status = 503; data = { error: 'Temporary lookup failure' }; }
      else data = { id: OLD, sealed: oldSealed, files: [{ name: 'old.mp4' }] };
    } else if (method === 'GET' && url === `/api/media/batches/${NEW}`) {
      if (rejectNewDetails) { status = 503; data = { error: 'New batch unverified' }; }
      else data = { id: NEW, ordering: 'name', files: [{ name: '001_photo.jpg' }], sealed: false };
    } else if (method === 'POST' && url.endsWith('/delete')) {
      if (failCleanup) { status = 503; data = {error: 'Deletion temporarily unavailable'}; }
      else data = { deleted: true };
    }
    else throw new Error(`Unexpected request: ${method} ${url}`);
    return { ok: status >= 200 && status < 300, status, json: async () => data };
  };
  class MockXHR {
    constructor() { this.upload = {}; this.headers = {}; this.status = 0; }
    open(method, url) { this.method = method; this.url = url; }
    setRequestHeader(key, value) { this.headers[key] = value; }
    async send(file) {
      uploadHeaders.push({...this.headers});
      const progress = loaded => this.upload.onprogress?.({
        loaded, total: file.size, lengthComputable: true });
      progress(Math.floor(file.size / 2));
      if (uploadFailure === 'network') { this.onerror?.(); return; }
      if (uploadFailure === 'timeout') { this.ontimeout?.(); return; }
      if (uploadFailure === 'abort') { this.onabort?.(); return; }
      if (uploadFailure === 'http') {
        this.status = 401;
        this.responseText = JSON.stringify({error: 'Jeton d’accès requis'});
        this.onload?.();
        return;
      }
      try {
        const response = await fetch(this.url, {
          method: this.method, body: file, headers: this.headers });
        this.status = response.status;
        this.responseText = JSON.stringify(await response.json());
        progress(file.size);
        this.onload?.();
      } catch (_) { this.onerror?.(); }
    }
  }
  const localStorage = { getItem: name => memory.get(name) || null,
    setItem: (name, value) => memory.set(name, value),
    removeItem: name => memory.delete(name) };
  const context = { document: { getElementById: get,
        createElement: tag => new Element(tag), querySelectorAll: () => [] },
    window: { addEventListener() {}, confirm() { return false; } },
    localStorage, sessionStorage: { getItem: () => authToken || null, setItem() {} },
    Blob, Intl, fetch, URL, setTimeout() {}, clearTimeout() {} };
  if (xhrAvailable) context.XMLHttpRequest = MockXHR;
  vm.runInNewContext(source, context, { timeout: 2000 });
  // Allow init() and the API responses to settle before the user upload.
  for (let i = 0; i < 6; i++) await Promise.resolve();
  if (calibrateClick) {
    const calibrate = get('calibrate-encoder').listeners.get('click');
    assert.equal(typeof calibrate, 'function');
    await calibrate();
  }
  const submit = get('upload-media').listeners.get('click');
  assert.equal(typeof submit, 'function');
  await submit();
  return { calls, stored: memory.get('mpt-local-media-batch-v1'),
    error: get('error').textContent, note: get('media-note').textContent,
    selected: get('visuals').value, progressNotes, progressValues,
    progressHidden: get('upload-progress').hidden, uploadHeaders,
    calibratedProfile: get('encoder-profile').value,
    calibrationText: get('encoder-calibration-status').textContent };
}

test('one-tap calibration opts into auto without deleting existing phone imports', async () => {
  const result = await simulate({calibrateClick: true, oldSealed: true});
  assert.equal(result.calibratedProfile, 'auto');
  assert.match(result.calibrationText, /Étalonnage disponible/);
  assert.ok(result.calls.includes('POST /api/encoder/calibration'));
  assert.ok(result.calls.includes('GET /api/encoder/calibration'));
  assert.equal(result.stored, NEW);
  assert.ok(!result.calls.includes(`POST /api/media/batches/${OLD}/delete`));
});

test('sealed previous batch is preserved after a successful new upload', async () => {
  const result = await simulate({ oldSealed: true });
  assert.equal(result.stored, NEW);
  assert.equal(result.selected, 'local');
  assert.ok(result.calls.includes(`GET /api/media/batches/${OLD}`));
  assert.ok(!result.calls.includes(`POST /api/media/batches/${OLD}/delete`));
  assert.ok(!result.calls.includes(`POST /api/media/batches/${NEW}/delete`));
});

test('only an explicitly unsealed prior draft is retired', async () => {
  const result = await simulate({ oldSealed: false });
  assert.equal(result.stored, NEW);
  assert.ok(result.calls.includes(`POST /api/media/batches/${OLD}/delete`));
  assert.ok(!result.calls.includes(`POST /api/media/batches/${NEW}/delete`));
});

test('failed previous-batch lookup preserves originals instead of guessing', async () => {
  const result = await simulate({ oldMissing: true });
  assert.equal(result.stored, NEW);
  assert.ok(!result.calls.includes(`POST /api/media/batches/${OLD}/delete`));
});

test('failed verification of new batch preserves old selection and cleans only new batch', async () => {
  const result = await simulate({ rejectNewDetails: true });
  assert.equal(result.stored, OLD);
  assert.ok(!result.calls.includes(`POST /api/media/batches/${OLD}/delete`));
  assert.ok(result.calls.includes(`POST /api/media/batches/${NEW}/delete`));
  assert.match(result.error, /New batch unverified/);
});


test('Android binary upload reports incremental progress and retains authentication headers', async () => {
  const result = await simulate({ authToken: 'session-test-token' });
  assert.equal(result.stored, NEW);
  assert.ok(result.progressNotes.some(message => message.includes('50 %')));
  assert.ok(result.progressNotes.some(message =>
    message.includes('100 % · vérification du média')));
  assert.ok(result.progressValues.includes(50));
  assert.ok(result.progressValues.includes(100));
  assert.equal(result.progressHidden, true);
  assert.equal(result.uploadHeaders[0].Authorization, 'Bearer session-test-token');
  assert.equal(result.uploadHeaders[0]['Content-Type'], 'application/octet-stream');
  assert.equal(result.uploadHeaders[0]['X-Media-Name'], 'photo.jpg');
});

for (const [failure, message] of [
  ['network', /Connexion interrompue/],
  ['timeout', /Délai d’import dépassé/],
  ['abort', /Import interrompu/],
  ['http', /Jeton d’accès requis/],
]) {
  test(`failed Android binary upload (${failure}) preserves previous batch`, async () => {
    const result = await simulate({ uploadFailure: failure });
    assert.equal(result.stored, OLD);
    assert.equal(result.progressHidden, true);
    assert.match(result.error, message);
    assert.ok(result.calls.includes(`POST /api/media/batches/${NEW}/delete`));
    assert.ok(!result.calls.includes(`POST /api/media/batches/${OLD}/delete`));
  });
}

test('browser without XHR uses previous safe fetch path', async () => {
  const result = await simulate({ xhrAvailable: false });
  assert.equal(result.stored, NEW);
  assert.ok(result.calls.includes(`POST /api/media/batches/${NEW}/files`));
  assert.ok(result.progressNotes.some(x => x.includes('0 %')));
});


test('successful upload survives a subsequent storage-panel 503 without deletion', async () => {
  const result = await simulate({ failStorageAfterUpload: true });
  assert.equal(result.stored, NEW);
  assert.equal(result.error, '');
  assert.ok(!result.calls.includes(`POST /api/media/batches/${NEW}/delete`));
  assert.match(result.note, /prêts pour le montage local/);
});

test('failed cleanup does not falsely claim an incomplete batch was deleted', async () => {
  const result = await simulate({ uploadFailure: 'network', failCleanup: true });
  assert.equal(result.stored, OLD);
  assert.ok(result.calls.includes(`POST /api/media/batches/${NEW}/delete`));
  assert.match(result.note, /Vérifiez le stockage/);
  assert.doesNotMatch(result.note, /lot incomplet supprimé/);
});
