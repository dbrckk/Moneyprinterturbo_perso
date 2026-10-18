# Delivery QA — synchronized video, audio and subtitles

The mobile studio runs `video_qa.py` after rendering and before publishing
individual downloadable MP4/SRT/storyboard artifacts. The audit uses only
Python's standard library and the existing FFmpeg / ffprobe installation.

## Why container duration is insufficient

An MP4 container's `format.duration` generally reports the **longest**
stream. A three-second AAC narration together with 1.5 seconds of H.264
frames still looks like a three-second video when checking only this value.

The QA audit now compares both `streams[video].duration` and
`streams[audio].duration` against the narration's expected duration,
**independently** of the MP4 format duration. It rejects major drift and
missing stream duration metadata. The measured durations appear in
`video.quality.json` under `checks.video_stream_duration` and
`checks.audio_stream_duration`. Muted videos have a null audio value.

This catches a real class of apparently successful but prematurely ended
renders. A valid stream duration is *not* a guarantee every compressed frame
decodes or that the content is aesthetically correct. Existing midpoint
contact-sheet extraction and FFmpeg audio activity/loudness checks remain
required independently.

## Exact editable subtitle integrity

The sidecar SRT is now parsed as complete numbered subtitle blocks. QA checks:

- SRT blocks are well formed and numbered consecutively from scene 1.
- Each start/end time matches the original narrated segment.
- Each subtitle's **actual words** match the scene text in the render manifest.
- UTF-8 BOM, Windows CRLF and line-wrapped subtitle text remain supported.

Previously, an SRT with correct cue timestamps but incorrect text could pass
the audit and be offered to mobile publishers. The new check fails delivery
rather than claiming that the subtitle file is synchronized with its narration.

### Test evidence

Real FFmpeg tests deliberately generate two structurally valid MP4s: one
with a three-second AAC stream and a 1.5-second video stream, and another
with the durations reversed. Both must fail QA. Tests also cover a valid
two-scene SRT, mismatched text with correct timestamps, repeated scene
numbers, malformed cue blocks, BOM and CRLF handling.

The audit is a **technical delivery gate**, not proof of factual accuracy,
licensing rights, speaker naturalness or visual relevance. Human review of
`video.storyboard.png` and `video.quality.json` is still recommended.
