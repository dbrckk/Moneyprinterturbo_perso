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


def boundary_rgb(asset: object, second: float) -> tuple[float, float, float] | None:
    """Read one tiny RGB thumbnail at a source timestamp, without originals."""
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
        if len(output) != _FRAME_BYTES:
            return None
        channels = tuple(sum(output[i] for i in range(channel, len(output), 3)) /
                         (_FRAME_BYTES / 3) for channel in range(3))
        return channels
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def choose_boundaries(
    slots: Sequence[object | None], durations: Sequence[float], *,
    probe: Callable[[object, float], tuple[float, float, float] | None] = boundary_rgb,
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
    if not decisions or len(decisions) > _MAX_BOUNDARIES:
        return tuple(decisions), []
    saved: dict[tuple[Path, float], tuple[float, float, float] | None] = {}
    cuts = 0
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
        # A verified continuous recording can switch at the narration
        # boundary without dissolving the moving subject into itself.
        if (a.file == b.file and 0 <= new_start - old_end <= 0.12):
            decisions[i] = "cut"
            cuts += 1
            continue
        # Skip scene boundaries that would require probing beyond the
        # source duration; FFmpeg renderer may loop these short clips.
        if durations[i] < 0.55 or durations[i + 1] < 0.55:
            continue
        previous_time = round(max(0.0, old_end - 0.23), 3)
        following_time = round(new_start + 0.18, 3)
        def sampled(asset: object, second: float):
            key = (asset.file, second)
            if key not in saved:
                try:
                    color = probe(asset, second)
                    if (not isinstance(color, tuple) or len(color) != 3 or
                            any(type(v) not in (int, float) or not math.isfinite(v)
                                or not 0 <= v <= 255 for v in color)):
                        color = None
                except (OSError, ValueError, TypeError, subprocess.SubprocessError):
                    color = None
                saved[key] = color
            return saved[key]
        c1 = sampled(a, previous_time)
        c2 = sampled(b, following_time)
        if c1 is None or c2 is None:
            continue
        distance = sum(abs(x - y) for x, y in zip(c1, c2)) / 3
        luma1 = (54 * c1[0] + 183 * c1[1] + 19 * c1[2]) / 256
        luma2 = (54 * c2[0] + 183 * c2[1] + 19 * c2[2]) / 256
        # Similar exposure AND restrained RGB difference imply a clean cut.
        # Inconclusive/very dark imagery keeps a fade, never an invented cut.
        if distance <= 24 and abs(luma1 - luma2) <= 22 and min(luma1, luma2) >= 25:
            decisions[i] = "cut"
            cuts += 1
    notes = ([f"Selected {cuts} clean cut(s) and {len(decisions)-cuts} fade(s) from local scene boundaries"]
             if cuts else [])
    return tuple(decisions), notes
