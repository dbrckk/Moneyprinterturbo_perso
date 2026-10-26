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


## Reused sports/action footage: restrained motion-onset alignment

When the same locally imported MP4 supplies multiple narration scenes, the
existing bounded FFmpeg scan (4 small frames/second, first 30 seconds) is
reused to seek past a **very short static start** on subsequent scenes.
This is not an action classifier, goal detector or semantic highlight picker.

An edit may advance by at most **0.75 seconds**, and only when two consecutive
intervals are nearly static followed by three consecutive intervals with
moderate pixel movement. A source hard cut, a one-frame flash, extremely large
camera movement, an absent decoder or insufficient remaining clip duration
keeps the original edit timestamp. Earlier, more confident cut, focus and
turbulence corrections always take priority. First use of a clip is unchanged.
The exported manifest records any adjusted `source_start_seconds`, and
warnings describe how many reused scenes received an onset adjustment.

The analysis stays offline and bounded to footage that is already being
sampled for other quality safeguards. It does not promise to identify
the most interesting moments in a sports recording; visual review is still
required before publication.
