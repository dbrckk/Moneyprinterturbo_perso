/** Portable, opt-in scenario export. Never serialize GitHub tokens or history. */
export const BACKUP_VERSION = 'mpt-draft-v1';
export const BACKUP_FIELDS = Object.freeze([
  'visual_ai','word_alignment','caption_style','transition','visual_style',
  'topic','script','script_mode','scene_queries','lang','aspect',
  'resolution','speech_rate','voice','visuals',
]);
const MAX_SIZES = Object.freeze({
  topic: 250, script: 5000, scene_queries: 3200,
});

function filteredDraft(values) {
  if (!values || typeof values !== 'object' || Array.isArray(values)) {
    throw new Error('Le brouillon doit être un objet de paramètres.');
  }
  const draft = {};
  for (const key of BACKUP_FIELDS) {
    // Backward-compatible: v1 backups created before CLIP had no such field.
    const value = key === 'visual_ai' && !values[key] ? 'off' : values[key];
    const max = MAX_SIZES[key] || 100;
    if (typeof value !== 'string' || value.length > max) {
      throw new Error('Champ de brouillon invalide : ' + key);
    }
    draft[key] = value;
  }
  if (!['off','clip'].includes(draft.visual_ai)) {
    throw new Error('Comparaison visuelle IA du brouillon invalide.');
  }
  if (!['commons','abstract'].includes(draft.visuals)) {
    throw new Error('Mode visuel du brouillon invalide.');
  }
  return draft;
}

export function exportPortableDraft(values) {
  return JSON.stringify({ format: BACKUP_VERSION, draft: filteredDraft(values) }, null, 2);
}

export function importPortableDraft(text) {
  if (typeof text !== 'string' || !text.trim() || text.length > 16000) {
    throw new Error('Sauvegarde vide ou trop volumineuse.');
  }
  let obj;
  try { obj = JSON.parse(text); }
  catch { throw new Error('La sauvegarde ne contient pas de JSON valide.'); }
  if (!obj || obj.format !== BACKUP_VERSION) {
    throw new Error('Version de sauvegarde non reconnue.');
  }
  return filteredDraft(obj.draft);
}
