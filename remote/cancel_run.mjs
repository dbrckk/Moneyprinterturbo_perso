/** Validate workflow cancellation targets; never cancel an inferred run ID. */
import { isExpectedRun } from './core.mjs';

function positiveRunId(value) {
  return (typeof value === 'number' && Number.isSafeInteger(value) && value > 0) ||
    (typeof value === 'string' && /^[1-9][0-9]*$/.test(value) &&
      Number.isSafeInteger(Number(value)));
}

export function cancelRunEndpoint(runId) {
  if (!positiveRunId(runId)) throw new Error('Identifiant du rendu GitHub invalide.');
  return `https://api.github.com/repos/dbrckk/Moneyprinterturbo_perso/actions/runs/${Number(runId)}/cancel`;
}

export function mayCancelRun(run, requestId, selectedRunId) {
  return positiveRunId(selectedRunId) && Number(run?.id) === Number(selectedRunId) &&
    (run?.status === 'queued' || run?.status === 'in_progress' ||
      run?.status === 'waiting' || run?.status === 'requested' ||
      run?.status === 'pending') && isExpectedRun(run, requestId);
}
