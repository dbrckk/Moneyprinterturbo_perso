import test from 'node:test';
import assert from 'node:assert/strict';

import { productionMode, MODE_DESCRIPTIONS, PRODUCTION_MODES } from '../remote/production_modes.mjs';
import { validateDraft, dispatchBody } from '../remote/core.mjs';

const original = Object.freeze({
  topic: 'Les arbres et les paysages', script: '', scene_queries: '',
  lang: 'fr', aspect: '9:16', resolution: '360',
  speech_rate: '145', script_mode: 'template', voice: 'espeak',
  visuals: 'abstract', visual_ai: 'off',
  visual_style: 'classic', caption_style: 'classic',
  word_alignment: 'estimated', transition: 'cut',
});

test('the first-run recommended creator mode activates real script IA and French neural voice', () => {
  const preset = productionMode(original);
  assert.equal(preset.topic, original.topic);
  assert.equal(preset.script, '');
  assert.equal(preset.lang, 'fr');
  assert.equal(preset.aspect, '9:16');
  assert.equal(preset.script_mode, 'local-ai');
  assert.equal(preset.voice, 'piper-fr-siwis');
  assert.equal(preset.visuals, 'commons');
  assert.equal(preset.resolution, '720');
  assert.equal(preset.visual_style, 'cinematic');
  assert.equal(preset.caption_style, 'kinetic');
  assert.equal(preset.word_alignment, 'estimated');
  assert.equal(preset.visual_ai, 'off');
  assert.equal(preset.transition, 'auto');
  assert.deepEqual(validateDraft(preset), preset);
  assert.equal(dispatchBody(preset, 'studio-12345678-1234-1234-1234-123456789abc')
    .inputs.voice, 'piper-fr-siwis');
  assert.match(MODE_DESCRIPTIONS.recommended, /GitHub Actions/);
});

test('deep mode is explicit and keeps CLIP/Whisper dependencies in compatible modes', () => {
  const result = productionMode(original, 'deep');
  assert.equal(result.visual_ai, 'clip');
  assert.equal(result.visuals, 'commons');
  assert.equal(result.word_alignment, 'forced');
  assert.equal(result.caption_style, 'kinetic');
  assert.equal(result.script_mode, 'local-ai');
  assert.equal(result.voice, 'piper-fr-siwis');
  assert.match(MODE_DESCRIPTIONS.deep, /Expérimental/);
  assert.deepEqual(validateDraft(result), result);
});

test('rapid preview does not download AI models or require Commons', () => {
  const result = productionMode({...original, scene_queries: 'old | scene'}, 'preview');
  assert.equal(result.script_mode, 'template');
  assert.equal(result.voice, 'espeak');
  assert.equal(result.visual_ai, 'off');
  assert.equal(result.visuals, 'abstract');
  assert.equal(result.resolution, '360');
  assert.equal(result.caption_style, 'classic');
  assert.equal(result.word_alignment, 'estimated');
  assert.equal(result.scene_queries, '');
  assert.deepEqual(validateDraft(result), result);
});

test('a handwritten script wins over automatic IA and is never silently erased', () => {
  const oldScript = 'La montagne est magnifique. Le soleil éclaire la vallée.';
  const data = {...original, script:oldScript, scene_queries:'montagne | vallée'};
  for (const name of PRODUCTION_MODES) {
    const result = productionMode(data, name);
    assert.equal(result.script, oldScript);
    assert.equal(result.script_mode, 'template');
    assert.equal(result.topic, data.topic);
    assert.equal(result.aspect, '9:16');
    if(name === 'preview') assert.equal(result.scene_queries, '');
    else assert.equal(result.scene_queries, 'montagne | vallée');
    assert.deepEqual(validateDraft(result), result);
  }
  assert.equal(data.script, oldScript, 'Original saved draft should not be mutated');
});

test('non-French languages never receive an incompatible French voice', () => {
  for(const lang of ['en', 'es', 'de', 'it', 'pt']) {
    for(const mode of PRODUCTION_MODES) {
      const result = productionMode({...original, lang, aspect:'16:9'}, mode);
      assert.equal(result.lang, lang);
      assert.equal(result.aspect, '16:9');
      assert.equal(result.voice, 'espeak');
      assert.deepEqual(validateDraft(result), result);
    }
  }
});

test('invalid modes, language and formats fail before dispatch', () => {
  for(const invalid of ['premium-paid', 'experimental;rm', '', null, 7]) {
    assert.throws(()=>productionMode(original, invalid), /inconnu/);
  }
  for(const overrides of [{lang:'xx'}, {lang:null}, {aspect:'4:3'}, {script:'x'.repeat(5001)}]) {
    assert.throws(()=>productionMode({...original,...overrides}), /invalide|reconnue|impossible|narration|Format/i);
  }
  for(const value of [undefined, null, 'invalid', []]) {
    assert.throws(()=>productionMode(value));
  }
});

test('credential-like extra properties never enter the preset or workflow', () => {
  const values = {...original, token:'github_pat_secret',
                  github_token:'really-secret',
                  html:'<script>alert(1)</script>'};
  const result = productionMode(values, 'recommended');
  assert.ok(!('token' in result));
  assert.ok(!('github_token' in result));
  assert.ok(!('html' in result));
  const payload = dispatchBody(result, 'studio-12345678-1234-1234-1234-123456789abc');
  assert.ok(!JSON.stringify(payload).includes('really-secret'));
  assert.ok(!JSON.stringify(payload).includes('github_pat'));
});
