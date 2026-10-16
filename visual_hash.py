"""Bounded perceptual signatures for CC0/PD still-image diversity.

Unlike hashing URLs or file bytes, a 64-bit difference hash recognizes similar
image structure after re-encoding. It is only a ranking heuristic: scene
selection never rejects all valid media because of a collision or hash failure.
"""
from __future__ import annotations

from pathlib import Path
import subprocess


def difference_hash(gray: bytes) -> int | None:
    """Compute horizontal dHash from a 9x8 grayscale thumbnail.

    Ignore near-flat images because their hashes are unstable, and comparisons
    would incorrectly mark otherwise different solid backgrounds as duplicates.
    """
    if len(gray) != 72 or max(gray) - min(gray) < 12:
        return None
    value = 0
    for y in range(8):
        for x in range(8):
            index = y * 9 + x
            value = (value << 1) | int(gray[index] > gray[index + 1])
    return value


def visual_signature(path: Path, *, timeout: int = 12) -> int | None:
    """Decode only 72 grayscale pixels with an existing FFmpeg installation.

    One source decode per accepted Wikimedia thumbnail. Errors are non-fatal.
    The path is never interpolated into shell text.
    """
    try:
        process = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
             "-i", str(path), "-vf", "scale=9:8:flags=area,format=gray",
             "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"],
            capture_output=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if process.returncode or len(process.stdout) != 72:
        return None
    return difference_hash(process.stdout)


def near_duplicate(first: int | None, second: int | None, *, distance: int = 5) -> bool:
    """Flag only very close 64-bit hashes; never infer similarity from missing data."""
    if not isinstance(first, int) or isinstance(first, bool):
        return False
    if not isinstance(second, int) or isinstance(second, bool):
        return False
    if not 0 <= first <= (1 << 64) - 1 or not 0 <= second <= (1 << 64) - 1:
        return False
    if not 0 <= distance <= 64:
        raise ValueError("Invalid dHash distance threshold")
    return (first ^ second).bit_count() <= distance
