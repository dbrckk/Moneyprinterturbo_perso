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

### Reproductible : comparaison interne des deux versions

La fonction Python `render(..., use_fused_xfade=False)` active l'ancien montage double-encodage **uniquement pour établir une référence de test** ; l'interface Android reste optimisée par défaut. Le script `bench/compare_pipeline.py` produit à 720p et 1080p deux versions des **mêmes scènes** avec les **mêmes sous-titres**, puis exécute `video_qa.py` sur chaque MP4. Les deux variantes utilisent le même preset, CRF et nombre d'images ; seules les passes de transition changent. Le JSON de benchmark est publié en artefact par le workflow `Pipeline Speed Comparison` et inclut les temps `render_performance` de chaque variante. L'ancienne voie n'est pas proposée aux utilisateurs finaux.


### Direct gradient compositor: one encode instead of three

For **graphic-only** videos with up to six scenes, `fast_abstract.py` now assembles
moving FFmpeg gradients, either `xfade`/wipe or exact cut-concat, original visual
overlays, ASS/SRT subtitles and optional AAC narration in **one final H.264 graph**.
No intermediate per-scene MP4 files are generated. This improves compression
generation loss and removes process and disk overhead. The existing fused media
path remains for real photographs, videos and longer storyboards.

An unsupported gradient graph triggers a **logged return to the old renderer**;
the output JSON identifies the actual strategy as `direct-abstract-onepass`,
`legacy-single-pass-fallback`, `fused-xfade`, `legacy-xfade` or `cut-concat`
under `render_performance.compositing_pipeline`.

**Standalone local benchmark**, 16 October 2026: three 5.5-second synthetic
gradient scenes with 12fps fade, same graphic overlay and H.264 veryfast CRF23,
excluding audio and model downloads. The original method encodes each scene,
their merged transition and the final overlay; the replacement encodes once.

| Frame size | Multi-stage time | Single-pass time | Reduction |
|---|---:|---:|---:|
| 720×1280 | 1.481s | 0.891s | 39.8% |
| 1080×1920 | 2.673s | 1.907s | 28.7% |

These **microbenchmarks** are not a claim of equivalent end-to-end improvement.
The `Pipeline Speed Comparison` workflow performs a reproducible actual-engine
comparison and uploads both storyboard QA reports. The switch
`use_fused_xfade=False` forces the original renderer for internal benchmark
comparison; there is no user-facing downgrade option.

A separate visual regression fixed the portrait karaoke caption grouping: groups
now fit the available width based on font size/margins instead of a fixed
34-character assumption (verified with a rendered 720p frame).

## 360p: montage local de cinq à huit scènes en une seule passe

En 360p (portrait 360×640 ou paysage 640×360), le moteur peut désormais
composer **jusqu'à huit** scènes photographiques/vidéo dans un unique graphe
FFmpeg, avec fondus, mouvements, habillage, sous-titres et narration AAC.
Il supprime les encodages H.264 intermédiaires tout en conservant la cadence
et les contrôles finaux. La sélection reste volontairement limitée à **quatre
scènes en 720p** et **deux scènes en 1080p** pour borner la mémoire et les
encodeurs/décodeurs simultanés. En cas d'échec FFmpeg, le moteur conserve
son chemin de repli à clips normalisés et l'indique dans le JSON de rendu.

**Mesure exploratoire contrôlée, 22 octobre 2026 :** six images source
synthétiques 360×640, fondu 12 fps, texte identique, mode muet et sous-titres
classiques, même environnement FFmpeg 8.0.1 : ancien montage `fused-xfade`
1,95 s contre `direct-media-onepass` 1,32 s (environ 32 % de temps total en
moins). La génération initiale des images n'entre pas dans les durées ; le
contrôle final de qualité H.264 réussit dans les deux cas. Résultat indicatif
**non extrapolable** aux rushs réels, aux autres résolutions ou à la totalité
du traitement IA.

Les régressions couvrent six scènes encodées et huit sources mixtes JPEG/MP4,
avec narration AAC, format de pixels, chronologie, attribution et comparaison
octet par octet des fichiers originaux. Les tests consacrés au moteur fusionné
historique activent explicitement `use_media_onepass=False`, afin que son
contrôle de réparation reste indépendant de la sélection du moteur rapide.
