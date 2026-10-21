"""Bounded, offline shot-boundary detection for local phone footage.

This only detects high-confidence abrupt visual changes. It never infers the
meaning of filmed events or overwrites originals. On failure it returns no
cuts, preserving the established chronological timeline.
"""
from __future__ import annotations

import math
from pathlib import Path
import subprocess

_FRAME_WIDTH = 48
_FRAME_HEIGHT = 28
_FRAME_BYTES = _FRAME_WIDTH * _FRAME_HEIGHT * 3
_SAMPLE_FPS = 4
_MAX_SECONDS = 30.0


def _change(first: bytes, second: bytes) -> float:
    """Mean absolute RGB channel difference in the range 0..255."""
    return sum(abs(a - b) for a, b in zip(first, second)) / _FRAME_BYTES


def probe_hard_cuts(path: Path, duration: float) -> list[float]:
    """Locate clear transitions followed and preceded by stable footage.

    The entire analysis stays below 30 seconds, 4 fps and 48x28 RGB.
    Multiple frames are required: moving cameras and flickers must not be
    mistaken for reliable editorial cuts. No third-party Python packages.
    """
    if (type(duration) not in (float, int) or
            not math.isfinite(duration) or duration < 2.0):
        return []
    window = min(_MAX_SECONDS, float(duration))
    try:
        process = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
             "-t", f"{window:.3f}", "-i", str(path),
             "-map", "0:v:0", "-an",
             "-vf", f"fps={_SAMPLE_FPS},scale={_FRAME_WIDTH}:{_FRAME_HEIGHT}:flags=area,format=rgb24",
             "-f", "rawvideo", "pipe:1"],
            capture_output=True, timeout=18, check=True)
        raw = process.stdout
        if not raw or len(raw) % _FRAME_BYTES:
            return []
        frames = [raw[i:i + _FRAME_BYTES]
                  for i in range(0, len(raw), _FRAME_BYTES)]
        if len(frames) < 5 or len(frames) > _MAX_SECONDS * _SAMPLE_FPS + 2:
            return []
        changes = [_change(frames[i - 1], frames[i])
                   for i in range(1, len(frames))]
        cuts: list[float] = []
        # A cut at frame i requires stability immediately before and after
        # the step, and a substantial visual difference at that instant.
        for i in range(2, len(frames) - 1):
            timestamp = i / _SAMPLE_FPS
            if (changes[i - 2] <= 18.0 and
                    changes[i - 1] >= 52.0 and
                    changes[i] <= 18.0 and
                    0.5 <= timestamp <= window - 0.5 and
                    (not cuts or timestamp - cuts[-1] >= 0.75)):
                cuts.append(round(timestamp, 3))
        return cuts[:48]
    except (OSError, subprocess.SubprocessError, ValueError, OverflowError):
        return []
