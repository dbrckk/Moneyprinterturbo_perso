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
