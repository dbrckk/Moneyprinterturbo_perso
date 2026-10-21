"""Chronological playback of reused user-owned video clips.

Media order is determined by the local asset selector, not inferred from
actual recorded events. When one local clip fills several storyboard scenes,
each subsequent scene begins further into that same clip. FFmpeg loops only
after the clip ends.
Local clip audio remains disabled in favour of the narrated soundtrack.
"""
from __future__ import annotations

from collections import Counter
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
    """Measure the first video stream, not an unrelated longer audio track.

    Some user MP4s have narration/music that outlasts their last video frame.
    Using the container duration causes sequential re-use to seek into EOF.
    Fall back to container duration only when a video stream exists but its
    individual duration is unavailable (common with some stream formats).
    """
    process = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=duration:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=15, check=True,
    )
    metadata = json.loads(process.stdout)
    streams = metadata.get("streams") if isinstance(metadata, dict) else None
    if not isinstance(streams, list) or not streams or not isinstance(streams[0], dict):
        raise ValueError("Local video source has no video stream")
    container = metadata.get("format")
    fallback = container.get("duration") if isinstance(container, dict) else None
    for raw in (streams[0].get("duration"), fallback):
        try:
            duration = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(duration) and 0.1 <= duration <= MAX_CLIP_SEEK:
            return duration
    raise ValueError("Unusable video source duration")


def plan_local_video_timeline(
    slots: list[MediaAsset | None], durations: list[float], *,
    probe: Callable[[Path], float] = probe_clip_duration,
    probe_intro: Callable[[Path, float], float] | None = None,
    probe_cuts: Callable[[Path, float], list[float]] | None = None,
) -> tuple[list[MediaAsset | None], list[str]]:
    """Assign advancing source seek times in narration scene order.

    A file is probed once, even when reused many times. The optional
    near-black intro check is also cached per distinct local video.
    When optional high-confidence hard cuts are available, reused footage
    can start up to 0.85 seconds later at a real shot boundary, rather than
    arbitrarily in the middle of a camera shot. The seek cursor advances
    from the chosen boundary; it never reverses the order of source events.
    For a short source,
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
    openings: dict[Path, float] = {}
    cuts: dict[Path, list[float]] = {}
    aligned: dict[Path, int] = {}
    appearances = Counter(asset.file for asset in slots if asset is not None
                          and asset.kind == "video" and asset.source_url == "local")
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
                # Shot analysis is only worthwhile when the same source must
                # supply multiple scenes. One FFmpeg probe per distinct clip.
                if probe_cuts is not None and appearances[path] > 1:
                    try:
                        found = probe_cuts(path, float(length))
                        if not isinstance(found, list) or len(found) > 128:
                            raise ValueError("Invalid shot boundary list")
                        cuts[path] = sorted({float(point) for point in found
                            if type(point) in (float, int) and math.isfinite(point)
                            and 0.5 <= point < float(length)})
                    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
                        cuts[path] = []
                if probe_intro is not None:
                    try:
                        leading = probe_intro(path, float(length))
                        if (isinstance(leading, bool) or
                                not isinstance(leading, (float, int)) or
                                not math.isfinite(leading) or
                                not 0 <= leading <= min(8.0, length - 1.0)):
                            leading = 0.0
                        openings[path] = float(leading)
                        if leading >= 0.45:
                            warnings.append(
                                f"Skipped {leading:.2f}s near-black opening in {path.name}")
                    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
                        openings[path] = 0.0
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
        previously_used = path in elapsed
        cursor = elapsed.get(path, 0.0)
        intro = openings.get(path, 0.0)
        usable = length - intro
        offset = round(intro + cursor % usable, 3)
        if offset >= length:
            offset = intro
        # When a source has a black opening, avoid looping back through it
        # inside a scene (FFmpeg stream_loop restarts at t=0).
        if intro and duration <= usable and offset + duration > length:
            offset = intro
        # Never jump ahead to a shot boundary on the first use of a clip.
        # On later scenes, align only to a nearby future change and only if
        # the complete narration scene still fits before video EOF.
        if previously_used and offset + duration <= length and path in cuts:
            chosen = next((point for point in cuts[path]
                           if offset + 0.08 <= point <= offset + 0.85
                           and point + duration <= length - 0.02), None)
            if chosen is not None:
                offset = round(chosen, 3)
                aligned[path] = aligned.get(path, 0) + 1
        planned.append(replace(asset, source_start_seconds=offset))
        elapsed[path] = offset - intro + duration
    for path, count in aligned.items():
        warnings.append(f"Aligned {count} scene(s) to source shot cuts in {path.name}")
    return planned, warnings
