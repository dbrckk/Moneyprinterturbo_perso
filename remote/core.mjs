/** Pure helpers shared by browser and node tests; never accept/store credentials. */
export const REPOSITORY = 'dbrckk/Moneyprinterturbo_perso';
export const WORKFLOW = 'free-video.yml';
export const API_ROOT = `https://api.github.com/repos/${REPOSITORY}`;
export const RUNS_ENDPOINT = `${API_ROOT}/actions/workflows/${WORKFLOW}/runs?event=workflow_dispatch&per_page=100`;
export const ACTIONS_URL = `https://github.com/${REPOSITORY}/actions/workflows/${WORKFLOW}`;
export const LANGS = Object.freeze(['fr', 'en', 'es', 'de', 'it', 'pt']);
export const ASPECTS = Object.freeze(['9:16', '16:9', '1:1']);
export const RESOLUTIONS = Object.freeze(['360', '720', '1080']);
export const SPEECH_RATES = Object.freeze(['120', '145', '170']);
export const SCRIPT_MODES = Object.freeze(['template', 'local-ai']);
export const VOICES = Object.freeze(['espeak', 'piper-fr-siwis']);
export const VISUAL_STYLES = Object.freeze(['cinematic', 'classic']);
export const CAPTION_STYLES = Object.freeze(['kinetic', 'classic']);
export const WORD_ALIGNMENTS = Object.freeze(['estimated', 'forced']);
export const TRANSITIONS = Object.freeze(['auto', 'fade', 'wipeleft', 'cut']);
export const VISUAL_AIS = Object.freeze(['off', 'clip']);

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

/** The same no-AI templates as free_studio.py, in the selected language. */
export function outlineFor(topic, language) {
  if (typeof topic !== 'string' || typeof language !== 'string') {
    throw new Error('Sujet ou langue invalide.');
  }
  const subject = topic.trim().replace(/\s+/g, ' ');
  if (subject.length < 2 || subject.length > 140 || /[\r\n]/.test(topic)) {
    throw new Error('Le sujet doit contenir 2 à 140 caractères sur une seule ligne.');
  }
  switch (language) {
    case 'fr':
      return `Découvrons ${subject}. Voici les idées essentielles pour comprendre ${subject}. Regardons les exemples et les usages les plus utiles. Pour aller plus loin, confrontez ces informations à des sources fiables.`;
    case 'en':
      return `Let's explore ${subject}. Here are the key ideas behind ${subject}. Consider the most useful examples and real-world applications. To learn more, verify these ideas using reliable sources.`;
    case 'es':
      return `Descubramos ${subject}. Estas son las ideas principales para entender ${subject}. Veamos algunos ejemplos y aplicaciones útiles. Consulta fuentes fiables para obtener más información.`;
    case 'de':
      return `Entdecken wir ${subject}. Das sind die wichtigsten Ideen zu ${subject}. Betrachten wir praktische Beispiele und Anwendungen. Prüfe diese Informationen anhand zuverlässiger Quellen.`;
    case 'it':
      return `Scopriamo ${subject}. Ecco le idee principali per capire ${subject}. Vediamo alcuni esempi e applicazioni utili. Verifica queste informazioni con fonti affidabili.`;
    case 'pt':
      return `Vamos explorar ${subject}. Estas são as ideias principais sobre ${subject}. Vejamos exemplos e aplicações úteis. Verifique estas informações em fontes confiáveis.`;
    default:
      throw new Error('Langue de canevas non prise en charge.');
  }
}

export function queriesOf(value) {
  if (!value.trim()) return [];
  return value.split('|').map(q => q.trim());
}

export function validateDraft(values) {
  const topic = String(values.topic ?? '').trim();
  const script = String(values.script ?? '').trim();
  const sceneQueries = String(values.scene_queries ?? '').trim();
  const scriptMode = String(values.script_mode ?? 'template');
  const voice = String(values.voice ?? 'espeak');
  const visualStyle = String(values.visual_style ?? 'cinematic');
  const visualAi = String(values.visual_ai ?? 'off');
  const captionStyle = String(values.caption_style ?? 'kinetic');
  const wordAlignment = String(values.word_alignment ?? 'estimated');
  const transition = String(values.transition ?? 'auto');
  if (topic.length < 2 || topic.length > 140 || /[\r\n]/.test(topic)) throw new Error('Le sujet doit contenir 2 à 140 caractères sur une seule ligne.');
  if (script.length > 5000) throw new Error('La narration ne peut pas dépasser 5 000 caractères.');
  if (!SCRIPT_MODES.includes(scriptMode)) throw new Error('Mode de scénario invalide.');
  if (!VOICES.includes(voice)) throw new Error('Moteur vocal invalide.');
  if (!VISUAL_STYLES.includes(visualStyle)) throw new Error('Habillage visuel invalide.');
  if (!VISUAL_AIS.includes(visualAi)) throw new Error('Comparaison visuelle IA invalide.');
  if (visualAi === 'clip' && values.visuals !== 'commons') throw new Error('CLIP nécessite des images Wikimedia Commons.');
  if (!CAPTION_STYLES.includes(captionStyle)) throw new Error('Style de sous-titres invalide.');
  if (!WORD_ALIGNMENTS.includes(wordAlignment)) throw new Error('Mode d’alignement des mots invalide.');
  if (wordAlignment === 'forced' && captionStyle !== 'kinetic') throw new Error('L’alignement audio exige les sous-titres progressifs.');
  if (!TRANSITIONS.includes(transition)) throw new Error('Transition visuelle invalide.');
  if (voice === 'piper-fr-siwis' && values.lang !== 'fr') throw new Error('La voix Piper SIWIS est disponible uniquement en français.');
  if (script && scriptMode === 'local-ai') throw new Error('Vide la narration pour activer la véritable IA locale, ou sélectionne le mode canevas.');
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
  return { topic, script, script_mode: scriptMode, voice, visual_style: visualStyle, visual_ai: visualAi, caption_style: captionStyle, word_alignment: wordAlignment, transition, scene_queries: sceneQueries, lang: values.lang, aspect: values.aspect,
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

/** Search for a correlated workflow dispatch without missing busy repositories.
 * On an ordinary repo, only ONE request is used. A second page is fetched
 * strictly when GitHub filled the first 100 dispatch results; max three pages.
 * The callback stops network work after the user selects another job.
 */
export async function findRunAcrossPages(fetchPage, id, isCurrent = () => true) {
  if (typeof fetchPage !== 'function' || typeof isCurrent !== 'function') {
    throw new Error('Invalid run lookup');
  }
  for (let page = 1; page <= 3 && isCurrent(); page++) {
    const endpoint = `${RUNS_ENDPOINT}&page=${page}`;
    const response = await fetchPage(endpoint);
    if (!isCurrent()) return null;
    if (!Array.isArray(response?.workflow_runs)) {
      throw new Error('GitHub a retourné une liste de rendus invalide.');
    }
    const match = findRun(response.workflow_runs, id);
    if (match) return match;
    if (response.workflow_runs.length < 100) break;
  }
  return null;
}

export function findRun(runs, id) {
  return Array.isArray(runs) ? runs.find(r => r?.event === 'workflow_dispatch' &&
    r?.display_title === runTitle(id) && r?.head_branch === 'main') ?? null : null;
}

/** Resume a previously known run directly, even after it leaves the last 30. */
export function knownRunEndpoint(runId) {
  const id = Number(runId);
  if (!Number.isSafeInteger(id) || id <= 0 || String(id) !== String(runId)) {
    throw new Error('Identifiant de rendu GitHub invalide.');
  }
  return `${API_ROOT}/actions/runs/${id}`;
}

/** Do not accidentally show another workflow's output for a saved run ID. */
export function isExpectedRun(run, requestId) {
  return !!run && run.event === 'workflow_dispatch' &&
    run.display_title === runTitle(requestId) && run.head_branch === 'main' &&
    Number.isSafeInteger(Number(run.id)) && Number(run.id) > 0 &&
    (run.repository == null || run.repository.full_name === REPOSITORY) &&
    (run.path == null || run.path === '.github/workflows/free-video.yml' ||
     run.path === `${REPOSITORY}/.github/workflows/free-video.yml@refs/heads/main`);
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
