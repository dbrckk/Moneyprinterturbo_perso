import test from 'node:test';
import assert from 'node:assert/strict';
import { verifyInput, sceneLabels, positiveLabelScore } from './clip_rank.mjs';

test('validate bounded local candidate list with no raw image uploads', () => {
  const valid = { candidates: [
    { id: 0, prompt: 'mountain landscape', path: '/tmp/a.png' },
    { id: 1, prompt: 'forest', path: '/tmp/b.jpg' },
  ] };
  assert.equal(verifyInput(valid).length, 2);
  assert.throws(() => verifyInput({ candidates: [valid.candidates[0]] }), /Expected/);
  assert.throws(() => verifyInput({ candidates: [valid.candidates[0], {...valid.candidates[0]}] }), /Invalid/);
  assert.throws(() => verifyInput({ candidates: [valid.candidates[0], {...valid.candidates[1], path: 'https://example.com/x.png'}] }), /Invalid/);
  assert.throws(() => verifyInput({ candidates: [valid.candidates[0], {...valid.candidates[1], prompt: 'river; rm -rf /'}] }), /Invalid/);
});

test('CLIP labels compare same target with fixed alternatives', () => {
  const labels = sceneLabels('mountain landscape');
  assert.equal(labels[0], 'a photo of mountain landscape');
  assert.equal(labels.length, 5);
  assert.equal(positiveLabelScore([{label: labels[0], score: 0.7999999}], labels[0]), 0.8);
  assert.throws(() => positiveLabelScore([{label: 'other', score: 1}], labels[0]), /Invalid/);
  assert.throws(() => positiveLabelScore([{label: labels[0], score: Infinity}], labels[0]), /Invalid/);
});
