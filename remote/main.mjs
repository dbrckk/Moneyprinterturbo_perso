import { API_ROOT, RUNS_ENDPOINT, ACTIONS_URL, dispatchBody, findRun, requestId,
  runTitle, safeError, segmentsOf, validateDraft, workflowRunLink,
  knownRunEndpoint, isExpectedRun } from './core.mjs';
import { runArtifactsEndpoint, findDirectMp4, findDirectStoryboard,
  findDirectSubtitles, findDirectQuality, directMp4Url } from './downloads.mjs';
import { exportPortableDraft, importPortableDraft } from './draft_backup.mjs';
import { shouldAutoResume, shouldVerifyCompletedRun } from './run_lifecycle.mjs';

const $ = id => document.getElementById(id);
const controls = ['word_alignment', 'caption_style', 'transition', 'visual_style', 'topic', 'script', 'script_mode', 'scene_queries', 'lang', 'aspect', 'resolution', 'speech_rate', 'voice'];
const DRAFT_KEY = 'mpt-remote-draft-v1';
const HISTORY_KEY = 'mpt-remote-jobs-v1';
const statusLabels = {queued:'En attente', in_progress:'Rendu en cours', completed:'Terminé', waiting:'En attente', requested:'Demande envoyée'};
let active = null;
let pollTimer = null;
let lastPoll = 0;
let lookupInFlight = null;

function storageRead(key, fallback) {
  try { const value = localStorage.getItem(key); return value ? JSON.parse(value) : fallback; }
  catch { return fallback; }
}
function storageWrite(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ } }
function formValues() {
  const out = Object.fromEntries(controls.map(key => [key, $(key).value]));
  out.visuals = document.querySelector('input[name="visuals"]:checked')?.value || 'abstract';
  return out;
}
function restoreDraft() {
  const saved = storageRead(DRAFT_KEY, {});
  for (const key of controls) {
    if (typeof saved[key] === 'string') $(key).value = saved[key];
  }
  if (saved.visuals === 'commons' || saved.visuals === 'abstract') {
    document.querySelector(`input[name="visuals"][value="${saved.visuals}"]`).checked = true;
  }
}
function saveDraft() { storageWrite(DRAFT_KEY, formValues()); }
function notify(message, kind = 'error') {
  const node = $('form-feedback');
  node.textContent = message;
  node.classList.toggle('success', kind === 'success');
  node.hidden = false;
}
function clearNotice() { $('form-feedback').hidden = true; }
function showScenes() {
  const narration = $('script').value;
  $('script-count').textContent = `${narration.length} / 5000`;
  const entries = narration.trim() ? segmentsOf(narration) : [];
  $('scene-count').textContent = entries.length ? `${entries.length} scène${entries.length>1?'s':''}` : ($('script_mode').value === 'local-ai' ? 'IA locale · scènes après génération' : 'Canevas automatique');
  const list = $('scenes');
  list.replaceChildren();
  const displayed = entries.length ? entries : [$('script_mode').value === 'local-ai' ? 'Le modèle IA générera les scènes pendant le rendu GitHub.' : 'Ajoute une narration pour prévisualiser tes scènes.'];
  for (const entry of displayed.slice(0,18)) {
    const li = document.createElement('li'); li.textContent = entry; list.append(li);
  }
}
function setStatus(kind, title, explanation) {
  const chip = $('status-indicator');
  chip.textContent = title;
  chip.className = 'status-chip' + (kind ? ` ${kind}` : '');
  $('status-copy').textContent = explanation;
}
function loadHistory() {
  const records = storageRead(HISTORY_KEY, []);
  return Array.isArray(records) ? records.filter(r => typeof r?.id === 'string' && /^studio-[a-f0-9-]{36}$/i.test(r.id)).slice(0,12) : [];
}
function historySave(rows) { storageWrite(HISTORY_KEY, rows.slice(0,12)); }
function showHistory() {
  const list = $('history'); list.replaceChildren();
  const rows = loadHistory();
  if (!rows.length) { const li = document.createElement('li'); li.className = 'empty-history'; li.textContent = 'Aucune demande mémorisée.'; list.append(li); return; }
  for (const item of rows) {
    const li = document.createElement('li');
    const link = document.createElement('a');
    link.href = item.runId ? workflowRunLink({id:item.runId}) : ACTIONS_URL;
    link.target = '_blank'; link.rel = 'noopener noreferrer';
    link.textContent = `${item.topic || 'Vidéo'} · ${item.state || 'Demande envoyée'} ↗`;
    const date = document.createElement('span'); date.className = 'run-date';
    date.textContent = new Date(item.createdAt).toLocaleString('fr-FR');
    li.append(link, date);
    const button = document.createElement('button');
    button.type='button';button.className='subtle-button';button.textContent='Suivre';
    button.addEventListener('click', () => {
      stopPoll(); active = item;
      for (const id of ['download-video','preview-storyboard','download-subtitles','download-quality']) $(id).hidden = true;
      lastPoll=0; $('run-actions').hidden = false; $('run-link').href=link.href;
      setStatus('running','Suivi','Recherche de la demande dans GitHub Actions…');
      if (shouldAutoResume(item) && navigator.onLine !== false) startPoll();
      else void lookupRun();
    });
    li.append(' · ',button);
    list.append(li);
  }
}
function replaceHistory(record) {
  const rows=loadHistory().filter(item => item.id !== record.id);
  rows.unshift(record); historySave(rows); showHistory();
}
async function resolveDirectVideo(run, requestId) {
  try {
    const response = await api(runArtifactsEndpoint(run.id));
    if (!active || active.id !== requestId || active.runId !== run.id) return;
    const storyboard = findDirectStoryboard(response?.artifacts);
    const preview = $('preview-storyboard');
    preview.hidden = !storyboard;
    if (storyboard) preview.href = directMp4Url(run.id, storyboard.id);
    // The standalone SRT and QA JSON are optional for older workflow runs.
    // Never guess artifact IDs, and never attach the GitHub PAT to download URLs.
    for (const [id, artifact] of [
      ['download-subtitles', findDirectSubtitles(response?.artifacts)],
      ['download-quality', findDirectQuality(response?.artifacts)]]) {
      const link = $(id);
      link.hidden = !artifact;
      if (artifact) link.href = directMp4Url(run.id, artifact.id);
    }
    const mp4 = findDirectMp4(response?.artifacts);
    if (!mp4) {
      $('download-video').hidden = true;
      setStatus('success', 'Vidéo prête', 'Ce rendu ne dispose pas encore d’un MP4 direct, ou son artefact a expiré. Utilise le lien du rendu GitHub pour télécharger l’archive.');
      return;
    }
    $('download-video').href = directMp4Url(run.id, mp4.id);
    $('download-video').hidden = false;
    setStatus('success', 'Vidéo prête', 'Le MP4 est accessible sans ZIP avec le bouton ci-dessous. GitHub peut demander une connexion à ton compte. Les sous-titres SRT et le rapport qualité sont aussi disponibles séparément lorsque les artefacts existent ; les autres fichiers restent dans l’archive.');
  } catch (error) {
    if (active?.id === requestId) setStatus('success', 'Vidéo prête', 'Lien MP4 direct indisponible : ' + error.message + ' Tu peux toujours ouvrir le rendu sur GitHub.');
  }
}
function updateRun(run) {
  if (!active) return;
  active = { ...active, runId: run.id, state: statusLabels[run.status] || run.status || 'En cours' };
  $('run-link').href = workflowRunLink(run);
  replaceHistory(active);
  if (run.status !== 'completed') {
    setStatus('running', statusLabels[run.status] || 'En cours', 'GitHub effectue le rendu. Cette page affiche le suivi ; l’onglet peut être fermé sans arrêter le workflow.');
    return;
  }
  if (run.conclusion === 'success') {
    active.state = 'Réussi';replaceHistory(active);
    setStatus('success', 'Vidéo prête', 'Le rendu est terminé. Recherche du MP4 sans ZIP…');
    void resolveDirectVideo(run, active.id);
  } else {
    active.state = run.conclusion || 'Échec';replaceHistory(active);
    setStatus('failed', 'Échec du rendu', `Exécution terminée : ${run.conclusion || 'résultat inconnu'}. Consulte les journaux GitHub pour déterminer la cause.`);
  }
  stopPoll();
}
async function api(url, options = {}) {
  const token = $('token').value.trim();
  const headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  let response;
  try { response = await fetch(url, {...options, headers, signal: controller.signal, cache:'no-store'}); }
  finally { clearTimeout(timer); }
  if (!response.ok) {
    let data = {};
    try { data = await response.json(); } catch { /* non-json GitHub response */ }
    throw new Error(safeError(response.status, data));
  }
  return response.status === 204 ? null : response.json();
}
async function lookupRun() {
  if (!active || Date.now() - lastPoll < 1500) return;
  const lookupFor=active.id;
  if (lookupInFlight === lookupFor) return;
  if (navigator.onLine === false) {
    setStatus('running','Connexion nécessaire','Le rendu GitHub continue. Le suivi reprendra au retour du réseau.');
    return;
  }
  lookupInFlight = lookupFor;
  lastPoll = Date.now();
  try {
    // Known historical runs can fall outside the most recent 30 workflow runs.
    // Query their exact numeric ID instead of losing the Android tracking link.
    let matched;
    if (active.runId) {
      const known = await api(knownRunEndpoint(active.runId));
      if (!active || active.id !== lookupFor) return;
      if (!isExpectedRun(known, lookupFor)) {
        throw new Error('Ce rendu GitHub ne correspond pas à la demande sauvegardée.');
      }
      matched = known;
    } else {
      const result = await api(RUNS_ENDPOINT);
      if (!active || active.id !== lookupFor) return;
      matched = findRun(result?.workflow_runs, lookupFor);
    }
    if (matched) updateRun(matched);
    else if (!active.runId) setStatus('running', 'En attente', 'Demande envoyée, mais GitHub ne l’affiche pas encore. Clique sur « Actualiser » ou ouvre GitHub Actions.');
  } catch (error) {
    if (active?.id === lookupFor) setStatus('failed', 'Suivi indisponible', `${error.message} Tu peux retrouver ton rendu directement dans GitHub Actions.`);
  } finally {
    if (lookupInFlight === lookupFor) lookupInFlight = null;
  }
}
function stopPoll() { if (pollTimer !== null) { clearInterval(pollTimer); pollTimer=null; } }
function startPoll() {
  stopPoll(); lastPoll=0;
  void lookupRun();
  const start=Date.now();
  pollTimer=setInterval(() => {
    if (document.visibilityState === 'hidden' || navigator.onLine === false) return;
    if (Date.now()-start > 20*60*1000) {
      stopPoll();
      if (active) setStatus('running','Suivi interrompu','Le suivi automatique s’arrête après 20 minutes. Le rendu GitHub continue : actualise ou ouvre la page du workflow.');
    } else { void lookupRun(); }
  },20000);
}
function templateFor(topic,lang) {
  if(lang==='fr')return `Découvrons ${topic}. Voici les éléments clés à connaître sur ${topic}. Examinons des exemples concrets et leurs applications. Consulte des sources fiables pour approfondir le sujet.`;
  if(lang==='en')return `Let's explore ${topic}. Here are the central ideas behind ${topic}. Look at examples and practical applications. Verify the details with reliable sources.`;
  return `${topic}. ${topic} : présentation et points clés. ${topic} : exemples et applications utiles.`;
}

$('make-outline').addEventListener('click', () => {
  const topic=$('topic').value.trim();
  if(topic.length<2){notify('Renseigne un sujet avant de créer un canevas.');return;}
  $('script').value=templateFor(topic,$('lang').value);$('script_mode').value='template';saveDraft();showScenes();clearNotice();
});
$('video-form').addEventListener('input', () => { saveDraft();showScenes();clearNotice(); });
$('video-form').addEventListener('change', saveDraft);
$('forget-token').addEventListener('click',()=>{ $('token').value='';$('token').focus(); });
$('clear-history').addEventListener('click',()=>{historySave([]);showHistory();});
$('copy-draft').addEventListener('click', async () => {
  try {
    const backup = exportPortableDraft(formValues());
    if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
    await navigator.clipboard.writeText(backup);
    notify('Brouillon copié. Colle-le dans le champ « Restaurer » de la nouvelle version du studio.', 'success');
  } catch {
    // Android browsers may restrict clipboard access; manual copy is available.
    $('backup-data').value = exportPortableDraft(formValues());
    $('backup-data').focus();$('backup-data').select();
    notify('Copie manuelle : le texte de sauvegarde est sélectionné ci-dessous.', 'success');
  }
});
$('restore-draft').addEventListener('click', () => {
  try {
    const draft = importPortableDraft($('backup-data').value);
    for (const key of controls) {
      const element = $(key);
      if (element.tagName === 'SELECT' && !Array.from(element.options).some(
          option => option.value === draft[key])) {
        throw new Error('Option non reconnue : ' + key);
      }
    }
    for (const key of controls) $(key).value = draft[key];
    document.querySelector('input[name="visuals"][value="' + draft.visuals + '"]').checked = true;
    saveDraft();showScenes();
    notify('Scénario et réglages restaurés sur cet appareil. Aucun jeton GitHub n’a été importé.', 'success');
  } catch (error) { notify('Impossible de restaurer : ' + error.message); }
});
$('refresh').addEventListener('click',()=>{lastPoll=0;void lookupRun();});
$('video-form').addEventListener('submit', async event => {
  event.preventDefault();clearNotice();
  let values, id;
  try { values=validateDraft(formValues());id=requestId(); }
  catch(error){notify(error.message);return;}
  if (!$('token').value.trim()) { notify('Un jeton GitHub « Actions : Read and write » est nécessaire pour lancer un rendu. Utilise le lien de création à droite.'); $('token').focus();return; }
  const launch=$('launch');launch.disabled=true;launch.querySelector('span').textContent='Envoi vers GitHub…';
  try {
    await api(`${API_ROOT}/actions/workflows/free-video.yml/dispatches`,{method:'POST',body:JSON.stringify(dispatchBody(values,id))});
    active={id,topic:values.topic,createdAt:new Date().toISOString(),state:'Demande envoyée'};
    $('download-video').hidden = true;
    $('preview-storyboard').hidden = true;
    $('download-subtitles').hidden = true;
    $('download-quality').hidden = true;
    replaceHistory(active);
    $('run-actions').hidden=false;
    $('run-link').href=ACTIONS_URL;
    setStatus('running','Demande acceptée',`GitHub a accepté ${runTitle(id)}. Recherche du rendu en cours…`);
    notify('Demande acceptée par GitHub. Le rendu est lancé ; suis sa progression dans le panneau Production.', 'success');
    startPoll();
  } catch(error){notify(`Impossible de lancer le rendu : ${error.name==='AbortError'?'délai réseau dépassé (vérifie sur GitHub avant de réessayer)':error.message}`);}
  finally{launch.disabled=false;launch.querySelector('span').textContent='Générer ma vidéo';}
});

// Installable Android shell: store only public frontend assets in Cache Storage.
// Drafts/history are already in localStorage; the GitHub PAT is never persisted.
function startOfflineShell() {
  const banner = $('offline-banner');
  const updateStatus = () => { banner.hidden = navigator.onLine !== false; };
  updateStatus();
  window.addEventListener('online', () => {
    updateStatus();
    if (shouldAutoResume(active) && pollTimer === null) startPoll();
  });
  window.addEventListener('offline', () => { updateStatus(); stopPoll(); });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') stopPoll();
    else if (shouldAutoResume(active) && navigator.onLine !== false && pollTimer === null) startPoll();
  });
  if ('serviceWorker' in navigator && window.isSecureContext) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('./sw.mjs', { scope: './', type: 'module' })
        .catch(() => { /* Offline support is optional; online rendering still works. */ });
    }, { once: true });
  }
}

restoreDraft();showScenes();showHistory();startOfflineShell();
const previous=loadHistory()[0];
if (previous) {
  active=previous;
  $('run-actions').hidden=false;
  $('run-link').href=previous.runId?workflowRunLink({id:previous.runId}):ACTIONS_URL;
  setStatus('',previous.state||'Dernière demande','Demande restaurée. Le suivi peut reprendre automatiquement.');
  // Always verify the saved run via GitHub before advertising an artifact.
  if (shouldAutoResume(previous) && navigator.onLine !== false) startPoll();
  else if (shouldVerifyCompletedRun(previous) && navigator.onLine !== false) void lookupRun();
}
