'use strict';
(() => {
  const $ = id => document.getElementById(id);
  let token = sessionStorage.getItem('mpt_studio_token') || '';
  let currentId = null;
  let videoUrl = null;
  let pollTimer = null;
  let encoderPollTimer = null;
  let lastCalibration = null;
  let lastStatus = '';
  const MEDIA_KEY = 'mpt-local-media-batch-v1';
  const WAKE_KEY = 'mpt-termux-wake-render-v1';
  try { $('keep-awake').checked = localStorage.getItem(WAKE_KEY) === '1'; }
  catch { $('keep-awake').checked = false; }
  const storedBatch = (() => { try { return localStorage.getItem(MEDIA_KEY) || ''; } catch { return ''; } })();
  const state = {has_ollama: false, has_piper: false, has_local_dir: false,
                 render_disk: null,
                 media_batch: /^[a-f0-9]{32}$/.test(storedBatch) ? storedBatch : ''};
  function rememberMediaBatch(id) {
    try { if (id) localStorage.setItem(MEDIA_KEY, id); else localStorage.removeItem(MEDIA_KEY); }
    catch { /* Browsers with restricted storage remain usable this session. */ }
  }
  const endpoint = (id, name = '') => `/api/jobs/${id}${name ? '/' + name : ''}`;

  async function api(url, options = {}) {
    const headers = Object.assign({}, options.headers || {});
    if (token) headers.Authorization = `Bearer ${token}`;
    if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
    const response = await fetch(url, {...options, headers, cache: 'no-store'});
    if (!response.ok) {
      let message = `Erreur serveur ${response.status}`;
      try { message = (await response.json()).error || message; } catch (_) { /* ignore */ }
      if (response.status === 401) $('auth').classList.remove('hidden');
      throw new Error(message);
    }
    return response;
  }
  async function json(url, options = {}) { return (await api(url, options)).json(); }
  const post = (url, payload) => json(url, {method: 'POST', body: JSON.stringify(payload)});

  function uploadMediaFile(url, file, onProgress) {
    // Fetch does not expose upload progress in widely deployed Android web
    // browsers. XHR is limited to this same-origin binary transfer; all
    // other API requests continue to use the existing authenticated fetch.
    if (typeof XMLHttpRequest !== 'function') {
      return api(url, {method: 'POST', body: file,
        headers: {'Content-Type': 'application/octet-stream',
                  'X-Media-Name': encodeURIComponent(file.name)}}).then(() => {});
    }
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', url, true);
      xhr.timeout = 20 * 60 * 1000; // whole-file ceiling, not per-chunk timeout
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.setRequestHeader('X-Media-Name', encodeURIComponent(file.name));
      if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
      let previousPercent = -1;
      xhr.upload.onprogress = event => {
        if (!file.size || !Number.isFinite(event.loaded)) return;
        const percent = Math.min(100, Math.max(0,
          Math.floor(100 * event.loaded / file.size)));
        if (percent > previousPercent) {
          previousPercent = percent;
          onProgress(percent);
        }
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) { resolve(); return; }
        let message = `Erreur serveur ${xhr.status}`;
        try {
          const response = JSON.parse(xhr.responseText);
          if (typeof response.error === 'string' && response.error) message = response.error;
        } catch (_) { /* a non-JSON server failure keeps the HTTP status */ }
        if (xhr.status === 401) $('auth').classList.remove('hidden');
        reject(new Error(message));
      };
      xhr.onerror = () => reject(new Error('Connexion interrompue pendant l’import. Réessayez.'));
      xhr.onabort = () => reject(new Error('Import interrompu. Réessayez le transfert.'));
      xhr.ontimeout = () => reject(new Error('Délai d’import dépassé. Réessayez.'));
      try { xhr.send(file); } catch (err) { reject(err); }
    });
  }

  function payload() {
    return {
      topic: $('topic').value.trim(), lang: $('lang').value,
      script: $('script').value.trim(), aspect: $('aspect').value,
      resolution: Number($('resolution').value),
      encoder_profile: $('encoder-profile').value, visuals: $('visuals').value,
      tts: $('tts').value, speech_rate: Number($('speech-rate').value),
      mute: $('mute').checked, keep_awake: $('keep-awake').checked,
      scene_queries: $('queries').value.trim(),
      media_batch: $('visuals').value === 'local' ? state.media_batch : ''
    };
  }
  const profileLabel = {balanced: 'Standard', detail: 'Détails', premium: 'Premium'};
  const calibrationApi = '/api/encoder/calibration';
  function showCalibration(data) {
    lastCalibration = data;
    const btn = $('calibrate-encoder');
    const apply = $('apply-calibration');
    const note = $('encoder-calibration-note');
    const selectedResolution = Number($('resolution').value);
    const eligible = selectedResolution !== 1080 && $('aspect').value === '9:16';
    btn.disabled = data.status === 'running' || !eligible;
    apply.hidden = true;
    if (!eligible) {
      note.textContent = 'Calibration 9:16 en 360p ou 720p uniquement. Modifiez le format et la résolution pour tester le téléphone.';
    } else if (data.status === 'running') {
      note.textContent = `Calibration locale ${data.resolution}p en cours. 8 courtes vidéos synthétiques, aucune vidéo personnelle envoyée.`;
    } else if (data.status === 'done' && data.resolution === selectedResolution
        && data.fps === 24 && data.advice?.applies_automatically === false) {
      const suggestion = profileLabel[data.suggested_profile] || 'Standard';
      const rates = data.advice.measured_encoded_fps || {};
      const detailRate = typeof rates.detail === 'number' ? rates.detail.toFixed(1) : '?';
      const premiumRate = typeof rates.premium === 'number' ? rates.premium.toFixed(1) : '?';
      note.textContent = `Conseil indicatif pour ${selectedResolution}p/24 ips, 15 s et 3 scènes : ${suggestion}. Mesuré (scène la plus lente) : détails ${detailRate} i/s, premium ${premiumRate} i/s. Les montages plus longs peuvent être automatiquement limités. Le test court ne mesure ni batterie ni surchauffe prolongée.`;
      apply.hidden = !profileLabel[data.suggested_profile];
    } else if (data.status === 'error') {
      note.textContent = `Calibration non terminée : ${data.error || 'erreur locale'}. Le profil Standard reste disponible.`;
    } else if (data.status === 'done') {
      note.textContent = 'Le dernier test concernait une autre résolution. Lancez un nouveau test si vous souhaitez une recommandation pour ce format.';
    } else {
      note.textContent = 'Calibration volontaire : 8 vidéos synthétiques de 2 secondes, sans vos médias. Le téléphone peut chauffer. Aucun profil n’est appliqué sans votre choix.';
    }
  }
  async function refreshCalibration() {
    clearTimeout(encoderPollTimer);
    try {
      const data = await json(calibrationApi);
      showCalibration(data);
      if (data.status === 'running') {
        encoderPollTimer = setTimeout(() => refreshCalibration().catch(showError), 2500);
      }
    } catch (err) {
      $('encoder-calibration-note').textContent = 'État de calibration indisponible : ' + String(err.message || err);
    }
  }
  function showError(err) { $('error').textContent = err ? String(err.message || err) : ''; }
  function splitSentences(text) {
    const compact = text.replace(/[ \t\f\v]+/g, ' ').trim();
    return compact ? compact.split(/(?<=[.!?。！？])\s+|\n+/).filter(s => s.trim()) : [];
  }
  function showScenes() {
    const scenes = splitSentences($('script').value);
    $('scene-count').textContent = `${scenes.length} scène${scenes.length > 1 ? 's' : ''}`;
    $('scenes').replaceChildren();
    for (let i = 0; i < Math.min(scenes.length, 18); i++) {
      const pill = document.createElement('span');
      pill.className = 'scene-pill';
      pill.textContent = `SCÈNE ${String(i + 1).padStart(2, '0')}`;
      pill.title = scenes[i];
      $('scenes').append(pill);
    }
  }
  function statusLabel(status) {
    return {queued: 'EN FILE', running: 'RENDU EN COURS', done: 'TERMINÉ', error: 'ÉCHEC', cancelled: 'ANNULÉ'}[status] || 'EN ATTENTE';
  }
  const renderStageNames = Object.freeze({
    voice: 'Préparation de la voix et des sous-titres',
    assets: 'Analyse et sélection des médias',
    scenes: 'Préparation des scènes et des transitions',
    encode: 'Encodage de la vidéo MP4',
    review: 'Vérification du montage terminé',
    publish: 'Finalisation des fichiers exportés'
  });
  function displayRenderProgress(job) {
    const panel = $('render-stage-panel');
    const progress = job.status === 'running' ? job.progress : null;
    const valid = progress && Object.hasOwn(renderStageNames, progress.stage) &&
      Number.isInteger(progress.step) && progress.step >= 1 && progress.step <= 6 &&
      progress.steps === 6 && Number.isInteger(progress.elapsed_seconds) &&
      progress.elapsed_seconds >= 0;
    panel.classList.toggle('hidden', !valid);
    if (!valid) return '';
    // Each segment denotes a *completed stage*, not a percentage of render
    // time. Encode and media analysis can take much longer than other stages.
    const completed = progress.step - 1;
    $('render-stage-label').textContent = `Étape ${progress.step}/6 · ${renderStageNames[progress.stage]}`;
    const minutes = Math.floor(progress.elapsed_seconds / 60);
    const seconds = String(progress.elapsed_seconds % 60).padStart(2, '0');
    $('render-stage-time').textContent = `${minutes}:${seconds} écoulées`;
    $('render-stage-bar').value = completed;
    return `Étape ${progress.step}/6 : ${renderStageNames[progress.stage]}.`;
  }
  function releaseVideo() {
    const player = $('video');
    player.onerror = null;
    player.pause();
    player.removeAttribute('src');
    // On Chrome Android removing src without load() may retain the decoder's
    // old network request and memory buffers after selecting another job.
    if (typeof player.load === 'function') player.load();
    if (videoUrl) URL.revokeObjectURL(videoUrl);
    videoUrl = null;
    player.classList.add('hidden');
    $('placeholder').classList.remove('hidden');
  }
  async function loadVideo(id) {
    if (currentId !== id) return;
    releaseVideo();
    try {
      if (!token) {
        // Local same-origin preview: the native video player issues HTTP
        // Range requests and does NOT build an entire MP4-sized Blob in RAM.
        $('video').onerror = () => {
          if (currentId !== id) return;
          releaseVideo();
          $('job-notice').textContent = 'Aperçu indisponible : vérifiez le serveur local et le MP4 exporté.';
        };
        $('video').src = endpoint(id, 'video.mp4');
      } else {
        // On authenticated LAN sessions the <video> element cannot send
        // Authorization headers, so preserve the protected fetch/Blob path.
        const media = await api(endpoint(id, 'video.mp4'));
        const blob = await media.blob();
        if (currentId !== id) return;
        videoUrl = URL.createObjectURL(blob);
        $('video').src = videoUrl;
      }
      if (currentId !== id) return;
      $('video').classList.remove('hidden');
      $('placeholder').classList.add('hidden');
    } catch (err) {
      if (currentId === id) {
        $('job-notice').textContent = `Vidéo disponible, mais aperçu impossible : ${err.message}`;
      }
    }
  }
  function displayRenderReview(job) {
    const panel = $('render-review');
    const report = job.status === 'done' ? job.render_review : null;
    const valid = report && report.status === 'exported-not-artistically-approved' &&
      Array.isArray(report.checks) && report.checks.length >= 1 && report.checks.length <= 6 &&
      report.checks.every(item => item && typeof item.title === 'string' &&
        typeof item.detail === 'string' && typeof item.attention === 'boolean');
    panel.classList.toggle('hidden', !valid);
    const list = $('render-review-checks');
    const warningList = $('render-review-warnings-list');
    list.replaceChildren();
    warningList.replaceChildren();
    if (!valid) {
      $('render-review-warnings').classList.add('hidden');
      return;
    }
    $('render-review-summary').textContent =
      typeof report.summary === 'string' ? report.summary : 'Contrôles disponibles.';
    // Never parse renderer warnings or metadata as HTML. The job may contain
    // user-authored text; textContent prevents markup injection.
    for (const item of report.checks) {
      const entry = document.createElement('li');
      entry.className = item.attention ? 'render-review-attention' : '';
      const heading = document.createElement('strong');
      heading.textContent = item.title;
      const detail = document.createElement('span');
      detail.textContent = item.detail;
      entry.append(heading, detail);
      list.append(entry);
    }
    const warnings = Array.isArray(report.warnings) ? report.warnings.slice(0, 6) : [];
    $('render-review-warnings').classList.toggle('hidden', !warnings.length);
    const more = Number.isInteger(report.warnings_total)
      ? Math.max(0, report.warnings_total - warnings.length) : 0;
    $('render-review-warnings-label').textContent =
      warnings.length ? 'Avertissements du rendu' +
      (more ? ' (+' + more + ' autres dans video.json)' : '') : '';
    for (const warning of warnings) {
      if (typeof warning !== 'string') continue;
      const entry = document.createElement('li');
      entry.textContent = warning;
      warningList.append(entry);
    }
  }
  async function loadJob(id) {
    const job = await json(endpoint(id));
    if (currentId !== id) return;
    $('status-badge').textContent = statusLabel(job.status);
    $('status-badge').className = 'status ' + (['queued', 'running'].includes(job.status) ? 'active' : ['error', 'cancelled'].includes(job.status) ? 'failed' : '');
    $('cancel-row').classList.toggle('hidden', !['queued', 'running'].includes(job.status));
    $('downloads').classList.toggle('hidden', job.status !== 'done');
    $('credits').classList.toggle('hidden', !job.has_credits);
    $('publish-kit').classList.toggle('hidden', !job.has_publication);
    displayRenderReview(job);
    const stageNotice = displayRenderProgress(job);
    if (job.status === 'done') {
      $('job-notice').textContent = `Vidéo terminée · ${job.duration_seconds ?? '?'} secondes · ${job.visual_mode || 'vidéo'}. ${job.warnings?.length ? job.warnings.length + ' avertissement(s) sur les médias.' : ''}`;
      if (lastStatus !== 'done') await loadVideo(id);
    } else if (job.status === 'error') {
      $('job-notice').textContent = `Le rendu a échoué : ${job.error || 'Erreur inconnue'}`;
    } else if (job.status === 'cancelled') {
      $('job-notice').textContent = 'Ce rendu a été annulé.';
    } else {
      const wakeNotice = job.status === 'running' && job.keep_awake_requested ?
        (job.keep_awake_active ? ' · Anti-veille Termux demandé et actif.' :
          ' · Anti-veille Termux indisponible : le rendu continue normalement.') : '';
      $('job-notice').textContent = (job.status === 'queued' ? 'Projet ajouté à la file d’attente…' :
        (stageNotice || 'Montage vidéo en cours sur votre machine. Le résultat sera affiché ici.')) + wakeNotice;
    }
    // A slow authenticated Blob response must never change the state of a
    // newer project selected while the previous video was still loading.
    if (currentId !== id) return;
    lastStatus = job.status;
    if (['running', 'queued'].includes(job.status)) {
      pollTimer = setTimeout(() => loadJob(id).catch(showError), 2000);
    } else {
      loadHistory().catch(() => {});
    }
  }
  function selectJob(id) {
    clearTimeout(pollTimer);
    currentId = id;
    releaseVideo();
    lastStatus = '';
    $('render-stage-panel').classList.add('hidden');
    $('render-review').classList.add('hidden');
    $('render-review-checks').replaceChildren();
    $('render-review-warnings-list').replaceChildren();
    $('downloads').classList.add('hidden');
    $('video').classList.add('hidden');
    $('placeholder').classList.remove('hidden');
    loadJob(id).catch(showError);
  }
  async function loadHistory() {
    const {jobs} = await json('/api/jobs');
    const panel = $('history');
    panel.replaceChildren();
    if (!jobs.length) {
      const p = document.createElement('p');
      p.className = 'empty'; p.textContent = 'Aucun rendu pour le moment.'; panel.append(p); return;
    }
    for (const job of jobs.slice(0, 8)) {
      const button = document.createElement('button');
      button.className = 'history-item'; button.type = 'button';
      const title = document.createElement('span'); title.textContent = job.topic;
      const badge = document.createElement('small'); badge.textContent = statusLabel(job.status);
      button.append(title, badge);
      button.addEventListener('click', () => selectJob(job.id));
      panel.append(button);
    }
  }
  const collator = new Intl.Collator('fr', {numeric: true, sensitivity: 'base'});
  function mediaNote(value) { $('media-note').textContent = value; }
  function megabytes(size) { return (size / (1024 * 1024)).toLocaleString('fr-FR', {
    maximumFractionDigits: 1
  }) + ' Mo'; }
  function showRenderDiskStatus() {
    const disk = state.render_disk;
    const note = $('render-storage-note');
    const resolution = Number($('resolution').value);
    const minimum = disk?.minimum_bytes?.[String(resolution)];
    const free = disk?.free_bytes;
    if (disk?.status !== 'ok' || !Number.isSafeInteger(free) || free < 0 ||
        !Number.isSafeInteger(minimum) || minimum <= 0) {
      note.textContent = 'Espace disque indisponible : le serveur vérifiera le stockage avant de lancer le rendu.';
      return;
    }
    const amount = megabytes(free);
    const needed = megabytes(minimum);
    note.textContent = free < minimum
      ? `Stockage insuffisant pour ${resolution}p : ${amount} libres, au moins ${needed} nécessaires. Libérez de la place sans effacer vos MP4 terminés.`
      : `Stockage disponible pour ${resolution}p : ${amount} libres (seuil prudent ${needed}). Le serveur surveille aussi le rendu en cours.`;
  }
  async function refreshStorage() {
    const usage = await json('/api/media/storage');
    state.render_disk = usage.render_disk || null;
    showRenderDiskStatus();
    $('storage-stats').textContent = 'Imports : ' + megabytes(usage.bytes) +
      ' / ' + megabytes(usage.limit_bytes) +
      ' · ' + usage.batch_count + ' lot(s) · ' +
      megabytes(usage.reclaimable_bytes) + ' récupérables après montage · ' +
      megabytes(usage.abandoned_bytes) + ' anciens brouillons/échecs.';
    $('clean-media').disabled = !usage.reclaimable_count;
    // Do not offer cleanup of the batch currently selected for editing.
    const abandoned = usage.batches.filter(b => b.abandoned && b.id !== state.media_batch);
    $('clean-abandoned').disabled = !abandoned.length;
    return usage;
  }
  $('clean-media').addEventListener('click', async () => {
    showError(null);
    const btn = $('clean-media');
    btn.disabled = true;
    try {
      const snapshot = await refreshStorage();
      if (!snapshot.reclaimable_count) return;
      if (!window.confirm('Supprimer les médias importés de ' +
        snapshot.reclaimable_count + ' montage(s) terminés (' +
        megabytes(snapshot.reclaimable_bytes) + ') ? ' +
        'Les MP4 finaux et leurs sous-titres seront conservés.')) return;
      const result = await post('/api/media/cleanup', {});
      if (result.deleted_batches.includes(state.media_batch)) {
        state.media_batch = '';
        rememberMediaBatch('');
        $('delete-media').hidden = true;
        $('media-files').value = '';
        if (!state.has_local_dir) $('visuals').value = 'commons';
      }
      mediaNote('Nettoyage terminé : ' + megabytes(result.reclaimed_bytes) +
        ' récupérés, ' + result.deleted_batches.length + ' lot(s). ' +
        'Les vidéos finales restent disponibles.');
    } catch (err) { showError(err); }
    finally { await refreshStorage().catch(() => {}); }
  });
  $('clean-abandoned').addEventListener('click', async () => {
    showError(null);
    const button = $('clean-abandoned'); button.disabled = true;
    try {
      const usage = await refreshStorage();
      const abandoned = usage.batches.filter(b => b.abandoned && b.id !== state.media_batch);
      if (!abandoned.length) return;
      const bytes = abandoned.reduce((sum, b) => sum + b.bytes, 0);
      if (!window.confirm('Supprimer ' + abandoned.length + ' lot(s) de médias ' +
        'de plus de 7 jours, issus de brouillons abandonnés ou de rendus échoués (' +
        megabytes(bytes) + ') ? Ces sources ne seront plus récupérables. ' +
        'Le projet actuellement sélectionné et tous les MP4 finaux sont conservés.')) return;
      const result = await post('/api/media/cleanup-abandoned',
                                {protect_batch: state.media_batch});
      mediaNote('Nettoyage des anciens imports : ' + megabytes(result.reclaimed_bytes) +
        ' libérés dans ' + result.deleted_batches.length + ' lot(s).');
    } catch (err) { showError(err); }
    finally { await refreshStorage().catch(() => {}); }
  });
  // On Android, selecting clips is enough: import starts immediately.
  // Keep the button as an explicit retry for flaky connections.
  $('media-files').addEventListener('change', () => {
    if ($('media-files').files.length && !$('upload-media').disabled) {
      $('upload-media').click();
    }
  });
  $('upload-media').addEventListener('click', async () => {
    showError(null);
    const files = [...$('media-files').files].sort((a, b) =>
      collator.compare(a.name, b.name) || a.name.localeCompare(b.name));
    if (!files.length) { showError('Sélectionnez des vidéos ou photos à importer.'); return; }
    if (files.length > 24 || files.some(f => !f.size || f.size >
        (/\.(jpe?g|png|webp)$/i.test(f.name) ? 18 : 350) * 1024 * 1024)) {
      showError('Maximum 24 fichiers, 350 Mo par vidéo et 18 Mo par image.');
      return;
    }
    if (files.reduce((sum, file) => sum + file.size, 0) > 800 * 1024 * 1024) {
      showError('Le projet dépasse 800 Mo de médias.'); return;
    }
    const button = $('upload-media'); button.disabled = true;
    $('media-files').disabled = true;
    $('upload-progress').value = 0;
    $('upload-progress').hidden = false;
    let batch = '';
    let committed = false;
    try {
      const created = await post('/api/media/batches', {});
      batch = created.id;
      let count = 0;
      for (const file of files) {
        const index = ++count;
        $('upload-progress').value = 0;
        mediaNote(`Import de ${index}/${files.length} : ${file.name} — 0 %`);
        await uploadMediaFile(`/api/media/batches/${batch}/files`, file, percent => {
          $('upload-progress').value = percent;
          mediaNote(`Import de ${index}/${files.length} : ${file.name} — ` +
                    (percent === 100 ? '100 % · vérification du média…' : `${percent} %`));
        });
      }
      // Replace an abandoned prior draft only after the full new batch works.
      // Validate the complete new import before switching the remembered
      // batch: a transient metadata failure must not discard the previous one.
      const plan = await json(`/api/media/batches/${batch}`);
      const previous = state.media_batch;
      state.media_batch = batch;
      rememberMediaBatch(batch);
      committed = true; // A valid batch must never be deleted on a later UI failure.
      $('visuals').value = 'local';
      $('delete-media').hidden = false;
      mediaNote(`${files.length} média(s) importé(s), prêts pour le montage local. ` +
                (plan.ordering === 'capture'
                  ? 'Ordre chronologique établi à partir des dates de capture intégrées.'
                  : 'Dates de capture absentes, ambiguës ou incomplètes : ordre des noms conservé.') +
                ' Aucune vidéo envoyée à GitHub.');
      if (previous && previous !== batch) {
        // A sealed import may still be needed for retry/re-render. Never
        // silently delete it during an unrelated new upload. On a failed
        // lookup, preserve the original rather than guessing its status.
        try {
          const oldBatch = await json(`/api/media/batches/${previous}`);
          if (oldBatch.sealed === false) {
            await post(`/api/media/batches/${previous}/delete`, {});
          }
        } catch (_) { /* Keep the old batch if its safety is uncertain. */ }
      }
      // Storage display is noncritical after the batch has been committed.
      // A temporary 503 must not roll back an otherwise successful upload.
      await refreshStorage().catch(() => {});
    } catch (err) {
      if (batch && !committed) {
        let deleted = false;
        try {
          const result = await post(`/api/media/batches/${batch}/delete`, {});
          deleted = result.deleted === true;
        } catch (_) { /* Server may be offline during mobile disconnect. */ }
        mediaNote(deleted
          ? 'Import interrompu : lot incomplet supprimé, vos anciens médias sont conservés.'
          : 'Import interrompu : ancien lot conservé. Vérifiez le stockage avant de réessayer.');
      }
      showError(err);
    } finally {
      button.disabled = false;
      $('media-files').disabled = false;
      $('upload-progress').hidden = true;
      $('upload-progress').value = 0;
      refreshStorage().catch(() => {});
    }
  });
  $('delete-media').addEventListener('click', async () => {
    if (!state.media_batch) return;
    showError(null);
    try {
      await post(`/api/media/batches/${state.media_batch}/delete`, {});
      state.media_batch = '';
      rememberMediaBatch('');
      $('delete-media').hidden = true;
      $('media-files').value = '';
      if (!state.has_local_dir) $('visuals').value = 'commons';
      mediaNote('Import supprimé du moteur local.');
      await refreshStorage();
    } catch (err) { showError(err); }
  });
  async function init() {
    const health = await json('/api/health');
    $('connection').textContent = health.ok ? 'Moteur disponible' : 'Indisponible';
    try {
      const config = await json('/api/config');
      state.has_ollama = config.has_ollama;
      state.has_piper = config.has_piper;
      state.has_local_dir = config.has_local_media;
      $('tts').querySelector('option[value="piper"]').disabled = !config.has_piper;
      $('visuals').querySelector('option[value="local"]').disabled = false;
      if (state.media_batch) {
        try {
          const existing = await json(`/api/media/batches/${state.media_batch}`);
          if (existing.files?.length) {
            $('visuals').value = 'local';
            $('delete-media').hidden = false;
            mediaNote(`${existing.files.length} média(s) retrouvé(s) après recharge de la page. ` +
              (existing.ordering === 'capture'
                ? 'Tri selon les dates de capture.'
                : 'Tri selon les noms de fichiers.'));
          } else {
            state.media_batch = '';
            rememberMediaBatch('');
          }
        } catch {
          // A stale local identifier is not a credential and cannot re-create
          // a deleted batch. The user can import a new selection.
          state.media_batch = '';
          rememberMediaBatch('');
        }
      }
      $('script-note').textContent = config.has_ollama ? 'Scénario générable avec Ollama local.' : 'Brouillon déterministe, sans génération IA.';
      $('auth').classList.add('hidden');
      loadHistory().catch(showError);
      refreshCalibration().catch(showError);
      refreshStorage().catch(() => {
        state.render_disk = null;
        showRenderDiskStatus();
        $('storage-stats').textContent = 'Impossible de consulter le stockage.';
      });
    } catch (err) { showError(err); }
  }
  $('auth-form').addEventListener('submit', event => {
    event.preventDefault();
    token = $('token').value;
    sessionStorage.setItem('mpt_studio_token', token);
    init().catch(showError);
  });
  $('draft').addEventListener('click', async () => {
    showError(null);
    const btn = $('draft'); btn.disabled = true;
    try {
      const draft = await post('/api/draft', payload());
      $('script').value = draft.script;
      $('script-note').textContent = draft.mode === 'local-ollama-ai' ? 'Scénario créé avec Ollama local.' : 'Brouillon automatique non généré par IA.';
      showScenes();
    } catch (err) { showError(err); } finally { btn.disabled = false; }
  });
  $('create-form').addEventListener('submit', async event => {
    event.preventDefault();
    showError(null);
    $('render').disabled = true;
    try {
      if ($('visuals').value === 'local' && !state.media_batch && !state.has_local_dir) {
        throw new Error('Importez au moins une vidéo ou photo avant de lancer ce montage.');
      }
      // Re-check immediately before enqueue: imports, downloads and other
      // applications may have changed the free space since page load.
      const usage = await refreshStorage();
      const disk = usage.render_disk;
      const free = disk?.free_bytes;
      const required = disk?.minimum_bytes?.[String($('resolution').value)];
      if (disk?.status === 'ok' && Number.isSafeInteger(free) &&
          Number.isSafeInteger(required) && free < required) {
        throw new Error(`Espace insuffisant pour le rendu : ${megabytes(free)} disponibles, minimum ${megabytes(required)}. Libérez de la place puis réessayez.`);
      }
      const job = await post('/api/jobs', payload());
      selectJob(job.id);
      loadHistory().catch(() => {});
    } catch (err) { showError(err); } finally { $('render').disabled = false; }
  });
  $('calibrate-encoder').addEventListener('click', async () => {
    showError(null);
    const resolution = Number($('resolution').value);
    if (resolution === 1080 || $('aspect').value !== '9:16') return;
    if (!window.confirm('Lancer un benchmark local sur ce téléphone ? Il crée 8 vidéos synthétiques et utilise le processeur pendant plusieurs minutes au maximum. Aucun média personnel n’est transféré.')) return;
    $('calibrate-encoder').disabled = true;
    try {
      const data = await post(calibrationApi, {resolution, fps: 24});
      showCalibration(data);
      refreshCalibration().catch(showError);
    } catch (err) {
      showError(err);
      refreshCalibration().catch(() => {});
    }
  });
  $('apply-calibration').addEventListener('click', () => {
    const data = lastCalibration;
    if (!data || data.status !== 'done' || data.advice?.applies_automatically !== false
        || data.resolution !== Number($('resolution').value)
        || data.fps !== 24 || $('aspect').value !== '9:16'
        || !profileLabel[data.suggested_profile]) return;
    $('encoder-profile').value = data.suggested_profile;
    $('encoder-calibration-note').textContent = 'Profil ' + profileLabel[data.suggested_profile] +
      ' sélectionné pour votre prochain rendu. Vous pouvez le modifier avant de générer la vidéo.';
  });
  $('resolution').addEventListener('change', () => {
    if (lastCalibration) showCalibration(lastCalibration);
    showRenderDiskStatus();
    refreshStorage().catch(() => {});
  });
  $('aspect').addEventListener('change', () => { if (lastCalibration) showCalibration(lastCalibration); });
  $('script').addEventListener('input', showScenes);
  $('mute').addEventListener('change', () => { $('tts').disabled = $('mute').checked; $('speech-rate').disabled = $('mute').checked; });
  $('keep-awake').addEventListener('change', () => {
    try { localStorage.setItem(WAKE_KEY, $('keep-awake').checked ? '1' : '0'); }
    catch { /* The preference is optional if browser storage is disabled. */ }
  });
  $('cancel').addEventListener('click', async () => {
    if (!currentId) return;
    try { await json(endpoint(currentId, 'cancel'), {method: 'POST'}); selectJob(currentId); } catch (err) { showError(err); }
  });
  $('refresh').addEventListener('click', () => loadHistory().catch(showError));
  for (const button of document.querySelectorAll('.dl')) {
    button.addEventListener('click', async () => {
      if (!currentId) return;
      const selectedId = currentId;
      const name = button.dataset.file;
      const filename = `${selectedId.slice(0, 8)}-${name}`;
      let blobUrl = null;
      try {
        let source = endpoint(selectedId, name);
        if (token) {
          // Only protected LAN mode requires the full Blob fallback.
          const blob = await (await api(source)).blob();
          blobUrl = URL.createObjectURL(blob);
          source = blobUrl;
        }
        const link = document.createElement('a');
        link.href = source;
        link.download = filename;
        document.body.append(link);
        link.click();
        link.remove();
        if (blobUrl) setTimeout(() => URL.revokeObjectURL(blobUrl), 3000);
      } catch (err) {
        if (blobUrl) URL.revokeObjectURL(blobUrl);
        showError(err);
      }
    });
  }
  window.addEventListener('pagehide', () => {
    clearTimeout(pollTimer);
    clearTimeout(encoderPollTimer);
    releaseVideo();
  });
  showScenes();
  init().catch(err => { $('connection').textContent = 'Serveur inaccessible'; showError(err); });
})();
