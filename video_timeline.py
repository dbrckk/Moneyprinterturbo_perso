"""Chronological playback of reused user-owned video clips.

The source media is neither reordered by filename nor semantically understood:
when one local clip fills several storyboard scenes, each subsequent scene
begins further into that same clip. FFmpeg loops only after the clip ends.
Local clip audio remains disabled in favour of the narrated soundtrack.
"""
from __future__ import annotations

from dataclasses import replace
import json
import math
from pathlib import Path
import subprocess
from typing import Callable

from media_assets import MediaAsset

MAX_CLIP_SEEK = 24 * 60 * 60


def source_seek_seconds(asset: object) -> float:
    """Validate the seek position before injecting it into an FFmpeg argv."""
    start = getattr(asset, "source_start_seconds", 0.0)
    if (isinstance(start, bool) or not isinstance(start, (int, float))
            or not math.isfinite(start) or not 0 <= start <= MAX_CLIP_SEEK):
        raise ValueError("Invalid source video seek time")
    return float(start)


def probe_clip_duration(path: Path) -> float:
    """Read local clip duration, without decoding frames or following URLs."""
    process = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=15, check=True,
    )
    duration = float(json.loads(process.stdout)["format"]["duration"])
    if not math.isfinite(duration) or not 0.1 <= duration <= MAX_CLIP_SEEK:
        raise ValueError("Unusable video source duration")
    return duration


def plan_local_video_timeline(
    slots: list[MediaAsset | None], durations: list[float], *,
    probe: Callable[[Path], float] = probe_clip_duration,
) -> tuple[list[MediaAsset | None], list[str]]:
    """Assign advancing source seek times in narration scene order.

    A file is probed once, even when reused many times. For a short source,
    start times wrap at the source duration rather than asking FFmpeg to seek
    beyond EOF. Failed metadata probes preserve the original beginning for
    that clip, without blocking unrelated media.
    """
    if len(slots) != len(durations):
        raise ValueError("Each narration scene requires one media slot")
    if any(not isinstance(value, (float, int)) or isinstance(value, bool)
           or not math.isfinite(value) or value <= 0 for value in durations):
        raise ValueError("Invalid narration timeline")
    elapsed: dict[Path, float] = {}
    lengths: dict[Path, float | None] = {}
    planned: list[MediaAsset | None] = []
    warnings: list[str] = []
    for asset, duration in zip(slots, durations, strict=True):
        if asset is None or asset.kind != "video" or asset.source_url != "local":
            planned.append(asset)
            continue
        path = asset.file
        if path not in lengths:
            try:
                length = probe(path)
                if (isinstance(length, bool) or not isinstance(length, (float, int))
                        or not math.isfinite(length) or not 0.1 <= length <= MAX_CLIP_SEEK):
                    raise ValueError("Invalid source clip duration")
                lengths[path] = float(length)
            except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError,
                    subprocess.TimeoutExpired):
                lengths[path] = None
                warnings.append(
                    f"Source duration unavailable for {path.name}; repeated clip starts at zero")
        length = lengths[path]
        if length is None:
            planned.append(replace(asset, source_start_seconds=0.0))
            continue
        # Actual narration durations, not crossfade extension frames, advance
        # the source cursor. The slight xfade overlap stays frame-accurate.
        cursor = elapsed.get(path, 0.0)
        offset = round(cursor % length, 3)
        if offset >= length:
            offset = 0.0
        planned.append(replace(asset, source_start_seconds=offset))
        elapsed[path] = cursor + duration
    return planned, warnings
