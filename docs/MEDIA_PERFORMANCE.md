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
