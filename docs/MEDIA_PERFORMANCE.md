# JPEG and MP4 single-encode performance

The module fast_media.py can composite real JPEG/PNG stills, MP4 footage and
generated gradients without first encoding intermediate scene MP4 files.

- At most four scenes at 360p/720p or two scenes at 1080p.
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
