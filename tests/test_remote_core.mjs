import test from 'node:test';
import assert from 'node:assert/strict';
import {segmentsOf, queriesOf, validateDraft, dispatchBody, findRun, runTitle, workflowRunLink, safeError} from '../remote/core.mjs';
const valid = {topic:'Les montagnes',script:'La forêt est vaste. Les montagnes sont hautes.',scene_queries:'forêt | montagne',lang:'fr',aspect:'9:16',resolution:'720',speech_rate:'145',script_mode:'template',voice:'espeak',visuals:'commons',visual_style:'cinematic',transition:'auto'};
const uid='studio-12345678-1234-1234-1234-123456789abc';
test('segmenter matches Python whitespace and sentence splitting',()=>{
  assert.deepEqual(segmentsOf('Bonjour. Salut !\nNouveau paragraphe.'),['Bonjour.','Salut !','Nouveau paragraphe.']);
  assert.equal(segmentsOf(' ' .repeat(8)).length,0);
  assert.ok(segmentsOf('bonjour '.repeat(20)).every(s=>s.length <=105 || s.split(' ').length===1));
});
test('accepts good settings and exact scene count',()=>{
  assert.deepEqual(queriesOf('A| B'),['A','B']);
  assert.equal(validateDraft(valid).topic,'Les montagnes');
  assert.deepEqual(dispatchBody(valid,uid),{ref:'main',inputs:{...valid,request_id:uid}});
});
test('invalid inputs never reach a workflow',()=>{
  const bad=[{topic:'x'},{script:'a. '.repeat(19)},{script:'x'.repeat(5001)},
    {scene_queries:'A'}, {visuals:'abstract'},{aspect:'5:4'},{resolution:'4320'}];
  for(const patch of bad)assert.throws(()=>validateDraft({...valid,...patch}));
  assert.throws(()=>dispatchBody(valid,'unsafe value'));
});
test('request correlation uses exact run title and default branch',()=>{
  const run={id:123,event:'workflow_dispatch',display_title:runTitle(uid),head_branch:'main',status:'completed',conclusion:'success'};
  assert.equal(findRun([run],uid),run);
  assert.equal(findRun([{...run,head_branch:'other'}],uid),null);
  assert.equal(findRun([{...run,display_title:runTitle(uid)+' x'}],uid),null);
  assert.equal(workflowRunLink(run),'https://github.com/dbrckk/Moneyprinterturbo_perso/actions/runs/123');
  assert.ok(!workflowRunLink({id:'bad'}).includes('bad'));
});
test('human-friendly API errors',()=>{
  assert.match(safeError(403,{}),/Actions/);
  assert.match(safeError(401,{}),/Jeton/);
});

test('real local AI is explicitly selected and rejects mixing with custom scripts',()=>{
  const ai = {...valid,script:'',scene_queries:'',script_mode:'local-ai'};
  assert.equal(validateDraft(ai).script_mode,'local-ai');
  assert.equal(dispatchBody(ai,uid).inputs.script_mode,'local-ai');
  assert.throws(()=>validateDraft({...valid,script_mode:'local-ai'}),/Vide la narration/);
  assert.throws(()=>validateDraft({...ai,script_mode:'paid-cloud'}),/Mode de scénario invalide/);
  assert.equal(validateDraft({...valid,script_mode:'template'}).script_mode,'template');
});


test('neural voice is limited to French and carried to workflow input',()=>{
  const voiced = {...valid,voice:'piper-fr-siwis'};
  assert.equal(validateDraft(voiced).voice,'piper-fr-siwis');
  assert.equal(dispatchBody(voiced,uid).inputs.voice,'piper-fr-siwis');
  assert.throws(()=>validateDraft({...voiced,lang:'en'}),/uniquement en français/);
  assert.throws(()=>validateDraft({...voiced,voice:'cloud-premium'}),/Moteur vocal invalide/);
  assert.equal(validateDraft({...valid,voice:undefined}).voice,'espeak');
});


test('visual style is validated and dispatched',()=>{
  assert.equal(validateDraft({...valid,visual_style:'cinematic'}).visual_style,'cinematic');
  assert.equal(dispatchBody({...valid,visual_style:'classic'},uid).inputs.visual_style,'classic');
  assert.throws(()=>validateDraft({...valid,visual_style:'unknown'}),/Habillage visuel invalide/);
  assert.equal(validateDraft({...valid,visual_style:undefined}).visual_style,'cinematic');
});

test('transition types reject unknown filters and reach GitHub dispatch',()=>{
  assert.equal(validateDraft({...valid, transition:undefined}).transition,'auto');
  for(const kind of ['cut','fade','wipeleft']){
    assert.equal(dispatchBody({...valid,transition:kind},uid).inputs.transition,kind);
  }
  assert.throws(()=>validateDraft({...valid, transition:'movie=/etc/passwd'}),/Transition visuelle invalide/);
});

// Caption style is explicitly checked before any GitHub workflow dispatch.
test('caption style is constrained', () => {
  const base = { topic: 'Test court', script: '', scene_queries: '', lang: 'fr',
    aspect: '9:16', resolution: '360', speech_rate: '145', visuals: 'abstract', caption_style: 'kinetic' };
  assert.equal(validateDraft(base).caption_style, 'kinetic');
  assert.throws(() => validateDraft({...base, caption_style: 'broken'}), /sous-titres/);
});
