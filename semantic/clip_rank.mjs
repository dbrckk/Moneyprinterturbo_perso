#!/usr/bin/env node
/** Optional CLIP comparison of locally downloaded Commons images.
 *
 * No image is sent to a hosted inference service. Only model weights are fetched
 * from Hugging Face on first run, then reused from a cache. Scores are relative
 * to the *same fixed negative labels*, NOT correctness probabilities.
 */
import { readFile, writeFile } from 'node:fs/promises';
import { resolve, isAbsolute } from 'node:path';

export const MODEL = 'Xenova/clip-vit-base-patch32';
export const REVISION = '6ef1ebc8b0766a7a8d11b146462c99cdf74dd22d';
const NEGATIVES = [
  'a photo of a person',
  'a photo of a vehicle',
  'a photo of a building',
  'a photo of an animal',
];

export function verifyInput(input) {
  if (!input || !Array.isArray(input.candidates) ||
      input.candidates.length < 2 || input.candidates.length > 32) {
    throw new Error('Expected 2–32 candidate images');
  }
  const ids = new Set();
  for (const item of input.candidates) {
    if (!item || !Number.isSafeInteger(item.id) || item.id < 0 ||
        ids.has(item.id) || typeof item.path !== 'string' ||
        !isAbsolute(item.path) || item.path.length > 2048 ||
        typeof item.prompt !== 'string' || !/^[a-z ]{3,70}$/.test(item.prompt)) {
      throw new Error('Invalid CLIP candidate');
    }
    ids.add(item.id);
  }
  return input.candidates;
}

export function sceneLabels(prompt) {
  return [`a photo of ${prompt}`, ...NEGATIVES];
}

export function positiveLabelScore(predictions, label) {
  if (!Array.isArray(predictions)) throw new Error('Missing CLIP predictions');
  const item = predictions.find(row => row?.label === label);
  if (typeof item?.score !== 'number' || !Number.isFinite(item.score) ||
      item.score < 0 || item.score > 1) {
    throw new Error('Invalid CLIP response');
  }
  return Math.round(item.score * 1e5) / 1e5;
}

async function main() {
  if (process.argv.length !== 4) throw new Error('Usage: node clip_rank.mjs INPUT.json OUTPUT.json');
  const [inputFile, outputFile] = process.argv.slice(2).map(path => resolve(path));
  const candidates = verifyInput(JSON.parse(await readFile(inputFile, 'utf8')));
  // Defer the optional dependency: unit tests and ordinary rendering need no
  // ML library, and loading this module alone never downloads a model.
  const { pipeline, RawImage, env } = await import('@huggingface/transformers');
  env.cacheDir = process.env.MPT_CLIP_CACHE_DIR || resolve('.cache/mpt-clip');
  const classifier = await pipeline('zero-shot-image-classification', MODEL, {
    revision: REVISION, dtype: 'q8', device: 'cpu'
  });
  const scores = [];
  for (const item of candidates) {
    const image = await RawImage.read(item.path);
    const labels = sceneLabels(item.prompt);
    const predictions = await classifier(image, labels, { hypothesis_template: '{}' });
    scores.push({ id: item.id, score: positiveLabelScore(predictions, labels[0]) });
  }
  await writeFile(outputFile, JSON.stringify({ model: MODEL, revision: REVISION, scores }) + '\n');
  if (typeof classifier.dispose === 'function') await classifier.dispose();
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(new URL(import.meta.url).pathname)) {
  main().catch(error => { console.error(String(error?.message || error)); process.exitCode = 1; });
}
