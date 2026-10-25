import test from 'node:test';
import assert from 'node:assert/strict';
import { cancelRunEndpoint, mayCancelRun } from '../remote/cancel_run.mjs';
import { runTitle } from '../remote/core.mjs';
const id='studio-12345678-1234-1234-1234-123456789abc';
const valid={id:42,status:'in_progress',event:'workflow_dispatch',
  display_title:runTitle(id),head_branch:'main',
  repository:{full_name:'dbrckk/Moneyprinterturbo_perso'},
  path:'.github/workflows/free-video.yml'};
test('canonical cancellation URLs cannot contain arbitrary host or token',()=>{
  assert.equal(cancelRunEndpoint(42),'https://api.github.com/repos/dbrckk/Moneyprinterturbo_perso/actions/runs/42/cancel');
  for(const value of [0,-1,'4?token=secret','4/../5',' 4 ','4.0','4e1',true,null,Number.MAX_SAFE_INTEGER+1])
    assert.throws(()=>cancelRunEndpoint(value));
});
test('only the selected, correlated active GitHub run is cancellable',()=>{
  for(const status of ['in_progress','queued','waiting','requested','pending'])
    assert.equal(mayCancelRun({...valid,status},id,42),true);
  for(const status of ['completed','cancelled','failure','success'])
    assert.equal(mayCancelRun({...valid,status},id,42),false);
  assert.equal(mayCancelRun({...valid,id:43},id,42),false);
  assert.equal(mayCancelRun({...valid,display_title:'other'},id,42),false);
  assert.equal(mayCancelRun({...valid,event:'push'},id,42),false);
  assert.equal(mayCancelRun({...valid,repository:{full_name:'other/repo'}},id,42),false);
  assert.equal(mayCancelRun({...valid,path:'.github/workflows/ci.yml'},id,42),false);
  assert.equal(mayCancelRun({...valid,head_branch:'other'},id,42),false);
});
