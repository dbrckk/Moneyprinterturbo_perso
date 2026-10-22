"""Guardrails for a single, verified post-encode cut -> fade repair.

Only bounded, locally-produced, *automatically* selected hard cuts can be
reconsidered. A retry may reuse already-normalized scene videos. The original MP4 is never overwritten unless a second FFmpeg
render is measurably better; video/speech/subtitle timings are unchanged.
"""
from __future__ import annotations

import json
import math
import re
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


def targeted_review_improved(
    original: Mapping[str, object], preview: Mapping[str, object],
    boundary_index: int,
) -> bool:
    """Quickly reject unhelpful retries using only the changed boundary.

    This does not replace improved_review: every promising candidate must
    undergo the entire MP4 boundary audit before publication.
    """
    if (not isinstance(original, dict) or not isinstance(preview, dict)
            or type(boundary_index) is not int or boundary_index < 0):
        return False
    before = original.get("boundaries")
    after = preview.get("boundaries")
    if (not isinstance(before, list) or not isinstance(after, list)
            or len(after) != 1 or boundary_index >= len(before)
            or original.get("samples_unavailable") != 0
            or original.get("boundaries_skipped") != 0
            or preview.get("samples_unavailable") != 0
            or preview.get("boundaries_skipped") != len(before) - 1):
        return False
    old, new = before[boundary_index], after[0]
    if not isinstance(old, dict) or not isinstance(new, dict):
        return False
    return (old.get("boundary") == boundary_index + 1
            and new.get("boundary") == boundary_index + 1
            and old.get("transition") == "cut"
            and old.get("review_confidence") == "high"
            and set(old.get("findings", [])).intersection(_HIGH) != set()
            and new.get("transition") == "fade"
            and new.get("review_confidence") in ("none", "low"))


def audio_stream_sha256(video: Path) -> str | None:
    """Hash compressed AAC payload through FFmpeg without decoding audio.

    Timestamp-only or container-level hashes are not sufficient: this checks
    that the narration's actual compressed audio bytes were stream-copied.
    """
    try:
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
             "-i", str(video), "-map", "0:a:0", "-c:a", "copy",
             "-f", "hash", "-hash", "SHA256", "-"],
            capture_output=True, check=True, timeout=20, text=True)
        value = result.stdout.strip()
        if re.fullmatch(r"SHA256=[0-9a-fA-F]{64}", value):
            return value.lower()
        return None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


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
        if reference is None:
            return True
        if not isinstance(reference, Path) or reference == video:
            return False
        baseline = inspect(reference)
        if baseline is None or actual != baseline:
            return False
        if any(item[0] == "audio" for item in actual):
            original_audio = audio_stream_sha256(reference)
            repaired_audio = audio_stream_sha256(video)
            if original_audio is None or original_audio != repaired_audio:
                return False
        return True
    except (OSError, ValueError, TypeError, KeyError,
            subprocess.SubprocessError, OverflowError, AttributeError):
        return False
