# Performance & voice batching on GitHub Actions CPU

## Current optimizations

- With Piper Python installed, `tts_batch.py` loads the ONNX voice **once per video**, generating a separate WAV for each scene. The original CLI remains available as a compatibility fallback when the Python module is absent. Backend use is recorded in `tts_backend`; a Python API failure is not silently concealed.
- PCM WAV timing is read directly from the WAV header instead of spawning `ffprobe` for every sentence; unknown WAV formats still fall back to FFprobe.
- Single-scene backgrounds skip an unnecessary FFmpeg concat/remux subprocess.
- GitHub Actions skips `apt-get` when the runner already includes the required FFmpeg and eSpeak tools; otherwise it installs missing tools.
- Every MP4 JSON contains `render_performance`: voice synthesis, media search, intermediate scene encode, final encode and total measured wall times. Model installation and downloads happen before these measurements.

## Benchmarks and limitations

Local isolated test on 16 October 2026 (36 reads of one 1.13-second French WAV): direct `wave` metadata read **0.37 ms total**, compared with **2560.7 ms** for invoking `ffprobe` 36 times. This demonstrates avoided subprocess overhead; it is **not** proof of a corresponding end-to-end speed improvement.

For actual performance comparison, generate the same narration and media at the same format/resolution on comparable GitHub runners, and examine several `render_performance` reports. Network media requests, model installation, CPU contention and cache misses introduce significant variance. The pinned SIWIS voice is validated in the real neural-smoke workflow, which now asserts the `piper-python-batch` backend.

The H.264/AAC video settings, subtitle formats, voice model hash verification and attribution requirements are unchanged.

## Montage multi-scènes : fondu et habillage dans un unique encodage final

Les scènes restent normalisées en clips CFR avant montage, afin de préserver les mouvements de caméra, les droits média et la détermination de la durée. Pour les fondus `fade` et balayages `wipeleft` disposant d'un chevauchement réel, `xfade_final.py` réalise `xfade`, habillage FFmpeg, sous-titres ASS/SRT et encodage H.264 dans **la même opération**. L'ancien processus réencodait d'abord le fondu dans `background.mp4` avant de réencoder les sous-titres ; la nouvelle voie élimine ce réencodage avec perte. Les coupes, transitions trop courtes et scènes uniques gardent leur mode précédent.

Mesure exploratoire locale le 16 octobre 2026, deux scènes **synthétiques** de 3 secondes à 12 images/seconde, H.264 `veryfast`, CRF 23, fondu 2 images, habillage graphique identique. Les fichiers ont été vérifiés à 3 secondes avec les deux variantes :

| Format | Deux étapes précédentes | Encodage fusionné | Différence |
|---|---:|---:|---:|
| 720 × 1280 | 0,806 s | 0,580 s | -28 % |
| 1080 × 1920 | 0,821 s | 0,457 s | -44,3 % |

**Ces chiffres ne représentent ni le temps total d'une vidéo ni une garantie** : ils ne comprennent ni le chargement de médias, ni la synthèse vocale, ni les scènes pré-encodées ; les fonds synthétiques se compressent facilement. Comparer aussi les journaux `render_performance` de vraies vidéos complexes. Le champ `compositing_pipeline` indique `fused-xfade` ou `cut-concat` pour chaque sortie, et les contrôles continuent de vérifier le codec, la durée audio/SRT et les dimensions.
