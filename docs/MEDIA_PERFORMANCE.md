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
