# Chronological footage reuse — offline local video inputs

MoneyPrinter historically placed each local MP4 at timestamp zero in every
narration scene. Reusing a long clip produced an identical opening over and
over, breaking chronological continuity.

Free Studio now probes each unique local video **once** with `ffprobe` and
advances that clip's playback cursor by the preceding narration scene's length.
A storyboard containing one 9-second video across three 2.6-second narration
scenes therefore selects offsets **0, 2.6 and 5.2 seconds**. Scenes 1–3
show later moments of the same recording in order.

This behaviour applies to **both** the single-pass and legacy FFmpeg render
engines, including crossfade/cut transitions. The generated `video.json`
includes `media[].source_start_seconds` for inspection in a mobile editor.

- If the source clip ends, playback loops and its start offset wraps within
  the source duration; the renderer never deliberately seeks beyond its end.
- Different source videos maintain independent cursors. Still images and
  Wikimedia Commons photos do not incur new duration probes.
- If a local clip's duration cannot be read, that clip keeps its previous
  beginning-of-video behaviour, and the manifest warns of the limitation.
- Input media audio remains muted in the Free Studio pipeline, preserving
  one narration track and consistent speech loudness.
- Clips are ordered by the user's filenames as before; this provides
  **temporal playback continuity**, not semantic detection of events, camera
  cuts, clip order, or spoken narration inside the source media.

The implementation needs only Python standard library, FFmpeg and ffprobe.
It does not upload local videos, invoke a paid service, or introduce an AI
dependency. Regression tests validate seek planning, wrap-around, single
duration probes, bad-media fallback, and actual decoded RGB frame colours in
both FFmpeg engines.
