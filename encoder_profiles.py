"""Conservative, explicit H.264 final-export presets for small Android devices.

The optional benchmark produces synthetic SSIM evidence; it does *not*
automatically certify a codec setting for user footage or device temperature.
This module owns the single render policy, including deterministic fallbacks
made before starting an expensive final FFmpeg encode.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

ENCODER_PROFILES = ("balanced", "detail", "premium", "auto")
_PARAMETERS = {
    "balanced": ("veryfast", 23),
    "detail": ("veryfast", 20),
    "premium": ("faster", 20),
}
# Ceiling on total pixel-frames processed by a particular export encode.
# Conservative phone-oriented budgets, not measured Android performance.
_DETAIL_PIXEL_FRAMES = 500_000_000
_PREMIUM_PIXEL_FRAMES = 130_000_000



def valid_ffmpeg_settings(preset: object, crf: object) -> bool:
    """Allow only settings tested by our bounded encoder comparison."""
    return (type(preset) is str and type(crf) is int
            and (preset, crf) in frozenset(_PARAMETERS.values()))


def mobile_h264_threads(command: list[str], *, width: int, height: int) -> list[str]:
    """Bound 1080p x264 worker memory without changing CRF/preset or sources.

    Insert the *output* codec thread cap directly before the destination, not
    before input paths. 720p and below retain original FFmpeg decisions.
    """
    if (type(width) is not int or type(height) is not int or
            width <= 0 or height <= 0):
        raise ValueError("Invalid encoder dimensions")
    if (width * height < 1080 * 1920 or
            not command or not isinstance(command[-1], str) or
            not any(command[i:i + 2] == ["-c:v", "libx264"]
                    for i in range(len(command) - 1))):
        return list(command)
    if "-threads:v" in command:
        return list(command)
    return [*command[:-1], "-threads:v", "2", command[-1]]


@dataclass(frozen=True)
class EncoderSelection:
    requested: str
    applied: str
    preset: str
    crf: int
    fallback_reason: str | None
    policy: str = "conservative-pixel-frame-budget-v1"

    def to_dict(self) -> dict[str, object]:
        return {
            "requested": self.requested,
            "applied": self.applied,
            "preset": self.preset,
            "crf": self.crf,
            "fallback_reason": self.fallback_reason,
            "policy": self.policy,
        }


def resolve_encoder_profile(
    requested: str, *, width: int, height: int,
    fps: int, duration: float, scenes: int,
) -> EncoderSelection:
    """Select once from an allowlist, before any final encode or repair retry.

    The baseline encoder is unchanged. Only explicitly chosen options or
    opt-in 'auto' may raise visual quality; for larger jobs, automatically
    reduce to a cheaper *validated* preset rather than attempt an enormous
    second encode or crash mobile Termux.
    """
    if not isinstance(requested, str) or requested not in ENCODER_PROFILES:
        raise ValueError("Unsupported H.264 encoder profile")
    if (type(width) is not int or type(height) is not int
            or width < 240 or height < 240
            or width > 4096 or height > 4096
            or width % 2 or height % 2
            or type(fps) is not int or not 1 <= fps <= 60
            or type(scenes) is not int or not 1 <= scenes <= 18
            or type(duration) not in (int, float)
            or not math.isfinite(duration) or not 0 < duration <= 3600):
        raise ValueError("Invalid workload for H.264 encoder selection")

    pixels = width * height
    pixel_frames = pixels * fps * duration
    can_detail = (
        pixels <= 720 * 1280 and fps <= 30 and scenes <= 12
        and pixel_frames <= _DETAIL_PIXEL_FRAMES)
    can_premium = (
        pixels <= 720 * 1280 and fps <= 24 and scenes <= 6
        and duration <= 45 and pixel_frames <= _PREMIUM_PIXEL_FRAMES)

    reason = None
    if requested == "auto":
        # No device calibration is inferred from a synthetic GitHub benchmark.
        # This is a cautious, deterministic resource policy, not hardware AI.
        chosen = "detail" if can_detail and pixel_frames <= 150_000_000 else "balanced"
    elif requested == "premium":
        chosen = "premium" if can_premium else ("detail" if can_detail else "balanced")
    elif requested == "detail":
        chosen = "detail" if can_detail else "balanced"
    else:
        chosen = "balanced"

    if requested not in ("balanced", "auto") and chosen != requested:
        reason = (f"Requested {requested} encoder exceeds conservative render "
                  f"budget; using {chosen} instead to limit device load")
    preset, crf = _PARAMETERS[chosen]
    return EncoderSelection(
        requested=requested, applied=chosen, preset=preset,
        crf=crf, fallback_reason=reason)
