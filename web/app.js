'use strict';
(() => {
  const $ = id => document.getElementById(id);
  let token = sessionStorage.getItem('mpt_studio_token') || '';
  let currentId = null;
  let videoUrl = null;
  let pollTimer = null;
  let lastStatus = '';
  const state = {has_ollama: false, has_piper: false};
  const endpoint = (id, name = '') => `/api/jobs/${id}${name ? '/' + name : ''}`;

  async function api(url, options = {}) {
    const headers = Object.assign({}, options.headers || {});
    if (token) headers.Authorization = `Bearer ${token}`;
    if (options.body) headers['Content-Type'] = 'application/json';
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

  function payload() {
    return {
      topic: $('topic').value.trim(), lang: $('lang').value,
      script: $('script').value.trim(), aspect: $('aspect').value,
      resolution: Number($('resolution').value), visuals: $('visuals').value,
      tts: $('tts').value, speech_rate: Number($('speech-rate').value),
      mute: $('mute').checked, scene_queries: $('queries').value.trim()
    };
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
  async function loadVideo(id) {
    if (videoUrl) URL.revokeObjectURL(videoUrl);
    videoUrl = null;
    $('video').pause();
    $('video').removeAttribute('src');
    $('video').classList.add('hidden');
    $('placeholder').classList.remove('hidden');
    try {
      const media = await api(endpoint(id, 'video.mp4'));
      const blob = await media.blob();
      if (currentId !== id) return;
      videoUrl = URL.createObjectURL(blob);
      $('video').src = videoUrl;
      $('video').classList.remove('hidden');
      $('placeholder').classList.add('hidden');
    } catch (err) {
      $('job-notice').textContent = `Vidéo disponible, mais aperçu impossible : ${err.message}`;
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
  async function init() {
    const health = await json('/api/health');
    $('connection').textContent = health.ok ? 'Moteur disponible' : 'Indisponible';
    try {
      const config = await json('/api/config');
      state.has_ollama = config.has_ollama;
      state.has_piper = config.has_piper;
      $('tts').querySelector('option[value="piper"]').disabled = !config.has_piper;
      $('visuals').querySelector('option[value="local"]').disabled = !config.has_local_media;
      $('script-note').textContent = config.has_ollama ? 'Scénario générable avec Ollama local.' : 'Brouillon déterministe, sans génération IA.';
      $('auth').classList.add('hidden');
      loadHistory().catch(showError);
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
      const job = await post('/api/jobs', payload());
      selectJob(job.id);
      loadHistory().catch(() => {});
    } catch (err) { showError(err); } finally { $('render').disabled = false; }
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
      try {
        const name = button.dataset.file;
        const blob = await (await api(endpoint(currentId, name))).blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url; link.download = `${currentId.slice(0, 8)}-${name}`;
        document.body.append(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 3000);
      } catch (err) { showError(err); }
    });
  }
  window.addEventListener('pagehide', () => { clearTimeout(pollTimer); if (videoUrl) URL.revokeObjectURL(videoUrl); });
  showScenes();
  init().catch(err => { $('connection').textContent = 'Serveur inaccessible'; showError(err); });
})();
