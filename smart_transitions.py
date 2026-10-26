"""Offline, guarded cut/fade selection for adjacent local media scenes.

The decision uses only coarse mean RGB thumbnails at the two edit points,
never AI guesses about narrative meaning. Ambiguous/failed sampling preserves
the existing gentle fade. Boundaries remain on narration timestamps.
"""
from __future__ import annotations

from pathlib import Path
import math
import subprocess
from typing import Callable, Sequence

_FRAME_BYTES = 48 * 27 * 3
_MAX_BOUNDARIES = 8


# The existing 48x27 decode is also divided into eighteen 8x9-pixel
# luminance regions. Comparing their layout rejects cuts between visually
# unrelated shots that happen to share an average color.
_GRID_COLS = 6
_GRID_ROWS = 3
_GRID_CELLS = _GRID_COLS * _GRID_ROWS
_MAX_SPATIAL_DIFFERENCE = 26.0


def boundary_features(
    asset: object, second: float,
) -> tuple[float, float, float, tuple[float, ...]] | None:
    """RGB means plus coarse spatial-luminance layout from ONE FFmpeg frame.

    Only local source thumbnails are decoded; no original files are modified,
    no AI inference/network use and no additional decode for spatial checks.
    """
    path = getattr(asset, "file", None)
    kind = getattr(asset, "kind", None)
    if (not isinstance(path, Path) or kind not in ("video", "image")
            or type(second) not in (int, float) or not math.isfinite(second)
            or second < 0 or second > 24 * 60 * 60):
        return None
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"]
    if kind == "video":
        cmd += ["-ss", f"{second:.3f}"]
    cmd += ["-i", str(path), "-map", "0:v:0", "-an",
            "-vf", "scale=48:27:flags=area,format=rgb24",
            "-frames:v", "1", "-f", "rawvideo", "pipe:1"]
    try:
        output = subprocess.run(cmd, check=True, capture_output=True,
                                timeout=10).stdout
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if len(output) != _FRAME_BYTES:
        return None
    pixel_count = 48 * 27
    means = tuple(
        sum(output[i] for i in range(channel, len(output), 3)) / pixel_count
        for channel in range(3)
    )
    grid_sum = [0.0] * _GRID_CELLS
    for pixel in range(pixel_count):
        offset = pixel * 3
        row, col = divmod(pixel, 48)
        cell = (row // 9) * _GRID_COLS + col // 8
        grid_sum[cell] += (
            54 * output[offset] + 183 * output[offset + 1]
            + 19 * output[offset + 2]
        ) / 256
    grid = tuple(value / 72 for value in grid_sum)
    return (*means, grid)


def boundary_rgb(asset: object, second: float) -> tuple[float, float, float] | None:
    """Compatibility API: RGB means of one 48x27 source frame."""
    result = boundary_features(asset, second)
    return result[:3] if result is not None else None


def choose_boundaries(
    slots: Sequence[object | None], durations: Sequence[float], *,
    probe: Callable[[object, float], tuple | None] | None = None,
) -> tuple[tuple[str, ...], list[str]]:
    """Return a cut or fade for each boundary, with safe fade as default.

    Only source-owned, local video-to-video pairs are considered. Crosscuts
    between other media remain fades. Two shots with similar global average
    colors can cut cleanly; substantially different-looking clips fade.
    This coarse test avoids dependence on object detection or model weights.
    """
    if len(slots) != len(durations) or not slots:
        raise ValueError("Scene slots and durations must match")
    if any(type(d) not in (int, float) or not math.isfinite(d) or d <= 0
           for d in durations):
        raise ValueError("Invalid scene duration")
    decisions = ["fade"] * (len(slots) - 1)
    if not decisions:
        return (), []
    cuts = 0
    candidates: list[int] = []
    for i in range(len(decisions)):
        a, b = slots[i], slots[i + 1]
        if any(asset is None or getattr(asset, "kind", None) != "video"
               or getattr(asset, "source_url", None) != "local"
               for asset in (a, b)):
            continue
        old_end = getattr(a, "source_start_seconds", 0.0) + durations[i]
        new_start = getattr(b, "source_start_seconds", 0.0)
        if (not all(type(n) in (int, float) and math.isfinite(n) and n >= 0
                    for n in (old_end, new_start))):
            continue
        # Continuity is free to verify: preserve hard cuts over arbitrarily
        # long recordings, even when more than eight narration scenes exist.
        # Decimal scene durations are not exactly representable as floats.
        # Permit up to 1ms of negative rounding error, but never classify
        # meaningful overlapping footage as continuous source playback.
        if (a.file == b.file and -0.001 <= new_start - old_end <= 0.12):
            decisions[i] = "cut"
            cuts += 1
            continue
        if durations[i] >= 0.55 and durations[i + 1] >= 0.55:
            candidates.append(i)

    # Bound FFmpeg decoding by the NUMBER of eligible scene boundaries,
    # not by the total length of the storyboard. Sample across the whole
    # montage rather than probing only the beginning, so later scenes also
    # receive quality-aware cuts. Non-probed pairs keep their safe fade.
    if len(candidates) > _MAX_BOUNDARIES:
        candidates = [
            candidates[round(n * (len(candidates) - 1) / (_MAX_BOUNDARIES - 1))]
            for n in range(_MAX_BOUNDARIES)
        ]
    saved: dict[tuple[Path, float], tuple | None] = {}
    selected_probe = probe if probe is not None else boundary_features
    for i in candidates:
        a, b = slots[i], slots[i + 1]
        old_end = getattr(a, "source_start_seconds", 0.0) + durations[i]
        new_start = getattr(b, "source_start_seconds", 0.0)
        previous_time = round(max(0.0, old_end - 0.23), 3)
        following_time = round(new_start + 0.18, 3)
        def sampled(asset: object, second: float):
            key = (asset.file, second)
            if key not in saved:
                try:
                    color = selected_probe(asset, second)
                    if (not isinstance(color, tuple) or len(color) not in (3, 4)
                            or any(type(v) not in (int, float) or not math.isfinite(v)
                                   or not 0 <= v <= 255 for v in color[:3])):
                        color = None
                    elif len(color) == 4:
                        grid = color[3]
                        if (not isinstance(grid, tuple) or len(grid) != _GRID_CELLS
                                or any(type(v) not in (int, float) or not math.isfinite(v)
                                       or not 0 <= v <= 255 for v in grid)):
                            color = None
                except (OSError, ValueError, TypeError, subprocess.SubprocessError):
                    color = None
                saved[key] = color
            return saved[key]
        c1 = sampled(a, previous_time)
        c2 = sampled(b, following_time)
        if c1 is None or c2 is None:
            continue
        distance = sum(abs(x - y) for x, y in zip(c1[:3], c2[:3])) / 3
        luma1 = (54 * c1[0] + 183 * c1[1] + 19 * c1[2]) / 256
        luma2 = (54 * c2[0] + 183 * c2[1] + 19 * c2[2]) / 256
        # Reject unrelated compositions even when their mean RGB/luma match:
        # opposing bright/dark regions can hide behind the same color average.
        if (len(c1) == 4) != (len(c2) == 4):
            continue
        if len(c1) == 4 and (sum(abs(x - y) for x, y in zip(c1[3], c2[3]))
                             / _GRID_CELLS > _MAX_SPATIAL_DIFFERENCE):
            continue
        # Inconclusive/very dark imagery keeps a fade, never an invented cut.
        if distance <= 24 and abs(luma1 - luma2) <= 22 and min(luma1, luma2) >= 25:
            decisions[i] = "cut"
            cuts += 1
    notes = ([f"Selected {cuts} clean cut(s) and {len(decisions)-cuts} fade(s) from local scene boundaries"]
             if cuts else [])
    return tuple(decisions), notes
