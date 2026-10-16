/** GitHub Actions artifact links for the unarchived MP4, never expose credentials. */
export const MP4_ARTIFACT_NAME = 'video.mp4';
export const STORYBOARD_ARTIFACT_NAME = 'video.storyboard.png';
export const SUBTITLES_ARTIFACT_NAME = 'video.srt';
export const QUALITY_ARTIFACT_NAME = 'video.quality.json';
export const REPO = 'dbrckk/Moneyprinterturbo_perso';

function positiveId(id) {
  return Number.isSafeInteger(Number(id)) && Number(id) > 0;
}

export function runArtifactsEndpoint(runId) {
  if (!positiveId(runId)) throw new Error('Identifiant d’exécution GitHub invalide.');
  return `https://api.github.com/repos/${REPO}/actions/runs/${Number(runId)}/artifacts?per_page=100`;
}

export function findDirectMp4(artifacts) {
  if (!Array.isArray(artifacts)) return null;
  return artifacts.find(a => a && a.name === MP4_ARTIFACT_NAME &&
    a.expired === false && positiveId(a.id) && a.size_in_bytes > 0) ?? null;
}

export function findDirectStoryboard(artifacts) {
  if (!Array.isArray(artifacts)) return null;
  return artifacts.find(a => a && a.name === STORYBOARD_ARTIFACT_NAME &&
    a.expired === false && positiveId(a.id) && a.size_in_bytes > 0) ?? null;
}

export function findDirectSubtitles(artifacts) {
  if (!Array.isArray(artifacts)) return null;
  return artifacts.find(a => a && a.name === SUBTITLES_ARTIFACT_NAME &&
    a.expired === false && positiveId(a.id) && a.size_in_bytes > 0) ?? null;
}

export function findDirectQuality(artifacts) {
  if (!Array.isArray(artifacts)) return null;
  return artifacts.find(a => a && a.name === QUALITY_ARTIFACT_NAME &&
    a.expired === false && positiveId(a.id) && a.size_in_bytes > 0) ?? null;
}

export function directMp4Url(runId, artifactId) {
  if (!positiveId(runId) || !positiveId(artifactId)) throw new Error('Lien de vidéo GitHub invalide.');
  // GitHub validates the authenticated account, then serves this unzipped v7 artifact.
  // Never insert a PAT in a URL: tokens must remain exclusively in request headers.
  return `https://github.com/${REPO}/actions/runs/${Number(runId)}/artifacts/${Number(artifactId)}`;
}
