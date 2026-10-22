"""Small, predictable exposure matching for phone video montages.

A few low-resolution frames are sampled locally with FFmpeg; matching is
applied only when all local video sources have valid, moderately different
luminance. Dark/night, blown-out daylight, unknown footage and large scene
changes retain the previous look. Source files are never changed.
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

MAX_SOURCES = 8
MAX_SHIFT = 0.05
_SAMPLE_WIDTH = 48
_SAMPLE_HEIGHT = 27
_FRAME_BYTES = _SAMPLE_WIDTH * _SAMPLE_HEIGHT * 3


def sample_luminance(path: Path, start: float, duration: float) -> float | None:
    """Sample at most three seconds of decoded RGB frames, at 1 fps."""
    if (type(start) not in (int, float) or type(duration) not in (int, float)
            or not math.isfinite(start) or not math.isfinite(duration)
            or start < 0 or duration < 0.5):
        return None
    seconds = min(3.0, duration)
    try:
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
             "-ss", f"{start:.3f}", "-i", str(path),
             "-t", f"{seconds:.3f}", "-map", "0:v:0", "-an",
             "-vf", f"fps=1,scale={_SAMPLE_WIDTH}:{_SAMPLE_HEIGHT}:flags=area,format=rgb24",
             "-f", "rawvideo", "pipe:1"],
            capture_output=True, timeout=12, check=True,
        )
        buf = result.stdout
        if not buf or len(buf) % _FRAME_BYTES or len(buf) > 4 * _FRAME_BYTES:
            return None
        readings = []
        for offset in range(0, len(buf), _FRAME_BYTES):
            frame = buf[offset:offset + _FRAME_BYTES]
            light = sum(54 * frame[i] + 183 * frame[i + 1] + 19 * frame[i + 2]
                        for i in range(0, len(frame), 3))
            readings.append(light / (256 * (len(frame) // 3)))
        return round(statistics.median(readings), 3)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def exposure_filter(asset: object) -> str:
    """Common FFmpeg eq options for both rendering paths."""
    value = getattr(asset, "exposure_shift", 0.0)
    if (type(value) not in (float, int) or not math.isfinite(value)
            or abs(value) > MAX_SHIFT):
        raise ValueError("Invalid automated exposure correction")
    return f"eq=contrast=1.05:saturation=1.07:brightness={value:.4f}"


def balance_exposure(
    slots: list[MediaAsset | None], durations: list[float],
    *, probe: Callable[[Path, float, float], float | None] = sample_luminance,
) -> tuple[list[MediaAsset | None], list[str]]:
    """Match mild clip-to-clip differences; preserve artistic lighting.

    Only user-owned videos are eligible and each unique path is measured
    once per distinct source window (at most twelve overall). If one reused
    clip varies markedly between scene offsets, preserve the full cohort. Images and Commons remain intact.
    """
    windows = color_sample_windows(slots, durations)
    if windows is None:
        return slots, []
    readings: dict[Path, float] = {}
    for path, positions in windows.items():
        measured: list[float] = []
        for start, duration in positions:
            try:
                value = probe(path, start, duration)
            except (OSError, ValueError, TypeError, subprocess.SubprocessError):
                return slots, []
            if (type(value) not in (float, int) or not math.isfinite(value)
                    or not 45 <= value <= 210):
                return slots, []
            measured.append(float(value))
        # Do not impose one brightness adjustment across dramatically
        # different parts of a reused clip (e.g. moving indoors/outdoors).
        if max(measured) - min(measured) > 18:
            return slots, []
        readings[path] = statistics.median(measured)
    lower, upper = min(readings.values()), max(readings.values())
    # Substantially different light conditions are usually deliberate (night
    # vs day, sunset vs stage spotlight) and must not be flattened.
    if upper - lower < 12 or upper - lower > 75:
        return slots, []
    target = statistics.median(readings.values())
    changes = {path: round(max(-MAX_SHIFT, min(MAX_SHIFT,
                         0.4 * (target - level) / 255)), 4)
               for path, level in readings.items()}
    if all(abs(value) < 0.003 for value in changes.values()):
        return slots, []
    result = [replace(asset, exposure_shift=changes[asset.file])
              if asset is not None and asset.source_url == "local"
              and asset.kind == "video" and asset.file in changes else asset
              for asset in slots]
    return result, [f"Matched mild exposure differences across {len(windows)} local video clips"]
