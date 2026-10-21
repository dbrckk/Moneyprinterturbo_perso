"""Fast, conservative screening of unusably dark introductions in local clips.

Only the *opening* of a phone clip is inspected: no cloud calls, no large
frame arrays, no assumptions about scene content, no automatic source edits.
Failure or ambiguous evidence always means keeping the original footage.
"""
from __future__ import annotations

import math
from pathlib import Path
import re
import subprocess

# FFmpeg blackdetect checks nearly black frames, not general low-light scenes.
# A long intentional dark scene is left intact if the detector cannot see
# clearly lit frames after it within our bounded first-window sample.
_BLACK_REGION = re.compile(
    r"black_start:\s*(\d+(?:\.\d+)?)\s+"
    r"black_end:\s*(\d+(?:\.\d+)?)\s+"
    r"black_duration:\s*(\d+(?:\.\d+)?)"
)


def probe_black_intro(path: Path, duration: float) -> float:
    """Return seconds to skip if the opening is reliably nearly black.

    Up to 10 source seconds are decoded at only four 160x90 frames per
    second. Require a black-to-visible transition strictly *inside* that
    analysis window, at least 0.45s from the beginning. No results are
    inferred from filename, timestamp, or file modification time.
    """
    if (isinstance(duration, bool) or not isinstance(duration, (int, float))
            or not math.isfinite(duration) or duration < 2.0):
        return 0.0
    window = min(10.0, duration * 0.70)
    if window < 1.5:
        return 0.0
    try:
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "info",
             "-t", f"{window:.3f}", "-i", str(path),
             "-map", "0:v:0", "-an",
             "-vf", "fps=4,scale=160:90,"
                    "blackdetect=d=0.40:pix_th=0.06:pic_th=0.98",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=17, check=True,
        )
        for match in _BLACK_REGION.finditer(result.stderr):
            start, end, length = (float(part) for part in match.groups())
            if (0 <= start <= 0.26 and 0.45 <= length
                    and length <= 8.0 and end > start
                    # Require at least ~1s of confirmed visible
                    # footage after the black region. FFmpeg may report
                    # black_end at the last sampled frame on an entirely
                    # black clip; do not mistake that EOF for a transition.
                    and end <= window - 0.85
                    and duration - end >= 1.0):
                return round(end, 3)
            # Never advance to a later dark scene: only the intro qualifies.
            break
    except (OSError, subprocess.SubprocessError, ValueError, OverflowError):
        pass
    return 0.0
