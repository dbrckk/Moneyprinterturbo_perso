"""One-pass FFmpeg rendering for storyboards containing only generated gradients.

Preserves the original scene timeline, captions and encoded delivery format.
Scenes containing real images or clips keep the existing multi-stage renderer.
"""
from __future__ import annotations

import math
from pathlib import Path

from cinematic import gradient_source, transition_plan


def build_onepass_abstract_command(
    durations: list[float], *, width: int, height: int, fps: int,
    transition: str, visual_filters: list[str], audio: Path | None,
    output: Path, total: float,
) -> list[str]:
    """Make one graph for backgrounds, transitions and final overlays.

    Input gradients have exact frame-quantized durations so we never encode
    intermediate H.264 files. A final output frame limit fixes the timeline.
    All dynamic words are supplied through trusted drawtext/ASS text files.
    """
    if not visual_filters or not all(isinstance(x, str) and x for x in visual_filters):
        raise ValueError("Missing final visual filters")
    if not 240 <= width <= 4096 or not 240 <= height <= 4096 or width % 2 or height % 2:
        raise ValueError("Invalid video dimensions")
    plan = transition_plan(durations, fps=fps, transition=transition)
    if not math.isfinite(total) or abs(total - sum(durations)) > 0.02:
        raise ValueError("Narration and video duration mismatch")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-filter_complex_threads", "1"]
    for idx, frames_duration in enumerate(plan.durations):
        frame_count = round(frames_duration * fps) + plan.extra_frames[idx]
        source = gradient_source(idx, width, height, fps)
        cmd.extend(["-f", "lavfi", "-i", f"{source}:d={frame_count/fps:.9f}"])
    if audio is not None:
        cmd.extend(["-i", str(audio)])
    graph: list[str] = []
    for idx in range(len(durations)):
        graph.append(f"[{idx}:v]format=yuv420p[scene{idx}]")
    last = "[scene0]"
    if len(durations) > 1:
        if plan.overlap_frames:
            for idx in range(1, len(durations)):
                result = f"[xf{idx}]"
                graph.append(
                    f"{last}[scene{idx}]xfade=transition={transition}:"
                    f"duration={plan.overlap_frames/fps:.9f}:"
                    f"offset={plan.offset_frames[idx-1]/fps:.9f}{result}")
                last = result
        else:
            concat = "".join(f"[scene{idx}]" for idx in range(len(durations)))
            graph.append(f"{concat}concat=n={len(durations)}:v=1:a=0[merged]")
            last = "[merged]"
    graph.append(f"{last}{','.join(visual_filters)}[video]")
    cmd.extend(["-filter_complex", ";".join(graph), "-map", "[video]"])
    if audio is not None:
        cmd.extend(["-map", f"{len(durations)}:a:0", "-c:a", "aac", "-b:a", "128k"])
    else:
        cmd.append("-an")
    cmd.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p", "-frames:v", str(plan.total_frames),
                "-movflags", "+faststart", "-t", f"{total:.3f}", str(output)])
    return cmd
