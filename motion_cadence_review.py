"""Optional, bounded motion/cadence review of delivered H.264 MP4s.

Measured on *decoded final pixels*, not on source filenames or camera
metadata. The report is advisory: a deliberately static shot is legitimate.
No automatic edits, no media writes, no cloud/model dependencies.
"""
from __future__ import annotations

import math
from pathlib import Path
import statistics
import subprocess
from collections.abc import Mapping, Sequence

_WIDTH = 48
_HEIGHT = 27
_FPS = 3
_FRAME_BYTES = _WIDTH * _HEIGHT
_MAX_SECONDS = 75.0
_MAX_SCENES = 18
_MIN_SCENE_SECONDS = 4.0
_MIN_SAMPLES = 5
_MOTION_THRESHOLD = 1.25


def _duration_plan(segments: Sequence[Mapping]) -> tuple[list[tuple[float, float]], float]:
    if not isinstance(segments, (list, tuple)) or not 1 <= len(segments) <= _MAX_SCENES:
        raise ValueError("Invalid scene count for cadence inspection")
    planned: list[tuple[float, float]] = []
    cursor = 0.0
    for scene in segments:
        if not isinstance(scene, Mapping):
            raise ValueError("Invalid scene structure")
        try:
            start = float(scene["start"])
            end = float(scene["end"])
        except (TypeError, ValueError, KeyError, OverflowError) as exc:
            raise ValueError("Invalid scene timing") from exc
        if (not math.isfinite(start) or not math.isfinite(end)
                or abs(start - cursor) > 0.06 or end <= start or end > 3600):
            raise ValueError("Discontinuous or invalid scene timing")
        planned.append((start, end))
        cursor = end
    return planned, cursor


def _central_motion(before: bytes, after: bytes) -> float:
    """Mean luma change in the middle third, away from titles/subtitles."""
    start = 8 * _WIDTH
    end = 18 * _WIDTH
    return sum(abs(a - b) for a, b in zip(
        before[start:end], after[start:end], strict=True)) / (end - start)


def inspect_encoded_motion_cadence(
    video: Path, segments: Sequence[Mapping], media: Sequence[Mapping] | None,
) -> dict:
    """Return evidence for unusually low motion and extreme scene pacing.

    A single 3fps/48x27 gray decode is bounded to 75 seconds / 18 scenes.
    Only scenes backed by actual video assets are tested for motion;
    photographs, animated graphics and missing provenance are exempt.
    """
    windows, total = _duration_plan(segments)
    durations = [end - start for start, end in windows]
    cadence = {
        "median_scene_seconds": round(statistics.median(durations), 3),
        "short_scenes": sum(length < 1.25 for length in durations),
        "long_scenes": sum(length > 9.0 for length in durations),
    }
    report: dict[str, object] = {
        "method": "decoded-mp4-center-luma-48x27-3fps",
        "status": "not-run",
        "cadence": cadence,
        "decoded_frames": 0,
        "video_scenes_sampled": 0,
        "scenes": [],
        "warnings": [],
    }
    warnings: list[str] = report["warnings"]
    if len(durations) >= 3 and cadence["short_scenes"] >= 3:
        warnings.append("Several narration scenes last under 1.25s; review pacing and subtitle readability")
    if len(durations) >= 3 and cadence["long_scenes"] >= 2:
        warnings.append("Several scenes exceed 9s; review whether the pacing is intentional")

    kinds: set[int] = set()
    if isinstance(media, (list, tuple)):
        for item in media:
            if not isinstance(item, Mapping) or item.get("kind") != "video":
                continue
            num = item.get("segment")
            if type(num) is int and 1 <= num <= len(windows):
                kinds.add(num - 1)
    eligible = [index for index, duration in enumerate(durations)
                if index in kinds and duration >= _MIN_SCENE_SECONDS]
    if not eligible:
        report["status"] = "no-eligible-video-scenes"
        return report
    if total > _MAX_SECONDS:
        report["status"] = "skipped-duration-budget"
        return report
    if not isinstance(video, Path) or not video.is_file():
        report["status"] = "unavailable"
        return report

    argv = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", str(video), "-t", f"{total:.3f}",
        "-map", "0:v:0", "-an", "-vf",
        f"fps={_FPS},scale={_WIDTH}:{_HEIGHT}:flags=area,format=gray",
        "-f", "rawvideo", "pipe:1",
    ]
    try:
        output = subprocess.run(argv, capture_output=True, check=True, timeout=25).stdout
    except (OSError, ValueError, subprocess.SubprocessError):
        report["status"] = "unavailable"
        return report
    maximum = (math.ceil(total * _FPS) + 2) * _FRAME_BYTES
    if (len(output) % _FRAME_BYTES or len(output) < 2 * _FRAME_BYTES
            or len(output) > maximum):
        report["status"] = "unavailable"
        return report

    frames = [output[offset:offset + _FRAME_BYTES]
              for offset in range(0, len(output), _FRAME_BYTES)]
    report["decoded_frames"] = len(frames)
    sampled: list[dict] = []
    for scene_index in eligible:
        start, end = windows[scene_index]
        motion: list[float] = []
        # Stay 0.45s clear of scene transitions, titles and blending.
        for frame_index in range(len(frames) - 1):
            first = frame_index / _FPS
            second = (frame_index + 1) / _FPS
            if start + 0.45 <= first and second <= end - 0.45:
                motion.append(_central_motion(frames[frame_index],
                                              frames[frame_index + 1]))
        if len(motion) < _MIN_SAMPLES:
            continue
        level = statistics.median(motion)
        low_fraction = sum(value <= _MOTION_THRESHOLD for value in motion) / len(motion)
        unusually_still = level <= _MOTION_THRESHOLD and low_fraction >= 0.85
        sampled.append({
            "scene": scene_index + 1,
            "duration_seconds": round(end - start, 3),
            "sampled_steps": len(motion),
            "median_motion_luma": round(level, 3),
            "low_motion_fraction": round(low_fraction, 3),
            "very_low_motion": unusually_still,
        })
        if unusually_still:
            warnings.append(
                f"Scene {scene_index + 1}: little movement measured in the exported video; "
                "verify that still-looking footage is intentional"
            )
    report["scenes"] = sampled
    report["video_scenes_sampled"] = len(sampled)
    report["status"] = "analyzed" if sampled else "insufficient-scene-samples"
    return report
