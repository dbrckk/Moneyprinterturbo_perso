import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../review_mobile.js', import.meta.url), 'utf8');

async function simulate(items, choices, notes = [], showSummary = false) {
  const elements = new Map();
  const selected = new Map(choices.map((choice, index) => [index, choice]));
  let exportedBlob;
  let clickedName;

  class Element {
    constructor(tag) {
      this.tag = tag;
      this.children = [];
      this.listeners = new Map();
      this.value = '';
      this.textContent = '';
    }
    set id(value) { this._id = value; elements.set(value, this); }
    get id() { return this._id; }
    append(...nodes) { this.children.push(...nodes); }
    setAttribute(name, value) { this[name] = value; }
    addEventListener(type, fn) { this.listeners.set(type, fn); }
    click() { clickedName = this.download; }
    remove() {}
  }

  const doc = {
    body: new Element('body'),
    createElement: tag => new Element(tag),
    createTextNode: txt => ({ textContent: txt }),
    getElementById: id => elements.get(id),
    querySelector: selector => {
      const match = selector.match(/^input\[name="choice-(\d+)"\]:checked$/);
      assert.ok(match, 'unexpected DOM selector');
      const choice = selected.get(Number(match[1]));
      return choice ? { value: choice } : null;
    },
  };
  for (const id of ['review-cases', 'progress', 'export', 'reveal', 'summary']) elements.set(id, new Element(id));
  elements.get('summary').hidden = true;
  elements.get('reveal').disabled = true;
  const runtime = {
    document: doc,
    Blob,
    URL: {
      createObjectURL(blob) { exportedBlob = blob; return 'blob:test'; },
      revokeObjectURL() {},
    },
    setTimeout() {},
  };
  const executable = source.replace('__REVIEW_DATA__', JSON.stringify(items));
  vm.runInNewContext(executable, runtime, { timeout: 1000 });
  notes.forEach((text, i) => { elements.get('notes-' + i).value = text; });
  const mayReveal = !elements.get('reveal').disabled;
  if (showSummary) elements.get('reveal').listeners.get('click')();
  elements.get('export').listeners.get('click')();
  // Blob.text() strips a UTF-8 BOM during decoding. Verify its raw bytes.
  const raw = Buffer.from(await exportedBlob.arrayBuffer());
  return { csv: await exportedBlob.text(),
    hasUtf8Bom: raw.subarray(0, 3).equals(Buffer.from([0xef, 0xbb, 0xbf])),
    filename: clickedName,
    mayReveal,
    summary: elements.get('summary').textContent,
    summaryHidden: elements.get('summary').hidden,
    revealDisabled: elements.get('reveal').disabled,
    visibleText: String(elements.get('review-cases').children.length) };
}

const pair = {
  query: 'forest',
  left: 'data:image/jpeg;base64,/9j/',
  right: 'data:image/jpeg;base64,/9j/',
  left_algorithm: 'clip',
  baseline_url: 'https://commons.wikimedia.org/wiki/File:baseline.jpg',
  clip_url: 'https://commons.wikimedia.org/wiki/File:clip.jpg',
  changed: true,
};

test('blinded left/right votes map back to correct algorithm sources', async () => {
  const result = await simulate([pair, { ...pair, query: 'mountain',
    left_algorithm: 'baseline' }], ['left', 'right']);
  assert.equal(result.filename, 'moneyprinter-human-review.csv');
  assert.match(result.csv, /"forest".*"clip",""/);
  assert.match(result.csv, /"mountain".*"clip",""/);
  assert.equal(result.csv.split('\r\n').filter(Boolean).length, 3);
});

test('unchanged pair exports valid tie and partial reviews remain blank', async () => {
  const result = await simulate([{ ...pair, changed: false },
    { ...pair, query: 'river' }], ['tie', '']);
  assert.match(result.csv, /"forest".*"tie",""/);
  assert.match(result.csv, /"river".*,"",""/);
});

test('reviewer notes starting with CSV formulas are neutralized', async () => {
  const formula = '=HYPERLINK("https://attacker.invalid","click")';
  const result = await simulate([pair], ['right'], [formula]);
  assert.match(result.csv, /"baseline","'=HYPERLINK\(""https:\/\/attacker.invalid"",""click""\)"/);
  assert.equal(result.hasUtf8Bom, true);
});

test('explicit both_bad remains distinct from tie', async () => {
  const result = await simulate([pair], ['both_bad']);
  assert.match(result.csv, /"both_bad",""/);
});


test('results remain blinded until every scene is scored', async () => {
  const partial = await simulate([pair, { ...pair, query: 'river' }], ['left', ''], [], true);
  assert.equal(partial.mayReveal, false);
  assert.equal(partial.summaryHidden, true);
  assert.equal(partial.summary, '');
  const complete = await simulate([pair, { ...pair, query: 'river', changed: false }],
    ['left', 'tie'], [], true);
  assert.equal(complete.mayReveal, true);
  assert.equal(complete.summaryHidden, false);
  assert.equal(complete.revealDisabled, true);
  assert.match(complete.summary, /Images choisies par CLIP : 1/);
  assert.match(complete.summary, /Équivalentes : 1/);
  assert.match(complete.summary, /Part CLIP parmi les préférences décisives : 100 %/);
  assert.match(complete.summary, /ne modifie aucun réglage/);
});

test('ties alone never invent a decisive CLIP win rate', async () => {
  const result = await simulate([{ ...pair, changed: false }], ['tie'], [], true);
  assert.match(result.summary, /Part CLIP parmi les préférences décisives : non calculable/);
  assert.match(result.summary, /0 vote\(s\)/);
});
