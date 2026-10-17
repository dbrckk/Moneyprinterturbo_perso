import test from 'node:test';
import assert from 'node:assert/strict';
import {shouldAutoResume,shouldVerifyCompletedRun,createRunSelection,
        isUncertainDispatchFailure,recentUnconfirmedRequest,classifyTrackingError} from '../remote/run_lifecycle.mjs';
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

test('stale history replies cannot update a newly selected run',()=>{
 const session=createRunSelection();const first=session.select();
 assert.equal(session.isCurrent(first),true);
 const next=session.select();assert.equal(session.isCurrent(first),false);
 assert.equal(session.isCurrent(next),true);
 assert.equal(session.current(),next);
});

test('uncertain POST failures never result in automatic retry',()=>{
  for(const error of [{name:'AbortError'},{name:'TypeError'},
                      {httpStatus:408},{httpStatus:429},
                      {httpStatus:500},{httpStatus:502},{httpStatus:503}])
    assert.equal(isUncertainDispatchFailure(error),true);
  for(const error of [null,{name:'ValidationError'},{httpStatus:401},
                      {httpStatus:403},{httpStatus:404},
                      {httpStatus:422},{httpStatus:400},{httpStatus:204}])
    assert.equal(isUncertainDispatchFailure(error),false);
  assert.equal(isUncertainDispatchFailure({name:'TypeError',httpStatus:422}),false);
});

test('ambiguous dispatch is resumable and blocks only fresh same-topic duplicates',()=>{
  const active={...base,state:'Envoi non confirmé',topic:'Montagne',
    createdAt:new Date(now-45_000).toISOString()};
  assert.equal(shouldAutoResume(active,now),true);
  assert.equal(recentUnconfirmedRequest([active],'Montagne',now),active);
  assert.equal(recentUnconfirmedRequest([active],'Océan',now),null);
  assert.equal(recentUnconfirmedRequest([active],'Montagne',now+6*60_000),null);
  assert.equal(recentUnconfirmedRequest([{...active,state:'Réussi'}],'Montagne',now),null);
  assert.equal(recentUnconfirmedRequest(null,'Montagne',now),null);
  assert.equal(recentUnconfirmedRequest([active],'Montagne',NaN),null);
});

test('rate limits and temporary GitHub failures never masquerade as video failure',()=>{
 assert.equal(classifyTrackingError({httpStatus:429}).kind,'rate-limit');
 assert.equal(classifyTrackingError({httpStatus:403,message:'API rate limit exceeded'}).kind,'rate-limit');
 assert.equal(classifyTrackingError({httpStatus:403,message:'Resource not accessible'}).kind,'auth');
 assert.equal(classifyTrackingError({httpStatus:401}).kind,'auth');
 assert.equal(classifyTrackingError({httpStatus:404}).kind,'not-found');
 assert.equal(classifyTrackingError({httpStatus:503}).kind,'temporary');
 assert.equal(classifyTrackingError({name:'AbortError'}).kind,'temporary');
 assert.equal(classifyTrackingError(null).kind,'unknown');
});
