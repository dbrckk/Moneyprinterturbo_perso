"""Preserve ultra-wide photos and videos without clipping their subjects.

For sources whose inferred center-crop retains less than 34% of original
image area, keep the whole photo over a soft, color-matched extension of
that same image. Requires FFmpeg only; no paid models, extra downloads or
unsafe shell execution. Both legacy and one-pass engines share this graph.
"""
from __future__ import annotations

import math
import re


def needs_soft_matte(asset: object) -> bool:
    if getattr(asset, "kind", None) != "image":
        return False
    value = getattr(asset, "crop_retention_estimate", None)
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and 0 < value < 0.34)



def needs_video_soft_matte(asset: object, *, width: int, height: int) -> bool:
    """Protect real local video when a center crop would discard most pixels.

    Source dimensions have already been verified during the normal asset
    import: no extra FFprobe invocation or analysis pass is required.
    Unknown dimensions fail closed to the existing center-crop behavior.
    This heuristic only measures geometry, never guesses subject location.
    """
    if getattr(asset, "kind", None) != "video":
        return False
    source_w = getattr(asset, "source_width", None)
    source_h = getattr(asset, "source_height", None)
    if (type(source_w) is not int or type(source_h) is not int
            or not 64 <= source_w <= 10000 or not 64 <= source_h <= 10000
            or type(width) is not int or type(height) is not int
            or width < 240 or height < 240 or width > 4096 or height > 4096
            or width % 2 or height % 2):
        return False
    source_aspect = source_w / source_h
    destination_aspect = width / height
    retention = min(source_aspect / destination_aspect,
                    destination_aspect / source_aspect)
    return retention < 0.40


def video_soft_matte_graph(
    source_index: int, *, width: int, height: int, fps: int,
    prefix: str, output_name: str, tail_filters: str = "",
    color_filters: str = "eq=contrast=1.05:saturation=1.07",
) -> list[str]:
    """Framed, moving footage over a blurred duplicate of its own pixels.

    FPS normalization precedes the split, so both video branches use the SAME
    decoded timeline, including variable-frame-rate footage and source seeks.
    No zoompan: unlike a photo, live footage must retain all of its frames
    and natural motion. Blur is computed at <= 1/16 output dimensions, not on
    1080p-sized frames, to bound Android CPU/memory costs.
    """
    if (type(source_index) is not int or source_index < 0
            or type(width) is not int or type(height) is not int
            or not 240 <= width <= 4096 or not 240 <= height <= 4096
            or width % 2 or height % 2 or type(fps) is not int
            or not 8 <= fps <= 60):
        raise ValueError("Invalid video framing dimensions or source index")
    if not all(type(name) is str and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name)
               for name in (prefix, output_name)):
        raise ValueError("Invalid FFmpeg video framing pad")
    if (type(tail_filters) is not str or
            any(char in tail_filters for char in ";[]\n\r")):
        raise ValueError("Invalid video framing tail")
    if (type(color_filters) is not str or
            not re.fullmatch(r"[A-Za-z0-9_,.=:+-]{1,240}", color_filters)):
        raise ValueError("Invalid video framing color filters")
    small_w = max(32, width // 16)
    small_h = max(32, height // 16)
    suffix = f",{tail_filters}" if tail_filters else ""
    return [
        (f"[{source_index}:v]fps={fps},setsar=1,split=2"
         f"[{prefix}bg][{prefix}fg]"),
        (f"[{prefix}bg]scale={small_w}:{small_h}:force_original_aspect_ratio=increase,"
         f"crop={small_w}:{small_h},gblur=sigma=2,"
         f"scale={width}:{height}:flags=bicubic,"
         f"eq=contrast=0.85:saturation=0.75,format=yuv420p[{prefix}matte]"),
        (f"[{prefix}fg]scale={width}:{height}:force_original_aspect_ratio=decrease,"
         f"setsar=1,format=yuv420p[{prefix}hero];"
         f"[{prefix}matte][{prefix}hero]"
         f"overlay=(W-w)/2:(H-h)/2:shortest=1,"
         f"{color_filters},scale=in_range=auto:out_range=tv,format=yuv420p"
         f"{suffix}[{output_name}]"),
    ]


def photo_zoom_filter(*, width: int, height: int, fps: int, frames: int,
                      zoom_percent: float = 0.08) -> str:
    """Keep a restrained Ken Burns move active across the ENTIRE scene.

    The former per-frame step hit its zoom cap after ~125 output frames,
    freezing long photo scenes. Scale progress by the actual *output* frame
    count, including any crossfade extension frames. This yields the same
    framing and movement regardless of render pipeline or scene length.
    """
    if (type(frames) is not int or not 1 <= frames <= 216000
            or type(width) is not int or type(height) is not int
            or not 240 <= width <= 4096 or not 240 <= height <= 4096
            or width % 2 or height % 2
            or type(fps) is not int or not 8 <= fps <= 60
            or type(zoom_percent) not in (float, int)
            or not math.isfinite(zoom_percent)
            or not 0.0 <= zoom_percent <= 0.10):
        raise ValueError("Invalid bounded photo camera motion")
    max_zoom = 1.0 + float(zoom_percent)
    # FFmpeg zoompan 'on' is the running output-frame number, beginning at 1.
    # The last emitted frame reaches the cap without pausing earlier.
    expression = (f"min({max_zoom:.5f},1+{zoom_percent:.5f}*on/{frames})"
                  if frames > 1 else "1")
    return f"zoompan=z='{expression}':d=1:s={width}x{height}:fps={fps}"

def soft_matte_graph(source_index: int, *, width: int, height: int, fps: int,
                     frames: int, prefix: str, output_name: str,
                     tail_filters: str = "") -> list[str]:
    """Return three branches with exactly one output pad.

    Source is internally split, not decoded twice. Blur is done at 1/16
    resolution for predictable CPU/memory cost. A restrained 4% camera zoom
    animates stills without cutting the entire scene's meaningful content.
    """
    if not isinstance(source_index, int) or source_index < 0:
        raise ValueError("Invalid source index")
    if (not isinstance(width, int) or not isinstance(height, int)
            or width < 240 or height < 240 or width > 4096 or height > 4096
            or width % 2 or height % 2 or not 8 <= fps <= 60):
        raise ValueError("Invalid framing dimensions or FPS")
    if not all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", value)
               for value in (prefix, output_name)):
        raise ValueError("Invalid FFmpeg pad name")
    if tail_filters and (";" in tail_filters or "[" in tail_filters or "]" in tail_filters):
        raise ValueError("Invalid framing tail filters")
    motion = photo_zoom_filter(width=width, height=height, fps=fps,
                               frames=frames, zoom_percent=0.04)
    small_w, small_h = max(32, width // 16), max(32, height // 16)
    suffix = ("," + tail_filters) if tail_filters else ""
    return [
        f"[{source_index}:v]split=2[{prefix}bg][{prefix}fg]",
        (f"[{prefix}bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
         f"crop={width}:{height},scale={small_w}:{small_h},gblur=sigma=5,"
         f"scale={width}:{height}:flags=bicubic,"
         "eq=contrast=0.85:saturation=0.75,format=yuv420p"
         f"[{prefix}matte]"),
        (f"[{prefix}fg]scale={width}:{height}:force_original_aspect_ratio=decrease,"
         f"setsar=1,format=yuv420p[{prefix}hero];"
         f"[{prefix}matte][{prefix}hero]overlay=(W-w)/2:(H-h)/2:shortest=1,"
         f"{motion},"
         "eq=contrast=1.05:saturation=1.07,"
         "scale=in_range=auto:out_range=tv,format=yuv420p"
         f"{suffix}[{output_name}]")
    ]
