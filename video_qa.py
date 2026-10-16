#!/usr/bin/env python3
"""Audit a generated short video and export a scene-by-scene contact sheet.

Uses only the Python standard library and FFmpeg/ffprobe. No network, no API,
no downloaded fonts or media. Quality checks are objective delivery checks,
NOT a claim that the creative quality or factual accuracy is certified.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile

MAX_METADATA_BYTES = 2_000_000
MAX_SCENES = 18
SRT_CUE = re.compile(r"(?m)^\s*\d+\s*\r?\n(\d\d:\d\d:\d\d,\d{3})\s*-->\s*(\d\d:\d\d:\d\d,\d{3})")


def execute(argv: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{argv[0]} failed ({result.returncode}): {result.stderr[-1600:]}")
    return result


def probe(video: Path) -> dict:
    return json.loads(execute([
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(video)
    ], timeout=25).stdout)


def srt_seconds(stamp: str) -> float:
    h, m, sec = stamp.split(":")
    s, milliseconds = sec.split(",")
    return 3600 * int(h) + 60 * int(m) + int(s) + int(milliseconds) / 1000


def read_metadata(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size > MAX_METADATA_BYTES:
        raise ValueError("Missing or oversized render metadata JSON")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        raise ValueError("Metadata must contain a scene list")
    if not 1 <= len(data["segments"]) <= MAX_SCENES:
        raise ValueError("Scene count must be between 1 and 18")
    return data


def _positive(value: object) -> bool:
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def validate(video: Path, metadata: dict, subtitle_path: Path, stream_info: dict) -> dict:
    """Perform delivery checks; collect all failed checks without early exit."""
    errors: list[str] = []
    warnings: list[str] = []
    streams = stream_info.get("streams", [])
    video_streams = [item for item in streams if item.get("codec_type") == "video"]
    audio_streams = [item for item in streams if item.get("codec_type") == "audio"]
    video_stream = video_streams[0] if video_streams else {}
    duration_raw = stream_info.get("format", {}).get("duration", 0)
    try:
        duration = float(duration_raw)
    except (TypeError, ValueError):
        duration = float("nan")
    expected = metadata.get("duration_seconds")
    if not _positive(duration) or duration > 3600:
        errors.append("Video duration is invalid or exceeds one hour")
    if not _positive(expected):
        errors.append("Expected duration is missing or invalid")
    if _positive(expected) and _positive(duration) and abs(duration - expected) > 0.30:
        errors.append(f"Video length drift: {duration:.3f}s vs narration {expected:.3f}s")
    if len(video_streams) != 1 or video_stream.get("codec_name") != "h264":
        errors.append("Expected exactly one H.264 video stream")
    if video_stream.get("pix_fmt") != "yuv420p":
        errors.append("Video must be yuv420p for mobile playback")
    expected_size = metadata.get("size")
    actual_size = [video_stream.get("width"), video_stream.get("height")]
    if actual_size != expected_size:
        errors.append(f"Video resolution mismatch: {actual_size} vs {expected_size}")
    has_speech = metadata.get("speech")
    if has_speech is True and (len(audio_streams) != 1 or audio_streams[0].get("codec_name") != "aac"):
        errors.append("Narrated video must have exactly one AAC audio stream")
    if has_speech is False and audio_streams:
        errors.append("Muted video unexpectedly contains an audio stream")
    if not isinstance(has_speech, bool):
        errors.append("Missing speech flag in metadata")
    segments = metadata["segments"]
    previous = 0.0
    for index, segment in enumerate(segments):
        try:
            start, end = float(segment["start"]), float(segment["end"])
        except (KeyError, ValueError, TypeError, AttributeError):
            errors.append(f"Scene {index + 1} has invalid timestamps")
            continue
        if not all(map(math.isfinite, (start, end))) or abs(start - previous) > 0.012 or end <= start:
            errors.append(f"Scene {index + 1} has a discontinuous or invalid timeline")
        previous = end
    if _positive(expected) and abs(previous - expected) > 0.025:
        errors.append("Last scene does not end at narrated duration")
    if subtitle_path.is_file() and subtitle_path.stat().st_size <= MAX_METADATA_BYTES:
        cues = [(srt_seconds(a), srt_seconds(b)) for a, b in SRT_CUE.findall(subtitle_path.read_text(encoding="utf-8-sig"))]
        if len(cues) != len(segments):
            errors.append(f"Subtitle cue count {len(cues)} differs from scenes {len(segments)}")
        else:
            for index, (start, end) in enumerate(cues):
                scene = segments[index]
                try:
                    if abs(start - float(scene["start"])) > 0.025 or abs(end - float(scene["end"])) > 0.025:
                        errors.append(f"Subtitle cue {index + 1} is not aligned with its scene")
                except (TypeError, ValueError, KeyError):
                    errors.append(f"Subtitle cue {index + 1} has invalid scene timestamps")
    else:
        errors.append("Missing or oversized editable SRT subtitles")
    if video.stat().st_size < 1000:
        errors.append("Video file is empty or suspiciously small")
    if not metadata.get("media"):
        warnings.append("No real photographic/video media are reported; verify visual relevance")
    if metadata.get("speech_engine") == "espeak":
        warnings.append("eSpeak is synthetic; consider a reviewed neural TTS voice")
    return {
        "ok": not errors,
        "checks": {"duration": round(duration, 3) if _positive(duration) else None,
                   "expected_duration": expected,
                   "resolution": actual_size,
                   "video_codec": video_stream.get("codec_name"),
                   "pixel_format": video_stream.get("pix_fmt"),
                   "audio_codec": audio_streams[0].get("codec_name") if audio_streams else None,
                   "scene_count": len(segments),
                   "bytes": video.stat().st_size},
        "errors": errors, "warnings": warnings,
    }


def contact_sheet(video: Path, metadata: dict, output: Path, *, columns: int = 3) -> dict:
    """Export each scene's midpoint, with a scene number burned into the tile."""
    if not 1 <= columns <= 6:
        raise ValueError("Contact sheet column count must be 1–6")
    size = metadata.get("size", [])
    if not isinstance(size, list) or len(size) != 2 or any(not isinstance(n, int) or n < 64 for n in size):
        raise ValueError("Video dimensions missing from metadata")
    width, height = size
    landscape = width > height
    thumb_w, thumb_h = ((320, 180) if landscape else (200, 200) if width == height else (180, 320))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mpt-contact-") as folder:
        folder_path = Path(folder)
        images = []
        for index, scene in enumerate(metadata["segments"]):
            start, end = float(scene["start"]), float(scene["end"])
            middle = (start + end) / 2
            path = folder_path / f"scene_{index:03d}.png"
            filter_text = (
                f"scale={thumb_w}:{thumb_h}:force_original_aspect_ratio=decrease,"
                f"pad={thumb_w}:{thumb_h}:(ow-iw)/2:(oh-ih)/2:color=0x08111f,"
                "drawbox=x=6:y=6:w=68:h=27:color=0x08111f@0.80:t=fill,"
                f"drawtext=text='SCENE {index+1:02d}':fontcolor=white:fontsize=12:x=12:y=13"
            )
            execute(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                     "-ss", f"{middle:.3f}", "-i", str(video),
                     "-vf", filter_text, "-frames:v", "1", str(path)], timeout=45)
            if not path.is_file() or path.stat().st_size < 500:
                raise RuntimeError(f"Cannot extract frame from scene {index+1}")
            images.append(path)
        rows = math.ceil(len(images) / columns)
        # All stills have one consistent raster size, so FFmpeg's tile filter
        # requires no extra image packages or proprietary fonts.
        execute(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-framerate", "1", "-start_number", "0", "-i", str(folder_path / "scene_%03d.png"),
                 "-vf", f"tile={columns}x{rows}:padding=8:margin=8:color=0x101827",
                 "-frames:v", "1", "-update", "1", str(output)], timeout=90)
    if not output.exists() or output.stat().st_size < 1000:
        raise RuntimeError("Contact sheet generation returned an empty image")
    return {"path": str(output), "scenes": len(images), "columns": columns, "rows": rows}


def audit(video: Path, *, metadata_path: Path | None = None,
          output_path: Path | None = None, sheet_path: Path | None = None) -> dict:
    video = video.expanduser().resolve()
    if not video.is_file():
        raise ValueError(f"Video missing: {video}")
    metadata_path = metadata_path or video.with_suffix(".json")
    metadata = read_metadata(metadata_path)
    result = validate(video, metadata, video.with_suffix(".srt"), probe(video))
    result["file"] = video.name
    sheet_path = sheet_path or video.with_suffix(".storyboard.png")
    if result["checks"]["video_codec"] == "h264":
        try:
            result["storyboard"] = contact_sheet(video, metadata, sheet_path)
        except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            result["errors"].append(f"Storyboard unavailable: {exc}")
            result["ok"] = False
    output_path = output_path or video.with_suffix(".quality.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit a Free Studio MP4 and generate storyboard PNG/quality JSON")
    parser.add_argument("video", type=Path, help="MP4 generated by Free Studio")
    parser.add_argument("--metadata", type=Path, help="Optional JSON path")
    parser.add_argument("--report", type=Path, help="Optional quality report path")
    parser.add_argument("--storyboard", type=Path, help="Optional contact sheet PNG path")
    args = parser.parse_args(argv)
    try:
        report = audit(args.video, metadata_path=args.metadata, output_path=args.report, sheet_path=args.storyboard)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        parser.exit(2, f"Quality audit failed: {exc}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())