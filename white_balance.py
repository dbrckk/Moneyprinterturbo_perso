"""Conservative neutral-reference white-balance matching for local phone videos.

This is a small, offline, explainable color-cast correction, not scene
recognition or professional color grading. When neutral-looking pixels cannot
be confidently identified in *every* source, all videos are kept unchanged.
"""
from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path
import statistics
import subprocess
from typing import Callable, TYPE_CHECKING

from color_sample_plan import color_sample_windows

if TYPE_CHECKING:
    from media_assets import MediaAsset

_MAX_SOURCES = 8
_MAX_GAIN_CHANGE = 0.06
_WIDTH, _HEIGHT = 48, 27
_FRAME_BYTES = _WIDTH * _HEIGHT * 3
_IDENTITY = (1.0, 1.0, 1.0)


def sample_neutral_rgb(path: Path, start: float, duration: float) -> tuple[float, float, float] | None:
    """Measure low-saturation midtones in <=3s of 48x27 @1fps phone footage.

    Reject vivid scenes instead of guessing their intended neutral balance.
    Reading bytes from FFmpeg avoids new Python packages and online services.
    """
    if (type(start) not in (int, float) or type(duration) not in (int, float)
            or not math.isfinite(start) or not math.isfinite(duration)
            or start < 0 or duration < 0.5):
        return None
    try:
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
             "-ss", f"{start:.3f}", "-i", str(path),
             "-t", f"{min(3.0, duration):.3f}", "-map", "0:v:0", "-an",
             "-vf", f"fps=1,scale={_WIDTH}:{_HEIGHT}:flags=area,format=rgb24",
             "-f", "rawvideo", "pipe:1"],
            capture_output=True, timeout=12, check=True)
        raw = result.stdout
        if not raw or len(raw) % _FRAME_BYTES or len(raw) > 4 * _FRAME_BYTES:
            return None
        neutral: list[tuple[float, float, float]] = []
        for offset in range(0, len(raw), _FRAME_BYTES):
            frame = raw[offset:offset + _FRAME_BYTES]
            count = 0
            sums = [0, 0, 0]
            for i in range(0, len(frame), 3):
                red, green, blue = frame[i], frame[i + 1], frame[i + 2]
                if (55 <= min(red, green, blue) and
                        max(red, green, blue) <= 205 and
                        max(red, green, blue) - min(red, green, blue) <= 42):
                    sums[0] += red
                    sums[1] += green
                    sums[2] += blue
                    count += 1
            # A small neutral patch should not recolor an entire vivid scene.
            if count < (_WIDTH * _HEIGHT) // 3:
                return None
            neutral.append(tuple(total / count for total in sums))
        if not neutral:
            return None
        channels = tuple(round(statistics.median(frame[channel] for frame in neutral), 3)
                         for channel in range(3))
        return channels
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return None


def white_balance_suffix(asset: object) -> str:
    """Validate gains and append exactly one safe FFmpeg filter if necessary."""
    gains = getattr(asset, "white_balance_gains", _IDENTITY)
    if (not isinstance(gains, tuple) or len(gains) != 3 or
            any(type(v) not in (int, float) or not math.isfinite(v) or
                not 1 - _MAX_GAIN_CHANGE <= v <= 1 + _MAX_GAIN_CHANGE
                for v in gains)):
        raise ValueError("Invalid per-channel color gains")
    if gains == _IDENTITY:
        return ""
    return (f",colorchannelmixer=rr={gains[0]:.4f}:"
            f"gg={gains[1]:.4f}:bb={gains[2]:.4f}")


def balance_white(
    slots: list[MediaAsset | None], durations: list[float],
    *, probe: Callable[[Path, float, float], tuple[float, float, float] | None] = sample_neutral_rgb,
) -> tuple[list[MediaAsset | None], list[str]]:
    """Align mild opposing casts around the median of measurable neutrals."""
    windows = color_sample_windows(slots, durations)
    if windows is None:
        return slots, []
    samples: dict[Path, tuple[float, float, float]] = {}
    for path, positions in windows.items():
        per_scene: list[tuple[float, float, float]] = []
        for start, length in positions:
            try:
                rgb = probe(path, start, length)
            except (OSError, ValueError, TypeError, subprocess.SubprocessError):
                return slots, []
            if (not isinstance(rgb, tuple) or len(rgb) != 3 or
                    any(type(v) not in (float, int) or not math.isfinite(v)
                        for v in rgb) or
                    not 70 <= min(rgb) <= max(rgb) <= 190 or
                    max(rgb) - min(rgb) > 37):
                return slots, []
            per_scene.append(tuple(float(v) for v in rgb))
        # A single repeated source may include a naturally warm interior
        # and cool outdoor sequence. Never grade both with one fixed gain.
        ratios = [(rgb[0] / rgb[1], rgb[2] / rgb[1]) for rgb in per_scene]
        if any(max(values[i] for values in ratios) -
               min(values[i] for values in ratios) > 0.06 for i in (0, 1)):
            return slots, []
        samples[path] = tuple(
            statistics.median(rgb[i] for rgb in per_scene) for i in range(3))
    # Disagreement must be meaningful but mild. Otherwise preserve the
    # original artistic color temperature and avoid needless grading.
    ratios = {path: (rgb[0] / rgb[1], rgb[2] / rgb[1])
              for path, rgb in samples.items()}
    spread = max(max(values[i] for values in ratios.values()) -
                 min(values[i] for values in ratios.values()) for i in (0, 1))
    if not 0.07 <= spread <= 0.35:
        return slots, []
    target_r = statistics.median(pair[0] for pair in ratios.values())
    target_b = statistics.median(pair[1] for pair in ratios.values())
    corrections: dict[Path, tuple[float, float, float]] = {}
    for path, rgb in samples.items():
        r_ratio, b_ratio = ratios[path]
        raw = (1.0 + 0.65 * (target_r / r_ratio - 1.0),
               1.0,
               1.0 + 0.65 * (target_b / b_ratio - 1.0))
        # Preserve sampled luma instead of unintentionally undoing the
        # separately-calculated per-source exposure matching.
        source_luma = 54 * rgb[0] + 183 * rgb[1] + 19 * rgb[2]
        shifted_luma = sum(weight * channel * gain
                           for weight, channel, gain in zip((54, 183, 19), rgb, raw))
        normalize = source_luma / shifted_luma
        corrections[path] = tuple(round(max(1 - _MAX_GAIN_CHANGE,
                                            min(1 + _MAX_GAIN_CHANGE, gain * normalize)), 4)
                                  for gain in raw)
    if not any(max(abs(value - 1.0) for value in gains) >= 0.007
               for gains in corrections.values()):
        return slots, []
    balanced = [
        replace(asset, white_balance_gains=corrections[asset.file])
        if asset is not None and asset.source_url == "local" and asset.kind == "video"
        and asset.file in corrections else asset
        for asset in slots
    ]
    return balanced, [f"Matched neutral color casts across {len(windows)} local video clips"]
