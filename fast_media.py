"""Single-encode FFmpeg composition of still images, videos and abstract gradients.

For short media storyboards, normalize all inputs into one frame-accurate CFR
filter graph instead of generating intermediate lossy H.264 scene clips.
Input paths are passed as distinct subprocess arguments, never via a shell.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Protocol

from cinematic import gradient_source, transition_plan
from scene_framing import needs_soft_matte, soft_matte_graph
from video_timeline import source_seek_seconds
from exposure_match import exposure_filter
from white_balance import white_balance_suffix


class SceneMedia(Protocol):
    kind: str
    file: Path


def build_onepass_media_command(
    assets: list[SceneMedia | None], durations: list[float], *,
    width: int, height: int, fps: int, transition: str,
    visual_filters: list[str], audio: Path | None, output: Path, total: float,
) -> list[str]:
    """Build a bounded filter graph with only one H.264 encode.

    At most four inputs limit concurrent decoder, scaling and xfade memory.
    The renderer chooses this mode conservatively and can fall back if needed.
    """
    if not 1 <= len(assets) <= 4 or len(assets) != len(durations):
        raise ValueError("Media single-pass mode supports 1–4 matching scenes")
    if not any(asset is not None for asset in assets):
        raise ValueError("At least one real media asset is required")
    if not 240 <= width <= 4096 or not 240 <= height <= 4096 or width % 2 or height % 2:
        raise ValueError("Invalid output dimensions")
    if not 8 <= fps <= 60:
        raise ValueError("Unsupported output frame rate")
    if not visual_filters or any(not isinstance(f, str) or not f for f in visual_filters):
        raise ValueError("Missing visual overlays")
    if not math.isfinite(total) or abs(total - sum(durations)) > .02:
        raise ValueError("Narration and scene timeline disagree")
    plan = transition_plan(durations, fps=fps, transition=transition)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-filter_complex_threads", "1"]
    graph: list[str] = []
    for i, asset in enumerate(assets):
        frames = round(plan.durations[i] * fps) + plan.extra_frames[i]
        runtime = frames / fps
        if asset is None:
            cmd += ["-f", "lavfi", "-i", f"{gradient_source(i,width,height,fps)}:d={runtime:.9f}"]
            effect = "format=yuv420p"
        elif asset.kind == "image":
            cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{runtime:.9f}", "-i", str(asset.file)]
            effect = (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                      f"crop={width}:{height},setsar=1,"
                      f"zoompan=z='min(zoom+0.0008,1.10)':d=1:s={width}x{height}:fps={fps},"
                      "eq=contrast=1.05:saturation=1.07,"
                      "scale=in_range=auto:out_range=tv,format=yuv420p")
        elif asset.kind == "video":
            cmd += ["-stream_loop", "-1"]
            seek = source_seek_seconds(asset)
            if seek:
                cmd += ["-ss", f"{seek:.3f}"]
            cmd += ["-t", f"{runtime:.9f}", "-i", str(asset.file)]
            effect = (f"fps={fps},scale={width}:{height}:force_original_aspect_ratio=increase,"
                      f"crop={width}:{height},setsar=1,"
                      f"{exposure_filter(asset)}{white_balance_suffix(asset)},format=yuv420p")
        else:
            raise ValueError("Unsupported media kind")
        # Force a known frame count, PTS origin, frame rate, pixel format and SAR
        # even for variable-frame-rate clips and full-range JPEG images.
        frame_tail = (f"trim=end_frame={frames},setpts=N/({fps}*TB),"
                      f"fps={fps},format=yuv420p,setparams=range=limited,setsar=1")
        if needs_soft_matte(asset):
            graph.extend(soft_matte_graph(i, width=width, height=height,
                fps=fps, prefix=f"soft{i}", output_name=f"s{i}",
                tail_filters=frame_tail))
        else:
            graph.append(f"[{i}:v]{effect},{frame_tail}[s{i}]")
    if audio is not None:
        cmd += ["-i", str(audio)]
    last = "[s0]"
    if len(assets) > 1:
        if plan.overlap_frames:
            for i in range(1, len(assets)):
                result = f"[xf{i}]"
                graph.append(f"{last}[s{i}]xfade=transition={transition}:"
                             f"duration={plan.overlap_frames/fps:.9f}:"
                             f"offset={plan.offset_frames[i-1]/fps:.9f}{result}")
                last = result
        else:
            graph.append("".join(f"[s{i}]" for i in range(len(assets))) +
                         f"concat=n={len(assets)}:v=1:a=0[merged]")
            last = "[merged]"
    graph.append(f"{last}{','.join(visual_filters)}[video]")
    cmd += ["-filter_complex", ";".join(graph), "-map", "[video]"]
    if audio is not None:
        cmd += ["-map", f"{len(assets)}:a:0", "-c:a", "copy"]
    else:
        cmd += ["-an"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-frames:v", str(plan.total_frames),
            "-movflags", "+faststart", "-t", f"{total:.3f}", str(output)]
    return cmd
