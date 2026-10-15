/** Pure helpers shared by browser and node tests; never accept/store credentials. */
export const REPOSITORY = 'dbrckk/Moneyprinterturbo_perso';
export const WORKFLOW = 'free-video.yml';
export const API_ROOT = `https://api.github.com/repos/${REPOSITORY}`;
export const RUNS_ENDPOINT = `${API_ROOT}/actions/workflows/${WORKFLOW}/runs?event=workflow_dispatch&per_page=30`;
export const ACTIONS_URL = `https://github.com/${REPOSITORY}/actions/workflows/${WORKFLOW}`;
export const LANGS = Object.freeze(['fr', 'en', 'es', 'de', 'it', 'pt']);
export const ASPECTS = Object.freeze(['9:16', '16:9', '1:1']);
export const RESOLUTIONS = Object.freeze(['360', '720', '1080']);
export const SPEECH_RATES = Object.freeze(['120', '145', '170']);

export function segmentsOf(script) {
  if (typeof script !== 'string') return [];
  const compact = script.trim().replace(/[ \t\f\v]+/g, ' ');
  if (!compact) return [];
  const pieces = compact.split(/(?<=[.!?。！？])\s+|\n+/u).map(s => s.trim()).filter(Boolean);
  const output = [];
  for (const part of pieces) {
    let buffer = '';
    for (const word of part.split(/\s+/)) {
      if (buffer && (buffer + ' ' + word).length > 105) {
        output.push(buffer);
        buffer = '';
      }
      buffer = buffer ? buffer + ' ' + word : word;
    }
    if (buffer) output.push(buffer);
  }
  return output;
}

export function queriesOf(value) {
  if (!value.trim()) return [];
  return value.split('|').map(q => q.trim());
}

export function validateDraft(values) {
  const topic = String(values.topic ?? '').trim();
  const script = String(values.script ?? '').trim();
  const sceneQueries = String(values.scene_queries ?? '').trim();
  if (topic.length < 2 || topic.length > 140 || /[\r\n]/.test(topic)) throw new Error('Le sujet doit contenir 2 à 140 caractères sur une seule ligne.');
  if (script.length > 5000) throw new Error('La narration ne peut pas dépasser 5 000 caractères.');
  const parts = script ? segmentsOf(script) : [];
  if (script && (!parts.length || parts.length > 18)) throw new Error('La narration doit contenir 1 à 18 scènes maximum.');
  if (sceneQueries) {
    if (values.visuals !== 'commons') throw new Error('Les mots-clés par scène nécessitent Wikimedia Commons.');
    if (!script) throw new Error('Renseignez la narration pour définir des recherches par scène.');
    const terms = queriesOf(sceneQueries);
    if (terms.length !== parts.length || terms.some(q => !q || q.length > 110)) {
      throw new Error(`Indiquez exactement ${parts.length} recherche(s) séparée(s) par |, sans entrée vide (110 caractères max chacune).`);
    }
  }
  if (!LANGS.includes(values.lang) || !ASPECTS.includes(values.aspect) || !RESOLUTIONS.includes(values.resolution)
      || !SPEECH_RATES.includes(values.speech_rate) || !['commons', 'abstract'].includes(values.visuals)) {
    throw new Error('Configuration vidéo invalide.');
  }
  return { topic, script, scene_queries: sceneQueries, lang: values.lang, aspect: values.aspect,
    resolution: values.resolution, speech_rate: values.speech_rate, visuals: values.visuals };
}

export function requestId() {
  const value = globalThis.crypto?.randomUUID?.();
  if (value) return `studio-${value}`;
  throw new Error('HTTPS est nécessaire pour générer un identifiant sécurisé.');
}

export function runTitle(id) { return `Free Studio · ${id}`; }

export function dispatchBody(values, id) {
  if (!/^studio-[0-9a-f-]{36}$/i.test(id)) throw new Error('Identifiant de demande invalide.');
  return { ref: 'main', inputs: { ...validateDraft(values), request_id: id } };
}

export function findRun(runs, id) {
  return Array.isArray(runs) ? runs.find(r => r?.event === 'workflow_dispatch' &&
    r?.display_title === runTitle(id) && r?.head_branch === 'main') ?? null : null;
}

export function workflowRunLink(run) {
  const id = Number(run?.id);
  return Number.isSafeInteger(id) && id > 0 ? `https://github.com/${REPOSITORY}/actions/runs/${id}` : ACTIONS_URL;
}

export function safeError(status, data) {
  const message = typeof data?.message === 'string' ? data.message : '';
  if (status === 401) return 'Jeton invalide ou expiré. Vérifiez le jeton GitHub.';
  if (status === 403) return 'Autorisation refusée ou limite GitHub atteinte. Le jeton doit avoir Actions : lecture et écriture uniquement sur ce dépôt.';
  if (status === 404) return 'Workflow introuvable ou dépôt inaccessible au jeton.';
  if (status === 422) return 'GitHub a refusé les paramètres du workflow. Vérifiez le sujet et la configuration.';
  return `GitHub a retourné ${status}${message ? ': ' + message.slice(0,160) : ''}.`;
}
