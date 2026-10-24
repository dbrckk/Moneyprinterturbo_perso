/** GitHub Actions artifact links for the unarchived MP4, never expose credentials. */
export const MP4_ARTIFACT_NAME = 'video.mp4';
export const STORYBOARD_ARTIFACT_NAME = 'video.storyboard.png';
export const SUBTITLES_ARTIFACT_NAME = 'video.srt';
export const QUALITY_ARTIFACT_NAME = 'video.quality.json';
export const CREDITS_ARTIFACT_NAME = 'video.credits.txt';
export const PUBLISH_ARTIFACT_NAME = 'video.publish.txt';
export const MANIFEST_ARTIFACT_NAME = 'video.json';
export const REPO = 'dbrckk/Moneyprinterturbo_perso';

/** Accept only canonical positive GitHub decimal IDs; never coerce booleans,
 * exponent notation, whitespace, fractional strings or URL-like input.
 */
function positiveId(id) {
  if (typeof id === 'number') return Number.isSafeInteger(id) && id > 0;
  if (typeof id !== 'string' || !/^[1-9][0-9]*$/.test(id)) return false;
  return Number.isSafeInteger(Number(id));
}

function validArtifact(artifact, expectedName) {
  return artifact && artifact.name === expectedName &&
    artifact.expired === false && positiveId(artifact.id) &&
    Number.isSafeInteger(artifact.size_in_bytes) && artifact.size_in_bytes > 0;
}

export function runArtifactsEndpoint(runId) {
  if (!positiveId(runId)) throw new Error('Identifiant d’exécution GitHub invalide.');
  return `https://api.github.com/repos/${REPO}/actions/runs/${Number(runId)}/artifacts?per_page=100`;
}

export function findDirectMp4(artifacts) {
  if (!Array.isArray(artifacts)) return null;
  return artifacts.find(a => validArtifact(a, MP4_ARTIFACT_NAME)) ?? null;
}

export function findDirectStoryboard(artifacts) {
  if (!Array.isArray(artifacts)) return null;
  return artifacts.find(a => validArtifact(a, STORYBOARD_ARTIFACT_NAME)) ?? null;
}

export function findDirectSubtitles(artifacts) {
  if (!Array.isArray(artifacts)) return null;
  return artifacts.find(a => validArtifact(a, SUBTITLES_ARTIFACT_NAME)) ?? null;
}

export function findDirectQuality(artifacts) {
  if (!Array.isArray(artifacts)) return null;
  return artifacts.find(a => validArtifact(a, QUALITY_ARTIFACT_NAME)) ?? null;
}

export function findDirectCredits(artifacts) {
  if (!Array.isArray(artifacts)) return null;
  return artifacts.find(a => validArtifact(a, CREDITS_ARTIFACT_NAME)) ?? null;
}

export function findDirectPublication(artifacts) {
  if (!Array.isArray(artifacts)) return null;
  return artifacts.find(a => validArtifact(a, PUBLISH_ARTIFACT_NAME)) ?? null;
}

export function findDirectManifest(artifacts) {
  if (!Array.isArray(artifacts)) return null;
  return artifacts.find(a => validArtifact(a, MANIFEST_ARTIFACT_NAME)) ?? null;
}

export function directMp4Url(runId, artifactId) {
  if (!positiveId(runId) || !positiveId(artifactId)) throw new Error('Lien de vidéo GitHub invalide.');
  // GitHub validates the authenticated account, then serves this unzipped v7 artifact.
  // Never insert a PAT in a URL: tokens must remain exclusively in request headers.
  return `https://github.com/${REPO}/actions/runs/${Number(runId)}/artifacts/${Number(artifactId)}`;
}
