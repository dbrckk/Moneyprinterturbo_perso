'use strict';
(() => {
  const $ = id => document.getElementById(id);
  let token = sessionStorage.getItem('mpt_studio_token') || '';
  let currentId = null;
  let videoUrl = null;
  let pollTimer = null;
  let encoderCalibrationTimer = null;
  let encoderCalibrationRequested = false;
  let lastStatus = '';
  const MEDIA_KEY = 'mpt-local-media-batch-v1';
  const storedBatch = (() => { try { return localStorage.getItem(MEDIA_KEY) || ''; } catch { return ''; } })();
  const state = {has_ollama: false, has_piper: false, has_local_dir: false,
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
      resolution: Number($('resolution').value), visuals: $('visuals').value,
      encoder_profile: $('encoder-profile').value,
      tts: $('tts').value, speech_rate: Number($('speech-rate').value),
      mute: $('mute').checked, scene_queries: $('queries').value.trim(),
      media_batch: $('visuals').value === 'local' ? state.media_batch : ''
    };
  }
  async function refreshEncoderCalibration() {
    clearTimeout(encoderCalibrationTimer);
    const status = await json('/api/encoder/calibration');
    const active = status.state === 'running';
    $('calibrate-encoder').disabled = active;
    const output = $('encoder-calibration-status');
    if (active) {
      output.textContent = 'Étalonnage en cours sur cet appareil… Ne lancez pas de montage.';
      encoderCalibrationTimer = setTimeout(() => {
        refreshEncoderCalibration().catch(err => {
          output.textContent = 'Étalonnage : suivi momentanément indisponible.';
          showError(err);
        });
      }, 1800);
    } else if (status.state === 'ready' && status.available) {
      const rates = status.encoded_fps || {};
      const speed = Number.isFinite(rates.detail) ? ` · détails : ${Math.round(rates.detail)} images/s` : '';
      output.textContent = `Étalonnage disponible sur cet appareil${speed}. Valable 14 jours. Seul le mode Automatique l'utilise.`;
      if (encoderCalibrationRequested) {
        $('encoder-profile').value = 'auto';
        encoderCalibrationRequested = false;
      }
    } else if (status.state === 'error') {
      output.textContent = status.error || 'Étalonnage impossible. Utilisez le profil Standard.';
      encoderCalibrationRequested = false;
    } else {
      output.textContent = 'Pas d’étalonnage récent. Standard et Automatique restent disponibles sans lui.';
      encoderCalibrationRequested = false;
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
  async function loadJob(id) {
    const job = await json(endpoint(id));
    if (currentId !== id) return;
    $('status-badge').textContent = statusLabel(job.status);
    $('status-badge').className = 'status ' + (['queued', 'running'].includes(job.status) ? 'active' : ['error', 'cancelled'].includes(job.status) ? 'failed' : '');
    $('cancel-row').classList.toggle('hidden', !['queued', 'running'].includes(job.status));
    $('downloads').classList.toggle('hidden', job.status !== 'done');
    $('credits').classList.toggle('hidden', !job.has_credits);
    if (job.status === 'done') {
      $('job-notice').textContent = `Vidéo terminée · ${job.duration_seconds ?? '?'} secondes · ${job.visual_mode || 'vidéo'}. ${job.warnings?.length ? job.warnings.length + ' avertissement(s) sur les médias.' : ''}`;
      if (lastStatus !== 'done') await loadVideo(id);
    } else if (job.status === 'error') {
      $('job-notice').textContent = `Le rendu a échoué : ${job.error || 'Erreur inconnue'}`;
    } else if (job.status === 'cancelled') {
      $('job-notice').textContent = 'Ce rendu a été annulé.';
    } else {
      $('job-notice').textContent = job.status === 'queued' ? 'Projet ajouté à la file d’attente…' : 'Montage vidéo en cours sur votre machine. Le résultat sera affiché ici.';
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
  async function refreshStorage() {
    const usage = await json('/api/media/storage');
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
      refreshEncoderCalibration().catch(err => {
        $('encoder-calibration-status').textContent = 'État de l’étalonnage indisponible.';
        showError(err);
      });
      refreshStorage().catch(() => {
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
      const job = await post('/api/jobs', payload());
      selectJob(job.id);
      loadHistory().catch(() => {});
    } catch (err) { showError(err); } finally { $('render').disabled = false; }
  });
  $('calibrate-encoder').addEventListener('click', async () => {
    showError(null);
    $('calibrate-encoder').disabled = true;
    encoderCalibrationRequested = true;
    try {
      await post('/api/encoder/calibration', {});
      await refreshEncoderCalibration();
    } catch (err) {
      encoderCalibrationRequested = false;
      $('calibrate-encoder').disabled = false;
      showError(err);
      refreshEncoderCalibration().catch(() => {});
    }
  });
  $('script').addEventListener('input', showScenes);
  $('mute').addEventListener('change', () => { $('tts').disabled = $('mute').checked; $('speech-rate').disabled = $('mute').checked; });
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
    clearTimeout(encoderCalibrationTimer);
    releaseVideo();
  });
  showScenes();
  init().catch(err => { $('connection').textContent = 'Serveur inaccessible'; showError(err); });
})();
