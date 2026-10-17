/** Stateless mobile job-recovery rules. Never persist GitHub credentials. */
const PENDING = new Set([
  'Demande envoyée', 'En attente', 'Rendu en cours',
  'queued', 'in_progress', 'requested', 'waiting', 'Envoi non confirmé',
]);
const MAX_RECOVERY_AGE = 48 * 60 * 60 * 1000;

export function shouldAutoResume(record, now = Date.now()) {
  if (!record || typeof record !== 'object' ||
      typeof record.id !== 'string' ||
      !/^studio-[0-9a-f-]{36}$/i.test(record.id) ||
      !PENDING.has(record.state) ||
      typeof record.createdAt !== 'string' || !Number.isFinite(now)) return false;
  const age = now - Date.parse(record.createdAt);
  return Number.isFinite(age) && age >= -60_000 && age <= MAX_RECOVERY_AGE;
}

export function shouldVerifyCompletedRun(record) {
  return !!record && record.state === 'Réussi' &&
    typeof record.id === 'string' && /^studio-[0-9a-f-]{36}$/i.test(record.id) &&
    Number.isSafeInteger(Number(record.runId)) && Number(record.runId) > 0;
}

/** Ignore late replies from a previously selected history entry. */
export function createRunSelection() {
  let generation=0;
  return {select(){return ++generation;},current(){return generation;},isCurrent(value){return value===generation;}};
}

/** A timed out POST may have been accepted by GitHub before the reply vanished.
 * Only definite client/validation failures are safe to treat as rejected.
 */
export function isUncertainDispatchFailure(error) {
  if (!error || typeof error !== 'object') return false;
  const code=error.httpStatus;
  if (Number.isInteger(code)) return code===408 || code===429 || code>=500;
  return error.name==='AbortError' || error.name==='TypeError';
}

/** Prevent an immediate duplicate GPU/CPU workflow after ambiguous delivery. */
export function recentUnconfirmedRequest(records, topic, now=Date.now()) {
  if (!Array.isArray(records) || typeof topic!=='string' || !Number.isFinite(now)) return null;
  return records.find(item=>{
    if (item?.state!=='Envoi non confirmé' || item.topic!==topic || !shouldAutoResume(item,now)) return false;
    const age=now-Date.parse(item.createdAt);
    return age>=0 && age<=5*60_000;
  }) ?? null;
}

/** GitHub API rate limiting is a temporary state, never a render failure. */
export function classifyTrackingError(error) {
  const status=Number(error?.httpStatus);
  if (status===429 || status===403 && /rate.limit|quota|secondary/i.test(String(error?.message||'')))
    return {kind:'rate-limit',message:'GitHub limite temporairement les requêtes. Le rendu continue. Attends quelques minutes avant d’actualiser.'};
  if (status===401 || status===403)
    return {kind:'auth',message:'Accès GitHub refusé. Vérifie les autorisations du jeton, puis actualise le suivi.'};
  if (status===404)
    return {kind:'not-found',message:'Ce rendu GitHub est introuvable ou inaccessible avec les autorisations actuelles.'};
  if (error?.name==='AbortError' || error?.name==='TypeError' || status>=500)
    return {kind:'temporary',message:'Connexion GitHub temporairement indisponible. Le rendu peut continuer ; réessaie plus tard.'};
  return {kind:'unknown',message:'Impossible de consulter le suivi GitHub. Vérifie la demande et réessaie.'};
}
