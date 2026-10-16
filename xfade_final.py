"""One final FFmpeg H.264 encode for cinematic xfade plus graphics.

All clips were normalized to identical CFR dimensions/time bases upstream.
Use a single filter graph to prevent the intermediate xfade transcoding pass.
"""
from __future__ import annotations
import math
from pathlib import Path
from typing import Protocol

class FramePlan(Protocol):
    total_frames: int
    overlap_frames: int
    offset_frames: tuple[int, ...]

def final_xfade_command(scenes: list[Path], plan: FramePlan, *, fps: int,
                        transition: str, overlays: list[str],
                        duration: float, output: Path, audio: Path | None = None,
                        preset: str = "veryfast", crf: int = 23) -> list[str]:
    """Build the no-shell FFmpeg command with a single output encode."""
    if transition not in {"fade", "wipeleft"}:
        raise ValueError("Only real crossfades use the single-pass pipeline")
    if not 8 <= fps <= 60 or not 2 <= len(scenes) <= 18:
        raise ValueError("Invalid frame rate or scene count")
    if not plan.overlap_frames or plan.overlap_frames % 2 or plan.total_frames <= 0:
        raise ValueError("No valid even-frame overlap")
    if len(plan.offset_frames) != len(scenes) - 1:
        raise ValueError("Transition offsets do not match scenes")
    if (not math.isfinite(duration) or duration <= 0 or
            abs(plan.total_frames / fps - duration) > 1 / fps + 1e-6):
        raise ValueError("Narration length differs from frame timeline")
    if not overlays or any(not isinstance(f, str) or not f for f in overlays):
        raise ValueError("Invalid graphics filters")
    if preset not in {"ultrafast","superfast","veryfast","faster","fast","medium"} or not 16 <= crf <= 30:
        raise ValueError("Unsupported encoder settings")
    if any(b <= a for a, b in zip(plan.offset_frames, plan.offset_frames[1:])):
        raise ValueError("Transition offsets are not increasing")
    command = ["ffmpeg","-hide_banner","-loglevel","error","-y",
               "-filter_complex_threads","1"]
    for scene in scenes:
        command += ["-i", str(scene)]
    if audio is not None:
        command += ["-i", str(audio)]
    clauses = []
    previous = "[0:v]"
    for index in range(1,len(scenes)):
        label = f"[xf{index}]"
        clauses.append(f"{previous}[{index}:v]xfade=transition={transition}:"
                       f"duration={plan.overlap_frames/fps:.6f}:"
                       f"offset={plan.offset_frames[index-1]/fps:.6f}{label}")
        previous = label
    clauses.append(f"{previous}{','.join(overlays)}[final_video]")
    command += ["-filter_complex",";".join(clauses),"-map","[final_video]",
                "-c:v","libx264","-preset",preset,"-crf",str(crf),
                "-pix_fmt","yuv420p","-frames:v",str(plan.total_frames)]
    if audio is None:
        command += ["-an"]
    else:
        command += ["-map",f"{len(scenes)}:a:0","-c:a","copy"]
    command += ["-movflags","+faststart","-t",f"{duration:.3f}",str(output)]
    return command
