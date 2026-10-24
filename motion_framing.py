"""Bounded local motion-guided horizontal reframing for moderately wide video.

This is NOT face, body or semantic object recognition. It locates *localized
movement* likely to fall outside the default center crop. Indeterminate,
static, multi-subject, globally-moving, malformed or unreadable shots retain
their original center crop. Ultra-wide video keeps the existing whole-frame
soft matte. No cloud, new dependencies, second FFprobe, model or source edits.
"""
from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path
import subprocess
from typing import Callable

from scene_framing import needs_video_soft_matte
from video_timeline import source_seek_seconds

_SAMPLE_WIDTH = 120
_SAMPLE_HEIGHT = 68
_FRAME_BYTES = _SAMPLE_WIDTH * _SAMPLE_HEIGHT
_MAX_FRAMES = 6
_MAX_VIDEO_PROBES = 4


def eligible(asset: object, *, width: int, height: int) -> bool:
    """Only moderate horizontal mismatch in user footage, with known geometry."""
    if (getattr(asset, "kind", None) != "video"
            or getattr(asset, "source_url", None) != "local"):
        return False
    source_w = getattr(asset, "source_width", None)
    source_h = getattr(asset, "source_height", None)
    if (type(width) is not int or type(height) is not int
            or type(source_w) is not int or type(source_h) is not int
            or width < 240 or height < 240 or width > 4096 or height > 4096
            or width % 2 or height % 2
            or not 64 <= source_w <= 10000 or not 64 <= source_h <= 10000):
        return False
    if needs_video_soft_matte(asset, width=width, height=height):
        return False
    # Horizontal crop only: portrait-to-landscape vertical framing is not
    # inferred from horizontal motion, and remains unchanged.
    ratio = (width / height) / (source_w / source_h)
    return 0.40 <= ratio <= 0.85


def crop_filter(asset: object, *, width: int, height: int) -> str:
    """Same crop expression in single-pass and intermediate clip encoders."""
    anchor = getattr(asset, "crop_horizontal_anchor", None)
    if (not eligible(asset, width=width, height=height)
            or type(anchor) not in (float, int)
            or not math.isfinite(anchor) or not 0.12 <= anchor <= 0.88):
        return f"crop={width}:{height}"
    # FFmpeg's in_w/out_w are evaluated after aspect-ratio preserving scale.
    # Anchor stays constant for the whole scene, preventing camera jitter.
    return (f"crop={width}:{height}:"
            f"x=(in_w-out_w)*{anchor:.4f}:y=(in_h-out_h)/2")


def motion_anchor(frames: bytes, *, retention: float) -> float | None:
    """Use temporal luma changes, not background brightness or color alone.

    At least two frame pairs must independently find dominant side movement,
    located mainly OUTSIDE the old center crop. Global camera motion,
    dark videos, or two similarly active subjects have no stable focal point.
    """
    if (type(frames) is not bytes
            or len(frames) % _FRAME_BYTES != 0
            or not 3 <= len(frames) // _FRAME_BYTES <= _MAX_FRAMES
            or type(retention) not in (float, int)
            or not math.isfinite(retention) or not 0.40 <= retention <= 0.85):
        return None

    count = len(frames) // _FRAME_BYTES
    first_center = (1.0 - retention) / 2.0
    last_center = (1.0 + retention) / 2.0
    estimates: list[tuple[float, float]] = []
    for index in range(1, count):
        previous = memoryview(frames)[(index-1)*_FRAME_BYTES:index*_FRAME_BYTES]
        current = memoryview(frames)[index*_FRAME_BYTES:(index+1)*_FRAME_BYTES]
        energies = [0] * _SAMPLE_WIDTH
        active_columns = 0
        for x in range(_SAMPLE_WIDTH):
            total = 0
            # Sample alternate rows; maximum 120*34*5 small pixel operations.
            for y in range(0, _SAMPLE_HEIGHT, 2):
                position = y * _SAMPLE_WIDTH + x
                diff = abs(current[position] - previous[position])
                if diff >= 16:
                    total += min(96, diff)
            energies[x] = total
            if total >= 150:
                active_columns += 1
        energy = sum(energies)
        if energy < 1400 or active_columns > _SAMPLE_WIDTH * 0.52:
            continue

        focus = sum((x + 0.5) * amount for x, amount in enumerate(energies))
        focus /= energy * _SAMPLE_WIDTH
        left = sum(energies[:_SAMPLE_WIDTH // 2])
        right = energy - left
        dominance = max(left, right) / energy
        outside = sum(amount for x, amount in enumerate(energies)
                      if not first_center <= (x + 0.5) / _SAMPLE_WIDTH <= last_center)
        if (dominance < 0.70 or outside / energy < 0.46
                or not (focus < first_center - 0.04
                        or focus > last_center + 0.04)):
            continue
        estimates.append((focus, energy))
    if len(estimates) < 2:
        return None
    # Consistent direction and position across independent frame pairs.
    direction = estimates[0][0] > 0.5
    if (any((focus > .5) != direction for focus, _ in estimates)
            or max(focus for focus, _ in estimates)
            - min(focus for focus, _ in estimates) > .16):
        return None
    focus = sum(x * energy for x, energy in estimates) / sum(
        energy for _, energy in estimates)
    start = min(max(focus - retention / 2.0, 0.0), 1.0 - retention)
    offset = start / (1.0 - retention)
    # Make conservative adjustments: do not shove the camera all the way
    # to a corner on noisy motion evidence.
    offset = min(.88, max(.12, offset))
    if abs(offset - 0.5) < .09:
        return None
    return round(offset, 4)


def probe_motion(
    video: Path, *, seek: float, duration: float, retention: float
) -> float | None:
    """Read at most six 120x68 luma frames from a user clip's selected window."""
    if (not isinstance(video, Path)
            or type(seek) not in (int, float) or not math.isfinite(seek)
            or not 0 <= seek <= 86400
            or type(duration) not in (int, float) or not math.isfinite(duration)
            or not 1.5 <= duration <= 3.0):
        return None
    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-ss", f"{seek:.3f}", "-t", f"{duration:.3f}",
        "-i", str(video), "-an", "-sn",
        "-vf", f"fps=2,scale={_SAMPLE_WIDTH}:{_SAMPLE_HEIGHT}:flags=area,"
               "format=gray",
        "-frames:v", str(_MAX_FRAMES), "-f", "rawvideo", "pipe:1",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=18, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or len(result.stdout) > _MAX_FRAMES * _FRAME_BYTES:
        return None
    return motion_anchor(result.stdout, retention=retention)


def plan_motion_crops(
    slots: list[object | None], durations: list[float], *,
    width: int, height: int,
    probe: Callable[..., float | None] = probe_motion,
) -> tuple[list[object | None], list[str]]:
    """Conservatively select one fixed camera anchor per eligible video scene.

    Up to four small FFmpeg reads *total* per render. Repeated footage at
    identical seeks reuses analysis. Unknown or failed decisions are neutral.
    """
    if (len(slots) != len(durations) or len(slots) > 18
            or type(width) is not int or type(height) is not int
            or not 240 <= width <= 4096 or not 240 <= height <= 4096
            or width % 2 or height % 2
            or any(type(d) not in (int, float) or not math.isfinite(d)
                   or d <= 0 for d in durations)):
        raise ValueError("Invalid motion framing scene plan")
    output = list(slots)
    notes = []
    checked = 0
    decisions: dict[tuple[Path, float, float], float | None] = {}
    adapted = 0
    for index, (asset, duration) in enumerate(zip(slots, durations, strict=True)):
        if (asset is None or not eligible(asset, width=width, height=height)
                or duration < 1.5):
            continue
        try:
            seek = source_seek_seconds(asset)
        except ValueError:
            continue
        sample_duration = min(3.0, float(duration))
        key = (asset.file, round(seek, 3), round(sample_duration, 3))
        if key not in decisions:
            if checked >= _MAX_VIDEO_PROBES:
                continue
            checked += 1
            aspect = width * asset.source_height / (height * asset.source_width)
            try:
                choice = probe(asset.file, seek=seek, duration=sample_duration,
                               retention=aspect)
            except (OSError, ValueError, TypeError, subprocess.SubprocessError):
                choice = None
            if (type(choice) not in (int, float) or not math.isfinite(choice)
                    or not .12 <= choice <= .88):
                choice = None
            decisions[key] = choice
        anchor = decisions[key]
        if anchor is not None:
            output[index] = replace(asset, crop_horizontal_anchor=anchor)
            adapted += 1
    if adapted:
        notes.append(
            f"Motion-guided horizontal crop applied to {adapted} local video "
            "scene(s); heuristic movement anchor, not subject recognition")
    return output, notes
