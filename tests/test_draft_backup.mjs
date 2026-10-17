import test from 'node:test';
import assert from 'node:assert/strict';
import { BACKUP_FIELDS, exportPortableDraft, importPortableDraft }
  from '../remote/draft_backup.mjs';

const valid = Object.fromEntries(BACKUP_FIELDS.map(name => [name, '']));
Object.assign(valid, {
  topic: 'Paysages naturels', script:'La forêt. La rivière.',
  script_mode:'template', voice:'espeak',lang:'fr', visuals:'commons',
  aspect:'9:16', resolution:'720', speech_rate:'145', caption_style:'kinetic',
  word_alignment:'estimated',visual_style:'cinematic', transition:'fade', visual_ai:'off'
});

test('portable backups round-trip all fields without GitHub secrets', () => {
  const state={...valid, token:'github_pat_secret', password:'other-secret',
               run_history:[{run_id:44}]};
  const json=exportPortableDraft(state);
  assert.ok(!json.includes('github_pat_secret'));
  assert.ok(!json.includes('other-secret'));
  assert.ok(!json.includes('run_history'));
  assert.deepEqual(importPortableDraft(json), valid);
});

test('old portable backup gains the safe disabled CLIP default', () => {
  const old = {...valid}; delete old.visual_ai;
  const text = JSON.stringify({format:'mpt-draft-v1', draft:old});
  assert.equal(importPortableDraft(text).visual_ai, 'off');
  assert.equal(exportPortableDraft(old).includes('"visual_ai": "off"'), true);
});

test('malformed and oversized backups are refused', () => {
  for (const invalid of ['', 'not json','{"format":"other"}',
                         JSON.stringify({format:'mpt-draft-v1',draft:{...valid,topic: 'x'.repeat(300)}}),
                         JSON.stringify({format:'mpt-draft-v1',draft:{...valid,visuals:'invalid'}})]) {
    assert.throws(()=>importPortableDraft(invalid));
  }
});

test('injected properties cannot enter selected form controls', () => {
  const malicious={format:'mpt-draft-v1',draft:{...valid,
    token:'github_pat_secret',__proto__:{polluted:true}}};
  const parsed=importPortableDraft(JSON.stringify(malicious));
  assert.equal(Object.hasOwn(parsed,'token'),false);
  assert.equal(Object.getPrototypeOf(parsed),Object.prototype);
});
