import test from 'node:test';
import assert from 'node:assert/strict';
import {segmentsOf, queriesOf, outlineFor, validateDraft, dispatchBody,
 findRun, findRunAcrossPages, RUNS_ENDPOINT, runTitle, workflowRunLink,
 knownRunEndpoint, isExpectedRun, safeError} from '../remote/core.mjs';
const valid = {topic:'Les montagnes',script:'La forêt est vaste. Les montagnes sont hautes.',scene_queries:'forêt | montagne',lang:'fr',aspect:'9:16',resolution:'720',speech_rate:'145',script_mode:'template',voice:'espeak',visuals:'commons',visual_ai:'off',visual_style:'cinematic',caption_style:'kinetic',word_alignment:'estimated',transition:'auto'};
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


test('optional CLIP is validated, dispatched, and forbidden without Commons',()=>{
  assert.equal(dispatchBody({...valid, visual_ai:'clip'}, uid).inputs.visual_ai, 'clip');
  assert.equal(validateDraft({...valid, visual_ai:undefined}).visual_ai, 'off');
  assert.throws(() => validateDraft({...valid, visual_ai:'paid-cloud'}), /Comparaison visuelle IA/);
  assert.throws(() => validateDraft({...valid, visual_ai:'clip', visuals:'abstract'}), /Commons/);
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

test('word alignment is explicit, opt-in, and restricted to kinetic captions', () => {
  assert.equal(validateDraft({...valid,word_alignment:undefined}).word_alignment, 'estimated');
  assert.equal(dispatchBody({...valid,word_alignment:'forced'},uid).inputs.word_alignment,'forced');
  assert.throws(()=>validateDraft({...valid,word_alignment:'forged'}),/alignement/);
  assert.throws(()=>validateDraft({...valid,word_alignment:'forced',caption_style:'classic'}),/progressifs/);
});


test('saved GitHub runs can be resumed directly after leaving recent history', () => {
  const run = {id: 12345, event:'workflow_dispatch',
    display_title:runTitle(uid),head_branch:'main',status:'completed',conclusion:'success'};
  assert.equal(knownRunEndpoint(12345),
    'https://api.github.com/repos/dbrckk/Moneyprinterturbo_perso/actions/runs/12345');
  assert.equal(knownRunEndpoint('12345'), knownRunEndpoint(12345));
  assert.equal(isExpectedRun(run,uid), true);
  for (const changes of [{event:'push'}, {display_title:'Unrelated run'},
                         {head_branch:'other'}, {id:0}]) {
    assert.equal(isExpectedRun({...run,...changes},uid),false);
  }
  for (const bad of [0,-1,'12?token=secret','1.5','abc',null,Number.MAX_SAFE_INTEGER+1]) {
    assert.throws(()=>knownRunEndpoint(bad));
  }
});


test('run validation refuses mismatched repo and workflow on saved IDs',()=>{
  const base={id:234,event:'workflow_dispatch',display_title:runTitle(uid),head_branch:'main',
   repository:{full_name:'dbrckk/Moneyprinterturbo_perso'},path:'.github/workflows/free-video.yml'};
  assert.equal(isExpectedRun(base,uid),true);
  assert.equal(isExpectedRun({...base,path:'other.yml'},uid),false);
  assert.equal(isExpectedRun({...base,repository:{full_name:'attacker/other'}},uid),false);
  assert.equal(isExpectedRun({...base,path:'dbrckk/Moneyprinterturbo_perso/.github/workflows/free-video.yml@refs/heads/main'},uid),true);
});

test('non-AI storyboard templates are translated in all six supported languages', () => {
  const intros = {
    fr:'Découvrons', en:"Let's explore", es:'Descubramos',
    de:'Entdecken wir', it:'Scopriamo', pt:'Vamos explorar',
  };
  for (const [lang, opening] of Object.entries(intros)) {
    const script = outlineFor('  La   montagne  ',lang);
    assert.ok(script.startsWith(opening + ' La montagne.'), lang);
    assert.ok(segmentsOf(script).length >= 4, lang);
    assert.ok(segmentsOf(script).length <= 18, lang);
  }
  assert.match(outlineFor('Le climat','fr'),/sources fiables/);
  assert.match(outlineFor('Les arbres','de'),/zuverlässiger Quellen/);
  assert.match(outlineFor('Los árboles','es'),/fuentes fiables/);
  assert.match(outlineFor('Le montagne','it'),/fonti affidabili/);
  assert.match(outlineFor('Oceano','pt'),/fontes confiáveis/);
  assert.throws(() => outlineFor('Sujet','unrecognized'),/Langue/);
  assert.throws(() => outlineFor('X','fr'),/2 à 140/);
  assert.throws(() => outlineFor('ligne\\nsuivante','fr'),/une seule ligne/);
});

test('Android workflow lookup checks at most three pages only when needed', async () => {
  assert.ok(RUNS_ENDPOINT.includes('per_page=100'));
  const requestId='studio-abcdefab-1234-1234-1234-0123456789ab';
  const irrelevant = Array.from({length:100}, (_,i) => ({
    id:i+1, event:'workflow_dispatch', display_title:'Someone else',
    head_branch:'main',
  }));
  const expected = {...irrelevant[0],id:900001,display_title:runTitle(requestId)};
  const seen=[];
  const found=await findRunAcrossPages(async url => {
    seen.push(url);
    return {workflow_runs:seen.length===1 ? irrelevant : [expected]};
  },requestId);
  assert.equal(found.id,900001);
  assert.equal(seen.length,2);
  assert.ok(seen[0].endsWith('page=1') && seen[1].endsWith('page=2'));
  assert.ok(seen.every(url => !url.includes('token=')));

  let calls=0;
  const empty=await findRunAcrossPages(async () => {
    calls++;
    return {workflow_runs:irrelevant.slice(0,12)};
  },requestId);
  assert.equal(empty,null);
  assert.equal(calls,1);

  calls=0;
  const capped=await findRunAcrossPages(async () => {
    calls++;
    return {workflow_runs:irrelevant};
  },requestId);
  assert.equal(capped,null);
  assert.equal(calls,3);
});

test('stale Android run selections stop additional GitHub API pagination',async () => {
  const requestId='studio-abcdefab-1234-1234-1234-0123456789ab';
  let current=true;
  let count=0;
  const match=await findRunAcrossPages(async () => {
    count++;
    current=false; // The user selected another saved run during the request.
    return {workflow_runs: Array.from({length:100}, (_,id) => ({
      id, event:'workflow_dispatch',display_title:'not our render',head_branch:'main',
    }))};
  },requestId,()=>current);
  assert.equal(match,null);
  assert.equal(count,1);
  await assert.rejects(() => findRunAcrossPages(async()=>({unexpected:true}),requestId),
    /liste de rendus invalide/);
});
