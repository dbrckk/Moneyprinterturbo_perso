import test from 'node:test';
import assert from 'node:assert/strict';
import {workflowJobsEndpoint,workflowStage} from '../remote/workflow_progress.mjs';

test('jobs API is restricted to a validated GitHub workflow run ID',()=>{
  assert.equal(workflowJobsEndpoint(12345),
    'https://api.github.com/repos/dbrckk/Moneyprinterturbo_perso/actions/runs/12345/jobs?per_page=100');
  for (const id of [null,0,-1,1.5,'0','NaN','123?token=secret',{},'1/../../secrets'])
    assert.throws(()=>workflowJobsEndpoint(id));
});

function stage(name, status='in_progress', jobStatus='in_progress') {
  return workflowStage([{name:'create-video',status:jobStatus,steps:[
    {name:'Set up job',status:'completed'}, {name,status}]}]);
}

test('real checked-in workflow steps map to truthful stage labels',()=>{
  const cases=[
    ['Validate request before costly installs and optional AI model downloads','Préparation'],
    ['Restore Qwen model cache (subsequent renders)','Modèles IA'],
    ['Install Ollama only for local AI scripts','Modèles IA'],
    ['Install and validate neural French voice (only when selected)','Voix'],
    ['Restore optional Whisper model cache','Sous-titres'],
    ['Generate actual narrated MP4','Encodage vidéo'],
    ['Verify output contains video and audio streams','Vérification'],
    ['Audit synchronization and export visual storyboard','Vérification'],
    ['Direct MP4 for Android (no ZIP)','Publication'],
    ['Downloadable MP4','Publication'],
  ];
  for(const [name,title] of cases) {
    const result=stage(name);
    assert.equal(result.title,title,name);
    assert.ok(result.detail.length>10,name);
    assert.ok(!JSON.stringify(result).includes('token'));
  }
});

test('one known job chosen among unrelated jobs; do not leak other jobs',()=>{
  const result=workflowStage([{name:'unrelated',status:'in_progress',steps:[
    {name:'Potential private token in another job',status:'in_progress'}]},
    {name:'create-video',status:'in_progress',steps:[
    {name:'Generate actual narrated MP4',status:'in_progress'}]}]);
  assert.equal(result.title,'Encodage vidéo');
  assert.equal(workflowStage([{name:'x',status:'in_progress',steps:[]},
    {name:'y',status:'in_progress',steps:[]}]),null);
});

test('queued, between steps, skipped, and completed results are not invented',()=>{
  assert.equal(stage('Generate actual narrated MP4','pending','queued').title,'En attente');
  assert.equal(stage('Generate actual narrated MP4','completed').title,'Préparation GitHub');
  assert.equal(stage('Generate actual narrated MP4','completed','completed'),null);
  assert.equal(stage('Generate actual narrated MP4','completed','failure'),null);
});

test('unknown or malicious step names never appear in displayed stage text',()=>{
  for(const name of ['echo PAT=github_pat_SECRET','User narration: confidential',
                    ''] ){
    const stageName=stage(name);
    assert.equal(stageName.title,'Préparation GitHub');
    assert.ok(!JSON.stringify(stageName).includes(name || 'not-a-real-name'));
  }
});

test('malformed GitHub payloads fall back safely without throwing',()=>{
  for(const jobs of [null,{},'oops',[],Array(101).fill({}),[{name:'create-video'}],
    [{name:'create-video',status:'in_progress',steps:[]}],
    [{name:'create-video',status:'in_progress',steps:'not array'}],
    [{name:'create-video',status:'in_progress',steps:Array(101).fill({})}]] ) {
      const result=workflowStage(jobs);
      if(jobs?.[0]?.steps?.length===0) assert.equal(result.title,'Préparation GitHub');
      else assert.equal(result,null);
  }
});
