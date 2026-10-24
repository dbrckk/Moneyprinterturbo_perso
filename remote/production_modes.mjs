/** Real existing free workflow combinations, not fictional quality promises.
 * No network calls, credits, token handling, private media upload or side effects.
 */
import {ASPECTS, LANGS, validateDraft} from './core.mjs';

export const PRODUCTION_MODES = Object.freeze(['recommended', 'deep', 'preview']);

export const MODE_DESCRIPTIONS = Object.freeze({
  recommended: 'Scénario IA Qwen si la narration est vide, voix Piper en français, HD, Commons, montage cinématique. Modèles téléchargés par GitHub Actions ; durée variable.',
  deep: 'Même création automatique + comparaison CLIP et alignement audio Whisper. Expérimental : plusieurs modèles CPU, rendu potentiellement long ou interrompu par les limites GitHub.',
  preview: 'Aperçu économique 360p, canevas sans IA, voix eSpeak et graphismes automatiques. Rapide, mais qualité de narration inférieure.',
});

/** Keep user topic, language, chosen format and personally written narration.
 * Never discard or send existing user footage: remote static mode does not
 * accept private gallery videos. For non-French, the verified French Piper
 * voice is correctly replaced with eSpeak rather than pretending supported.
 */
export function productionMode(values, requested = 'recommended') {
  if (!PRODUCTION_MODES.includes(requested)) {
    throw new Error('Mode de production inconnu.');
  }
  if (!values || typeof values !== 'object' || Array.isArray(values)) {
    throw new Error('Réglages du studio invalides.');
  }
  const lang = values.lang;
  if (!LANGS.includes(lang)) throw new Error('Langue non reconnue.');
  if (!ASPECTS.includes(values.aspect)) throw new Error('Format vidéo invalide.');
  const hasScript = typeof values.script === 'string' && values.script.trim().length > 0;
  const base = {
    // A strict key list avoids copying the GitHub token or unrelated future
    // browser state into any draft or GitHub workflow payload.
    topic: values.topic,
    script: typeof values.script === 'string' ? values.script : '',
    lang, aspect: values.aspect,
    // A custom script has priority over generated narration. A previously
    // saved custom script is never deleted to enable the Ollama option.
    script_mode: hasScript || requested === 'preview' ? 'template' : 'local-ai',
    // Abstract previews cannot accept Commons search overrides. The saved
    // form is only changed after the user selects this explicit preset.
    scene_queries: requested === 'preview' ? '' :
                   hasScript ? String(values.scene_queries ?? '') : '',
    voice: requested === 'preview' || lang !== 'fr' ? 'espeak' : 'piper-fr-siwis',
    visuals: requested === 'preview' ? 'abstract' : 'commons',
    visual_ai: requested === 'deep' ? 'clip' : 'off',
    caption_style: requested === 'preview' ? 'classic' : 'kinetic',
    word_alignment: requested === 'deep' ? 'forced' : 'estimated',
    visual_style: 'cinematic',
    transition: 'auto',
    resolution: requested === 'preview' ? '360' : '720',
    speech_rate: '145',
  };
  // The exact rules are shared with the GitHub workflow dispatch. Fail now
  // instead of losing a long CPU render to invalid field combinations.
  const checked = validateDraft(base);
  return checked;
}
