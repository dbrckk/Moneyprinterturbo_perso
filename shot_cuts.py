"""Bounded, offline shot-boundary detection for local phone footage.

This only detects high-confidence abrupt visual changes. It never infers the
meaning of filmed events or overwrites originals. On failure it returns no
cuts, preserving the established chronological timeline.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import subprocess

_FRAME_WIDTH = 96
_FRAME_HEIGHT = 54
_FRAME_BYTES = _FRAME_WIDTH * _FRAME_HEIGHT * 3
_SAMPLE_FPS = 4
_MAX_SECONDS = 30.0


def _change(first: bytes, second: bytes) -> float:
    """Mean absolute RGB channel difference in the range 0..255."""
    return sum(abs(a - b) for a, b in zip(first, second)) / _FRAME_BYTES


@dataclass(frozen=True)
class ShotProfile:
    """Single, bounded video decode shared by cuts and focus detection."""
    cuts: tuple[float, ...] = ()
    focus: tuple[float, ...] = ()


def _focus_energy(frame: bytes) -> float:
    """Cheap local contrast proxy, not a general artistic-quality score."""
    width = _FRAME_WIDTH
    height = _FRAME_HEIGHT
    total = 0
    # One luminance channel avoids rewarding RGB color differences alone.
    # Neighbor comparisons are sampled every other pixel for Android CPU.
    for y in range(2, height - 1, 2):
        for x in range(2, width - 1, 2):
            index = (y * width + x) * 3
            light = (frame[index] * 2 + frame[index + 1] * 5 +
                     frame[index + 2]) >> 3
            left = (frame[index - 3] * 2 + frame[index - 2] * 5 +
                    frame[index - 1]) >> 3
            above = (frame[index - width * 3] * 2 +
                     frame[index - width * 3 + 1] * 5 +
                     frame[index - width * 3 + 2]) >> 3
            total += abs(light - left) + abs(light - above)
    samples = len(range(2, width - 1, 2)) * len(range(2, height - 1, 2))
    return round(total / (2 * samples), 3)


def probe_shot_profile(path: Path, duration: float) -> ShotProfile:
    """Inspect up to 30 seconds, decoding only 4 small frames per second.

    Detect strongly separated stable shots and measure local image detail
    with the *same* FFmpeg subprocess. No cloud calls or model weights.
    """
    empty = ShotProfile()
    if (type(duration) not in (float, int) or
            not math.isfinite(duration) or duration < 2.0):
        return empty
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
            return empty
        frames = [raw[i:i + _FRAME_BYTES]
                  for i in range(0, len(raw), _FRAME_BYTES)]
        if len(frames) < 5 or len(frames) > _MAX_SECONDS * _SAMPLE_FPS + 2:
            return empty
        changes = [_change(frames[i - 1], frames[i])
                   for i in range(1, len(frames))]
        cuts: list[float] = []
        for i in range(2, len(frames) - 1):
            timestamp = i / _SAMPLE_FPS
            if (changes[i - 2] <= 18.0 and
                    changes[i - 1] >= 52.0 and
                    changes[i] <= 18.0 and
                    0.5 <= timestamp <= window - 0.5 and
                    (not cuts or timestamp - cuts[-1] >= 0.75)):
                cuts.append(round(timestamp, 3))
        return ShotProfile(tuple(cuts[:48]), tuple(_focus_energy(f) for f in frames))
    except (OSError, subprocess.SubprocessError, ValueError, OverflowError):
        return empty


def probe_hard_cuts(path: Path, duration: float) -> list[float]:
    """Backwards-compatible cut-only API used by existing callers and tests."""
    return list(probe_shot_profile(path, duration).cuts)


def clearer_start(profile: ShotProfile, current: float, scene: float,
                  clip_duration: float) -> float:
    """Move at most 0.75s ahead to a sustained noticeably clearer frame.

    A nearby cut is handled separately and cannot be crossed here.
    Comparing neighboring samples within a shot reduces preference for
    completely different backgrounds over naturally soft subject matter.
    Never alter a start near the end of a clip or beyond the 30s sample.
    """
    if not isinstance(profile, ShotProfile) or len(profile.focus) < 5:
        return current
    if not all(math.isfinite(v) and v >= 0 for v in
               (current, scene, clip_duration)) or scene <= 0:
        return current
    base = int(current * _SAMPLE_FPS + 1e-6)
    q = profile.focus
    if base + 1 >= len(q) or current + scene + 0.25 >= clip_duration:
        return current
    baseline = min(q[base], q[base + 1])
    # Uniform darkness is handled by the separate intro detector, not by
    # sharpness: black scenes can be intentional.
    if baseline < 1.0:
        return current
    for index in range(base + 1, min(base + 4, len(q) - 1)):
        candidate = index / _SAMPLE_FPS
        if (candidate <= current + 0.12 or candidate - current > 0.75 or
                candidate + scene > clip_duration - 0.02):
            continue
        if any(current - 0.10 <= cut <= candidate + 0.10 for cut in profile.cuts):
            continue
        improvement = min(q[index], q[index + 1])
        if improvement >= baseline * 1.45 and improvement - baseline >= 1.4:
            return round(candidate, 3)
    return current
