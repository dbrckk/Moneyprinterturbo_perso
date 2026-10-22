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

from final_scene_review import inspect_final_boundaries

MAX_METADATA_BYTES = 2_000_000
MAX_SCENES = 18
SRT_TIME = r"(\d{2}:\d{2}:\d{2},\d{3})"
SRT_CUE_LINE = re.compile(rf"^{SRT_TIME}\s+-->\s+{SRT_TIME}$")


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


def parse_srt_cues(path: Path) -> list[tuple[int, float, float, str]]:
    """Parse the full editable SRT, including the actual subtitle text.

    FFmpeg can render a video successfully while a corrupt SRT sidecar contains
    identical timestamps but wrong narration, duplicate cue numbers or garbage.
    Do not accept partial regex matches that skip malformed entries.
    """
    content = path.read_text(encoding="utf-8-sig")
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise ValueError("Empty SRT subtitles")
    cues: list[tuple[int, float, float, str]] = []
    for block in re.split(r"\n[ \t]*\n", normalized):
        lines = block.split("\n")
        if len(lines) < 3 or not re.fullmatch(r"[0-9]+", lines[0].strip()):
            raise ValueError("Malformed SRT cue header")
        match = SRT_CUE_LINE.fullmatch(lines[1].strip())
        if not match:
            raise ValueError("Malformed SRT cue timing")
        start, end = srt_seconds(match.group(1)), srt_seconds(match.group(2))
        subtitle = " ".join(" ".join(lines[2:]).split())
        if not subtitle or not 0 <= start < end:
            raise ValueError("Empty or invalid SRT subtitle")
        cues.append((int(lines[0].strip()), start, end, subtitle))
    return cues


def stream_duration(stream: dict) -> float | None:
    """Read stream-specific time, not MP4 container length (which can be AAC).

    Some malformed MP4s contain three seconds of AAC and only two seconds of
    H.264. ffprobe format.duration says three seconds, hiding the cut-off video.
    """
    try:
        raw = stream.get("duration", float("nan"))
        if isinstance(raw, bool):
            return None
        value = float(raw)
    except (TypeError, ValueError, AttributeError):
        return None
    return value if math.isfinite(value) and value > 0 else None


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
    # MP4 format.duration follows the longest track. A full-length AAC track
    # can mask an early-ended H.264 track in that container. Check them
    # independently rather than trusting the apparent final MP4 duration.
    video_time = stream_duration(video_stream)
    if video_time is None:
        errors.append("Video stream duration unavailable or invalid")
    elif _positive(expected) and abs(video_time - expected) > 0.30:
        errors.append(
            f"Video stream length drift: {video_time:.3f}s vs narration {expected:.3f}s")
    audio_time = None
    if metadata.get("speech") is True and len(audio_streams) == 1:
        audio_time = stream_duration(audio_streams[0])
        if audio_time is None:
            errors.append("Audio stream duration unavailable or invalid")
        elif _positive(expected) and abs(audio_time - expected) > 0.35:
            errors.append(
                f"Audio stream length drift: {audio_time:.3f}s vs narration {expected:.3f}s")
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
        try:
            cues = parse_srt_cues(subtitle_path)
        except (OSError, ValueError, UnicodeError):
            errors.append("Invalid or malformed editable SRT subtitles")
        else:
            if len(cues) != len(segments):
                errors.append(f"Subtitle cue count {len(cues)} differs from scenes {len(segments)}")
            else:
                for index, (number, start, end, subtitle) in enumerate(cues):
                    scene = segments[index]
                    if number != index + 1:
                        errors.append(f"Subtitle cue {index + 1} has an invalid sequence number")
                    try:
                        if abs(start - float(scene["start"])) > 0.025 or abs(end - float(scene["end"])) > 0.025:
                            errors.append(f"Subtitle cue {index + 1} is not aligned with its scene")
                        if subtitle != " ".join(str(scene["text"]).split()):
                            errors.append(f"Subtitle cue {index + 1} text differs from narration")
                    except (TypeError, ValueError, KeyError):
                        errors.append(f"Subtitle cue {index + 1} has invalid scene metadata")
    else:
        errors.append("Missing or oversized editable SRT subtitles")
    if video.stat().st_size < 1000:
        errors.append("Video file is empty or suspiciously small")
    if not metadata.get("media"):
        warnings.append("No real photographic/video media are reported; verify visual relevance")
    if metadata.get("speech_engine") == "espeak":
        warnings.append("eSpeak is synthetic; consider a reviewed neural TTS voice")
    # These are review prompts, not false claims of machine-certified visual
    # quality: photo/art classification is based on Commons textual metadata.
    listed_media = metadata.get("media", [])
    if isinstance(listed_media, list):
        for item in listed_media:
            if not isinstance(item, dict):
                continue
            scene = item.get("segment", "?")
            if item.get("visual_medium") == "artwork-likely":
                warnings.append(f"Scene {scene}: selected medium may be artwork, not photography")
            # Only score licensed Commons images that carry explicit evidence.
            # Older manifests and locally supplied media are not guessed.
            coverage = item.get("query_term_coverage")
            term_count = item.get("query_term_count")
            source = item.get("source")
            if (isinstance(source, str) and
                    source.startswith("https://commons.wikimedia.org/wiki/") and
                    isinstance(term_count, int) and not isinstance(term_count, bool) and
                    term_count >= 2 and isinstance(coverage, (int, float)) and
                    not isinstance(coverage, bool) and math.isfinite(coverage) and
                    0 <= coverage < 0.5):
                warnings.append(
                    f"Scene {scene}: fewer than half the search concepts match Wikimedia text; review visual relevance")
            retained = item.get("crop_retention_estimate")
            if (_positive(retained) and retained <= 1 and retained < 0.34
                    and item.get("render_framing") != "soft-matte-full-image"):
                warnings.append(f"Scene {scene}: source framing loses over two thirds of image area to center crop; review storyboard")
    commons_stats = metadata.get("commons_fetch")
    if isinstance(commons_stats, dict):
        duplicates = commons_stats.get("scenes_reusing_image")
        if isinstance(duplicates, int) and not isinstance(duplicates, bool) and duplicates > 0:
            warnings.append(f"{duplicates} Commons scene(s) reuse a previously selected image; review variety")
        adjacent = commons_stats.get("adjacent_visual_reuses")
        if isinstance(adjacent, int) and not isinstance(adjacent, bool) and adjacent > 0:
            warnings.append(
                f"{adjacent} consecutive Commons scene transition(s) reuse visually similar imagery; review the storyboard")
    return {
        "ok": not errors,
        "checks": {"duration": round(duration, 3) if _positive(duration) else None,
                   "expected_duration": expected,
                   "resolution": actual_size,
                   "video_codec": video_stream.get("codec_name"),
                   "pixel_format": video_stream.get("pix_fmt"),
                   "audio_codec": audio_streams[0].get("codec_name") if audio_streams else None,
                   "scene_count": len(segments),
                   "video_stream_duration": round(video_time, 3) if video_time is not None else None,
                   "audio_stream_duration": round(audio_time, 3) if audio_time is not None else None,
                   "bytes": video.stat().st_size},
        "errors": errors, "warnings": warnings,
    }


def inspect_midpoint_frames(video: Path, segments: list[dict]) -> dict:
    """Sample actual rendered pixels, not labels in the storyboard PNG.

    Midpoint sampling can flag very dark or nearly uniform frames, but cannot
    establish artistic merit, correct subject matter, or scene motion.
    All findings are non-blocking prompts for human storyboard review.
    """
    scenes: list[dict[str, object]] = []
    warnings: list[str] = []
    unreadable = 0
    for index, segment in enumerate(segments):
        try:
            start, end = float(segment["start"]), float(segment["end"])
            midpoint = (start + end) / 2
            if not math.isfinite(midpoint) or not 0 <= midpoint <= 3600:
                raise ValueError("Invalid scene time")
            process = subprocess.run(
                ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
                 "-ss", f"{midpoint:.3f}", "-i", str(video),
                 "-vf", "scale=16:16:flags=area,format=gray",
                 "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"],
                capture_output=True, timeout=20, check=False,
            )
            if process.returncode or len(process.stdout) != 256:
                raise RuntimeError("Missing frame sample")
            pixels = sorted(process.stdout)
            median = (pixels[127] + pixels[128]) / 2
            dark_share = sum(pixel <= 18 for pixel in pixels) / 256
            flat_share = sum(abs(pixel - median) <= 4 for pixel in pixels) / 256
            findings: list[str] = []
            if dark_share >= 0.90:
                findings.append("mostly-dark")
                warnings.append(
                    f"Scene {index+1}: midpoint preview is almost entirely dark; review storyboard")
            elif flat_share >= 0.97:
                findings.append("near-uniform")
                warnings.append(
                    f"Scene {index+1}: midpoint preview is nearly uniform; review storyboard")
            scenes.append({
                "scene": index + 1, "median_luma": round(median, 1),
                "dark_pixel_share": round(dark_share, 3),
                "near_median_pixel_share": round(flat_share, 3),
                "findings": findings,
            })
        except (OSError, ValueError, KeyError, TypeError, RuntimeError,
                subprocess.SubprocessError):
            unreadable += 1
    if unreadable:
        warnings.append(
            f"Visual midpoint sampling unavailable for {unreadable} scene(s); review storyboard")
    return {
        "method": "rendered-midpoint-16x16-luma", "scenes": scenes,
        "samples_unavailable": unreadable, "warnings": warnings,
    }


def silence_share(start: float, end: float, intervals: list[list[float]]) -> float:
    """Estimate fraction of scene covered by quiet audio; works across gaps."""
    if not all(math.isfinite(v) for v in (start, end)) or end <= start:
        return 0.0
    overlap = sum(max(0.0, min(end, finish) - max(start, begin))
                  for begin, finish in intervals)
    return min(1.0, overlap / (end - start))


def audio_loudness(video: Path) -> dict[str, object]:
    """Analyze AAC loudness independently of its intended mastering target.

    FFmpeg reports measured LUFS/true peak; these are objective audio metrics,
    not a certificate of human-perceived quality. No second audio encoding.
    """
    process = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "info",
         "-i", str(video), "-map", "0:a:0",
         "-af", "ebur128=peak=true,silencedetect=noise=-48dB:d=0.4",
         "-f", "null", "-"],
        capture_output=True, text=True, timeout=100,
    )
    if process.returncode:
        raise RuntimeError("Audio loudness analysis failed: " + process.stderr[-800:])
    integrated = re.findall(r"Integrated loudness:\s*I:\s*([-+\d.]+)\s*LUFS",
                            process.stderr)
    peak = re.findall(r"True peak:\s*Peak:\s*([-+\d.]+)\s*dBFS",
                      process.stderr)
    if not integrated:
        raise RuntimeError("FFmpeg did not report integrated audio loudness")
    lufs = float(integrated[-1])
    true_peak = float(peak[-1]) if peak else None
    quiet: list[list[float]] = []
    beginning: float | None = None
    # FFmpeg can report silence_end at EOF, even for a fully silent track.
    for event in re.finditer(r"silence_(start|end):\s*([0-9]+(?:\.[0-9]+)?)",
                             process.stderr):
        position = float(event.group(2))
        if event.group(1) == "start":
            beginning = position
        elif beginning is not None and position >= beginning:
            quiet.append([round(beginning, 4), round(position, 4)])
            beginning = None
    # Some FFmpeg builds leave an open silence_start at EOF. Preserve it,
    # otherwise an entirely quiet AAC stream can escape per-scene checks.
    if beginning is not None:
        try:
            duration = float(json.loads(execute([
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "json", str(video)], timeout=25).stdout)["format"]["duration"])
            if math.isfinite(duration) and duration > beginning:
                quiet.append([round(beginning, 4), round(duration, 4)])
        except (KeyError, TypeError, ValueError, RuntimeError, subprocess.TimeoutExpired):
            pass
    return {"integrated_lufs": round(lufs, 2) if math.isfinite(lufs) else None,
            "true_peak_dbfs": (round(true_peak, 2) if true_peak is not None
                               and math.isfinite(true_peak) else None),
            "quiet_intervals_seconds": quiet}


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
    if metadata.get("speech") is True and result["checks"]["audio_codec"] == "aac":
        try:
            result["audio_loudness"] = audio_loudness(video)
            measured = result["audio_loudness"]["integrated_lufs"]
            peak = result["audio_loudness"]["true_peak_dbfs"]
            # Valid AAC packets do not imply an audible narration. A silent
            # audio stream used to pass technical QA despite missing voice.
            if measured is None or measured <= -55 or peak is None or peak <= -55:
                result["errors"].append(
                    "Narrated MP4 contains no detectable voice audio (silent AAC track)")
                result["ok"] = False
            for index, scene in enumerate(metadata.get("segments", [])):
                try:
                    start, end = float(scene["start"]), float(scene["end"])
                except (KeyError, ValueError, TypeError):
                    continue
                if end - start >= 1.0 and silence_share(
                        start, end, result["audio_loudness"]["quiet_intervals_seconds"]) >= 0.97:
                    result["errors"].append(
                        f"Scene {index+1}: narration is almost entirely silent")
                    result["ok"] = False
            if measured is not None and (measured < -21 or measured > -11):
                result["warnings"].append(
                    f"Measured audio loudness {measured} LUFS may be unsuitable for mobile")
            if peak is not None and peak > -0.1:
                result["warnings"].append("Audio true peak is very close to 0 dBFS; review clipping")
        except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
            result["errors"].append(
                f"Narrated MP4 audio activity could not be verified: {type(exc).__name__}")
            result["ok"] = False
    sheet_path = sheet_path or video.with_suffix(".storyboard.png")
    if result["checks"]["video_codec"] == "h264":
        try:
            result["storyboard"] = contact_sheet(video, metadata, sheet_path)
            review = inspect_midpoint_frames(video, metadata["segments"])
            result["visual_review"] = {
                key: value for key, value in review.items() if key != "warnings"
            }
            result["warnings"].extend(review["warnings"])
        except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            result["errors"].append(f"Storyboard unavailable: {exc}")
            result["ok"] = False
        # A separate low-resolution scan checks real *encoded* boundaries,
        # including unintended black flashes that midpoint-only QA misses.
        # These are review prompts, never delivery errors: edits may be
        # intentionally high-contrast and fades do not certify artistry.
        try:
            crossings = inspect_final_boundaries(
                video, metadata["segments"], metadata.get("scene_transitions"))
            result["boundary_visual_review"] = {
                key: val for key, val in crossings.items() if key != "warnings"
            }
            result["warnings"].extend(crossings["warnings"])
        except (ValueError, OSError, RuntimeError,
                subprocess.SubprocessError) as exc:
            result["warnings"].append(
                "Encoded scene-boundary review could not be completed: " +
                type(exc).__name__)
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