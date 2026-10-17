/** Stateless mobile job-recovery rules. Never persist GitHub credentials. */
const PENDING = new Set([
  'Demande envoyée', 'En attente', 'Rendu en cours',
  'queued', 'in_progress', 'requested', 'waiting',
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
