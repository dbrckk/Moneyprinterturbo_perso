import test from 'node:test';
import assert from 'node:assert/strict';
import {shouldAutoResume,shouldVerifyCompletedRun} from '../remote/run_lifecycle.mjs';
const now=Date.parse('2026-10-17T08:00:00Z');
const base={id:'studio-12345678-1234-1234-1234-123456789abc',
  createdAt:new Date(now-3600_000).toISOString(),state:'Demande envoyée'};

test('recent pending requests resume; completed and failed ones do not',()=>{
  for(const state of ['Demande envoyée','En attente','Rendu en cours',
                      'queued','in_progress','requested','waiting']) {
    assert.equal(shouldAutoResume({...base,state},now),true);
  }
  for(const state of ['Réussi','failure','cancelled','Échec','completed']) {
    assert.equal(shouldAutoResume({...base,state},now),false);
  }
});

test('corrupted, old or future local state never triggers polling',()=>{
  assert.equal(shouldAutoResume({...base,id:'invalid'},now),false);
  assert.equal(shouldAutoResume({...base,createdAt:'garbage'},now),false);
  assert.equal(shouldAutoResume({...base,createdAt:new Date(now-49*3600_000).toISOString()},now),false);
  assert.equal(shouldAutoResume({...base,createdAt:new Date(now+120_000).toISOString()},now),false);
  assert.equal(shouldAutoResume(null,now),false);
  assert.equal(shouldAutoResume({...base},NaN),false);
});

test('completed run needs a valid GitHub run and request id',()=>{
  assert.equal(shouldVerifyCompletedRun({...base,state:'Réussi',runId:1001}),true);
  for(const id of [null,0,'bad',-1]){
    assert.equal(shouldVerifyCompletedRun({...base,state:'Réussi',runId:id}),false);
  }
  assert.equal(shouldVerifyCompletedRun({...base,state:'Réussi',id:'bad',runId:1001}),false);
});
