"""Bounded post-encode scene-boundary review of actual rendered MP4 pixels.

This is an advisory visual audit, not a creative-quality certificate. It
never alters uploaded footage or prevents publishing a valid render. It
samples up to eight narration boundaries with one short, low-resolution
FFmpeg decode per boundary and stores measurable evidence.
"""
from __future__ import annotations

import math
from pathlib import Path
import statistics
import subprocess
from typing import Sequence

_WIDTH = 48
_HEIGHT = 27
_FRAME_BYTES = _WIDTH * _HEIGHT * 3
_FPS = 12
_MAX_BOUNDARIES = 8


def sample_boundary_luminance(video: Path, boundary: float, total: float) -> tuple[float, ...] | None:
    """Inspect ~1 second of decoded final pixels around a scene boundary."""
    if (not isinstance(video, Path) or
            any(type(v) not in (float, int) or not math.isfinite(v)
                for v in (boundary, total)) or
            not 0.55 <= boundary <= total - 0.55):
        return None
    start = boundary - 0.5
    try:
        finished = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
             "-ss", f"{start:.5f}", "-i", str(video),
             "-t", "1.000", "-map", "0:v:0", "-an",
             "-vf", f"fps={_FPS},scale={_WIDTH}:{_HEIGHT}:flags=area,format=rgb24",
             "-f", "rawvideo", "pipe:1"],
            capture_output=True, timeout=14, check=True)
        raw = finished.stdout
        if (not raw or len(raw) % _FRAME_BYTES or
                not 8 <= len(raw) // _FRAME_BYTES <= 14):
            return None
        values: list[float] = []
        for start_byte in range(0, len(raw), _FRAME_BYTES):
            frame = raw[start_byte:start_byte + _FRAME_BYTES]
            luma = sum(54 * frame[i] + 183 * frame[i + 1] + 19 * frame[i + 2]
                       for i in range(0, len(frame), 3))
            values.append(round(luma / (256 * _WIDTH * _HEIGHT), 3))
        return tuple(values)
    except (OSError, subprocess.SubprocessError, OverflowError, ValueError):
        return None


def kind_for_flash(transitions: Sequence[str] | None, index: int) -> str:
    return transitions[index] if transitions is not None else "unspecified"


def inspect_final_boundaries(
    video: Path, segments: Sequence[dict], transitions: Sequence[str] | None = None,
) -> dict:
    """Look for boundary black flashes, brightness shifts and harsh fades.

    All findings are non-blocking review prompts. Cut edits may *intend* a
    large change; they are recorded as exposure deltas but never classified
    as an erroneous hard cut. The transition names are validated before
    use to prevent misleading QA metadata.
    """
    count = max(0, len(segments) - 1)
    if transitions is not None and (
            len(transitions) != count or
            any(item not in ("cut", "fade", "wipeleft") for item in transitions)):
        raise ValueError("Invalid transition decisions for visual review")
    if not segments:
        raise ValueError("No scenes in video")
    try:
        total = float(segments[-1]["end"])
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        raise ValueError("Missing final scene duration") from exc
    if not math.isfinite(total) or not 0 < total <= 3600:
        raise ValueError("Invalid final scene duration")
    if count == 0:
        return {"method": "rendered-boundary-rgb-48x27-12fps",
                "boundaries": [], "sampled_count": 0,
                "samples_unavailable": 0, "boundaries_skipped": 0,
                "warnings": []}
    take = min(count, _MAX_BOUNDARIES)
    selected = ([0] if take == 1 else
                [round(i * (count - 1) / (take - 1)) for i in range(take)])
    records: list[dict] = []
    warnings: list[str] = []
    unavailable = 0
    for index in selected:
        try:
            when = float(segments[index]["end"])
        except (TypeError, ValueError, KeyError, AttributeError):
            unavailable += 1
            continue
        if not math.isfinite(when):
            unavailable += 1
            continue
        pixels = sample_boundary_luminance(video, when, total)
        if pixels is None:
            unavailable += 1
            continue
        old = statistics.median(pixels[:3])
        new = statistics.median(pixels[-3:])
        delta = abs(new - old)
        steps = [abs(b - a) for a, b in zip(pixels, pixels[1:])]
        max_step = max(steps)
        peak_step_index = steps.index(max_step)
        # The *time course* matters. A fade extending over several frames
        # differs from a nearly instantaneous jump at the edit point.
        total_variation = sum(steps)
        max_step_share = max_step / total_variation if total_variation else 0.0
        peak_near_edit = abs((peak_step_index + 0.5) - (len(pixels) / 2)) <= 2.0
        middle = pixels[max(0, len(pixels)//2 - 2):min(len(pixels), len(pixels)//2 + 2)]
        # A black flash must be isolated: dark frames on BOTH sides of
        # the boundary can be an intentional fade-through-black. Require
        # a brief dip bracketed by visible frames inside the overlap.
        dark_indices = [j for j, value in enumerate(pixels) if value < 15]
        first_dark = dark_indices[0] if dark_indices else -1
        last_dark = dark_indices[-1] if dark_indices else -1
        isolated_dark = (
            0 < first_dark <= last_dark < len(pixels) - 1
            and last_dark - first_dark <= 6
            and min(pixels[first_dark - 1], pixels[last_dark + 1]) >= 45
        )
        black_flash = (isolated_dark and old >= 55 and new >= 55
                       and min(middle, default=255) < 15
                       and kind_for_flash(transitions, index) != "fade")
        kind = transitions[index] if transitions is not None else "unspecified"
        # A 1-3-frame near-white pulse bracketed by ordinary, comparable
        # luminance is a useful warning even when the final MP4 is valid.
        # Do not confuse a lasting exposure/scene change with a flash.
        bright_frames = [j for j, value in enumerate(pixels)
                         if value >= 190 and value >= max(old, new) + 85]
        first_bright = bright_frames[0] if bright_frames else -1
        last_bright = bright_frames[-1] if bright_frames else -1
        bright_flash = (
            len(bright_frames) in (1, 2, 3)
            and last_bright - first_bright + 1 == len(bright_frames)
            and len(pixels)//2 - 3 <= first_bright <= last_bright <= len(pixels)//2 + 2
            and first_bright > 0 and last_bright < len(pixels) - 1
            and max(pixels[first_bright - 1], pixels[last_bright + 1]) <= 150
            and abs(new - old) <= 35
            and old <= 135 and new <= 135
        )
        findings: list[str] = []
        if black_flash:
            findings.append("unexpected-black-flash")
            warnings.append(
                f"Boundary {index + 1}: final MP4 has an almost-black flash between visible scenes; review export")
        if bright_flash:
            findings.append("unexpected-bright-flash")
            warnings.append(
                f"Boundary {index + 1}: final MP4 contains a brief very bright pulse; review export")
        # Large exposure differences may be artistically intentional, so
        # issue a review hint, not an error and not automatic recoloring.
        if delta >= 75 and old >= 25 and new >= 25:
            findings.append("large-exposure-change")
            warnings.append(
                f"Boundary {index + 1}: rendered scenes differ substantially in luminance; review lighting")
        # A near-instantaneous jump on an advertised fade is suspicious;
        # intentional hard cuts are never incorrectly warned about here.
        if (kind == "fade" and max_step >= 118 and
                abs(new - old) >= 90 and max_step_share >= 0.70
                and peak_near_edit and not black_flash):
            findings.append("abrupt-change-during-fade")
            warnings.append(
                f"Boundary {index + 1}: final fade contains a large sudden brightness step; review transition")
        records.append({
            "boundary": index + 1, "time_seconds": round(when, 3),
            "transition": kind, "outgoing_luma": round(old, 2),
            "incoming_luma": round(new, 2),
            "luma_delta": round(delta, 2),
            "largest_frame_luma_step": round(max_step, 2),
            "max_step_fraction": round(max_step_share, 3),
            "dominant_step_near_boundary": bool(peak_near_edit and max_step_share >= 0.70),
            "brief_bright_pulse_frames": len(bright_frames) if bright_flash else 0,
            "findings": findings,
        })
    if unavailable:
        warnings.append(
            f"Encoded visual boundary review unavailable for {unavailable} sampled transition(s)")
    return {
        "method": "rendered-boundary-rgb-48x27-12fps",
        "boundaries": records, "sampled_count": len(records),
        "samples_unavailable": unavailable,
        "boundaries_skipped": count - take,
        "warnings": warnings,
    }
