"""Bounded scene-aware sample windows for local-video color adjustments.

A reused video can contain unrelated lighting conditions at different seek
positions. All intended windows must be measurable before correcting a whole
group of clips. Distinct media offsets are sampled separately; identical
windows are deduplicated. Never modifies source footage.
"""
from __future__ import annotations

import math
from pathlib import Path

MAX_SOURCES = 8
MAX_WINDOWS = 12


def color_sample_windows(slots: list, durations: list[float]) -> dict[Path, list[tuple[float, float]]] | None:
    """Return bounded windows by local video path, or None to skip grading.

    A path's different source offsets are never silently treated as one
    sample. On invalid timing, >8 sources or >12 unique windows, fail closed
    and preserve the original artistic lighting without costly FFmpeg work.
    """
    if len(slots) != len(durations):
        raise ValueError("Media slots and scene durations differ")
    windows: dict[Path, list[tuple[float, float]]] = {}
    total_windows = 0
    for asset, duration in zip(slots, durations, strict=True):
        if (asset is None or getattr(asset, "kind", None) != "video"
                or getattr(asset, "source_url", None) != "local"):
            continue
        path = getattr(asset, "file", None)
        start = getattr(asset, "source_start_seconds", 0.0)
        if (not isinstance(path, Path)
                or type(start) not in (int, float) or not math.isfinite(start)
                or start < 0 or start > 86400
                or type(duration) not in (int, float)
                or not math.isfinite(duration) or duration < 0.5):
            return None
        window = (round(float(start), 3), round(float(duration), 3))
        group = windows.setdefault(path, [])
        if window not in group:
            group.append(window)
            total_windows += 1
            if len(windows) > MAX_SOURCES or total_windows > MAX_WINDOWS:
                return None
    return windows if 2 <= len(windows) <= MAX_SOURCES else None
