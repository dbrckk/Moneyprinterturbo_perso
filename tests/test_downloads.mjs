import test from 'node:test';
import assert from 'node:assert/strict';
import { MP4_ARTIFACT_NAME, findDirectMp4, directMp4Url, runArtifactsEndpoint } from '../remote/downloads.mjs';

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
  for (const invalid of [0, -3, '', '1&token=abc', 'https://evil.test', Number.MAX_SAFE_INTEGER+1]) {
    assert.throws(() => runArtifactsEndpoint(invalid));
    assert.throws(() => directMp4Url(123, invalid));
  }
});
