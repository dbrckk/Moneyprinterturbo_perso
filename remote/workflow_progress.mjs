/** Read-only GitHub Actions stage labels for the hosted (non-Termux) studio.
 * Only known workflow steps become visible; never expose script inputs or
 * unknown step names in UI. No ETA or invented percent-complete metric.
 */
import {knownRunEndpoint} from './core.mjs';

export function workflowJobsEndpoint(runId) {
  return `${knownRunEndpoint(runId)}/jobs?per_page=100`;
}

const STEPS = Object.freeze([
  ['Validate request before', 'Préparation', 'Vérification des paramètres avant le rendu'],
  ['Install render tools', 'Préparation', 'Installation des outils vidéo'],
  ['Restore optional CLIP', 'Modèles IA', 'Récupération du modèle de sélection visuelle'],
  ['Install optional local CPU vision', 'Modèles IA', 'Installation de la sélection visuelle CLIP'],
  ['Restore Qwen', 'Modèles IA', 'Récupération du modèle de scénario'],
  ['Install Ollama', 'Modèles IA', 'Préparation de la génération du scénario'],
  ['Restore verified Piper', 'Voix', 'Récupération de la voix neuronale'],
  ['Install and validate neural French', 'Voix', 'Installation de la voix neuronale française'],
  ['Restore optional Whisper', 'Sous-titres', 'Récupération du modèle d’alignement vocal'],
  ['Install optional acoustic', 'Sous-titres', 'Installation de l’alignement acoustique'],
  ['Generate actual narrated MP4', 'Encodage vidéo', 'Création des scènes, de la narration et du MP4'],
  ['Verify output contains video', 'Vérification', 'Contrôle des flux audio et vidéo'],
  ['Audit synchronization and export', 'Vérification', 'Audit des sous-titres et du montage'],
  ['Direct storyboard PNG', 'Publication', 'Publication de la planche des scènes'],
  ['Direct MP4 for Android', 'Publication', 'Mise à disposition du MP4 sans ZIP'],
  ['Sous-titres éditables SRT', 'Publication', 'Publication des sous-titres'],
  ['Rapport qualité pour Android', 'Publication', 'Publication du rapport qualité'],
  ['Crédits des sources', 'Publication', 'Publication des crédits'],
  ['Descriptions et hashtags', 'Publication', 'Publication des descriptions et hashtags'],
  ['Fiche des scènes', 'Publication', 'Publication du manifeste de montage'],
  ['Downloadable MP4', 'Publication', 'Sauvegarde des fichiers du rendu'],
]);

/** @returns {{title:string,detail:string}|null} */
export function workflowStage(jobs) {
  if (!Array.isArray(jobs) || jobs.length === 0 || jobs.length > 100) return null;
  const job = jobs.find(item => item?.name === 'create-video') ||
    (jobs.length === 1 ? jobs[0] : null);
  if (!job || !Array.isArray(job.steps) || job.steps.length > 100) return null;
  if (job.status === 'queued' || job.status === 'waiting')
    return {title:'En attente',detail:'Le calcul n’a pas encore commencé sur GitHub'};
  if (job.status !== 'in_progress') return null;
  const active = job.steps.find(step => step?.status === 'in_progress');
  if (!active) return {title:'Préparation GitHub',detail:'Passage à la prochaine étape de production'};
  if (typeof active.name !== 'string') return null;
  const match = STEPS.find(([prefix]) => active.name.startsWith(prefix));
  return match ? {title:match[1],detail:match[2]} :
    {title:'Préparation GitHub',detail:'Exécution des outils de production'};
}
