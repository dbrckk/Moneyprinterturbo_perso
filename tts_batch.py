"""Optional Piper in-process batch synthesis and near-zero-cost WAV timing.

Piper is optional. Without its Python package, callers can retain CLI fallback.
"""
from __future__ import annotations

import importlib
import math
from pathlib import Path
import wave


def wav_duration_seconds(path: Path) -> float:
    """Read PCM WAV duration without starting ffprobe for each sentence."""
    with wave.open(str(path), "rb") as reader:
        sample_rate = reader.getframerate()
        frame_count = reader.getnframes()
        if sample_rate <= 0 or frame_count <= 0:
            raise ValueError("Invalid WAV header (zero samples or sample rate)")
        duration = frame_count / sample_rate
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Invalid WAV duration")
    return duration


def synthesize_piper_batch(sentences: list[str], model: Path, work: Path) -> bool:
    """Return False only if Piper Python is absent; fail loudly otherwise."""
    if not sentences or len(sentences) > 18 or any(not isinstance(s, str) or not s.strip() for s in sentences):
        raise ValueError("Invalid Piper batch")
    try:
        piper = importlib.import_module("piper")
    except ModuleNotFoundError as exc:
        if exc.name == "piper":
            return False
        raise
    voice_class = getattr(piper, "PiperVoice", None)
    if voice_class is None or not callable(getattr(voice_class, "load", None)):
        raise RuntimeError("Installed Piper package does not expose PiperVoice.load")
    voice = voice_class.load(str(model))
    synthesize = getattr(voice, "synthesize_wav", None)
    if not callable(synthesize):
        synthesize = getattr(voice, "synthesize", None)
    if not callable(synthesize):
        raise RuntimeError("Installed Piper voice cannot synthesize WAV")
    for index, sentence in enumerate(sentences):
        output = work / f"voice_{index:02}.wav"
        with wave.open(str(output), "wb") as writer:
            synthesize(sentence, writer)
        wav_duration_seconds(output)
    return True
