"""Guardrails for a single, verified post-encode cut -> fade repair.

Only bounded, locally-produced, *automatically* selected hard cuts can be
reconsidered. A retry may reuse already-normalized scene videos. The original MP4 is never overwritten unless a second FFmpeg
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
    if (not isinstance(report, dict) or not 2 <= len(assets) <= 8
            or len(transitions) != len(assets) - 1
            or report.get("samples_unavailable") != 0
            or report.get("boundaries_skipped") != 0):
        return None
    records = report.get("boundaries")
    if not isinstance(records, list) or len(records) != len(transitions):
        return None
    candidates = []
    for index, record in enumerate(records):
        if not isinstance(record, dict) or record.get("boundary") != index + 1:
            return None
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
        elif vb > va or a.get("transition") != b.get("transition"):
            return False  # Do not worsen or silently modify another edit.
    return changed and new_score < baseline_score


def encoded_duration_ok(
    video: Path, *, expected: float, fps: int, reference: Path | None = None,
) -> bool:
    """Require timed H.264 (and, if present, AAC) with original stream layout.

    Comparing to the unmodified first render guards against a repaired video
    silently losing narration, changing dimensions or adding another stream.
    """
    if (not isinstance(video, Path) or not video.is_file()
            or video.stat().st_size < 1000
            or type(expected) not in (float, int) or not math.isfinite(expected)
            or not 0 < expected <= 60
            or type(fps) is not int or not 8 <= fps <= 30):
        return False

    def inspect(path: Path) -> tuple[tuple[object, ...], ...] | None:
        if not path.is_file() or path.stat().st_size < 1000:
            return None
        checked = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,codec_name,width,height,sample_rate,channels,duration:"
             "format=duration", "-of", "json", str(path)],
            capture_output=True, timeout=15, check=True, text=True)
        metadata = json.loads(checked.stdout)
        allowance = max(0.14, 2.0 / fps)
        container = float(metadata["format"]["duration"])
        if not math.isfinite(container) or abs(container - expected) > allowance:
            return None
        streams = metadata.get("streams", [])
        videos = [s for s in streams if s.get("codec_type") == "video"]
        audios = [s for s in streams if s.get("codec_type") == "audio"]
        if len(videos) != 1 or len(audios) > 1 or len(streams) != 1 + len(audios):
            return None
        if videos[0].get("codec_name") != "h264":
            return None
        if audios and audios[0].get("codec_name") != "aac":
            return None
        fingerprints = []
        for stream in [*videos, *audios]:
            seconds = float(stream["duration"])
            if not math.isfinite(seconds) or abs(seconds - expected) > allowance:
                return None
            fingerprints.append(
                (stream.get("codec_type"), stream.get("codec_name"),
                 stream.get("width"), stream.get("height"),
                 stream.get("sample_rate"), stream.get("channels")))
        return tuple(fingerprints)

    try:
        actual = inspect(video)
        if actual is None:
            return False
        return (reference is None or (
            isinstance(reference, Path) and reference != video
            and (baseline := inspect(reference)) is not None
            and actual == baseline))
    except (OSError, ValueError, TypeError, KeyError,
            subprocess.SubprocessError, OverflowError, AttributeError):
        return False
