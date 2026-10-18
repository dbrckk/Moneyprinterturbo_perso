import test from 'node:test';
import assert from 'node:assert/strict';
import { MP4_ARTIFACT_NAME, STORYBOARD_ARTIFACT_NAME,
  SUBTITLES_ARTIFACT_NAME, QUALITY_ARTIFACT_NAME,
  CREDITS_ARTIFACT_NAME, MANIFEST_ARTIFACT_NAME,
  findDirectMp4, findDirectStoryboard, findDirectSubtitles, findDirectQuality,
  findDirectCredits, findDirectManifest,
  directMp4Url, runArtifactsEndpoint } from '../remote/downloads.mjs';

test('only nonexpired, nonempty v7 MP4 artifact is eligible', () => {
  const items = [
    {id:2, name:'free-studio-video', expired:false, size_in_bytes:300},
    {id:3, name:MP4_ARTIFACT_NAME, expired:true, size_in_bytes:220},
    {id:4, name:MP4_ARTIFACT_NAME, expired:false, size_in_bytes:0},
    {id:5, name:MP4_ARTIFACT_NAME, expired:false, size_in_bytes:740},
  ];
  assert.equal(findDirectMp4(items).id, 5);
  assert.equal(findDirectMp4(items.slice(0,3)), null);
  assert.equal(findDirectMp4({}), null);
});

test('run API endpoint and direct link are GitHub-owned and numeric', () => {
  assert.equal(runArtifactsEndpoint(123),
    'https://api.github.com/repos/dbrckk/Moneyprinterturbo_perso/actions/runs/123/artifacts?per_page=100');
  assert.equal(directMp4Url(123, 456),
    'https://github.com/dbrckk/Moneyprinterturbo_perso/actions/runs/123/artifacts/456');
  for (const invalid of [0, -3, '', '1&token=abc', 'https://evil.test', Number.MAX_SAFE_INTEGER+1,
    true, false, null, undefined, 1.5, '1.0', '1e3', ' 1', '01', '+1', 'Infinity', {}, []]) {
    assert.throws(() => runArtifactsEndpoint(invalid));
    assert.throws(() => directMp4Url(123, invalid));
  }
});

test('storyboard preview is offered only for nonexpired individual PNG artifacts', () => {
  assert.equal(STORYBOARD_ARTIFACT_NAME, 'video.storyboard.png');
  assert.equal(findDirectStoryboard([{id:44,name:STORYBOARD_ARTIFACT_NAME,
    expired:false,size_in_bytes:11000}]).id,44);
  assert.equal(findDirectStoryboard([{id:45,name:STORYBOARD_ARTIFACT_NAME,
    expired:true,size_in_bytes:11000}]),null);
  assert.equal(findDirectStoryboard([{id:45,name:'free-studio-video',
    expired:false,size_in_bytes:11000}]),null);
  assert.equal(findDirectStoryboard(null),null);
});


test('optional unzipped subtitle and audio QA artifacts are separately validated', () => {
  const artifacts = [
    {id: 70, name: SUBTITLES_ARTIFACT_NAME, expired:false, size_in_bytes:590},
    {id: 71, name: QUALITY_ARTIFACT_NAME, expired:false, size_in_bytes:2500},
    {id: 72, name: SUBTITLES_ARTIFACT_NAME, expired:true, size_in_bytes:590},
  ];
  assert.equal(SUBTITLES_ARTIFACT_NAME,'video.srt');
  assert.equal(QUALITY_ARTIFACT_NAME,'video.quality.json');
  assert.equal(findDirectSubtitles(artifacts).id,70);
  assert.equal(findDirectQuality(artifacts).id,71);
  assert.equal(findDirectSubtitles([{id:0,name:SUBTITLES_ARTIFACT_NAME,
    expired:false,size_in_bytes:900}]),null);
  assert.equal(findDirectQuality([{id:71,name:QUALITY_ARTIFACT_NAME,
    expired:false,size_in_bytes:0}]),null);
  assert.equal(findDirectQuality(null),null);
  assert.equal(findDirectSubtitles([]),null);
  assert.ok(directMp4Url(44,71).includes('github.com/dbrckk/'));
});

test('direct publisher attribution and project manifest require valid unexpired nonempty artifacts',()=>{
  const assets=[
    {id:91,name:CREDITS_ARTIFACT_NAME,expired:false,size_in_bytes:180},
    {id:92,name:MANIFEST_ARTIFACT_NAME,expired:false,size_in_bytes:2900},
    {id:93,name:CREDITS_ARTIFACT_NAME,expired:true,size_in_bytes:300},
    {id:94,name:MANIFEST_ARTIFACT_NAME,expired:false,size_in_bytes:0},
  ];
  assert.equal(CREDITS_ARTIFACT_NAME,'video.credits.txt');
  assert.equal(MANIFEST_ARTIFACT_NAME,'video.json');
  assert.equal(findDirectCredits(assets).id,91);
  assert.equal(findDirectManifest(assets).id,92);
  assert.equal(findDirectCredits(assets.slice(2)),null);
  assert.equal(findDirectManifest(assets.slice(3)),null);
  assert.equal(findDirectCredits(null),null);
  assert.equal(findDirectManifest([]),null);
  assert.equal(findDirectCredits([{id:0,name:CREDITS_ARTIFACT_NAME,expired:false,size_in_bytes:4}]),null);
});

test('artifact metadata must be a positive integer byte count, never a coerced string',()=>{
  const valid={id:9001,name:MP4_ARTIFACT_NAME,expired:false,size_in_bytes:1024};
  assert.equal(findDirectMp4([valid]).id,9001);
  for(const invalid of ['1024',true,1.2,-1,Infinity,NaN,null,undefined]) {
    assert.equal(findDirectMp4([{...valid,size_in_bytes:invalid}]),null);
  }
  for(const id of ['01','1e3','1.0',' 1',true,1.5]) {
    assert.equal(findDirectMp4([{...valid,id}]),null);
  }
});
