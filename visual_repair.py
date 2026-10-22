"""Guardrails for a single, verified post-encode cut -> fade repair.

Only very short, locally-produced, *automatically* selected hard cuts can be
reconsidered. The original MP4 is never overwritten unless a second FFmpeg
render is measurably better; video/speech/subtitle timings are unchanged.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess
from typing import Mapping, Sequence


_HIGH = {"unexpected-black-flash", "unexpected-bright-flash"}


def repairable_boundary(
    report: Mapping[str, object] | None,
    transitions: Sequence[str],
    assets: Sequence[object | None],
) -> int | None:
    """Find exactly one high-confidence flash on a selected local-video cut."""
    if (not isinstance(report, dict) or not 2 <= len(assets) <= 4
            or len(transitions) != len(assets) - 1
            or report.get("samples_unavailable") != 0
            or report.get("boundaries_skipped") != 0):
        return None
    records = report.get("boundaries")
    if not isinstance(records, list) or len(records) != len(transitions):
        return None
    candidates = []
    for record in records:
        if not isinstance(record, dict):
            return None
        number = record.get("boundary")
        if type(number) is not int or not 1 <= number <= len(transitions):
            return None
        index = number - 1
        findings = record.get("findings")
        if not isinstance(findings, list) or not all(isinstance(x, str) for x in findings):
            return None
        if record.get("review_confidence") != "high":
            continue
        left, right = assets[index:index + 2]
        if (transitions[index] == "cut"
                and record.get("transition") == "cut"
                and set(findings).intersection(_HIGH)
                and left is not None and right is not None
                and getattr(left, "source_url", None) == "local"
                and getattr(right, "source_url", None) == "local"
                and getattr(left, "kind", None) == "video"
                and getattr(right, "kind", None) == "video"):
            candidates.append(index)
        else:
            return None  # Another high-priority anomaly needs human review.
    return candidates[0] if len(candidates) == 1 else None


def improved_review(original: Mapping[str, object], replacement: Mapping[str, object],
                    boundary_index: int) -> bool:
    """Accept only a lower-risk encoded result with complete comparable scans."""
    if (not isinstance(original, dict) or not isinstance(replacement, dict)
            or type(boundary_index) is not int or boundary_index < 0):
        return False
    first, second = original.get("boundaries"), replacement.get("boundaries")
    if (not isinstance(first, list) or not isinstance(second, list)
            or len(first) != len(second)
            or original.get("samples_unavailable") != 0
            or replacement.get("samples_unavailable") != 0
            or original.get("boundaries_skipped") != 0
            or replacement.get("boundaries_skipped") != 0):
        return False
    scores = {"none": 0, "low": 1, "medium": 2, "high": 3}
    baseline_score = new_score = 0
    changed = False
    for a, b in zip(first, second, strict=True):
        if not isinstance(a, dict) or not isinstance(b, dict):
            return False
        if a.get("boundary") != b.get("boundary"):
            return False
        va, vb = scores.get(a.get("review_confidence")), scores.get(b.get("review_confidence"))
        if va is None or vb is None:
            return False
        baseline_score += va
        new_score += vb
        if a["boundary"] == boundary_index + 1:
            if va != 3 or vb > 1 or b.get("transition") != "fade":
                return False
            changed = True
        elif vb > va:
            return False  # Never introduce a worse neighbouring boundary.
    return changed and new_score < baseline_score


def encoded_duration_ok(video: Path, *, expected: float, fps: int) -> bool:
    """Verify FFprobe's actual H.264 and AAC stream durations, not only metadata."""
    if (not isinstance(video, Path) or not video.is_file()
            or video.stat().st_size < 1000 or
            type(expected) not in (float, int) or not math.isfinite(expected)
            or not 0 < expected <= 30
            or type(fps) is not int or not 8 <= fps <= 30):
        return False
    try:
        checked = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,duration:format=duration", "-of", "json",
             str(video)], capture_output=True, timeout=15, check=True, text=True)
        metadata = json.loads(checked.stdout)
        allowance = max(0.14, 2.0 / fps)
        container = float(metadata["format"]["duration"])
        if not math.isfinite(container) or abs(container - expected) > allowance:
            return False
        streams = metadata.get("streams", [])
        video_streams = [s for s in streams if s.get("codec_type") == "video"]
        if len(video_streams) != 1:
            return False
        for stream in streams:
            if stream.get("codec_type") in ("video", "audio"):
                seconds = float(stream["duration"])
                if not math.isfinite(seconds) or abs(seconds - expected) > allowance:
                    return False
        return True
    except (OSError, ValueError, TypeError, KeyError,
            subprocess.SubprocessError, OverflowError):
        return False
