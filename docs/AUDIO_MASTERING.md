# Mobile speech mastering (2026-10-16)

## Objective

Preserve clearly intelligible eSpeak/Piper narration on Android while avoiding
unnecessary second-generation AAC loss. Audio mastering uses free FFmpeg.

The narration WAVs are concatenated on one timeline, then processed by a
single `loudnorm=I=-16:TP=-1.5:LRA=11` pass and encoded once as
48 kHz, 160 kb/s AAC in `narration.m4a`.

All visual FFmpeg pipelines map the AAC packets with `-c:a copy` into the
final H.264 MP4. This avoids a second 128 kb/s AAC encode and keeps the same
audio source for optional acoustic word alignment and final muxing. Silent
videos have no audio and do not report mastering.

## Objective audit

`video.quality.json` gains `audio_loudness.integrated_lufs` and
`audio_loudness.true_peak_dbfs`, measured from the **delivered MP4** with
FFmpeg EBU R128 analysis (not guessed from a requested filter target).
Reported levels outside -21 to -11 LUFS or near 0 dBFS true peak generate a
review warning, not an automatic failure.

A local French eSpeak sample originally measured -22.1 LUFS / -4.7 dBFS true
peak. An isolated FFmpeg one-pass loudnorm sample subsequently measured
approximately -16.7 LUFS / -1.4 dBFS true peak. These sample values
are **not** a general guarantee for Piper, silence-heavy tracks, all mobile
speakers or every audio signal.

There is no new subscription, service API or Python package. Correctness is
covered by a real French narrated MP4 test that verifies H.264/AAC, SRT,
duration, output LUFS and true peak. GitHub Actions smoke tests also verify
the existing French Piper path and the CPU word-alignment path.

## Limits

One-pass loudnorm is dynamic and does not guarantee exact integrated LUFS
on every signal. AAC packet rounding, codec delay and short narrations can
change true peaks slightly. The quality check warns on unsuitable levels
rather than falsely certifying subjective voice quality. Longer narrated
projects should still be auditioned on a real phone.

## Silent sources and safety

When all source WAVs contain digitally silent PCM, the renderer explicitly skips loudnorm because the FFmpeg filter may otherwise feed NaN/Infinity values into the AAC encoder. It still produces a valid silent AAC stream for compatibility tests and records `audio_mastering.method=skipped-silent-input` with **no claim of a target LUFS**. The video metadata also contains an explicit warning that the TTS source is silent, requiring review; non-silent production voices retain mastering. This safeguard does not suppress FFmpeg errors on non-silent input.
