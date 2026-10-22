"""Preserve extremely wide Wikimedia photos inside vertical videos.

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
