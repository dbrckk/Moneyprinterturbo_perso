# JPEG and MP4 single-encode performance

The module fast_media.py can composite real JPEG/PNG stills, MP4 footage and
generated gradients without first encoding intermediate scene MP4 files.

- At most eight scenes at 360p, four scenes at 720p, or two scenes at 1080p in the direct, single-encode path.
- Normalized CFR, square pixels, frame-accurate transitions and source audio isolation.
- Full-range JPEG colors are converted to limited-range mobile yuv420p, also in the legacy path.
- If the single-pass FFmpeg graph fails, the renderer uses the earlier composition path.
- The video JSON exposes the actual compositing_pipeline.
- The final H.264 bitrate/preset, editable SRT and kinetic ASS are unchanged.

## Reproducible benchmark

The workflow named Media Pipeline Speed Comparison generates genuine JPEG and
H.264 MP4 files using FFmpeg test patterns. The imagery is **synthetic**, not an
actual photograph of a place. It compares the same two scenes at 720p and 1080p,
with the same graphic overlays, frame rate (12 FPS), and fade transition.

Each video must pass video_qa.py, including pixel format and the scene/subtitle
timeline. The benchmark uploads comparison.json and the contact sheets.
Timing covers the renderer only, not fixture creation, runner setup, model
downloads or external Wikimedia lookups. Results vary by CPU and input detail.

## Résultats mesurés dans GitHub Actions, 16 octobre 2026

Comparaison sur le même runner avec un JPEG et un MP4 de test, deux scènes muettes de 5,25 s, fondu, 12 FPS :

| Résolution | Montage précédent | Encodage unique | Gain observé |
|---|---:|---:|---:|
| 720p | 1,664 s | 1,176 s | 29,3 % |
| 1080p | 2,934 s | 2,210 s | 24,7 % |

L'exécution 38891936478 a vérifié H.264, yuv420p, durée, scènes et SRT sur chaque MP4. Les planches de contrôle avant/après présentent les mêmes scènes ; l'écart absolu moyen des pixels des planches (redimensionnées) était de 0,325/255 à 720p et 0,216/255 à 1080p. **Ces chiffres ne concernent que cet essai synthétique** et n'établissent ni gain universel ni qualité esthétique.

Le benchmark mesure désormais aussi le SSIM sur la vidéo entière et échoue sous 0,95. Il joint les quatre vidéos d'essai, les planches de scènes et les JSON pour inspection indépendante. SSIM mesure la proximité visuelle entre les deux méthodes, pas la qualité éditoriale ou le réalisme du contenu.

## More reliable timing protocol

The current benchmark now runs **three complete renders per method at each resolution** and reports the **median** instead of one potentially noisy observation. It alternates method order between trials to limit systematic runner-order bias, checks the MP4 and subtitles after **every** render, and calculates whole-video SSIM between the final pair. `comparison.json` includes every raw trial in `trials_seconds`. The measured media remains a reproducible synthetic JPEG plus MP4; no claim is made about typical user-uploaded footage or external Commons download times.

## Bounded preparation of longer 720p projects (22 October 2026)

When five or more scene clips must be normalized at 720p (the legacy/fused
composition paths), their independent FFmpeg encoders can now use **two
workers**, never more. Frames, per-scene indexed filenames, H.264 settings,
fade and cut timing, caption streams and input files are unchanged. The
collection step returns clips in their original order regardless of encoder
completion order. Any encoder failure aborts the render rather than composing
incomplete output. Single-scene and short jobs remain serial.

**1080p remains deliberately serial** for intermediate scene preparation:
measuring two simultaneous 1080p encodes in an isolated 4-CPU / 4-GiB
container increased combined FFmpeg RSS from about 460 MiB to 921 MiB,
for only a small speed improvement in this test. A six-scene 720p encode
fixture (48 frames per scene, H.264 veryfast/CRF23) measured about 2.01 s
serial vs 1.39 s with two encoders; combined RSS rose from 232 MiB to
463 MiB. These timings exclude scene selection, narration, compositing and
upload; source scenes were synthetic video exports and the numbers should
not be generalized to phones or all media types.

The final frame-accurate composition is still executed **only after** all
scene encoders complete. 720p projects with four or fewer sources continue
to use the direct-media single-pass pipeline by default. Unit regressions
verify worker concurrency, input ordering, memory-conservative 1080p policy
and fail-closed behavior. Real five-scene 720p cut and fade exports are
checked by the delivery QA suite.

## Updated GitHub Actions reference (22 October 2026)

The successful [Media Pipeline Speed Comparison run](https://github.com/dbrckk/Moneyprinterturbo_perso/actions/runs/39846609763)
measured medians over three trials: 720p 1.681s fused vs 1.229s direct
(26.9% reduction), and 1080p 2.970s fused vs 2.181s direct (26.6%
reduction). Full video SSIM was 0.993633 and 0.995633 respectively.
These results concern two synthetic scenes and validate the existing
single-pass path, *not* the new five-plus-scene parallel pre-encoder.

## Duration-aware photographic camera movement (22 October 2026)

Single-pass media and legacy scene encoders now apply the **same** bounded
photo zoom progression across the **whole output scene**, including the
extra frames required by a crossfade. Previously each photo advanced by
0.0008 zoom units per output frame until hitting 1.10; at 12 FPS, a photo
longer than about 10 seconds appeared to stop moving before the narration
ended. Extreme-wide images on a blurred soft matte stopped even earlier
because their 4% zoom reached its cap after about 67 frames.

The new FFmpeg `zoompan` uses the output-frame index and exact quantized
scene duration. Full-cover photos advance gradually to at most 1.08× and
extreme-wide soft-matte photos to at most 1.04×. The end of a long photo
scene therefore does not contain an unintended static plateau. Video
clips, original images, FPS and H.264 quality parameters are unchanged;
no ML/cloud provider, new FFmpeg process or external dependency is added.

Regression coverage includes two actual 15-second JPEG exports (portrait
and extreme-wide), each rendered through the direct and legacy paths,
with FFprobe frame-count checks, decoded late-scene pixel comparisons,
source-byte integrity and matching camera positions. This establishes the
presence of continued movement, not a subjective claim of artistic quality.

## Encodage H.264 1080p : nombre de threads borné (23 octobre 2026)

Les rendus Full HD 1080 × 1920 limitent désormais le **codeur** libx264 à
**2 threads** au moment de l'encodage, quel que soit le chemin de montage
(scenes normalisées, fond avec fondus, encodage final et seconde tentative de
réparation). Le calcul des filtres, la fréquence d'images, les sous-titres,
les presets et le CRF restent inchangés. Les sorties 360p et 720p conservent
le choix de threads précédent. La politique évite d'augmenter sans limite
les tampons internes de x264 sur un smartphone ; elle ne garantit pas une
limite globale de RSS à toute la chaîne FFmpeg.

Sur un échantillon synthétique `testsrc2` de 96 images à 1080 × 1920,
24 images/s, libx264 veryfast CRF 23 (Linux, 23 octobre 2026), une seule
mesure de chaque option a fourni :

| x264 | Pic RSS FFmpeg | Durée d'encodage | SSIM vs auto |
|---|---:|---:|---:|
| Threads automatiques | 438 012 KiB | 1,62 s | référence |
| 2 threads | 329 848 KiB | 1,12 s | 0,995334 |
| 4 threads | 375 736 KiB | 1,04 s | 0,995336 |

Soit **24,7 % de RSS en moins** dans cette mesure pour 2 threads versus
threads automatiques. Le choix privilégie la mémoire plutôt que la vitesse
maximale. Ce n'est pas un benchmark sur Android et ces gains ne doivent pas
être généralisés à toutes les sources, tous les filtres, ni toutes les
configurations matérielles. Les tests automatisés vérifient la politique
de préparation des commandes et la validité des fichiers réels.

## Préparation des montages longs selon la mémoire disponible (24 octobre 2026)

Les projets **720p d'au moins cinq scènes** ne lancent plus systématiquement
un deuxième encodeur FFmpeg. Juste avant la normalisation, le studio consulte
`MemAvailable` dans `/proc/meminfo` et, si elles sont présentes, les limites
cgroup v2 (`memory.max` / `memory.current`) ou v1. La mémoire utilisable est la
**plus petite** des valeurs disponibles. Un deuxième travailleur n'est autorisé
que si le processeur possède au moins deux cœurs et si au moins **2 Gio**
sont annoncés disponibles. Faute de mesure fiable, le travail est séquentiel.

Cette règle ne diminue ni la résolution, ni le débit d'images, ni le nombre
de scènes. Les photos, vidéos, sous-titres et profils H.264 restent identiques.
Elle peut augmenter le temps de rendu sur les appareils sous pression mémoire,
mais réduit le risque d'avoir deux gros encodages concurrents sur un téléphone.
L'estimation `/proc` et les quotas cgroup ne constituent **pas une garantie**
contre le gestionnaire de mémoire Android ni contre la surchauffe. Les projets
1080p restent strictement séquentiels pour les clips intermédiaires.

Le manifeste MP4 renseigne désormais `render_performance.scene_encoding_workers` :
`0` lorsque le montage utilise l'encodage direct sans clips intermédiaires,
`1` pour la préparation séquentielle, `2` lorsque la mémoire permet deux
encodeurs. Ainsi un changement de performance est explicable après le rendu.

### Mesurer un montage synthétique multiscène sur son appareil

Depuis le répertoire du dépôt (Termux/Android, ordinateur ou runner GitHub) :

```sh
python bench/long_mobile_montage.py --resolution 720
```

Le banc d'essai génère six fichiers fictifs (trois PNG et trois MP4), un
montage avec fondus, narration française eSpeak, piste AAC et sous-titres SRT.
Il vérifie H.264, les six scènes, le manifeste et la qualité de livraison,
ainsi que l'intégrité des sources. Tous les fichiers sont effacés par défaut.
Pour conserver les MP4 et le JSON de mesures dans un **dossier vide** :

```sh
python bench/long_mobile_montage.py --resolution 720 --out-dir outputs/benchmark-long-local
```

Une comparaison explicite des temps de rendu avec un puis deux encodeurs est
possible par `--compare-workers`. Elle est **refusée** lorsque l'appareil ne
satisfait pas les mêmes conditions de mémoire et de CPU que le mode parallèle.
La comparaison est coûteuse et peut chauffer le téléphone ; elle n'est jamais
lancée automatiquement. Les métriques portent sur des sources *synthétiques*,
non sur des vrais clips filmés ; elles **ne mesurent pas** le pic de RSS, la
batterie, les limites propres au GPU, ni le comportement thermique sur Android.

Un workflow GitHub Actions volontaire, **Long Local Montage Benchmark**, permet
de lancer ce même rendu synthétique depuis le navigateur d'un smartphone et
de récupérer le JSON, les MP4 et les sous-titres comme artefacts. Le résultat
mesure un **runner Linux GitHub**, pas la mémoire disponible ni la vitesse du
smartphone. Pour mesurer le téléphone lui-même, exécuter la commande ci-dessus
dans Termux ; aucun envoi vers GitHub n'est requis.
